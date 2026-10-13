import json

import pytest
from fastapi.testclient import TestClient
from openai import OpenAIError

from app.server import create_app
from tests.conftest import FakeClient, text_chunks, tool_chunks

SESSION = {"session_id": "test-session-1"}


@pytest.fixture
def kiosk(tmp_path):
    """가짜 모델을 붙인 서버. script 에 응답을 넣어 두고 요청을 보낸다."""
    fake = FakeClient()
    return TestClient(create_app(client=fake, index_dir=tmp_path)), fake


def chat(client, message):
    response = client.post("/api/chat", json={**SESSION, "message": message})
    assert response.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")]


def test_pages_and_menu(kiosk):
    client, _ = kiosk
    assert "공차 키오스크" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/config").json()["ready"] is True
    tabs = client.get("/api/menu").json()["tabs"]
    first = tabs[0]["items"][0]
    assert client.get(first["image"]).status_code == 200
    detail = client.get(f"/api/menu/{first['id']}").json()
    assert detail["name"] == first["name"] and detail["nutrition"]
    assert client.get("/api/menu/nope").status_code == 404


def test_session_opens_with_greeting_and_chips(kiosk):
    client, _ = kiosk
    session = client.post("/api/session", json=SESSION).json()
    assert "시흥배곧점" in session["greeting"] and len(session["chips"]) == 4
    assert session["order"]["lines"] == [] and session["language"] == "ko"
    english = client.post("/api/session/language", json={**SESSION, "language": "en"}).json()
    assert english["greeting"].startswith("Hello") and english["chips"][0]["label"] == "What's popular?"
    assert client.post("/api/session/language", json={**SESSION, "language": "xx"}).status_code == 400


def test_chat_stream_updates_the_cart(kiosk):
    client, fake = kiosk
    fake.script += [tool_chunks(("add_to_cart", {"menu_name": "타로 밀크티", "size": "L", "temperature": "ICE"})),
                    text_chunks("**타로 밀크티** L 사이즈를 담았어요.")]
    events = chat(client, "타로 밀크티 아이스 L 하나 주세요")
    assert [e["type"] for e in events][-1] == "done"
    assert "담았어요" in "".join(e["text"] for e in events if e["type"] == "delta")
    lines = client.post("/api/session", json=SESSION).json()["order"]["lines"]
    assert lines[0]["name"] == "타로 밀크티" and lines[0]["sugar"] == 50 and lines[0]["line_total"] == 4900

    # 화면의 +/− 버튼과 매장·할인 선택도 같은 주문을 고친다
    line_id = lines[0]["line_id"]
    more = client.post("/api/order/line", json={**SESSION, "line_id": line_id, "quantity": 3}).json()
    assert more["order"]["totals"]["subtotal"] == 14700
    opted = client.post("/api/order/options", json={**SESSION, "dine_option": "takeout", "discount": "skt"}).json()
    assert opted["order"]["totals"]["total"] == 13230 and opted["chips"][0]["label"] == "결제할게요"
    emptied = client.post("/api/order/line", json={**SESSION, "line_id": line_id, "quantity": 0}).json()
    assert emptied["order"]["lines"] == []
    assert client.post("/api/order/line", json={**SESSION, "line_id": 99, "quantity": 1}).status_code == 400


def test_reset_and_staff_call(kiosk):
    client, fake = kiosk
    fake.script += [text_chunks("안녕하세요!")]
    chat(client, "안녕")
    assert len(client.post("/api/session", json=SESSION).json()["history"]) == 2
    assert client.post("/api/staff", json=SESSION).json()["staff_call"]["reason"]
    fresh = client.post("/api/session/reset", json=SESSION).json()
    assert fresh["history"] == [] and fresh["staff_call"] is None


def test_without_api_key_the_screen_still_opens(tmp_path, monkeypatch):
    def no_key():
        raise OpenAIError("OPENAI_API_KEY 가 없습니다")

    monkeypatch.setattr("app.server.OpenAI", no_key)
    client = TestClient(create_app(index_dir=tmp_path))
    config = client.get("/api/config").json()
    assert config["ready"] is False and "OPENAI_API_KEY" in config["error"]
    assert client.get("/api/menu").json()["tabs"]
    events = chat(client, "안녕")
    assert [e["type"] for e in events] == ["error", "done"]


def test_add_and_edit_with_options_from_the_menu_popup(kiosk):
    client, _ = kiosk
    config = client.get("/api/config").json()
    assert config["sugar_levels"] == [0, 30, 50, 70, 100] and config["max_toppings"] == 3
    assert {"id": "pearl", "name": "펄", "price": 600} in config["toppings"]
    menu_id = next(i["id"] for t in client.get("/api/menu").json()["tabs"] for i in t["items"] if i["name"] == "블랙 밀크티")

    added = client.post("/api/order/item", json={**SESSION, "menu_id": menu_id, "size": "J", "temperature": "ICE", "sugar": 70,
                                                 "ice": "less", "toppings": ["pearl", "cheese_foam"], "quantity": 2}).json()
    line = added["order"]["lines"][0]
    assert added["line_id"] == line["line_id"]
    assert line["unit_price"] == 6000 + 600 + 900 and line["line_total"] == 15000
    assert (line["sugar"], line["ice"], [t["id"] for t in line["toppings"]]) == (70, "less", ["pearl", "cheese_foam"])

    edited = client.post("/api/order/item", json={**SESSION, "line_id": line["line_id"], "size": "L", "temperature": "HOT",
                                                  "sugar": 30, "toppings": [], "quantity": 1, "tumbler": True}).json()
    line = edited["order"]["lines"][0]
    assert (line["size"], line["temperature"], line["ice"], line["toppings"], line["tumbler"]) == ("L", "HOT", None, [], True)
    assert edited["order"]["totals"]["subtotal"] == 4900

    too_many = {**SESSION, "menu_id": menu_id, "size": "L", "temperature": "ICE", "toppings": ["pearl", "aloe", "coconut", "milk_foam"]}
    assert client.post("/api/order/item", json=too_many).status_code == 400
    assert client.post("/api/order/item", json={**SESSION, "menu_id": "nope"}).status_code == 404
    assert len(client.post("/api/session", json=SESSION).json()["order"]["lines"]) == 1
