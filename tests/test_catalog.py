def test_resolve_exact_and_alias(catalog):
    assert catalog.resolve("아메리카노")[0]["name"] == "아메리카노"
    assert catalog.resolve("블랙밀크티+펄")[0]["name"] == "블랙 밀크티 + 펄"
    assert catalog.resolve("망고 쥬스")[0]["name"] == "망고 주스"
    assert catalog.resolve("초코 밀크티")[0]["name"] == "초콜렛 밀크티"


def test_resolve_drops_temperature_words(catalog):
    assert catalog.resolve("아이스 아메리카노")[0]["name"] == "아메리카노"
    assert catalog.resolve("따뜻한 카페라떼")[0]["name"] == "카페라떼"


def test_resolve_ambiguous_returns_candidates(catalog):
    menu, candidates = catalog.resolve("라떼")
    assert menu is None
    assert {m["name"] for m in candidates} == {"카페라떼", "바닐라 카페라떼"}


def test_resolve_unknown_menu(catalog):
    assert catalog.resolve("카푸치노") == (None, [])


def test_find_mentions_prefers_longest_name(catalog):
    text = "**블랙 밀크티 + 펄**과 **타로 밀크티**를 추천해요. 카푸치노는 없어요."
    assert [m["name"] for m in catalog.find_mentions(text)] == ["블랙 밀크티 + 펄", "타로 밀크티"]


def test_board_follows_hti_categories(catalog):
    tabs = {t["id"]: t["items"] for t in catalog.board()}
    assert {"recommend", "new", "밀크티", "커피", "dessert", "etc"} <= set(tabs)
    assert [i["badges"][0] for i in tabs["recommend"][:5]] == ["인기1위", "인기2위", "인기3위", "인기4위", "인기5위"]
    assert all(item["orderable"] for item in tabs["커피"])
    assert not any(item["orderable"] for item in tabs["dessert"])  # 디저트는 가격 정보가 없다


def test_find_drinks_applies_every_condition(catalog):
    found = catalog.find_drinks(caffeine="none", no_milk=True)
    assert found and all(m["tags"]["caffeine_level"] == "무카페인" and not m["tags"]["contains_milk"] for m in found)
    names = {m["name"] for m in found}
    assert "망고 주스" in names and not {"청포도 스무디", "얼그레이티", "타로 밀크티"} & names
    hot_cheap = catalog.find_drinks(hot=True, max_price=4500, sort="price")
    assert [m["name"] for m in hot_cheap][0] == "아메리카노"
    assert all("HOT" in m["temperatures"] and catalog.min_price(m) <= 4500 for m in hot_cheap)
    assert catalog.find_drinks(flavor="달콤한", sort="popular")[0]["name"] == "딸기 쥬얼리 밀크티"  # 인기 1위
    assert catalog.find_drinks(category="커피", max_price=1000) == []


def test_detect_language():
    from app.i18n import detect_language
    assert detect_language("What do you recommend?") == "en"
    assert detect_language("おすすめは何ですか") == "ja"
    assert detect_language("有什么推荐的吗") == "zh"
    assert detect_language("아메리카노 Large 하나요") == "ko"
    assert [detect_language(t) for t in ("ICE L", "ok", "ㅁㄴㅇㄹ", "1", "americano")] == [None] * 5
