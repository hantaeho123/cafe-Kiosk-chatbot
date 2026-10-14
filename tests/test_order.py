import pytest

from app.order import Order, OrderError, changes
from app.stock import Stock


def menu(catalog, name):
    return catalog.resolve(name)[0]


def test_asks_for_missing_size_and_temperature(catalog):
    order = Order(catalog)
    with pytest.raises(OrderError) as error:
        order.add(menu(catalog, "아메리카노"))
    result = error.value.result
    assert result["error"] == "need_options"
    assert result["need"] == ["size", "temperature"]
    assert [s["size"] for s in result["choices"]["size"]] == ["L", "J"]
    assert not order.lines


def test_fills_recommended_sugar_regular_ice_and_default_cup(catalog):
    order = Order(catalog)
    line, defaults, _ = order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE")
    assert (line["sugar"], line["ice"], line["cup"]) == (50, "regular", "disposable")
    assert len(defaults) == 3


def test_single_choice_options_are_filled_in(catalog):
    order = Order(catalog)
    line, _, notes = order.add(menu(catalog, "망고 스무디"), sugar=50)
    assert (line["size"], line["temperature"], line["sugar"], line["ice"]) == ("L", "ICE", None, None)
    assert notes  # 당도 고정 메뉴라 요청을 반영하지 못했다고 알려 준다


def test_rejects_invalid_choices(catalog):
    order = Order(catalog)
    for kwargs, code in [
        ({"size": "G", "temperature": "ICE"}, "invalid_size"),
        ({"size": "L", "temperature": "ICE", "sugar": 20}, "invalid_sugar"),
        ({"size": "L", "temperature": "ICE", "toppings": ["pearl", "aloe", "coconut", "milk_foam"]}, "too_many_toppings"),
        ({"size": "L", "temperature": "ICE", "quantity": 0}, "invalid_quantity"),
        ({"size": "L", "temperature": "ICE", "cup": "종이컵"}, "invalid_cup"),
    ]:
        with pytest.raises(OrderError) as error:
            order.add(menu(catalog, "블랙 밀크티"), **kwargs)
        assert error.value.result["error"] == code
    with pytest.raises(OrderError) as error:
        order.add(menu(catalog, "망고 스무디"), temperature="HOT")
    assert error.value.result["error"] == "invalid_temperature"
    with pytest.raises(OrderError) as error:
        order.add(menu(catalog, "스틱 치즈케익"))
    assert error.value.result["error"] == "not_for_sale"
    assert not order.lines


def test_ice_levels_follow_the_hti_and_no_ice_is_not_selectable(catalog):
    assert list(catalog.ice_levels) == ["less", "regular", "full"]
    order = Order(catalog)
    for said in ("none", "얼음 없이"):
        with pytest.raises(OrderError) as error:
            order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE", ice=said)
        assert error.value.result["error"] == "ice_not_selectable" and error.value.result["closest"] == "less"
    line, _, _ = order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="HOT", ice="none")
    assert line["ice"] is None  # HOT 에는 얼음 옵션이 없으니 따질 것도 없다


def test_extra_shot_is_not_counted_as_one_of_three_toppings(catalog):
    order = Order(catalog)
    line, _, _ = order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE",
                           toppings=["pearl", "aloe", "coconut", "extra_shot"])
    assert len(line["toppings"]) == 4


def test_cup_follows_the_dine_option(catalog):
    order = Order(catalog)
    order.set_options(dine_option="매장")
    store, _, _ = order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE")
    own, _, _ = order.add(menu(catalog, "타로 밀크티"), size="L", temperature="ICE", cup="텀블러")
    assert (store["cup"], own["cup"]) == ("store", "personal")
    order.set_options(dine_option="포장")  # 포장으로 바꾸면 매장컵은 일회용컵이 되고 개인컵은 그대로다
    assert (store["cup"], own["cup"]) == ("disposable", "personal")
    with pytest.raises(OrderError) as error:
        order.update(store["line_id"], cup="store")
    assert error.value.result["error"] == "invalid_cup"
    order.set_options(dine_option="매장")
    assert store["cup"] == "store"
    assert "매장컵" in order.line_text(store)


def test_prices_toppings_and_discount(catalog):
    order = Order(catalog)
    order.add(menu(catalog, "블랙 밀크티"), size="J", temperature="ICE", toppings=["펄", "extra_shot"], quantity=2)
    assert order.totals()["subtotal"] == (5700 + 800 + 500) * 2
    order.discount, order.stamp = "skt", {"code": "x", "label": "앱"}
    totals = order.totals()
    assert totals["discount"] == (5700 + 800) * 2 // 10  # 샷추가는 기본 토핑이 아니라 할인 대상이 아니다
    assert totals["total"] == totals["due"] == totals["subtotal"] - totals["discount"]
    assert totals["stamps"] == 2


def test_same_item_merges_into_one_line(catalog):
    order = Order(catalog)
    for toppings in (["pearl", "aloe"], ["aloe", "pearl"]):
        order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE", toppings=toppings)
    assert len(order.lines) == 1 and order.lines[0]["quantity"] == 2
    order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE", toppings=["pearl", "aloe"], cup="personal")
    assert len(order.lines) == 2  # 컵이 다르면 다른 줄이다


def test_update_and_remove(catalog):
    order = Order(catalog)
    line, _, _ = order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE", ice="less", toppings=["pearl"])
    order.update(line["line_id"], temperature="HOT", quantity=3)
    assert (line["temperature"], line["ice"], line["quantity"], line["toppings"]) == ("HOT", None, 3, ["pearl"])
    order.update(line["line_id"], temperature="ICE", toppings=[])
    assert (line["ice"], line["toppings"]) == ("regular", [])
    order.remove(line["line_id"])
    assert not order.lines
    with pytest.raises(OrderError):
        order.remove(line["line_id"])


def test_request_notes(catalog):
    order = Order(catalog)
    line, _, _ = order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE")
    whole = order.add_note("  두 잔 중  하나는 포장 ")
    mine = order.add_note("얼음 빼 주세요", line["line_id"])
    assert whole["text"] == "두 잔 중 하나는 포장" and whole["line_id"] is None
    assert order.add_note("얼음 빼 주세요", line["line_id"]) is mine  # 같은 메모를 두 번 붙이지 않는다
    assert "요청 메모 2번: 얼음 빼 주세요" in order.line_text(line)
    assert "주문 전체 요청 메모 1번: 두 잔 중 하나는 포장" in order.summary_text()
    view = order.to_dict()
    assert [n["text"] for n in view["notes"]] == ["두 잔 중 하나는 포장"]
    assert [n["text"] for n in view["lines"][0]["notes"]] == ["얼음 빼 주세요"]
    for text, code in [("", "empty_note"), ("가" * 61, "note_too_long")]:
        with pytest.raises(OrderError) as error:
            order.add_note(text)
        assert error.value.result["error"] == code
    with pytest.raises(OrderError) as error:
        order.add_note("없는 줄", 99)
    assert error.value.result["error"] == "line_not_found"
    for i in range(3):
        order.add_note(f"메모 {i}")
    with pytest.raises(OrderError) as error:
        order.add_note("여섯 번째")
    assert error.value.result["error"] == "too_many_notes"
    order.remove(line["line_id"])  # 줄을 빼면 그 줄의 메모도 함께 사라진다
    assert all(n["line_id"] is None for n in order.notes)
    order.remove_note(whole["note_id"])
    with pytest.raises(OrderError):
        order.remove_note(whole["note_id"])


def test_sold_out_menu_cannot_be_ordered(catalog):
    stock = Stock(catalog, names=["타로 밀크티"])
    order = Order(catalog, stock)
    with pytest.raises(OrderError) as error:
        order.add(menu(catalog, "타로 밀크티"), size="L", temperature="ICE")
    assert error.value.result["error"] == "sold_out"
    line, _, _ = order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE")
    stock.set(line["menu_id"], True)  # 담은 뒤에 품절되면 주문 내역에 표시된다
    assert order.to_dict()["lines"][0]["sold_out"] and "지금 품절" in order.summary_text()


def test_changes_between_the_order_and_a_draft(catalog):
    order = Order(catalog)
    order.set_options(dine_option="매장")
    kept, _, _ = order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE")
    gone, _, _ = order.add(menu(catalog, "아메리카노"), size="L", temperature="HOT")
    draft = order.clone()
    draft.update(kept["line_id"], sugar=30, quantity=2)
    draft.remove(gone["line_id"])
    added, _, _ = draft.add(menu(catalog, "타로 밀크티"), size="J", temperature="ICE")
    draft.add_note("빨대 두 개", None)
    draft.add_note("펄 조금만", added["line_id"])
    draft.set_options(dine_option="포장")
    assert [l["quantity"] for l in order.lines] == [1, 1] and not order.notes  # 복사본을 고쳐도 주문은 그대로다

    diff = changes(order, draft)
    by_type = {c["type"]: c for c in diff}
    assert [c["type"] for c in diff] == ["update", "add", "remove", "note_add", "dine"]
    assert by_type["update"]["fields"] == ["sugar", "quantity", "cup"]  # 포장으로 바뀌며 컵도 일회용컵이 된다
    assert (by_type["update"]["before"]["line_total"], by_type["update"]["after"]["line_total"]) == (4600, 9200)
    assert by_type["add"]["after"]["name"] == "타로 밀크티" and by_type["add"]["after"]["notes"][0]["text"] == "펄 조금만"
    assert by_type["remove"]["before"]["name"] == "아메리카노"
    assert by_type["note_add"]["note"]["text"] == "빨대 두 개"  # 새 줄에 붙은 메모는 그 줄과 함께 보여 준다
    assert (by_type["dine"]["before"], by_type["dine"]["after"]) == ("dine_in", "takeout")
    assert changes(order, order.clone()) == []

    order.adopt(draft)
    assert [l["line_id"] for l in order.lines] == [kept["line_id"], added["line_id"]] and order.dine_option == "takeout"
    line, _, _ = order.add(menu(catalog, "아메리카노"), size="L", temperature="HOT")
    assert line["line_id"] > added["line_id"]  # 줄 번호는 다시 쓰지 않는다


def test_straw_and_carrier_only_for_takeout(catalog):
    order = Order(catalog)
    assert order.set_options(straw=True)  # 포장이 아니면 기록하지 않고 안내만 한다
    assert order.straw is None
    order.set_options(dine_option="포장", straw=True, carrier=False)
    assert (order.straw, order.carrier) == (True, False)
    order.set_options(dine_option="매장")
    assert (order.straw, order.carrier) == (None, None)
