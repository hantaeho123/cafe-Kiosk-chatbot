"""서버가 직접 만드는 문구(인사말, 빠른 답변 버튼, 오류 안내)의 언어별 번역. 모델의 답변은 모델이 직접 번역한다."""
import re

LANGUAGES = {"ko": "한국어", "en": "English", "zh": "中文", "ja": "日本語"}


def detect_language(text):
    """손님이 쓴 글의 언어를 글자 종류로 짐작한다. 짧거나 애매하면 None 을 돌려주고, 그때는 화면 언어를 그대로 쓴다.

    "ICE L", "ok", "아메리카노 Large" 같은 입력에 언어가 바뀌지 않도록 영어는 세 글자 이상인 낱말이 둘 이상일 때만 본다.
    """
    if re.search(r"[\u3040-\u30ff]", text):  # 히라가나·가타카나
        return "ja"
    words = re.findall(r"[A-Za-z]{3,}", text)
    if re.search(r"[가-힣]", text):
        return "ko" if len(words) < 2 else None
    if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 2:  # 한자
        return "zh"
    return "en" if len(words) >= 2 else None

TEXTS = {
    "greeting": {
        "ko": "안녕하세요, 공차 {store}입니다. 메뉴가 궁금하시거나 주문하실 게 있으면 편하게 말씀해 주세요. "
              "말로 주문하신 내용은 확인 화면에서 [확인]을 눌러야 담겨요.\n"
              "예) \"카페인 적고 덜 단 거 추천해줘\", \"블랙 밀크티 L 아이스 하나, 당도 30%에 펄 추가\", \"얼음 없이도 돼요?\"",
        "en": "Hello, welcome to Gong Cha {store}. Ask me about the menu or tell me what you'd like to order. "
              "Orders placed by chat are added only after you tap [Confirm] on the confirmation screen.\n"
              "e.g. \"Something low in caffeine and not too sweet\", \"One iced Black Milk Tea, large, 30% sugar with pearls\", \"Can I get it without ice?\"",
        "zh": "您好，欢迎光临贡茶{store}。想了解菜单或想点单，请直接告诉我。通过对话点的饮品，需要在确认画面点击［确认］后才会加入订单。\n"
              "例如：“推荐咖啡因少、不太甜的”、“一杯冰的黑奶茶大杯，三分糖加珍珠”、“可以去冰吗？”",
        "ja": "いらっしゃいませ、ゴンチャ{store}です。メニューのご質問やご注文をお気軽にどうぞ。チャットでのご注文は、確認画面で［確認］を押すと追加されます。\n"
              "例）「カフェイン少なめで甘さ控えめのおすすめは？」「ブラックミルクティーのアイスLを1つ、甘さ30%でパール追加」「氷なしにできますか？」",
    },
    # 모더레이션에 걸린 입력에 대한 안내
    "blocked": {
        "ko": "죄송하지만 그 내용은 제가 도와드리기 어려워요. 메뉴 안내나 주문이 필요하시면 말씀해 주세요.",
        "en": "I'm sorry, I can't help with that. Let me know if you'd like menu information or to place an order.",
        "zh": "抱歉，这个内容我无法协助。如需了解菜单或点单，请告诉我。",
        "ja": "申し訳ございませんが、その内容にはお応えできません。メニューのご案内やご注文はお気軽にどうぞ。",
    },
    "error": {
        "ko": "죄송해요, 답변을 만드는 중에 문제가 생겼어요. 잠시 후 다시 말씀해 주시거나 직원을 불러 주세요.",
        "en": "Sorry, something went wrong while answering. Please try again in a moment or call a staff member.",
        "zh": "抱歉，生成回答时出现了问题。请稍后再试，或呼叫店员。",
        "ja": "申し訳ございません、回答中に問題が発生しました。少し待ってからもう一度お試しいただくか、スタッフをお呼びください。",
    },
    "busy": {
        "ko": "앞의 답변을 아직 만들고 있어요. 잠시만 기다려 주세요.",
        "en": "I'm still working on the previous answer. One moment, please.",
        "zh": "上一条回答还在生成中，请稍等。",
        "ja": "前の回答を作成中です。少々お待ちください。",
    },
    # 사이즈·온도를 고르는 버튼을 눌렀을 때 대신 입력되는 말
    "pick_size_temp": {"ko": "{menu} {temp} {size} 사이즈로 주세요", "en": "{menu}: {temp}, size {size} please",
                       "zh": "{menu} 要{temp}的{size}杯", "ja": "{menu}を{temp}の{size}サイズでお願いします"},
    "pick_size": {"ko": "{menu} {size} 사이즈로 주세요", "en": "{menu}: size {size}, please",
                  "zh": "{menu} 要{size}杯", "ja": "{menu}を{size}サイズでお願いします"},
    "pick_temp": {"ko": "{menu} {temp}로 주세요", "en": "{menu}: {temp}, please",
                  "zh": "{menu} 要{temp}的", "ja": "{menu}を{temp}でお願いします"},
}

# 상황별 빠른 답변 버튼. 누르면 그 문장이 그대로 입력된다.
CHIPS = {
    "start": {
        "ko": ["인기 메뉴 추천해줘", "카페인 적고 덜 단 거 추천해줘", "2만 원으로 셋이 마실 거 추천해줘", "키오스크 사용법 알려줘"],
        "en": ["What's popular?", "Something low in caffeine and not too sweet", "Drinks for three within ₩20,000", "How do I use this kiosk?"],
        "zh": ["推荐人气饮品", "推荐咖啡因少、不太甜的", "两万韩元三个人喝，推荐一下", "这台点单机怎么用？"],
        "ja": ["人気メニューを教えて", "カフェイン少なめで甘さ控えめのおすすめは？", "2万ウォンで3人分のおすすめは？", "キオスクの使い方を教えて"],
    },
    "cart": {
        "ko": ["주문 내역 확인해줘", "다른 메뉴도 추천해줘", "요청 사항 남길게요"],
        "en": ["Show my order", "Recommend something else", "I have a special request"],
        "zh": ["确认一下订单", "再推荐别的", "我有特别要求"],
        "ja": ["注文内容を確認して", "ほかのおすすめは？", "お願いしたいことがあります"],
    },
    "paying": {
        "ko": ["카드는 어디에 꽂아요?", "할인은 어떻게 받아요?", "결제가 안 돼요"],
        "en": ["Where do I insert my card?", "How do I get a discount?", "My payment isn't going through"],
        "zh": ["卡插在哪里？", "怎么享受折扣？", "付款失败了"],
        "ja": ["カードはどこに入れますか？", "割引はどうやって受けますか？", "支払いができません"],
    },
}

# 누르면 문장을 보내는 대신 화면이 바로 동작하는 버튼. 결제와 변경 확인은 손님이 직접 눌러야 하기 때문이다.
ACTIONS = {
    "pay": {"ko": "결제하기", "en": "Pay", "zh": "结账", "ja": "お会計"},
    "resume_pay": {"ko": "결제 화면으로 돌아가기", "en": "Back to payment", "zh": "返回付款画面", "ja": "お支払い画面に戻る"},
    "confirm": {"ko": "변경 확인하기", "en": "Review changes", "zh": "确认变更", "ja": "変更を確認する"},
    "reject": {"ko": "변경 취소", "en": "Discard changes", "zh": "取消变更", "ja": "変更を取り消す"},
    "undo": {"ko": "방금 변경 되돌리기", "en": "Undo last change", "zh": "撤销刚才的变更", "ja": "直前の変更を元に戻す"},
    "dine_in": {"ko": "매장에서 먹고 갈게요", "en": "For here", "zh": "在店里喝", "ja": "店内で"},
    "takeout": {"ko": "포장할게요", "en": "To go", "zh": "打包带走", "ja": "持ち帰りで"},
}


# 화면 버튼의 언어별 이름 (web/i18n.js 와 같아야 한다). 다른 언어로 답할 때 모델이 화면에 보이는 이름으로 안내하게 한다.
BUTTON_NAMES = {
    "en": {"확인": "Confirm", "취소": "Cancel", "변경 확인하기": "Review changes", "고쳐서 다시 말하기": "Say it again", "결제하기": "Pay", "이전": "Back",
           "처음으로": "Start over", "시간 연장": "More time", "직원 호출": "Call staff", "교환권 사용": "Use a voucher",
           "옵션 변경": "Edit options", "삭제": "Remove", "전체 취소": "Clear all", "되돌리기": "Undo", "닫기": "Close",
           "다시 시도": "Try again", "다른 결제수단": "Another method"},
    "zh": {"확인": "确认", "취소": "取消", "변경 확인하기": "确认变更", "고쳐서 다시 말하기": "重新说一遍", "결제하기": "结账", "이전": "返回",
           "처음으로": "重新开始", "시간 연장": "延长时间", "직원 호출": "呼叫店员", "교환권 사용": "使用兑换券",
           "옵션 변경": "修改选项", "삭제": "删除", "전체 취소": "全部取消", "되돌리기": "撤销", "닫기": "关闭",
           "다시 시도": "重试", "다른 결제수단": "其他支付方式"},
    "ja": {"확인": "確認", "취소": "取り消す", "변경 확인하기": "変更を確認する", "고쳐서 다시 말하기": "言い直す", "결제하기": "お会計", "이전": "戻る",
           "처음으로": "最初から", "시간 연장": "時間延長", "직원 호출": "スタッフを呼ぶ", "교환권 사용": "引換券を使う",
           "옵션 변경": "オプション変更", "삭제": "削除", "전체 취소": "すべて取り消す", "되돌리기": "元に戻す", "닫기": "閉じる",
           "다시 시도": "もう一度試す", "다른 결제수단": "別の支払い方法"},
}


def button_names(language):
    """다른 언어 화면에서 버튼이 어떤 이름으로 보이는지. 한국어면 빈 글."""
    names = BUTTON_NAMES.get(language)
    return ", ".join(f"[{ko}]=[{name}]" for ko, name in names.items()) if names else ""


def money(amount, language):
    return f"{amount:,}원" if language == "ko" else f"₩{amount:,}"


def text(key, language, **values):
    return TEXTS[key].get(language, TEXTS[key]["ko"]).format(**values)


def chips(key, language):
    return [{"label": s, "text": s} for s in CHIPS[key].get(language, CHIPS[key]["ko"])]


def action(key, language):
    return {"label": ACTIONS[key].get(language, ACTIONS[key]["ko"]), "action": key}
