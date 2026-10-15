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
    assert {"best", "new", "밀크티", "커피", "dessert"} <= set(tabs) and not {"etc", "1리터 배달 메뉴"} & set(tabs)  # MD·1리터 배달 메뉴는 숨긴다
    # 매장 메뉴판과 같다: BEST SELLER 14개를 메뉴판 순서대로, 신메뉴(NEW) 다음에 시즌 메뉴
    assert len(tabs["best"]) == 14 and tabs["best"][0]["name"] == "타로 쥬얼리 시그니처 밀크티" and tabs["best"][-1]["name"] == "우롱티 + 코코넛 + 밀크폼"
    assert all(i["badges"] == ["BEST"] for i in tabs["best"]) and tabs["best"][0]["marks"] == ["caffeine_free", "iced_only"]
    assert [i["badges"] for i in tabs["new"][:2]] == [["NEW"], ["NEW"]] and tabs["new"][2]["badges"] == ["SEASON"]
    assert all(item["orderable"] for item in tabs["커피"])
    assert all(item["orderable"] for item in tabs["dessert"])  # 매장 키오스크 화면에 가격이 있는 푸드만 보이고 주문할 수 있다


def test_sold_out_shows_on_the_board_and_is_left_out_of_suggestions(catalog):
    from app.stock import Stock
    stock = Stock(catalog)  # data/kiosk/stock.json 의 초깃값
    assert stock.names() == ["초코멜로 스무디"]
    best = catalog.suggest()[0]
    assert best["name"] == "타로 쥬얼리 시그니처 밀크티"  # 메뉴판 BEST SELLER 첫째
    stock.set(best["id"], True)
    marked = [i["name"] for t in catalog.board(stock.ids()) for i in t["items"] if i["sold_out"]]
    assert set(marked) == {"초코멜로 스무디", "타로 쥬얼리 시그니처 밀크티"}
    assert best["id"] not in [m["id"] for m in catalog.suggest(stock.ids())]
    assert best["id"] not in [m["id"] for m in catalog.find_drinks(flavor="달콤한", exclude_ids=stock.ids())]
    stock.set(best["id"], False)
    assert stock.version == 2 and stock.to_dict()["sold_out"] == stock.ids()


def test_facts_for_comparing_menus(catalog):
    black, smoothie = (catalog.facts(catalog.resolve(n)[0]) for n in ("블랙 밀크티", "망고 스무디"))
    assert black["price"] == "L 4,600원, J 5,700원" and black["caffeine_mg"] == 128 and black["hot_available"]
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
    assert catalog.find_drinks(flavor="달콤한", sort="popular")[0]["name"] == "브라운슈가 시그니처 밀크티 +펄"  # 메뉴판 BEST SELLER 순서
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


def test_menus_have_translated_names_and_descriptions(catalog):
    """외국어 화면용 번역(data/curated/menu_i18n.json). 화면은 그 언어의 이름을 먼저 보여 주고 한글 이름을 작게 붙인다."""
    taro = catalog.resolve("타로 밀크티")[0]
    card = catalog.card(taro)
    assert card["name"] == "타로 밀크티" and card["names"] == {"en": "Taro Milk Tea", "zh": "芋头奶茶", "ja": "タロミルクティー"}
    assert set(catalog.detail(taro)["descriptions"]) == {"en", "zh", "ja"}
    for menu in catalog.items:
        assert set(catalog.names(menu)) == {"en", "zh", "ja"}, menu["name"]
        described = {lang for lang, t in menu["i18n"].items() if t.get("description")}
        assert described == ({"en", "zh", "ja"} if menu["description"] else set()), menu["name"]


def test_menus_can_be_found_by_their_foreign_names(catalog):
    assert catalog.resolve("Black Milk Tea with Pearls")[0]["name"] == "블랙 밀크티 + 펄"
    assert catalog.resolve("芋头奶茶")[0]["name"] == "타로 밀크티" and catalog.resolve("タロミルクティー")[0]["name"] == "타로 밀크티"
    assert [m["name"] for m in catalog.find_mentions("One Americano and a Taro Milk Tea, please")] == ["아메리카노", "타로 밀크티"]


def test_prices_are_store_prices_and_unpriced_menus_are_hidden(catalog):
    """가격은 매장 판매가(data/raw/store/prices.csv: 메뉴판·키오스크 사진, 다이닝코드)로 통일한다. 못 찾은 메뉴는 숨긴다."""
    cake = catalog.resolve("스틱 치즈케익")[0]
    assert cake["price"] == 2600 and cake["price_source"] == "store" and catalog.orderable(cake)  # 키오스크 화면에 가격이 있는 푸드는 주문할 수 있다
    assert catalog.card(cake)["price"] == 2600 and catalog.facts(cake)["price"] == "2,600원" and catalog.visible(cake)
    guava = catalog.resolve("핑크구아바 봉봉 아이스티")[0]
    assert [(s["size"], s["price"]) for s in guava["sizes"]] == [("L", 5300), ("J", 6400)] and catalog.orderable(guava)
    black = catalog.resolve("블랙 밀크티")[0]
    assert black["price_source"] == "store" and [s["price"] for s in black["sizes"]] == [4600, 5700]  # 배달가 4,900원보다 300원 낮다
    soup = catalog.resolve("머쉬룸 수프")[0]
    assert soup["price"] is None and not catalog.visible(soup)  # 가격을 못 찾은 것은 비워 두고 숨긴다
    liter = catalog.resolve("아메리카노 1L")[0]
    assert not catalog.orderable(liter) and not catalog.visible(liter)  # 배달 전용(매장 메뉴판에 없음)
    hidden = catalog.resolve("더블 말차 밀크티 + 펄")[0]
    assert not catalog.orderable(hidden) and not catalog.visible(hidden)
    shown = [i["id"] for t in catalog.board() for i in t["items"]]
    assert soup["id"] not in shown and hidden["id"] not in shown and cake["id"] in shown


def test_topping_price_depends_on_size(catalog):
    assert (catalog.topping_price("pearl", "L"), catalog.topping_price("pearl", "J")) == (600, 800)
    assert (catalog.topping_price("cheese_foam", "L"), catalog.topping_price("cheese_foam", "J")) == (900, 1100)
    assert catalog.topping_price("extra_shot", "J") == 500