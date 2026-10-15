"""POS·바코드 리더·결제 단말기(시뮬레이션).

결제는 손님의 행동(바코드 찍기, 카드 꽂기, 휴대폰 갖다 대기)으로만 진행된다. 모델에게는 이 모듈을 부르는 도구가 없다.
실제 기기 대신 data/kiosk/barcodes.json 의 시연용 바코드와 단말기 시나리오를 쓴다.
"""
import itertools
import json
from datetime import datetime

from app.config import KIOSK_DIR
from app.order import DISCOUNTS, OrderError

_order_numbers = itertools.count(101)

# 바코드를 찍는 목적 → 그때 읽혀야 하는 바코드 종류
PURPOSES = {"stamp": "app", "membership": "membership", "coupon": "coupon",
            "menu_voucher": "menu_voucher", "gift_card": "gift_card", "pay": "pay"}
FAILURES = {
    "declined": "카드사에서 승인을 거절했습니다(한도 초과 또는 잔액 부족)",
    "unreadable": "카드의 IC 칩을 읽지 못했습니다",
}


class Barcodes:
    def __init__(self, path=KIOSK_DIR / "barcodes.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.items = {b["code"].upper(): b for b in data["items"]}
        self.scenarios = data["terminal_scenarios"]

    def read(self, code):
        """바코드 리더가 읽은 값. 모르는 코드나 손상된 바코드는 읽지 못한 것으로 본다."""
        item = self.items.get(str(code or "").strip().upper())
        if not item or item["kind"] == "unreadable":
            raise OrderError("barcode_unreadable", "바코드를 읽지 못했습니다. 화면 밝기를 올려 다시 비추거나 직원을 불러 주세요.")
        return item

    def demo(self):
        """화면의 시연용 바코드 목록."""
        return {
            "barcodes": [{k: b.get(k) for k in ("code", "kind", "label", "label_en")} for b in self.items.values()],
            "scenarios": {k: v for k, v in self.scenarios.items() if not k.startswith("_")},
        }


class Pos:
    def __init__(self, catalog, barcodes, board=None):
        self.catalog = catalog
        self.barcodes = barcodes
        self.board = board  # 결제가 끝난 주문을 직원 화면에 올린다

    # ------------------------------------------------------------ 결제 화면 열고 닫기

    def start(self, order):
        """손님이 [결제하기]를 눌렀을 때. 결제 화면이 열려 있는 동안에는 주문을 바꿀 수 없다."""
        if order.status == "paid":
            raise OrderError("already_paid", "이미 결제가 끝난 주문입니다.")
        if not order.lines:
            raise OrderError("empty_cart", "주문 내역이 비어 있어 결제할 수 없습니다.")
        if not order.dine_option:
            raise OrderError("need_dine_option", "매장에서 먹고 갈지 포장할지 먼저 골라 주세요.")
        sold_out = [order.catalog.by_id[l["menu_id"]]["name"] for l in order.lines
                    if order.stock and order.stock.is_sold_out(l["menu_id"])]
        if sold_out:
            raise OrderError("sold_out_in_cart", "품절된 메뉴가 주문 내역에 있습니다: " + ", ".join(sold_out) + ". 빼고 다시 결제해 주세요.",
                             menus=sold_out)
        order.status = "paying"
        order.failure = None

    def cancel(self, order):
        """결제 화면에서 [이전]으로 나갈 때. 금액권 차감은 되돌리고, 찍어 둔 할인·적립·쿠폰은 그대로 둔다."""
        if order.status != "paying":
            return
        order.scanned -= {t["code"] for t in order.tenders if t.get("code")}
        order.tenders.clear()
        order.failure = None
        order.status = "ordering"

    # ------------------------------------------------------------ 바코드 리더

    def scan(self, order, purpose, code):
        """바코드를 읽어 목적에 맞게 처리한다. 화면에 띄울 결과를 돌려준다."""
        if purpose not in PURPOSES:
            raise OrderError("bad_purpose", "알 수 없는 바코드 용도입니다.")
        if order.status == "paid":
            raise OrderError("already_paid", "이미 결제가 끝난 주문입니다.")
        if (purpose == "menu_voucher") != (order.status == "ordering"):
            raise OrderError("wrong_screen", "교환권은 주문 화면에서, 할인·적립·결제 바코드는 결제 화면에서 찍습니다.")
        item = self.barcodes.read(code)
        if item["kind"] != PURPOSES[purpose]:
            raise OrderError("barcode_wrong_kind", f"'{item['label']}'은(는) 여기에서 쓸 수 없는 바코드입니다.",
                             expected=PURPOSES[purpose], got=item["kind"], label=item["label"])
        if item["code"] in order.scanned:
            raise OrderError("barcode_used", f"'{item['label']}'은(는) 이 주문에서 이미 사용했습니다.", label=item["label"])
        result = getattr(self, f"_scan_{purpose}")(order, item)
        order.scanned.add(item["code"])
        return {"purpose": purpose, "label": item["label"], "label_en": item.get("label_en"), **(result or {})}

    def _scan_stamp(self, order, item):
        if order.stamp:
            order.scanned.discard(order.stamp["code"])
        order.stamp = {"code": item["code"], "label": item["label"]}

    def _scan_membership(self, order, item):
        # 공식 이용안내: 기타 서비스와 중복 혜택 불가, 메뉴권과 동시 사용 불가 (금액권·스탬프 적립은 가능)
        if order.coupon:
            raise OrderError("conflict_coupon", "통신사 할인은 쿠폰과 함께 쓸 수 없습니다. 쿠폰을 빼고 다시 찍어 주세요.")
        if any(l["voucher"] for l in order.lines):
            raise OrderError("conflict_voucher", "통신사 할인은 교환권(메뉴권)과 함께 쓸 수 없습니다.")
        order.discount = item["telecom"]

    def _scan_coupon(self, order, item):
        if order.discount:
            raise OrderError("conflict_discount", "쿠폰은 통신사 할인과 함께 쓸 수 없습니다. 통신사 할인을 빼고 다시 찍어 주세요.")
        if item["benefit"] == "free_drink" and all(l["voucher"] or not order._is_drink(l) for l in order.lines):
            raise OrderError("coupon_not_applicable", "이 쿠폰을 쓸 수 있는 음료가 주문에 없습니다.")
        if order.coupon:  # 쿠폰은 한 주문에 한 장: 새로 찍은 쿠폰으로 바꾼다
            order.scanned.discard(order.coupon["code"])
        order.coupon = {"code": item["code"], "label": item["label"], "benefit": item["benefit"], "amount": item.get("amount", 0)}

    def _scan_menu_voucher(self, order, item):
        if order.discount:
            raise OrderError("conflict_discount", "교환권(메뉴권)은 통신사 할인과 함께 쓸 수 없습니다. 통신사 할인을 먼저 빼 주세요.")
        menu, _ = self.catalog.resolve(item["menu"])
        if not menu:
            raise OrderError("voucher_menu_missing", f"교환권의 메뉴({item['menu']})를 이 매장에서 판매하지 않습니다. 직원에게 문의해 주세요.")
        temperature = "ICE" if "ICE" in menu["temperatures"] else menu["temperatures"][0]
        line, _, _ = order.add(menu, size=item["size"], temperature=temperature,
                               voucher={"code": item["code"], "label": item["label"]})
        return {"line_id": line["line_id"], "menu": menu["name"]}

    def _scan_gift_card(self, order, item):
        due = order.totals()["due"]
        if due <= 0:
            raise OrderError("nothing_due", "더 낼 금액이 없습니다.")
        amount = min(item["balance"], due)
        order.tenders.append({"method": "gift_card", "name": self.catalog.payment_methods["gift_card"], "amount": amount,
                              "code": item["code"], "label": item["label"], "balance_left": item["balance"] - amount})
        order.failure = None
        if order.totals()["due"] == 0:
            self._finish(order)
        return {"amount": amount, "balance_left": item["balance"] - amount}

    def _scan_pay(self, order, item):
        self._tender(order, item["method"])

    def remove_benefit(self, order, kind):
        """결제 화면에서 찍어 둔 할인·적립·쿠폰을 뺀다."""
        if order.status == "paid":
            raise OrderError("already_paid", "이미 결제가 끝난 주문입니다.")
        if order.tenders:
            raise OrderError("payment_started", "이미 일부를 결제해서 할인을 바꿀 수 없습니다. [이전]으로 돌아가 결제를 다시 시작해 주세요.")
        if kind == "discount":
            order.scanned -= {code for code, b in self.barcodes.items.items() if b["kind"] == "membership"}
            order.discount = None
        elif kind in ("coupon", "stamp"):
            current = getattr(order, kind)
            if current:
                order.scanned.discard(current["code"])
            setattr(order, kind, None)
        else:
            raise OrderError("bad_benefit", "알 수 없는 혜택입니다.")

    # ------------------------------------------------------------ 결제 단말기

    def pay(self, order, method, scenario="ok"):
        """카드를 꽂거나 휴대폰을 갖다 댔을 때. scenario 는 시연용으로 고른 카드의 상태다. 승인됐는지를 돌려준다."""
        if order.status != "paying":
            raise OrderError("not_paying", "결제 화면이 열려 있지 않습니다.")
        due = order.totals()["due"]
        if method == "none":  # 교환권·쿠폰으로 낼 금액이 0원이 된 주문
            if due != 0:
                raise OrderError("amount_due", "아직 낼 금액이 남아 있습니다.")
            self._finish(order)
            return True
        if method not in ("card", "nfc"):
            raise OrderError("invalid_payment_method", "결제수단을 다시 골라 주세요.")
        if due <= 0:
            raise OrderError("nothing_due", "더 낼 금액이 없습니다.")
        if scenario != "ok":
            attempts = (order.failure or {}).get("attempts", 0) + 1
            order.failure = {"method": method, "reason": scenario if scenario in FAILURES else "declined", "attempts": attempts}
            order.failure["reason_text"] = FAILURES[order.failure["reason"]]
            return False
        self._tender(order, method)
        return True

    def _tender(self, order, method):
        if order.status != "paying":
            raise OrderError("not_paying", "결제 화면이 열려 있지 않습니다.")
        due = order.totals()["due"]
        if due <= 0:
            raise OrderError("nothing_due", "더 낼 금액이 없습니다.")
        order.tenders.append({"method": method, "name": self.catalog.payment_methods[method], "amount": due})
        self._finish(order)

    def _finish(self, order):
        order.status = "paid"
        order.failure = None
        order.receipt = {
            "order_number": next(_order_numbers),
            "paid_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "payments": [{"method": t["method"], "name": t["name"], "amount": t["amount"]} for t in order.tenders],
            "discount_name": DISCOUNTS.get(order.discount),
            "print_receipt": None,  # 손님이 결제 뒤에 고른다
        }
        if self.board:
            self.board.add_order(order)

    def set_receipt(self, order, print_receipt):
        if order.status != "paid":
            raise OrderError("not_paid", "결제가 끝난 뒤에 영수증을 고를 수 있습니다.")
        order.receipt["print_receipt"] = bool(print_receipt)
