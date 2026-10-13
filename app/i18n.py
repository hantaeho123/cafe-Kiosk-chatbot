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
        "ko": "안녕하세요, 공차 {store}입니다. 메뉴가 궁금하시거나 주문하실 게 있으면 편하게 말씀해 주세요.\n"
              "예) \"달콤한 음료 추천해줘\", \"블랙 밀크티 얼마예요?\", \"아메리카노 한 잔 주세요\"",
        "en": "Hello, welcome to Gong Cha {store}. Ask me about the menu or tell me what you'd like to order.\n"
              "e.g. \"Recommend something sweet\", \"How much is the Black Milk Tea?\", \"One Americano, please\"",
        "zh": "您好，欢迎光临贡茶{store}。想了解菜单或想点单，请直接告诉我。\n"
              "例如：“推荐一款甜的饮料”、“黑奶茶多少钱？”、“来一杯美式咖啡”",
        "ja": "いらっしゃいませ、ゴンチャ{store}です。メニューのご質問やご注文をお気軽にどうぞ。\n"
              "例）「甘いドリンクをおすすめして」「ブラックミルクティーはいくら？」「アメリカーノを1つください」",
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
        "ko": ["인기 메뉴 추천해줘", "달콤한 음료 추천해줘", "카페인 없는 음료 있어요?", "신메뉴 뭐 있어요?"],
        "en": ["What's popular?", "Recommend something sweet", "Any caffeine-free drinks?", "What's new?"],
        "zh": ["推荐人气饮品", "推荐甜的饮料", "有不含咖啡因的吗？", "有什么新品？"],
        "ja": ["人気メニューを教えて", "甘いドリンクのおすすめは？", "カフェインなしはありますか？", "新メニューは？"],
    },
    "cart": {
        "ko": ["결제할게요", "주문 내역 확인해줘", "다른 메뉴도 추천해줘"],
        "en": ["I'd like to pay", "Show my order", "Recommend something else"],
        "zh": ["我要结账", "确认一下订单", "再推荐别的"],
        "ja": ["お会計をお願いします", "注文内容を確認して", "ほかのおすすめは？"],
    },
    "dine": {
        "ko": ["매장에서 먹고 갈게요", "포장할게요"],
        "en": ["For here", "To go"],
        "zh": ["在店里喝", "打包带走"],
        "ja": ["店内で", "持ち帰りで"],
    },
}


def money(amount, language):
    return f"{amount:,}원" if language == "ko" else f"₩{amount:,}"


def text(key, language, **values):
    return TEXTS[key].get(language, TEXTS[key]["ko"]).format(**values)


def chips(key, language):
    return [{"label": s, "text": s} for s in CHIPS[key].get(language, CHIPS[key]["ko"])]
