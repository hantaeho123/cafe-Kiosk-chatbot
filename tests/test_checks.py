from app.chatbot import checks


def test_amounts_must_appear_somewhere_in_what_the_model_was_given():
    context = "아메리카노: L 4,200원, J 4,700원\n샷추가 +500원\n{\"total\": \"13,600원\", \"paid\": 8400}"
    assert checks.ungrounded_amounts("L은 4,200원, 두 잔이면 8,400원이에요. 합계 13,600원.", context) == []
    assert checks.ungrounded_amounts("세 잔이면 12,600원이고 한 사람당 약 4,300원이에요.", context) == [12600, 4300]
    assert checks.ungrounded_amounts("₩9,999 or 4,200 won", context) == [9999]
    # 돈이 아닌 숫자(열량·카페인·당도)는 보지 않는다
    assert checks.ungrounded_amounts("열량 357kcal, 카페인 129mg, 당도 30%", context) == []


def test_tool_call_arguments_count_as_context():
    messages = [{"role": "user", "content": "2만 원으로 셋이"},
                {"role": "assistant", "content": None, "tool_calls": [{"function": {"arguments": '{"total_budget": 20000}'}}]}]
    assert checks.ungrounded_amounts("예산 20,000원 안에서 골랐어요.", checks.message_text(messages)) == []


def test_allergy_notice_needs_both_staff_and_cross_contamination():
    assert checks.asks_about_allergy("저 우유 알러지 있어요") and checks.asks_about_allergy("I have a nut allergy")
    assert not checks.asks_about_allergy("우유 들어가요?")
    assert checks.has_allergy_notice("같은 기구로 다루어 섞일 수 있으니 직원에게 확인해 주세요.", "ko")
    assert not checks.has_allergy_notice("표기에는 우유가 있어요. 직원에게 물어보세요.", "ko")
    assert checks.has_allergy_notice("Made with shared equipment; please ask our staff.", "en")


def test_request_note_caveat():
    assert checks.has_note_caveat("메모로 전달해 드릴게요. 매장 사정에 따라 어려울 수 있어요.", "ko")
    assert not checks.has_note_caveat("얼음 빼 달라는 메모를 올렸어요.", "ko")
    assert checks.has_note_caveat("I'll pass it on, but it may not be possible.", "en")
