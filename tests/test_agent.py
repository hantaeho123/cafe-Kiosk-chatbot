import json

import pytest

from app.chatbot.agent import KioskAgent
from app.pos import Barcodes, Pos
from app.session import Session
from app.staff import StaffBoard
from app.stock import Stock
from tests.conftest import FakeClient, text_chunks, tool_chunks


@pytest.fixture
def talk(catalog, retriever):
    """대본(script)대로 답하는 가짜 모델과 한 턴 대화한다. (이벤트 목록, 세션, 가짜 클라이언트)"""
    session = Session(catalog, Stock(catalog, names=["초코멜로 스무디"]), StaffBoard())
    session.order.set_options(dine_option="매장")  # 시작 화면에서 고른 것으로 친다

    def run(message, *script):
        client = FakeClient(script)
        agent = KioskAgent(client, retriever, catalog, moderation=True)
        return list(agent.respond(session, message)), session, client

    return run


def of(events, kind):
    return [e for e in events if e["type"] == kind]


def tool_result(client, request=1, back=1):
    """모델에게 돌려준 도구 결과. request 번째 요청의 뒤에서 back 번째 메시지."""
    return json.loads(client.requests[request]["messages"][-back]["content"])


BLACK = ("add_to_cart", {"menu_name": "블랙 밀크티", "size": "L", "temperature": "ICE"})
TARO = ("add_to_cart", {"menu_name": "타로 밀크티", "size": "L", "temperature": "ICE"})


def test_answer_is_grounded_in_retrieved_documents_and_realtime_state(talk):
    events, session, client = talk("아메리카노 중 사이즈 얼마인가요?",
                                   text_chunks("**아메리카노**는 L 4,200원, J 4,700원이에요."))
    assert "".join(e["text"] for e in of(events, "delta")) == "**아메리카노**는 L 4,200원, J 4,700원이에요."
    assert of(events, "sources")[0]["items"][0]["title"] == "아메리카노"
    assert [c["name"] for c in of(events, "cards")[0]["items"]] == ["아메리카노"]
    assert events[-1] == {"type": "done"}
    # 모델에게 보낸 메시지: 시스템 프롬프트 + (실시간 상태·검색 문서·손님 발화)
    messages = client.requests[0]["messages"]
    assert messages[0]["role"] == "system" and "[키오스크 설정]" in messages[0]["content"]
    turn = messages[-1]["content"]
    assert "<참고문서>" in turn and "메뉴명: 아메리카노" in turn
    assert "<실시간상태>\n품절 메뉴: 초코멜로 스무디\n장바구니: 비어 있음" in turn and "확인을 기다리는 변경안: 없음" in turn
    # 대화 기록에는 문서를 붙이지 않은 원래 발화만 남긴다
    assert session.history == [{"role": "user", "content": "아메리카노 중 사이즈 얼마인가요?"},
                               {"role": "assistant", "content": "**아메리카노**는 L 4,200원, J 4,700원이에요."}]


def test_order_missing_options_offers_choices(talk):
    events, session, client = talk("아메리카노 하나 주세요",
                                   tool_chunks(("add_to_cart", {"menu_name": "아메리카노"})),
                                   text_chunks("사이즈와 HOT/ICE를 알려 주세요."))
    tool = of(events, "tool")[0]
    assert tool["name"] == "add_to_cart" and not tool["ok"] and tool["args"] == {"menu_name": "아메리카노"}
    assert not session.order.lines and session.draft is None and of(events, "proposal") == []
    chips = of(events, "chips")[0]["items"]
    assert len(chips) == 4 and chips[0] == {"label": "ICE · L 4,200원", "text": "아메리카노 ICE L 사이즈로 주세요"}
    # 두 번째 호출에는 도구 결과가 모델에게 전달된다
    sent = client.requests[1]["messages"]
    assert sent[-2]["tool_calls"][0]["function"]["name"] == "add_to_cart"
    assert json.loads(sent[-1]["content"])["need"] == ["size", "temperature"]


def test_chat_orders_wait_for_the_customer_to_confirm(talk):
    """모델은 변경안만 올린다. 손님이 변경 확인 화면에서 확인해야 주문에 반영된다."""
    events, session, client = talk(
        "아메리카노 아이스 L 두 잔이랑 블랙 밀크티 펄 추가해서 J로 주세요",
        tool_chunks(("add_to_cart", {"menu_name": "아메리카노", "size": "L", "temperature": "ICE", "quantity": 2}),
                    ("add_to_cart", {"menu_name": "블랙 밀크티", "size": "J", "temperature": "ICE", "toppings": ["pearl"]})),
        text_chunks("두 가지를 변경 확인 화면에 올렸어요. 맞으면 [확인]을 눌러 주세요."))
    assert [t["ok"] for t in of(events, "tool")] == [True, True]
    result = tool_result(client)
    assert result["applied"] is False and "[확인]" in result["next"] and "15,000원" in result["waiting_for_confirmation"]
    assert session.order.lines == []  # 아직 담기지 않았다
    proposal = of(events, "proposal")[0]
    assert [c["type"] for c in proposal["changes"]] == ["add", "add"] and proposal["changed"] is True
    assert (proposal["before"]["total"], proposal["after"]["total"]) == (0, 15000)
    # 변경안을 그대로 둔 채 다른 것을 물으면 확인 화면을 다시 띄우지 않는다(답변을 가리지 않게). 버튼은 그대로 남는다.
    events, session, client = talk("펄은 뭐예요?", text_chunks("타피오카로 만든 쫀득한 토핑이에요. 올려 둔 변경은 [변경 확인하기]에서 확인해 주세요."))
    assert of(events, "proposal")[0]["changed"] is False and len(of(events, "proposal")[0]["changes"]) == 2
    assert "도구를 다시 부르지 말고" in client.requests[0]["messages"][-1]["content"]
    assert [c.get("action") for c in of(events, "chips")[0]["items"]] == ["confirm", "reject"]
    state = of(events, "state")[-1]
    assert state["order"]["lines"] == [] and len(state["pending"]["changes"]) == 2
    assert [c.get("action") for c in of(events, "chips")[0]["items"]] == ["confirm", "reject"]

    session.confirm()  # 손님이 화면에서 [확인]을 눌렀다
    assert [l["quantity"] for l in session.order.lines] == [2, 1] and session.draft is None
    events, session, client = talk("결제할게요", text_chunks("총 15,000원이에요. [결제하기]를 눌러 주세요."))
    turn = client.requests[0]["messages"][-1]["content"]
    assert "<화면조작>" in turn and "[확인]을 눌러 주문에 반영되었다" in turn  # 버튼으로 한 일도 모델에게 알려 준다
    assert "금액(POS 계산): 주문 15,000원 = 결제 금액 15,000원" in turn
    chips = of(events, "chips")[0]["items"]
    assert chips[0] == {"label": "결제하기", "action": "pay"} and chips[-1]["action"] == "undo"
    assert session.events == []  # 한 번 전한 화면 조작은 다시 보내지 않는다


def test_model_has_no_tools_for_payment_or_discounts(catalog, retriever):
    specs = {s["function"]["name"]: s["function"]["parameters"]["properties"] for s in KioskAgent(FakeClient(), retriever, catalog).specs}
    assert not {"checkout", "pay", "apply_discount"} & set(specs)
    assert set(specs["set_order_options"]) == {"dine_option", "straw", "carrier"}
    assert specs["add_to_cart"]["ice"]["enum"] == ["less", "regular", "full"] and "cup" in specs["add_to_cart"]
    assert {"compare_menus", "menu_options", "calculate_price", "add_request_note", "undo_last_change"} <= set(specs)


def test_say_it_again_to_fix_a_misunderstanding(talk):
    """고쳐 다시 말하기: 확인 전이면 변경안을 버리고, 반영된 뒤면 되돌리는 변경안을 올린다."""
    talk("블랙 밀크티 L 아이스 하나", tool_chunks(BLACK), text_chunks("확인 화면에 올렸어요."))
    events, session, client = talk("아니 그거 말고 타로 밀크티로",
                                   tool_chunks(("undo_last_change", {})), tool_chunks(TARO),
                                   text_chunks("블랙 밀크티는 취소하고 타로 밀크티를 올렸어요."))
    turn = client.requests[0]["messages"][-1]["content"]
    assert "확인을 기다리는 변경안(주문 변경)" in turn and "추가: #1 블랙 밀크티" in turn
    assert tool_result(client)["discarded"] is True
    assert [(c["type"], c["after"]["name"]) for c in of(events, "proposal")[0]["changes"]] == [("add", "타로 밀크티")]
    session.confirm()
    assert [l["menu_id"] for l in session.order.lines] == [catalog_id(session, "타로 밀크티")]

    events, session, client = talk("방금 거 취소해줘", tool_chunks(("undo_last_change", {})),
                                   text_chunks("타로 밀크티를 빼는 것으로 올렸어요."))
    proposal = of(events, "proposal")[0]
    assert proposal["undo"] is True and [c["type"] for c in proposal["changes"]] == ["remove"]
    assert len(session.order.lines) == 1  # 되돌리기도 손님이 확인해야 한다
    session.confirm()
    assert session.order.lines == [] and session.undo_stack == []
    events, _, client = talk("또 취소", tool_chunks(("undo_last_change", {})), text_chunks("되돌릴 변경이 없어요."))
    assert tool_result(client)["error"] == "nothing_to_undo"


def catalog_id(session, name):
    return session.catalog.resolve(name)[0]["id"]


def test_pending_line_can_be_adjusted_before_confirming(talk):
    talk("블랙 밀크티 L 아이스 하나", tool_chunks(BLACK), text_chunks("확인 화면에 올렸어요."))
    events, session, _ = talk("당도 30으로, 얼음 적게, 펄 추가",
                              tool_chunks(("update_cart_item", {"line_id": 1, "sugar": 30, "ice": "less", "toppings": ["pearl"]})),
                              text_chunks("옵션을 바꿔 올렸어요."))
    change = of(events, "proposal")[0]["changes"][0]
    assert change["type"] == "add" and (change["after"]["sugar"], change["after"]["ice"]) == (30, "less")
    assert change["after"]["line_total"] == 5500 and session.order.lines == []


def test_request_note_is_passed_on_but_not_promised(talk):
    talk("블랙 밀크티 L 아이스 하나", tool_chunks(BLACK), text_chunks("올렸어요."))[1].confirm()
    events, session, client = talk("얼음은 빼 주세요",
                                   tool_chunks(("update_cart_item", {"line_id": 1, "ice": "none"})),
                                   tool_chunks(("add_request_note", {"note": "얼음 빼 주세요", "line_id": 1})),
                                   text_chunks("얼음 없이는 고를 수 없어서 메모로 전달해 드릴게요. 매장 사정에 따라 어려울 수 있어요."))
    assert tool_result(client)["error"] == "ice_not_selectable"  # 화면에 없는 옵션은 된다고 하지 않는다
    assert "약속하지" in tool_result(client, request=2)["promise"]
    change = of(events, "proposal")[0]["changes"][0]
    assert change["type"] == "note_add" and change["note"]["text"] == "얼음 빼 주세요" and change["note"]["line_name"] == "블랙 밀크티"
    assert session.order.notes == []
    session.confirm()
    assert session.order.notes[0]["line_id"] == 1


def test_comparison_puts_a_table_on_the_screen(talk):
    events, _, client = talk("블랙 밀크티랑 타로 밀크티 뭐가 달라요?",
                             tool_chunks(("compare_menus", {"menu_names": ["블랙 밀크티", "타로 밀크티"]})),
                             text_chunks("**블랙 밀크티**는 홍차 맛이 진하고 **타로 밀크티**는 고소해요."))
    table = of(events, "compare")[0]["items"]
    assert [m["name"] for m in table] == ["블랙 밀크티", "타로 밀크티"] and table[0]["image"]
    result = tool_result(client)
    assert "view" not in result and result["menus"][0]["price"] == "L 4,900원, J 6,000원"
    assert result["menus"][0]["caffeine_mg"] == 128 and result["menus"][1]["flavors"]


def test_sold_out_menus_are_not_recommended_or_added(talk):
    events, session, client = talk("초코멜로 스무디 하나 주세요",
                                   tool_chunks(("add_to_cart", {"menu_name": "초코멜로 스무디"})),
                                   tool_chunks(("find_menus", {"category": "스무디"})),
                                   text_chunks("**초코멜로 스무디**는 지금 품절이에요."))
    assert "초코멜로 스무디 (메뉴 상세) — 지금 품절" in client.requests[0]["messages"][-1]["content"]
    assert tool_result(client)["error"] == "sold_out" and session.draft is None
    assert "초코멜로 스무디" not in [m["name"] for m in tool_result(client, request=2)["menus"]]
    assert of(events, "cards")[0]["items"][0]["sold_out"] is True


def test_budget_is_divided_and_totalled_by_the_pos(talk):
    events, _, client = talk("2만 원으로 셋이 마실 거 추천해줘",
                             tool_chunks(("find_menus", {"total_budget": 20000, "people": 3, "limit": 30})),
                             tool_chunks(("calculate_price", {"budget": 20000, "items": [
                                 {"menu_name": "아메리카노", "quantity": 2}, {"menu_name": "타로 밀크티", "size": "J", "toppings": ["pearl"]}]})),
                             text_chunks("세 잔에 15,000원이에요."))
    found = tool_result(client)
    assert found["budget"]["per_person"] == "6,666원" and found["menus"]
    assert all(int(m["price"].split()[1].replace(",", "").rstrip("원,")) <= 6666 for m in found["menus"])
    priced = tool_result(client, request=2)
    assert priced["total"] == "15,000원" and priced["budget"] == {"budget": "20,000원", "within_budget": True, "left": "5,000원"}
    assert priced["assumed"] == ["아메리카노: 사이즈를 말하지 않아 가장 작은 L 로 계산"]


def test_topping_already_in_the_menu_is_pointed_out(talk):
    _, _, client = talk("타로 밀크티 펄 추가해서 하나", tool_chunks(("add_to_cart", {
        "menu_name": "타로 밀크티 + 펄", "size": "L", "temperature": "ICE", "toppings": ["pearl", "aloe"]})), text_chunks("올렸어요."))
    assert "펄이(가) 이미 들어 있는데" in tool_result(client)["notes"][0]  # 모델이 같은 토핑을 한 번 더 붙였을 때 알려 준다


def test_menu_options_tell_what_can_be_chosen(talk):
    _, _, client = talk("망고 스무디 당도 조절 돼요?", tool_chunks(("menu_options", {"menu_name": "망고 스무디"})),
                        text_chunks("**망고 스무디**는 당도가 고정이에요."))
    options = tool_result(client)
    assert options["sugar"] == {"status": "고정(바꿀 수 없음)", "levels": [], "recommended": None}
    assert options["temperatures"] == ["ICE"] and options["cups"] == ["매장컵", "일회용컵", "개인컵"]


def test_order_cannot_change_while_the_payment_screen_is_open(talk, catalog):
    _, session, _ = talk("블랙 밀크티 L 아이스 하나", tool_chunks(BLACK), text_chunks("올렸어요."))
    session.confirm()
    Pos(catalog, Barcodes()).start(session.order)  # 손님이 [결제하기]를 눌렀다
    events, session, client = talk("타로 밀크티도 하나 추가요", tool_chunks(TARO),
                                   text_chunks("결제 화면에서 [이전]을 눌러 돌아오시면 추가해 드릴게요."))
    assert "결제 화면: 손님이 진행 중" in client.requests[0]["messages"][-1]["content"]
    assert tool_result(client)["error"] == "payment_open" and len(session.order.lines) == 1
    assert of(events, "chips")[0]["items"][0]["action"] == "resume_pay"


def test_paid_order_ends_and_the_next_message_starts_a_new_one(talk, catalog):
    _, session, _ = talk("블랙 밀크티 L 아이스 하나", tool_chunks(BLACK), text_chunks("올렸어요."))
    session.confirm()
    pos = Pos(catalog, Barcodes())
    pos.start(session.order)
    pos.pay(session.order, "card")
    events, session, _ = talk("안녕하세요", text_chunks("안녕하세요! 무엇을 도와드릴까요?"))
    assert session.order.status == "ordering" and not session.order.lines
    assert len(session.history) == 2  # 결제 뒤 첫 마디부터 새 손님의 대화다


def test_staff_call_from_chat_reaches_the_staff_screen(talk):
    events, session, _ = talk("직원 좀 불러 주세요", tool_chunks(("call_staff", {"reason": "손님이 직원을 찾음"})),
                              text_chunks("직원을 호출했어요."))
    assert session.board.to_dict()["calls"][0]["reason"] == "손님이 직원을 찾음"
    assert of(events, "state")[-1]["staff_call"]["status"] == "waiting"


def test_extra_search_for_compound_question(talk):
    events, _, client = talk("타로 밀크티 칼로리 알려주고 카페인 없는 음료도 추천해줘",
                             tool_chunks(("search_knowledge", {"query": "카페인 없는 음료", "doc_type": "recommendation"})),
                             text_chunks("**타로 밀크티**는 ..."))
    tool = of(events, "tool")[0]
    assert tool["ok"] and tool["sources"] and all(s["type"] == "recommendation" for s in tool["sources"])
    result = tool_result(client)
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
                              tool_chunks(("add_to_cart", {"menu": "아메리카노"}), ("no_such_tool", {}),
                                          ("find_menus", {"taste": "달콤한"})),
                              text_chunks("다시 말씀해 주세요."))
    assert [t["ok"] for t in of(events, "tool")] == [False, False, False]
    assert not session.order.lines and session.draft is None


def test_multi_condition_recommendation_uses_the_menu_filter(talk):
    events, _, client = talk("카페인 없고 우유도 안 들어간 음료 추천해줘",
                             tool_chunks(("find_menus", {"caffeine": "none", "no_milk": True})),
                             text_chunks("**망고 주스**를 추천해요."))
    assert of(events, "tool")[0]["ok"]
    result = tool_result(client)
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


# ---------------------------------------------------------------- 답변 검증 (checks.py)

def test_reply_with_an_amount_from_nowhere_is_rewritten(talk):
    events, session, client = talk("블랙 밀크티 세 잔이면 얼마예요?", text_chunks("세 잔이면 14,777원이에요."),
                                   text_chunks("정확한 합계는 주문 내역에서 확인해 주세요."))
    assert of(events, "replace") == [{"type": "replace", "text": "정확한 합계는 주문 내역에서 확인해 주세요."}]
    assert of(events, "verified")[0]["rewritten"] is True
    assert "14,777원" in client.requests[1]["messages"][-1]["content"]  # 무엇이 근거 없는 금액인지 알려 주고 다시 쓰게 한다
    assert session.history[-1]["content"] == "정확한 합계는 주문 내역에서 확인해 주세요."


def test_a_caution_is_added_when_the_rewrite_does_not_help(talk):
    events, session, _ = talk("블랙 밀크티 세 잔이면 얼마예요?", text_chunks("세 잔이면 14,777원이에요."), text_chunks("14,777원 맞아요."))
    assert of(events, "verified")[0]["amounts"] == [14777] and not of(events, "replace")
    assert session.history[-1]["content"].endswith("결제 화면에 나오는 값으로 확인해 주세요.")


def test_allergy_question_always_carries_the_notice(talk):
    events, session, _ = talk("우유 알레르기 있는데 타로 밀크티 마셔도 돼요?", text_chunks("알레르기 표기에는 우유가 있어요."))
    assert of(events, "verified")[0]["allergy_notice"] == "added"
    assert "직원에게 꼭 확인" in session.history[-1]["content"]
    events, session, _ = talk("그럼 알레르기 표기에 우유가 없는 건요?",
                              text_chunks("표기 기준으로는 망고 주스예요. 같은 기구를 써서 섞일 수 있으니 직원에게 확인해 주세요."))
    assert of(events, "verified")[0]["allergy_notice"] == "model"
    assert session.history[-1]["content"].count("직원") == 1  # 이미 있으면 또 붙이지 않는다


def test_request_note_caveat_is_added_when_the_model_forgets(talk):
    events, session, _ = talk("자스민티 L 아이스 하나, 얼음은 빼 달라고 남겨 주세요",
                              tool_chunks(("add_to_cart", {"menu_name": "자스민티", "size": "L", "temperature": "ICE", "request_note": "얼음 빼 주세요"})),
                              text_chunks("자스민티와 메모를 확인 화면에 올렸어요."))
    assert of(events, "verified")[0]["note_notice"] == "added"
    assert "어려울 수 있어요" in session.history[-1]["content"]


# ---------------------------------------------------------------- 모델이 헷갈리지 않게 코드가 알려 주는 것

def test_menus_just_shown_are_numbered_for_the_next_turn(talk):
    _, _, client = talk("첫 번째 걸로 주세요", text_chunks("어떤 메뉴를 말씀하시나요?"))
    assert "직전에 안내한 메뉴(순서대로): 없음" in client.requests[0]["messages"][-1]["content"]
    talk("추천해줘", text_chunks("**타로 밀크티**와 **블랙 밀크티**를 추천해요."))
    talk("당도 30%가 뭐예요?", text_chunks("시럽을 3분의 1쯤 넣은 거예요."))  # 메뉴가 안 나온 답변은 목록을 지우지 않는다
    _, _, client = talk("첫 번째 걸로 주세요", text_chunks("타로 밀크티 말씀이시죠?"))
    assert "직전에 안내한 메뉴(순서대로): 1) 타로 밀크티 2) 블랙 밀크티" in client.requests[0]["messages"][-1]["content"]


def test_no_match_tells_which_condition_to_drop(talk):
    _, _, client = talk("카페인 적고 덜 단 시원한 거", tool_chunks(
        ("find_menus", {"caffeine": "low", "sugar_adjustable": True, "flavor": "시원한"})), text_chunks("조건을 하나 빼면 있어요."))
    assert tool_result(client)["total"] == 18  # '시원한'은 맛이 아니라 '차갑게'로 읽는다
    _, _, client = talk("카페인 적고 덜 단 스무디", tool_chunks(
        ("find_menus", {"caffeine": "low", "sugar_adjustable": True, "flavor": "스무디류"})), text_chunks("조건을 하나 빼면 있어요."))
    result = tool_result(client)
    assert result["total"] == 0 and "맛 (시원한)" in [r["without"] for r in result["relaxed"]] and result["relaxed"][0]["menus"]
    _, _, client = talk("카페인 적고 덜 단 시원한 거", tool_chunks(
        ("find_menus", {"caffeine": "low", "sugar_adjustable": True, "temperature": "ICE", "sort": "sugar"})), text_chunks("이런 메뉴가 있어요."))
    menus = tool_result(client)["menus"]
    assert menus and all("ICE" in m["temperatures"] and m["sugar_adjustable"] for m in menus)
    assert [m["sugar_g"] for m in menus] == sorted(m["sugar_g"] for m in menus)


def test_budget_search_comes_with_a_priced_example(talk):
    _, _, client = talk("2만 원으로 셋이", tool_chunks(("find_menus", {"total_budget": 20000, "people": 3})), text_chunks("세 잔이에요."))
    example = tool_result(client)["example"]
    assert len(example["lines"]) == 3 and example["budget"]["within_budget"] is True
    assert example["total"] == "16,800원" and example["budget"]["left"] == "3,200원"


def test_adding_a_menu_already_in_the_order_is_pointed_out(talk):
    talk("블랙 밀크티 L 아이스 하나", tool_chunks(BLACK), text_chunks("확인 화면에 올렸어요."))[1].confirm()
    _, _, client = talk("블랙은 70으로", tool_chunks(("add_to_cart", {**BLACK[1], "sugar": 70})), text_chunks("올렸어요."))
    assert "주문에 이미 '블랙 밀크티'" in tool_result(client)["notes"][-1] and "remove_cart_item" in tool_result(client)["notes"][-1]
    _, session, client = talk("하나 더", tool_chunks(("undo_last_change", {})), tool_chunks(BLACK), text_chunks("올렸어요."))
    assert "수량이 1잔에서 2잔으로" in tool_result(client, request=2)["notes"][-1]


def test_dine_option_rides_along_with_the_order(talk):
    events, _, _ = talk("타로 밀크티 L 아이스 하나 포장이요", tool_chunks(
        ("add_to_cart", {**TARO[1], "dine_option": "포장"})), text_chunks("확인 화면에 올렸어요."))
    assert sorted(c["type"] for c in of(events, "proposal")[0]["changes"]) == ["add", "dine"]


def test_unavailable_menu_is_not_swapped_for_another(talk):
    _, _, client = talk("초코멜로 스무디 주세요", tool_chunks(("add_to_cart", {"menu_name": "초코멜로 스무디"})), text_chunks("품절이에요."))
    assert tool_result(client)["error"] == "sold_out" and "대신 올리지 마세요" in tool_result(client)["next"]


def test_saying_yes_is_not_a_confirmation(talk):
    talk("블랙 밀크티 L 아이스 하나", tool_chunks(BLACK), text_chunks("확인 화면에 올렸어요."))
    events, session, client = talk("네 맞아요")  # 모델을 부르지 않는다
    assert client.requests == [] and "[확인]을 눌러 주세요" in of(events, "delta")[0]["text"]
    assert of(events, "proposal")[0]["changed"] is True and not session.order.lines  # 확인 화면을 다시 띄울 뿐 주문은 그대로
    events, _, client = talk("네 그리고 타로 밀크티도 하나요", tool_chunks(TARO), text_chunks("타로 밀크티도 올렸어요."))
    assert len(client.requests) == 2  # 다른 요청이 섞이면 모델에게 넘긴다
    session.confirm()
    events, _, client = talk("네", text_chunks("더 필요한 게 있으세요?"))
    assert len(client.requests) == 1  # 기다리는 변경안이 없으면 평소처럼 답한다


# ---------------------------------------------------------------- 애매하거나 서로 맞지 않는 요청

def test_conflicting_request_is_asked_about_not_guessed(talk):
    """ "아이스 … 따뜻하게 … 얼음 많이"를 한쪽으로 골라 올려 놓고 손님 말을 그대로 따라 하던 문제. """
    events, session, client = talk("아이스 아메리카노 따뜻하게 스몰 사이즈로 얼음 많이 한 잔.",
                                   tool_chunks(("add_to_cart", {"menu_name": "아메리카노", "size": "L", "temperature": "HOT", "ice": "full"})),
                                   text_chunks("아이스와 따뜻하게 중 어느 쪽으로 드릴까요? 스몰 사이즈는 없고 L이 가장 작아요."))
    turn = client.requests[0]["messages"][-1]["content"]
    assert "<주의>" in turn and "차갑게('아이스')와 따뜻하게('따뜻')" in turn and "'스몰' 사이즈는 없습니다" in turn
    assert "'아메리카노'은(는) 얼음량이 고정이라 바꿀 수 없습니다" in turn  # '얼음 많이 가능해요'라고 답하지 않게
    result = tool_result(client)
    assert result["error"] == "conflicting_request" and "올리지 않았습니다" in result["message"]
    assert not of(events, "proposal") and session.draft is None
    assert [c["label"] for c in of(events, "chips")[0]["items"]] == ["ICE", "HOT"]  # 고를 수 있게 버튼을 준다


def test_hot_with_an_ice_level_is_also_a_conflict(talk):
    _, session, client = talk("블랙 밀크티 L 얼음 적게요", tool_chunks(("add_to_cart", {**BLACK[1], "temperature": "HOT", "ice": "less"})),
                              text_chunks("따뜻하게인가요, 아이스인가요?"))
    assert tool_result(client)["error"] == "conflicting_request" and session.draft is None


def test_two_drinks_with_different_temperatures_are_not_blocked(talk):
    events, _, client = talk("아이스 아메리카노 L 하나랑 따뜻한 블랙 밀크티 L 하나",
                             tool_chunks(("add_to_cart", {"menu_name": "아메리카노", "size": "L", "temperature": "ICE"}),
                                         ("add_to_cart", {**BLACK[1], "temperature": "HOT"})), text_chunks("두 잔을 올렸어요."))
    assert "<주의>" in client.requests[0]["messages"][-1]["content"]  # 알려는 주되
    assert [c["type"] for c in of(events, "proposal")[0]["changes"]] == ["add", "add"]  # 여러 잔이면 막지 않는다


def test_option_that_cannot_be_applied_is_spelled_out(talk):
    events, session, client = talk("망고 스무디 당도 30%로 하나", tool_chunks(("add_to_cart", {"menu_name": "망고 스무디", "sugar": 30})),
                                   text_chunks("**망고 스무디**를 당도 30%로 확인 화면에 올렸어요."))
    assert tool_result(client)["unapplied"] == ["sugar"] and "proposed 에 적힌 실제 옵션" in tool_result(client)["say"]
    assert of(events, "verified")[0]["unapplied"] == "added"
    assert "당도은(는) 이 메뉴에서 바꿀 수 없어 반영하지 못했어요" in session.history[-1]["content"]
    session.reject()
    events, session, _ = talk("망고 스무디 당도 30%로 하나", tool_chunks(("add_to_cart", {"menu_name": "망고 스무디", "sugar": 30})),
                              text_chunks("**망고 스무디**는 당도가 고정이라 30%로는 못 바꿔요. 기본 당도로 올렸어요."))
    assert of(events, "verified")[0]["unapplied"] == "model"  # 모델이 밝혔으면 또 붙이지 않는다


def test_foreign_reply_uses_the_screens_names_and_price_format(talk):
    events, session, client = talk("Could you recommend something without caffeine?",
                                   tool_chunks(("find_menus", {"caffeine": "none"})),
                                   text_chunks("**Taro Milk Tea with Pearls** (타로 밀크티 + 펄) is L 5,500원."))
    menus = tool_result(client)["menus"]
    assert menus[0]["name"] == "타로 밀크티 + 펄" and menus[0]["name_local"] == "Taro Milk Tea with Pearls"  # 메뉴판과 같은 이름을 준다
    # 가격은 ₩ 로, 메뉴 이름 뒤의 한글 괄호는 뺀다. 그래도 어떤 메뉴를 말했는지는 기억한다
    assert session.history[-1]["content"] == "**Taro Milk Tea with Pearls** is L ₩5,500."
    assert session.shown == ["타로 밀크티 + 펄"] and of(events, "cards")[0]["items"][0]["name"] == "타로 밀크티 + 펄"
    assert "이 언어의 이름만 쓴다" in client.requests[0]["messages"][-1]["content"]
    _, _, client = talk("타로 밀크티 얼마예요?", text_chunks("**타로 밀크티**는 L 4,900원이에요."))
    assert session.history[-1]["content"].endswith("4,900원이에요.")  # 한국어 답변은 그대로
