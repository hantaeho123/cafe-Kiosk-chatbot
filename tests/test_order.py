import pytest

from app.order import Order, OrderError


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


def test_fills_recommended_sugar_and_regular_ice(catalog):
    order = Order(catalog)
    line, defaults, _ = order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE")
    assert (line["sugar"], line["ice"]) == (50, "regular")
    assert len(defaults) == 2


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


def test_prices_toppings_and_discount(catalog):
    order = Order(catalog)
    order.add(menu(catalog, "블랙 밀크티"), size="J", temperature="ICE", toppings=["펄", "extra_shot"], quantity=2)
    assert order.totals()["subtotal"] == (6000 + 600 + 500) * 2
    order.set_options(dine_option="포장", discount="skt", stamp=True)
    totals = order.totals()
    assert totals["discount"] == (6000 + 600) * 2 // 10  # 샷추가는 기본 토핑이 아니라 할인 대상이 아니다
    assert totals["total"] == totals["subtotal"] - totals["discount"]
    assert totals["stamps"] == 2


def test_same_item_merges_into_one_line(catalog):
    order = Order(catalog)
    for toppings in (["pearl", "aloe"], ["aloe", "pearl"]):
        order.add(menu(catalog, "블랙 밀크티"), size="L", temperature="ICE", toppings=toppings)
    assert len(order.lines) == 1 and order.lines[0]["quantity"] == 2


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


def test_checkout_needs_items_and_dine_option(catalog):
    order = Order(catalog)
    with pytest.raises(OrderError) as error:
        order.checkout("card", True)
    assert error.value.result["error"] == "empty_cart"
    order.add(menu(catalog, "아메리카노"), size="L", temperature="HOT")
    with pytest.raises(OrderError) as error:
        order.checkout("card", True)
    assert error.value.result["error"] == "need_dine_option"
    order.set_options(dine_option="매장")
    assert "아직 안 물어봄" in order.summary_text()
    with pytest.raises(OrderError) as error:
        order.checkout("card", True)
    assert error.value.result["error"] == "need_benefits"  # 적립·할인 여부(HTI 7.4)를 묻기 전에는 결제하지 않는다
    order.set_options(discount="none", stamp=False)
    assert "아직 안 물어봄" not in order.summary_text()
    with pytest.raises(OrderError) as error:
        order.checkout("bitcoin", True)
    assert error.value.result["error"] == "invalid_payment_method"
    # 첫 호출은 최종 확인용으로 주문 내용만 돌려주고, 같은 내용으로 다시 호출해야 결제된다
    with pytest.raises(OrderError) as error:
        order.checkout("kakaopay", False)
    assert error.value.result["error"] == "need_confirmation" and "아메리카노" in error.value.result["order"]
    order.update(order.lines[0]["line_id"], quantity=2)  # 확인 뒤에 주문이 바뀌면 다시 확인받는다
    with pytest.raises(OrderError) as error:
        order.checkout("kakaopay", False)
    assert error.value.result["error"] == "need_confirmation" and order.status == "ordering"
    receipt = order.checkout("kakaopay", False)
    assert order.status == "paid" and receipt["order_number"] >= 101
    assert order.to_dict()["receipt"]["payment_method"]["name"] == "카카오페이"


def test_straw_and_carrier_only_for_takeout(catalog):
    order = Order(catalog)
    assert order.set_options(straw=True)  # 포장이 아니면 기록하지 않고 안내만 한다
    assert order.straw is None
    order.set_options(dine_option="포장", straw=True, carrier=False)
    assert (order.straw, order.carrier) == (True, False)
    order.set_options(dine_option="매장")
    assert (order.straw, order.carrier) == (None, None)
