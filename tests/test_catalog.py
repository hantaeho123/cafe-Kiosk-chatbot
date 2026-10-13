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


def test_board_has_best_season_and_category_tabs(catalog):
    tabs = {t["id"]: t["items"] for t in catalog.board()}
    assert {"recommend", "new", "밀크티", "커피", "dessert", "etc"} <= set(tabs)
    assert [i["badges"][0] for i in tabs["recommend"][:5]] == ["인기1위", "인기2위", "인기3위", "인기4위", "인기5위"]
    assert all(item["orderable"] for item in tabs["커피"])
    assert not any(item["orderable"] for item in tabs["dessert"])  # 디저트는 가격 정보가 없다


def test_sold_out_shows_on_the_board_and_is_left_out_of_suggestions(catalog):
    from app.stock import Stock
    stock = Stock(catalog)  # data/kiosk/stock.json 의 초깃값
    assert stock.names() == ["초코멜로 스무디"]
    best = catalog.suggest()[0]
    assert best["name"] == "딸기 쥬얼리 밀크티"
    stock.set(best["id"], True)
    marked = [i["name"] for t in catalog.board(stock.ids()) for i in t["items"] if i["sold_out"]]
    assert set(marked) == {"초코멜로 스무디", "딸기 쥬얼리 밀크티"}
    assert best["id"] not in [m["id"] for m in catalog.suggest(stock.ids())]
    assert best["id"] not in [m["id"] for m in catalog.find_drinks(flavor="달콤한", exclude_ids=stock.ids())]
    stock.set(best["id"], False)
    assert stock.version == 2 and stock.to_dict()["sold_out"] == stock.ids()


def test_facts_for_comparing_menus(catalog):
    black, smoothie = (catalog.facts(catalog.resolve(n)[0]) for n in ("블랙 밀크티", "망고 스무디"))
    assert black["price"] == "L 4,900원, J 6,000원" and black["caffeine_mg"] == 128 and black["hot_available"]
    assert black["sugar_option"] == "조절 가능(추천 50%)" and smoothie["sugar_option"] == "고정"
    assert black["basis"] == "ICE L, 당도 0% 기준"


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
    adjustable = catalog.find_drinks(caffeine="low", sugar_adjustable=True)
    assert adjustable and all(m["options"]["sugar"]["adjustable"] is True for m in adjustable)
    assert all("우유" not in m["allergens"] for m in catalog.find_drinks(exclude_allergens=["우유"]))


def test_detect_language():
    from app.i18n import detect_language
    assert detect_language("What do you recommend?") == "en"
    assert detect_language("おすすめは何ですか") == "ja"
    assert detect_language("有什么推荐的吗") == "zh"
    assert detect_language("아메리카노 Large 하나요") == "ko"
    assert [detect_language(t) for t in ("ICE L", "ok", "ㅁㄴㅇㄹ", "1", "americano")] == [None] * 5


def test_menus_can_be_found_by_their_english_names(catalog):
    assert catalog.resolve("Black Milk Tea with Pearls")[0]["name"] == "블랙 밀크티 + 펄"
    assert [m["name"] for m in catalog.find_mentions("One Americano and a Taro Milk Tea, please")] == ["아메리카노", "타로 밀크티"]
    card = catalog.card(catalog.resolve("타로 밀크티")[0])
    assert card["name"] == "타로 밀크티" and card["name_en"] == "Taro Milk Tea"  # 화면은 외국어일 때 영문명을 먼저 보여 준다
    assert all(m["name_en"] for m in catalog.items)
