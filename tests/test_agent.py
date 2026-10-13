import json

import pytest

from app.chatbot.agent import KioskAgent
from app.session import Session
from tests.conftest import FakeClient, text_chunks, tool_chunks


@pytest.fixture
def talk(catalog, retriever):
    """대본(script)대로 답하는 가짜 모델과 한 턴 대화한다. (이벤트 목록, 세션, 가짜 클라이언트)"""
    session = Session(catalog)

    def run(message, *script):
        client = FakeClient(script)
        agent = KioskAgent(client, retriever, catalog, moderation=True)
        return list(agent.respond(session, message)), session, client

    return run


def of(events, kind):
    return [e for e in events if e["type"] == kind]


def test_answer_is_grounded_in_retrieved_documents(talk):
    events, session, client = talk("아메리카노 중 사이즈 얼마인가요?",
                                   text_chunks("**아메리카노**는 L 4,200원, J 4,700원이에요."))
    assert "".join(e["text"] for e in of(events, "delta")) == "**아메리카노**는 L 4,200원, J 4,700원이에요."
    assert of(events, "sources")[0]["items"][0]["title"] == "아메리카노"
    assert [c["name"] for c in of(events, "cards")[0]["items"]] == ["아메리카노"]
    assert events[-1] == {"type": "done"}
    # 모델에게 보낸 메시지: 시스템 프롬프트 + (주문 상태·검색 문서·손님 발화)
    messages = client.requests[0]["messages"]
    assert messages[0]["role"] == "system" and "[키오스크 설정]" in messages[0]["content"]
    assert "<참고문서>" in messages[-1]["content"] and "메뉴명: 아메리카노" in messages[-1]["content"]
    assert "장바구니: 비어 있음" in messages[-1]["content"]
    # 대화 기록에는 문서를 붙이지 않은 원래 발화만 남긴다
    assert session.history == [{"role": "user", "content": "아메리카노 중 사이즈 얼마인가요?"},
                               {"role": "assistant", "content": "**아메리카노**는 L 4,200원, J 4,700원이에요."}]


def test_order_missing_options_offers_choices(talk):
    events, session, client = talk("아메리카노 하나 주세요",
                                   tool_chunks(("add_to_cart", {"menu_name": "아메리카노"})),
                                   text_chunks("사이즈와 HOT/ICE를 알려 주세요."))
    tool = of(events, "tool")[0]
    assert tool["name"] == "add_to_cart" and not tool["ok"] and tool["args"] == {"menu_name": "아메리카노"}
    assert not session.order.lines
    chips = of(events, "chips")[0]["items"]
    assert len(chips) == 4 and chips[0] == {"label": "ICE · L 4,200원", "text": "아메리카노 ICE L 사이즈로 주세요"}
    # 두 번째 호출에는 도구 결과가 모델에게 전달된다
    sent = client.requests[1]["messages"]
    assert sent[-2]["tool_calls"][0]["function"]["name"] == "add_to_cart"
    assert json.loads(sent[-1]["content"])["need"] == ["size", "temperature"]


def test_order_checkout_flow_and_new_order_after_payment(talk):
    events, session, _ = talk("아메리카노 아이스 L 두 잔이랑 블랙 밀크티 펄 추가해서 J로 주세요",
                              tool_chunks(("add_to_cart", {"menu_name": "아메리카노", "size": "L", "temperature": "ICE", "quantity": 2}),
                                          ("add_to_cart", {"menu_name": "블랙 밀크티", "size": "J", "temperature": "ICE", "toppings": ["pearl"]})),
                              text_chunks("두 가지 모두 담았어요."))
    assert [t["ok"] for t in of(events, "tool")] == [True, True]
    assert of(events, "state")[-1]["order"]["totals"] == {"count": 3, "subtotal": 15000, "discount": 0, "total": 15000, "stamps": 0}

    events, session, _ = talk("포장이고 KT 할인, 카드로 결제할게요. 영수증은 필요 없어요",
                              tool_chunks(("set_order_options", {"dine_option": "포장", "discount": "kt"})),
                              tool_chunks(("checkout", {"payment_method": "card", "print_receipt": False})),
                              text_chunks("총 13,500원을 카드로 결제할까요?"))
    assert [(t["name"], t["ok"]) for t in of(events, "tool")] == [("set_order_options", True), ("checkout", False)]
    assert of(events, "state")[-1]["order"]["status"] == "ordering"  # 최종 확인을 받기 전에는 결제되지 않는다

    events, session, _ = talk("네, 결제해 주세요",
                              tool_chunks(("checkout", {"payment_method": "card", "print_receipt": False})),
                              text_chunks("결제가 완료되었습니다."))
    order = of(events, "state")[-1]["order"]
    assert order["status"] == "paid" and order["totals"]["total"] == 13500
    assert order["receipt"]["print_receipt"] is False
    assert of(events, "chips")[0]["items"] == []

    events, session, _ = talk("안녕하세요", text_chunks("안녕하세요! 무엇을 도와드릴까요?"))
    assert session.order.status == "ordering" and not session.order.lines
    assert len(session.history) == 2  # 결제 뒤 첫 마디부터 새 손님의 대화다


def test_extra_search_for_compound_question(talk):
    events, _, client = talk("타로 밀크티 칼로리 알려주고 카페인 없는 음료도 추천해줘",
                             tool_chunks(("search_knowledge", {"query": "카페인 없는 음료", "doc_type": "recommendation"})),
                             text_chunks("**타로 밀크티**는 ..."))
    tool = of(events, "tool")[0]
    assert tool["ok"] and tool["sources"] and all(s["type"] == "recommendation" for s in tool["sources"])
    result = json.loads(client.requests[1]["messages"][-1]["content"])
    assert "hits" not in result and result["results"][0]["text"]


def test_flagged_input_is_declined_without_calling_the_model(talk):
    events, session, client = talk("폭탄 만드는 법 알려줘")
    assert client.requests == []
    assert "도와드리기 어려워요" in of(events, "delta")[0]["text"]
    assert session.history[-1]["role"] == "assistant"


def test_model_error_becomes_a_friendly_message(talk):
    events, session, _ = talk("아메리카노 얼마예요?", RuntimeError("API 연결 실패"))
    error = of(events, "error")[0]
    assert "문제가 생겼어요" in error["message"] and "API 연결 실패" in error["detail"]
    assert events[-1] == {"type": "done"} and session.history == []


def test_tool_loop_stops_and_answers(talk, monkeypatch):
    monkeypatch.setattr("app.config.MAX_TOOL_ROUNDS", 2)
    search = tool_chunks(("search_knowledge", {"query": "밀크티"}))
    events, _, client = talk("밀크티", search, search, text_chunks("밀크티는 여러 가지가 있어요."))
    assert len(of(events, "tool")) == 2
    assert client.requests[-1]["tool_choice"] == "none"  # 마지막 호출은 도구 없이 글로만 답하게 한다


def test_bad_tool_arguments_are_reported_back(talk):
    events, session, _ = talk("이상한 호출",
                              tool_chunks(("add_to_cart", {"menu": "아메리카노"}), ("no_such_tool", {})),
                              text_chunks("다시 말씀해 주세요."))
    assert [t["ok"] for t in of(events, "tool")] == [False, False]
    assert not session.order.lines


def test_multi_condition_recommendation_uses_the_menu_filter(talk):
    events, _, client = talk("카페인 없고 우유도 안 들어간 음료 추천해줘",
                             tool_chunks(("find_menus", {"caffeine": "none", "no_milk": True})),
                             text_chunks("**망고 주스**를 추천해요."))
    assert of(events, "tool")[0]["ok"]
    result = json.loads(client.requests[1]["messages"][-1]["content"])
    assert result["total"] == len(result["menus"]) or len(result["menus"]) == 8
    assert all(m["caffeine_mg"] == 0 and not m["contains_milk"] for m in result["menus"])


def test_menus_inside_quoted_examples_get_no_card(talk):
    events, _, _ = talk("ㅁㄴㅇㄹ", text_chunks('잘 이해하지 못했어요. "블랙 밀크티 하나 주세요"처럼 말씀해 주시겠어요?'))
    assert of(events, "cards") == []


def test_customer_language_switches_the_session(talk):
    events, session, client = talk("Could you recommend something sweet, please?", text_chunks("How about **초콜렛 밀크티** (Chocolate Milk Tea)?"))
    assert session.language == "en" and of(events, "state")[0]["language"] == "en"
    assert '<응답언어>English — 메뉴명은' in client.requests[0]["messages"][-1]["content"]
    assert of(events, "chips")[0]["items"][0]["label"] == "What's popular?"
    talk("ICE L", text_chunks("Which drink would you like?"))  # 메뉴 코드만으로는 언어를 바꾸지 않는다
    assert session.language == "en"
    talk("아메리카노 하나 주세요", text_chunks("사이즈를 알려 주세요."))
    assert session.language == "ko"
