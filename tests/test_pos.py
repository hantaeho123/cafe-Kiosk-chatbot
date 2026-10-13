"""POS·바코드 리더·결제 단말기. 결제는 손님의 행동으로만 진행된다."""
import pytest

from app.order import Order, OrderError
from app.pos import Barcodes, Pos
from app.staff import StaffBoard
from app.stock import Stock


@pytest.fixture
def shop(catalog):
    stock, board = Stock(catalog, names=[]), StaffBoard()
    return Pos(catalog, Barcodes(), board), stock, board


def an_order(catalog, stock, *items, dine="매장"):
    order = Order(catalog, stock)
    if dine:
        order.set_options(dine_option=dine)
    for name, options in items:
        order.add(catalog.resolve(name)[0], **options)
    return order


def code_of(error):
    return error.value.result["error"]


BLACK = ("블랙 밀크티", {"size": "L", "temperature": "ICE"})  # 4,900원
AMERICANO = ("아메리카노", {"size": "L", "temperature": "ICE"})  # 4,200원


def test_payment_screen_needs_items_dine_option_and_stock(catalog, shop):
    pos, stock, _ = shop
    with pytest.raises(OrderError) as error:
        pos.start(an_order(catalog, stock))
    assert code_of(error) == "empty_cart"
    with pytest.raises(OrderError) as error:
        pos.start(an_order(catalog, stock, BLACK, dine=None))
    assert code_of(error) == "need_dine_option"
    order = an_order(catalog, stock, BLACK)
    stock.set(order.lines[0]["menu_id"], True)
    with pytest.raises(OrderError) as error:
        pos.start(order)
    assert code_of(error) == "sold_out_in_cart" and error.value.result["menus"] == ["블랙 밀크티"]
    stock.set(order.lines[0]["menu_id"], False)
    pos.start(order)
    assert order.status == "paying"
    pos.cancel(order)
    assert order.status == "ordering"


def test_barcode_reader_checks_what_was_scanned(catalog, shop):
    pos, stock, _ = shop
    order = an_order(catalog, stock, BLACK)
    with pytest.raises(OrderError) as error:  # 할인 바코드는 결제 화면에서만 찍는다
        pos.scan(order, "membership", "SKT-1001")
    assert code_of(error) == "wrong_screen"
    pos.start(order)
    for code, expected in [("DAMAGED", "barcode_unreadable"), ("no-such-code", "barcode_unreadable"), ("CP-2000", "barcode_wrong_kind")]:
        with pytest.raises(OrderError) as error:
            pos.scan(order, "membership", code)
        assert code_of(error) == expected
    assert order.discount is None
    assert pos.scan(order, "membership", "skt-1001")["label"] == "SKT T멤버십 바코드"
    assert order.discount == "skt" and order.totals()["discount"] == 490
    with pytest.raises(OrderError) as error:
        pos.scan(order, "membership", "SKT-1001")
    assert code_of(error) == "barcode_used"


def test_stamp_discount_and_coupon_rules(catalog, shop):
    pos, stock, _ = shop
    order = an_order(catalog, stock, BLACK, AMERICANO)
    pos.start(order)
    pos.scan(order, "stamp", "GC-APP-1001")
    pos.scan(order, "membership", "KT-1001")
    assert order.totals()["stamps"] == 2 and order.totals()["discount"] == 910  # 스탬프는 통신사 할인과 함께 쓸 수 있다
    with pytest.raises(OrderError) as error:  # 쿠폰은 통신사 할인과 함께 쓸 수 없다
        pos.scan(order, "coupon", "CP-2000")
    assert code_of(error) == "conflict_discount"
    pos.remove_benefit(order, "discount")
    pos.scan(order, "coupon", "CP-2000")
    assert order.totals()["coupon"] == 2000 and order.totals()["total"] == 7100
    with pytest.raises(OrderError) as error:
        pos.scan(order, "membership", "KT-1001")
    assert code_of(error) == "conflict_coupon"
    pos.scan(order, "coupon", "CP-FREE")  # 쿠폰은 한 장만: 새로 찍은 쿠폰으로 바뀐다
    assert order.coupon["benefit"] == "free_drink" and order.totals()["coupon"] == 4900  # 가장 비싼 한 잔의 메뉴 가격
    pos.remove_benefit(order, "coupon")
    pos.remove_benefit(order, "stamp")
    totals = order.totals()
    assert (totals["coupon"], totals["discount"], totals["stamps"], totals["total"]) == (0, 0, 0, 9100)


def test_menu_voucher_puts_its_menu_in_the_cart(catalog, shop):
    pos, stock, _ = shop
    order = an_order(catalog, stock, AMERICANO)
    result = pos.scan(order, "menu_voucher", "MV-BMT-PEARL-L")
    line = order.lines[-1]
    assert result["menu"] == "블랙 밀크티 + 펄" and (line["size"], line["quantity"]) == ("L", 1)
    assert order.totals()["voucher"] == 5500 and order.totals()["total"] == 4200  # 메뉴 가격은 교환권이 낸다
    order.update(line["line_id"], toppings=["aloe"], sugar=30)  # 옵션은 바꿀 수 있고 토핑 값만 더 낸다
    assert order.totals()["total"] == 4200 + 700
    for change in ({"size": "J"}, {"quantity": 2}):
        with pytest.raises(OrderError) as error:
            order.update(line["line_id"], **change)
        assert code_of(error) == "voucher_locked"
    with pytest.raises(OrderError) as error:
        pos.scan(order, "menu_voucher", "MV-BMT-PEARL-L")
    assert code_of(error) == "barcode_used"
    pos.start(order)
    with pytest.raises(OrderError) as error:  # 통신사 할인은 교환권(메뉴권)과 함께 쓸 수 없다
        pos.scan(order, "membership", "SKT-1001")
    assert code_of(error) == "conflict_voucher"
    assert "교환권 사용" in order.summary_text()


def test_gift_card_then_card_and_the_order_reaches_the_staff_screen(catalog, shop):
    pos, stock, board = shop
    order = an_order(catalog, stock, BLACK, AMERICANO, dine="포장")
    order.add_note("얼음 빼 주세요", order.lines[0]["line_id"])
    order.add_note("캐리어에 담아 주세요")
    with pytest.raises(OrderError) as error:
        pos.pay(order, "card")
    assert code_of(error) == "not_paying"
    pos.start(order)
    used = pos.scan(order, "gift_card", "GV-3000")
    assert (used["amount"], used["balance_left"]) == (3000, 0)
    assert order.status == "paying" and order.totals()["due"] == 6100  # 모자란 금액은 다른 수단으로 이어서 낸다
    pos.cancel(order)  # 결제 화면에서 나가면 금액권 차감을 되돌린다
    assert order.tenders == [] and order.totals()["due"] == 9100
    pos.start(order)
    pos.scan(order, "gift_card", "GV-3000")
    assert pos.pay(order, "card") is True
    assert order.status == "paid" and order.totals()["due"] == 0
    receipt = order.receipt
    assert receipt["order_number"] >= 101 and receipt["print_receipt"] is None
    assert [(p["method"], p["amount"]) for p in receipt["payments"]] == [("gift_card", 3000), ("card", 6100)]
    pos.set_receipt(order, True)
    assert order.to_dict()["receipt"]["print_receipt"] is True

    made = board.to_dict()["orders"][0]  # 요청 메모가 제조 화면으로 전달된다
    assert made["number"] == receipt["order_number"] and made["dine_option"] == "포장하기" and made["has_notes"]
    assert made["lines"][0]["name"] == "블랙 밀크티" and made["lines"][0]["options"].startswith("L · ICE · 당도 50%")
    assert made["lines"][0]["notes"] == ["얼음 빼 주세요"] and made["notes"] == ["캐리어에 담아 주세요"]
    assert board.done(made["number"])["status"] == "done"


def test_declined_payment_can_be_retried(catalog, shop):
    pos, stock, _ = shop
    order = an_order(catalog, stock, BLACK)
    pos.start(order)
    assert pos.pay(order, "card", "declined") is False
    assert order.status == "paying" and order.failure["reason"] == "declined" and order.failure["attempts"] == 1
    assert pos.pay(order, "card", "unreadable") is False
    assert order.failure["attempts"] == 2 and "직전 결제 실패" in order.summary_text()
    assert pos.pay(order, "nfc") is True  # 다른 결제수단으로 다시 시도
    assert order.status == "paid" and order.failure is None and order.receipt["payments"][0]["name"] == "휴대폰 결제(삼성페이 등)"
    with pytest.raises(OrderError) as error:
        pos.start(order)
    assert code_of(error) == "already_paid"


def test_payment_barcode_and_orders_with_nothing_left_to_pay(catalog, shop):
    pos, stock, _ = shop
    order = an_order(catalog, stock, AMERICANO)
    pos.start(order)
    pos.scan(order, "pay", "KAKAOPAY-1001")
    assert order.status == "paid" and order.receipt["payments"] == [{"method": "kakaopay", "name": "카카오페이", "amount": 4200}]

    free = an_order(catalog, stock)
    pos.scan(free, "menu_voucher", "MV-AMERICANO-L")
    pos.start(free)
    assert free.totals()["due"] == 0
    with pytest.raises(OrderError) as error:
        pos.pay(free, "card")
    assert code_of(error) == "nothing_due"
    assert pos.pay(free, "none") is True and free.status == "paid" and free.receipt["payments"] == []


def test_demo_barcodes_are_listed_for_the_screen():
    demo = Barcodes().demo()
    kinds = {b["kind"] for b in demo["barcodes"]}
    assert {"app", "membership", "coupon", "menu_voucher", "gift_card", "pay", "unreadable"} == kinds
    assert [s["id"] for s in demo["scenarios"]["card"]] == ["ok", "declined", "unreadable"]


def test_staff_calls(catalog):
    board = StaffBoard()
    call = board.call("바코드가 안 읽혀요", "button")
    assert call["status"] == "waiting" and board.to_dict()["calls"][0] is call
    assert board.answer(call["id"])["status"] == "coming"
    with pytest.raises(KeyError):
        board.answer(999)
