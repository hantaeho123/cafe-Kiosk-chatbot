import json
import re

import pytest
from fastapi.testclient import TestClient
from openai import OpenAIError

from app.server import create_app
from tests.conftest import FakeClient, text_chunks, tool_chunks

SESSION = {"session_id": "test-session-1"}
TARO = ("add_to_cart", {"menu_name": "타로 밀크티", "size": "L", "temperature": "ICE"})


@pytest.fixture
def kiosk(tmp_path):
    """가짜 모델을 붙인 서버. script 에 응답을 넣어 두고 요청을 보낸다."""
    fake = FakeClient()
    return TestClient(create_app(client=fake, index_dir=tmp_path)), fake


def post(client, path, **body):
    return client.post(path, json={**SESSION, **body})


def chat(client, message):
    response = post(client, "/api/chat", message=message)
    assert response.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")]


def menu_id(client, name):
    return next(i["id"] for t in client.get("/api/menu").json()["tabs"] for i in t["items"] if i["name"] == name)


def error_code(response):
    return response.json()["detail"]["code"]


def test_pages_menu_and_config(kiosk):
    client, _ = kiosk
    assert "공차 키오스크" in client.get("/").text and "직원 화면" in client.get("/staff").text
    for name in ("app.js", "i18n.js", "pay.js", "staff.js", "style.css"):
        assert client.get(f"/static/{name}").status_code == 200
    config = client.get("/api/config").json()
    assert config["ready"] is True
    assert config["ice_levels"] == ["less", "regular", "full"] and [c["id"] for c in config["cups"]] == ["store", "disposable", "personal"]
    assert config["sugar_help"]["levels"]["30"] and config["toppings"][0]["description"]
    assert {p["id"]: p["device"] for p in config["payment_methods"]}["card"] == "card_slot"
    assert config["devices"]["card_slot"]["location"] and config["idle"] == {"idle_seconds": 120, "warning_seconds": 30}
    assert any(b["kind"] == "gift_card" for b in config["demo"]["barcodes"])

    menu = client.get("/api/menu").json()
    first = menu["tabs"][0]["items"][0]
    assert client.get(first["image"]).status_code == 200 and first["sold_out"] is False
    sold_out = [i["name"] for t in menu["tabs"] for i in t["items"] if i["sold_out"]]
    assert set(sold_out) == {"초코멜로 스무디"} and len(menu["stock"]["sold_out"]) == 1
    detail = client.get(f"/api/menu/{first['id']}").json()
    assert detail["name"] == first["name"] and detail["nutrition"] and detail["kcal"] is not None
    assert detail["prices"][0]["volume_ml"]["ICE"]
    assert client.get("/api/menu/nope").status_code == 404


def test_session_opens_with_greeting_and_chips(kiosk):
    client, _ = kiosk
    session = post(client, "/api/session").json()
    assert "시흥배곧점" in session["greeting"] and len(session["chips"]) == 4
    assert session["order"]["lines"] == [] and session["language"] == "ko"
    assert session["pending"] is None and session["can_undo"] is False
    english = post(client, "/api/session/language", language="en").json()
    assert english["greeting"].startswith("Hello") and english["chips"][0]["label"] == "What's popular?"
    assert post(client, "/api/session/language", language="xx").status_code == 400


def test_chat_proposal_is_applied_only_after_confirming_on_screen(kiosk):
    client, fake = kiosk
    post(client, "/api/order/options", dine_option="dine_in")
    fake.script += [tool_chunks(TARO), text_chunks("**타로 밀크티** L 를 확인 화면에 올렸어요. [확인]을 눌러 주세요.")]
    events = chat(client, "타로 밀크티 아이스 L 하나 주세요")
    assert [e["type"] for e in events][-1] == "done"
    proposal = next(e for e in events if e["type"] == "proposal")
    assert proposal["changes"][0]["after"]["name"] == "타로 밀크티" and proposal["after"]["total"] == 4600
    waiting = post(client, "/api/session").json()
    assert waiting["order"]["lines"] == [] and waiting["pending"]["changes"][0]["type"] == "add"
    assert [c["action"] for c in waiting["chips"]] == ["confirm", "reject"]
    assert error_code(post(client, "/api/pay/start")) == "pending_changes"  # 확인을 건너뛰고 결제로 갈 수 없다

    confirmed = post(client, "/api/order/confirm").json()
    line = confirmed["order"]["lines"][0]
    assert (line["name"], line["sugar"], line["cup"], line["line_total"]) == ("타로 밀크티", 50, "store", 4600)
    assert confirmed["applied"][0]["type"] == "add" and confirmed["pending"] is None and confirmed["can_undo"] is True
    assert confirmed["chips"][0] == {"label": "결제하기", "action": "pay"}
    assert error_code(post(client, "/api/order/confirm")) == "nothing_pending"

    fake.script += [tool_chunks(("update_cart_item", {"line_id": line["line_id"], "quantity": 3})), text_chunks("세 잔으로 올렸어요.")]
    chat(client, "세 잔으로 바꿔 줘")
    rejected = post(client, "/api/order/reject").json()  # [취소]를 누르면 그대로다
    assert rejected["pending"] is None and rejected["order"]["lines"][0]["quantity"] == 1


def test_customer_edits_the_order_with_buttons(kiosk):
    client, fake = kiosk
    post(client, "/api/order/options", dine_option="dine_in")
    added = post(client, "/api/order/item", menu_id=menu_id(client, "타로 밀크티"), size="L", temperature="ICE").json()
    line_id = added["line_id"]
    more = post(client, "/api/order/line", line_id=line_id, quantity=3).json()
    assert more["order"]["totals"]["subtotal"] == 13800 and more["can_undo"] is True
    takeout = post(client, "/api/order/options", dine_option="takeout", straw=True).json()
    assert takeout["order"]["lines"][0]["cup"] == "disposable" and takeout["order"]["straw"] is True
    undone = post(client, "/api/order/undo").json()  # 되돌리기: 직전 변경 하나
    assert undone["order"]["dine_option"] == "dine_in" and undone["order"]["lines"][0]["cup"] == "store"
    assert post(client, "/api/order/line", line_id=99, quantity=1).status_code == 400
    assert post(client, "/api/order/undo").json()["order"]["lines"][0]["quantity"] == 1  # 실패한 조작은 기록에 남지 않는다

    # 확인을 기다리던 변경안은 손님이 주문을 직접 고치면 사라진다
    fake.script += [tool_chunks(("update_cart_item", {"line_id": line_id, "sugar": 30})), text_chunks("올렸어요.")]
    chat(client, "당도 30으로")
    assert post(client, "/api/session").json()["pending"]
    emptied = post(client, "/api/order/clear").json()  # 전체 취소
    assert emptied["order"]["lines"] == [] and emptied["pending"] is None
    assert post(client, "/api/order/undo").json()["order"]["lines"][0]["name"] == "타로 밀크티"


def test_option_screen_adds_edits_and_suggests(kiosk):
    client, fake = kiosk
    post(client, "/api/order/options", dine_option="dine_in")
    black = menu_id(client, "블랙 밀크티")
    added = post(client, "/api/order/item", menu_id=black, size="J", temperature="ICE", sugar=70, ice="less",
                 toppings=["pearl", "cheese_foam"], quantity=2).json()
    line = added["order"]["lines"][0]
    assert added["line_id"] == line["line_id"]
    assert line["unit_price"] == 5700 + 800 + 1100 and line["line_total"] == 15200
    assert (line["sugar"], line["ice"], [t["id"] for t in line["toppings"]]) == (70, "less", ["pearl", "cheese_foam"])
    # 추가 제안 창: 아직 담지 않은 인기 메뉴 (품절 제외)
    assert len(added["suggestions"]) == 3 and added["suggestions"][0]["name"] == "딸기 쥬얼리 밀크티"
    assert black not in [s["id"] for s in added["suggestions"]]

    edited = post(client, "/api/order/item", line_id=line["line_id"], size="L", temperature="HOT", sugar=30, toppings=[],
                  quantity=1, cup="personal").json()
    line = edited["order"]["lines"][0]
    assert (line["size"], line["temperature"], line["ice"], line["toppings"], line["cup"]) == ("L", "HOT", None, [], "personal")
    assert edited["order"]["totals"]["subtotal"] == 4600 and "suggestions" not in edited

    too_many = dict(menu_id=black, size="L", temperature="ICE", toppings=["pearl", "aloe", "coconut", "milk_foam"])
    assert error_code(post(client, "/api/order/item", **too_many)) == "too_many_toppings"
    assert post(client, "/api/order/item", menu_id="nope").status_code == 404
    assert len(post(client, "/api/session").json()["order"]["lines"]) == 1

    # 변경 확인 화면의 "옵션 직접 고치기": 변경안의 줄을 고쳐도 [확인] 전에는 주문이 그대로다
    fake.script += [tool_chunks(TARO), text_chunks("올렸어요.")]
    chat(client, "타로 밀크티 L 아이스 하나")
    pending = post(client, "/api/session").json()["pending"]["changes"][0]["after"]
    fixed = post(client, "/api/order/item", draft=True, line_id=pending["line_id"], size="J", temperature="ICE", sugar=30,
                 ice="regular", toppings=["pearl"], quantity=1).json()
    after = fixed["pending"]["changes"][0]["after"]
    assert (after["size"], after["sugar"], after["line_total"]) == ("J", 30, 6500) and len(fixed["order"]["lines"]) == 1
    assert len(post(client, "/api/order/confirm").json()["order"]["lines"]) == 2
    assert error_code(post(client, "/api/order/item", draft=True, line_id=1, size="L", temperature="ICE")) == "nothing_pending"


def test_customer_pays_with_the_terminal(kiosk):
    client, fake = kiosk
    post(client, "/api/order/item", menu_id=menu_id(client, "블랙 밀크티"), size="L", temperature="ICE")
    assert error_code(post(client, "/api/pay/start")) == "need_dine_option"
    post(client, "/api/order/options", dine_option="takeout")
    assert error_code(post(client, "/api/scan", purpose="membership", code="SKT-1001")) == "wrong_screen"
    paying = post(client, "/api/pay/start").json()
    assert paying["order"]["status"] == "paying" and paying["chips"][0]["action"] == "resume_pay"
    assert error_code(post(client, "/api/order/line", line_id=1, quantity=2)) == "payment_open"  # 결제 중에는 주문을 못 바꾼다

    assert error_code(post(client, "/api/scan", purpose="membership", code="DAMAGED")) == "barcode_unreadable"
    scanned = post(client, "/api/scan", purpose="membership", code="SKT-1001").json()
    assert scanned["scan"]["label"] == "SKT T멤버십 바코드" and scanned["order"]["totals"]["total"] == 4140
    post(client, "/api/scan", purpose="stamp", code="GC-APP-1001")
    removed = post(client, "/api/pay/benefit", kind="discount").json()
    assert removed["order"]["discount"] is None and removed["order"]["stamp"] is True

    declined = post(client, "/api/pay/tender", method="card", scenario="declined").json()
    assert declined["approved"] is False and declined["order"]["failure"]["reason"] == "declined"
    back = post(client, "/api/pay/cancel").json()  # [이전]: 주문 화면으로 돌아간다
    assert back["order"]["status"] == "ordering"
    post(client, "/api/pay/start")
    gift = post(client, "/api/scan", purpose="gift_card", code="GV-3000").json()
    assert gift["scan"]["amount"] == 3000 and gift["order"]["totals"]["due"] == 1600
    paid = post(client, "/api/pay/tender", method="card").json()
    assert paid["approved"] is True and paid["order"]["status"] == "paid" and paid["chips"] == []
    receipt = paid["order"]["receipt"]
    assert [p["amount"] for p in receipt["payments"]] == [3000, 1600] and receipt["print_receipt"] is None
    assert paid["order"]["totals"]["stamps"] == 1
    printed = post(client, "/api/pay/receipt", print_receipt=True).json()
    assert printed["order"]["receipt"]["print_receipt"] is True
    assert error_code(post(client, "/api/order/clear")) == "already_paid"

    board = client.get("/api/staff/board").json()
    assert board["orders"][0]["number"] == receipt["order_number"]
    assert client.post("/api/staff/order", json={"number": receipt["order_number"]}).json()["status"] == "done"

    fake.script += [text_chunks("안녕하세요!")]
    chat(client, "안녕하세요")  # 결제 뒤의 첫 마디는 새 주문이다
    assert post(client, "/api/session").json()["order"]["status"] == "ordering"


def test_menu_voucher_is_scanned_from_the_ordering_screen(kiosk):
    client, _ = kiosk
    post(client, "/api/order/options", dine_option="dine_in")
    scanned = post(client, "/api/scan", purpose="menu_voucher", code="MV-AMERICANO-L").json()
    line = scanned["order"]["lines"][0]
    assert scanned["scan"]["menu"] == "아메리카노" and line["voucher"]["label"] == "아메리카노 L 교환권"
    assert (line["covered"], scanned["order"]["totals"]["total"]) == (3900, 0)
    assert error_code(post(client, "/api/scan", purpose="menu_voucher", code="GV-3000")) == "barcode_wrong_kind"
    assert error_code(post(client, "/api/order/line", line_id=line["line_id"], quantity=2)) == "voucher_locked"
    post(client, "/api/pay/start")
    done = post(client, "/api/pay/tender", method="none").json()
    assert done["order"]["status"] == "paid" and done["order"]["receipt"]["payments"] == []


def test_staff_screen_calls_and_sold_out(kiosk):
    client, _ = kiosk
    called = post(client, "/api/staff", reason="바코드가 읽히지 않아요").json()
    assert called["staff_call"]["status"] == "waiting"
    board = client.get("/api/staff/board").json()
    assert board["calls"][0]["reason"] == "바코드가 읽히지 않아요" and len(board["menus"]) == 76
    client.post("/api/staff/call", json={"id": board["calls"][0]["id"]})
    live = client.get("/api/live", params=SESSION).json()
    assert live["staff_call"]["status"] == "coming"  # 직원이 확인하면 키오스크에도 보인다

    taro = menu_id(client, "타로 밀크티")
    version = live["stock"]["version"]
    stock = client.post("/api/staff/stock", json={"menu_id": taro, "sold_out": True}).json()
    assert taro in stock["sold_out"] and stock["version"] == version + 1
    assert client.get(f"/api/menu/{taro}").json()["sold_out"] is True
    post(client, "/api/order/options", dine_option="dine_in")
    assert error_code(post(client, "/api/order/item", menu_id=taro, size="L", temperature="ICE")) == "sold_out"
    client.post("/api/staff/stock", json={"menu_id": taro, "sold_out": False})
    assert post(client, "/api/order/item", menu_id=taro, size="L", temperature="ICE").status_code == 200
    assert client.post("/api/staff/stock", json={"menu_id": "nope", "sold_out": True}).status_code == 404


def test_reset_and_staff_call(kiosk):
    client, fake = kiosk
    fake.script += [text_chunks("안녕하세요!")]
    chat(client, "안녕")
    assert len(post(client, "/api/session").json()["history"]) == 2
    assert post(client, "/api/staff").json()["staff_call"]["reason"]
    fresh = post(client, "/api/session/reset").json()
    assert fresh["history"] == [] and fresh["staff_call"] is None and fresh["order"]["dine_option"] is None


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
    # 대화 없이도 버튼만으로 주문하고 결제할 수 있다
    post(client, "/api/order/options", dine_option="dine_in")
    post(client, "/api/order/item", menu_id=menu_id(client, "아메리카노"), size="L", temperature="HOT")
    post(client, "/api/pay/start")
    assert post(client, "/api/pay/tender", method="nfc").json()["order"]["status"] == "paid"


# ---------------------------------------------------------------- 공개 주소로 올렸을 때의 보호 장치

def test_chat_is_rate_limited_per_customer(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.CHAT_LIMIT_PER_SESSION", 2)
    fake = FakeClient([text_chunks("네."), text_chunks("네.")])
    client = TestClient(create_app(client=fake, index_dir=tmp_path))
    assert [chat(client, "안녕")[-1]["type"] for _ in range(2)] == ["done", "done"]
    events = chat(client, "안녕")
    assert events[0]["type"] == "error" and "대화가 많아" in events[0]["message"] and len(fake.requests) == 2
    # 다른 손님은 영향을 받지 않고, 화면 버튼 주문은 계속 된다
    assert client.post("/api/session", json={"session_id": "another-customer"}).status_code == 200
    assert client.get("/api/menu").status_code == 200


def test_password_protects_every_screen_when_set(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ACCESS_PASSWORD", "tea-time")
    client = TestClient(create_app(client=FakeClient(), index_dir=tmp_path))
    for path in ("/", "/staff", "/api/menu", "/api/staff/board", "/static/app.js"):
        denied = client.get(path)
        assert denied.status_code == 401 and denied.headers["www-authenticate"].startswith("Basic"), path
    assert client.get("/api/config").status_code == 200  # 배포 서버의 상태 확인용
    assert client.get("/", auth=("anyone", "wrong")).status_code == 401
    assert client.get("/", auth=("anyone", "tea-time")).status_code == 200
    assert client.post("/api/session", json=SESSION, auth=("kiosk", "tea-time")).status_code == 200


def test_old_sessions_are_dropped_first(catalog, monkeypatch):
    from app.session import SessionStore
    monkeypatch.setattr("app.session.MAX_SESSIONS", 3)
    store = SessionStore(catalog)
    first = store.get("a")
    for sid in ("b", "c"):
        store.get(sid)
    store.get("a")  # 다시 쓴 세션은 최근 것이 된다
    store.get("d")
    assert store.get("a") is first and list(store._sessions) == ["c", "d", "a"]


def test_browser_always_gets_the_current_screen_files(kiosk):
    """js·css 주소에 버전이 붙고 저장해 두지 않게 한다. 예전 app.js 가 남아 번역이 안 보이던 문제."""
    client, _ = kiosk
    home = client.get("/")
    assert home.headers["cache-control"] == "no-cache"
    scripts = re.findall(r'src="(/static/[\w.]+\.js\?v=\w{10})"', home.text)
    assert [s.split("?")[0] for s in scripts] == ["/static/i18n.js", "/static/app.js", "/static/pay.js"]
    assert re.search(r'href="/static/style\.css\?v=\w{10}"', home.text) and "staff.js?v=" in client.get("/staff").text
    served = client.get(scripts[1])
    assert served.status_code == 200 and served.headers["cache-control"] == "no-cache"
    assert client.get(scripts[1], headers={"If-None-Match": served.headers["etag"]}).status_code == 304  # 안 바뀌었으면 다시 받지 않는다
