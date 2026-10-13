"""답변을 코드로 다시 확인한다. 프롬프트로만 지키던 규칙을 모델에게만 맡겨 두지 않기 위해서다.

- 금액(G5): 답변에 나온 금액은 모두 모델이 받은 글(실시간 상태·참고문서·도구 결과·대화) 어딘가에 있어야 한다.
  없으면 모델이 직접 계산했거나 지어낸 숫자다.
- 알레르기(G2): 손님이 알레르기를 물었으면 교차 오염 가능성과 직원 확인 안내가 반드시 들어가야 한다.
- 요청 메모(G9): 메모를 새로 올렸으면 "전달만 되고 안 될 수도 있다"는 말이 반드시 들어가야 한다.
"""
import re

_NUMBER = r"\d{1,3}(?:,\d{3})+|\d{3,}"
# "4,200원", "4,200 won", "₩4,200" 처럼 돈으로 읽히는 숫자만 본다. 열량·카페인 같은 숫자는 보지 않는다.
_AMOUNT = re.compile(rf"(?:₩\s*({_NUMBER}))|(?:({_NUMBER})\s*(?:원|won\b|KRW|ウォン|韩元|韓元))", re.IGNORECASE)
_ALLERGY = re.compile(r"알레르기|알러지|allerg|过敏|過敏|アレルギー", re.IGNORECASE)
# 답변에 안내가 이미 들어 있는지: (직원에게 확인) 과 (섞일 수 있음) 이 둘 다 있어야 한다.
_NOTICE = {
    "ko": (r"직원", r"섞일|교차|같은 기구"),
    "en": (r"staff", r"cross|shared|same equipment|traces"),
    "zh": (r"店员|工作人员|员工", r"交叉|混入|同一|共用"),
    "ja": (r"スタッフ|店員", r"混入|同じ器具|コンタミ|共用"),
}


# 요청 메모를 들어준다고 약속하지 않았는지: "어려울 수 있다"는 뜻의 말이 있어야 한다.
_NOT_PROMISED = {
    "ko": r"어려울|안 될 수|못 해 드릴|약속",
    "en": r"may not|might not|not be possible|can't guarantee|cannot guarantee|no guarantee",
    "zh": r"可能无法|不能保证|无法保证|未必",
    "ja": r"場合があ|お約束|保証",
}


def _number(text):
    return int(text.replace(",", ""))


def message_text(messages):
    """모델에게 보낸 메시지 전체를 한 덩어리 글로. 도구 호출의 인자도 포함한다."""
    parts = []
    for m in messages:
        parts.append(m.get("content") or "")
        parts += [c["function"]["arguments"] for c in m.get("tool_calls", [])]
    return "\n".join(parts)


def ungrounded_amounts(reply, context):
    """답변의 금액 가운데 context 에 없는 것(큰 순서대로 중복 없이). 비어 있으면 통과다."""
    known = {_number(n) for n in re.findall(_NUMBER, context)}
    found = {_number(a or b) for a, b in _AMOUNT.findall(reply)}
    return sorted(found - known, reverse=True)


def asks_about_allergy(user_text):
    return bool(_ALLERGY.search(user_text))


def has_allergy_notice(reply, language):
    staff, mixing = _NOTICE.get(language, _NOTICE["ko"])
    return bool(re.search(staff, reply, re.IGNORECASE) and re.search(mixing, reply, re.IGNORECASE))


def has_note_caveat(reply, language):
    return bool(re.search(_NOT_PROMISED.get(language, _NOT_PROMISED["ko"]), reply, re.IGNORECASE))


# 변경안이 확인을 기다리는 동안 손님이 말로만 동의했을 때("네", "맞아요", "yes"). 모델을 부르지 않고 [확인]을 눌러 달라고 답한다.
_YES = {"네", "넵", "넹", "예", "응", "어", "맞아", "맞아요", "맞습니다", "좋아", "좋아요", "그래", "그래요", "그대로", "그렇게",
        "해", "해줘", "해줘요", "해주세요", "주세요", "확인", "확인해줘", "확인해주세요", "담아줘", "담아주세요", "오케이",
        "ok", "okay", "yes", "yep", "yeah", "sure", "correct", "right", "confirm", "please", "it", "do",
        "对", "好", "好的", "是", "是的", "可以", "确认", "はい", "ええ", "うん", "お願いします", "確認", "それで"}


def is_bare_yes(user_text):
    words = [w for w in re.split(r"[\s.,!~?。！、]+", user_text.strip().lower()) if w]
    return 0 < len(words) <= 4 and all(w in _YES for w in words)


# ---------------------------------------------------------------- 애매하거나 서로 맞지 않는 요청
# "아이스 아메리카노 따뜻하게 스몰 사이즈로 얼음 많이"처럼 한 문장 안에서 말이 부딪치면, 모델이 한쪽을 골라 올려 놓고
# 답변은 손님 말을 그대로 따라 하는 일이 있었다. 부딪치는 표현을 코드가 먼저 찾아 모델에게 알려 준다.
_COLD = re.compile(r"아이스(?!티|크림)|차갑|차가운|시원|\bice[d]?\b|\bcold\b|冰|アイス(?!ティー|クリーム)|冷た", re.IGNORECASE)
_HOT = re.compile(r"따뜻|따듯|뜨거|뜨겁|핫(?!치킨)|\bhot\b|\bwarm\b|热|熱|ホット|温か", re.IGNORECASE)
_ICE_AMOUNT = re.compile(r"얼음|\bice\b (?:less|more|extra|regular)|(?:less|more|extra|no|regular) ice|少冰|多冰|去冰|氷", re.IGNORECASE)
_NO_SUCH_SIZE = re.compile(r"스몰|미디엄|미디움|미듐|톨\s*사이즈|레귤러|그란데|벤티|숏\s*사이즈|중간\s*사이즈|"
                           r"\b(?:small|medium|tall|grande|venti)\b|中杯|小杯|[SM]\s*サイズ|\b[SM]\s*사이즈", re.IGNORECASE)


def request_warnings(user_text):
    """손님 말에서 찾은 문제. conflict 는 '같은 음료에 대한 말이면 올리지 말고 물어야 하는 것', notes 는 답변에 꼭 밝혀야 하는 것."""
    cold, hot = _COLD.search(user_text), _HOT.search(user_text)
    conflict, notes = None, []
    if cold and hot:
        conflict = (f"손님 말에 차갑게('{cold.group()}')와 따뜻하게('{hot.group()}')가 함께 있습니다. 한 음료에 대한 말이면 "
                    "어느 쪽인지 정할 수 없으니 올리지 말고 ICE 와 HOT 중 무엇을 원하는지 물어보세요.")
    elif hot and _ICE_AMOUNT.search(user_text):
        conflict = ("손님이 따뜻하게와 얼음량을 함께 말했습니다. HOT 음료에는 얼음이 들어가지 않으니, 한 음료에 대한 말이면 올리지 말고 "
                    "따뜻하게(얼음 없음)인지 아이스(얼음량 선택)인지 물어보세요.")
    size = _NO_SUCH_SIZE.search(user_text)
    if size:
        notes.append(f"손님이 말한 '{size.group().strip()}' 사이즈는 없습니다. 사이즈는 L(가장 작음)과 J(큼)뿐이라고 답변에서 먼저 밝히고, "
                     "어느 사이즈로 할지 확인하세요.")
    return conflict, notes


# 반영하지 못한 옵션을 답변이 이미 밝혔는지 (고정이라 못 바꾼다는 말)
_FIXED = {"ko": r"고정|바꿀 수 없|조절이 안|조절할 수 없|반영하지 못", "en": r"fixed|can't be changed|cannot be changed|not adjustable",
          "zh": r"固定|无法(调整|更改)|不能(调整|更改)", "ja": r"固定|変更できません|調整できません"}


def mentions_fixed(reply, language):
    return bool(re.search(_FIXED.get(language, _FIXED["ko"]), reply, re.IGNORECASE))
