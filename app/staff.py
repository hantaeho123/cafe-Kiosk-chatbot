"""직원 화면(/staff)에 띄우는 것: 직원 호출과, 결제가 끝난 주문(요청 메모 포함). 시연용이라 서버 메모리에만 둔다."""
import itertools
import threading
from datetime import datetime

from app.order import DINE_OPTIONS

KEEP = 30  # 화면에 남겨 두는 최근 건수


class StaffBoard:
    def __init__(self):
        self.calls = []
        self.orders = []
        self._call_ids = itertools.count(1)
        self._lock = threading.Lock()

    def call(self, reason, source):
        """직원 호출. source 는 어디서 불렀는지: "button"(화면 버튼) 또는 "chat"(대화)."""
        entry = {"id": next(self._call_ids), "time": datetime.now().strftime("%H:%M"), "reason": reason, "source": source,
                 "status": "waiting"}
        with self._lock:
            self.calls.append(entry)
            del self.calls[:-KEEP]
        return entry

    def answer(self, call_id):
        """직원이 호출을 확인했다. 키오스크 화면에도 '직원이 가고 있다'고 보인다."""
        with self._lock:
            for entry in self.calls:
                if entry["id"] == call_id:
                    entry["status"] = "coming"
                    return entry
        raise KeyError(call_id)

    def add_order(self, order):
        """결제가 끝난 주문을 제조 화면에 올린다. 요청 메모가 여기에서 직원에게 전달된다."""
        entry = {
            "number": order.receipt["order_number"],
            "time": order.receipt["paid_at"][-5:],
            "dine_option": DINE_OPTIONS.get(order.dine_option, ""),
            "extras": [label for label, wanted in (("빨대", order.straw), ("캐리어", order.carrier)) if wanted],
            "lines": [{"name": order.catalog.by_id[l["menu_id"]]["name"], "quantity": l["quantity"],
                       "options": " · ".join(order.option_parts(l)), "voucher": bool(l["voucher"]),
                       "notes": [n["text"] for n in order.notes if n["line_id"] == l["line_id"]]} for l in order.lines],
            "notes": [n["text"] for n in order.notes if n["line_id"] is None],
            "has_notes": bool(order.notes),
            "status": "new",
        }
        with self._lock:
            self.orders.append(entry)
            del self.orders[:-KEEP]

    def done(self, number):
        with self._lock:
            for entry in self.orders:
                if entry["number"] == number:
                    entry["status"] = "done"
                    return entry
        raise KeyError(number)

    def to_dict(self):
        with self._lock:
            return {"calls": list(reversed(self.calls)), "orders": list(reversed(self.orders))}
