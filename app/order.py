"""주문 상태(장바구니·옵션·결제). 가격 계산과 옵션 검증은 LLM 이 아니라 이 코드가 한다."""
import itertools
import json
import re
from datetime import datetime

from app.catalog import SIZE_NAMES

MAX_QUANTITY = 20
# 통신사 제휴 할인 (data/kiosk/policies.json 의 telecom): 제조 음료와 기본 토핑에 10%, 최대 2만원.
DISCOUNTS = {"skt": "SKT T멤버십 10% 할인", "kt": "KT 멤버십 10% 할인"}
DISCOUNT_RATE = 0.10
DISCOUNT_CAP = 20000
DINE_OPTIONS = {"dine_in": "매장에서 먹고 가기", "takeout": "포장하기"}

_SIZE_WORDS = {"라지": "L", "large": "L", "점보": "J", "jumbo": "J", "기가": "G", "giga": "G"}
_TEMP_WORDS = {"ice": "ICE", "iced": "ICE", "cold": "ICE", "아이스": "ICE", "차가운": "ICE",
               "hot": "HOT", "핫": "HOT", "따뜻한": "HOT", "뜨거운": "HOT"}
_DINE_WORDS = {"매장": "dine_in", "dine_in": "dine_in", "포장": "takeout", "takeout": "takeout"}
_order_numbers = itertools.count(101)


class OrderError(Exception):
    """주문을 처리할 수 없을 때. result 는 그대로 도구 결과로 모델에게 돌려준다."""

    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.result = {"ok": False, "error": code, "message": message, **extra}


def won(n):
    return f"{n:,}원"


def _word(value, words):
    """'라지'·'아이스' 같은 말을 메뉴판 코드(L, ICE)로 바꾼다."""
    if not value:
        return None
    value = str(value).strip()
    return words.get(value.lower(), value.upper())


class Order:
    def __init__(self, catalog):
        self.catalog = catalog
        self.lines = []
        self._line_ids = itertools.count(1)
        self.dine_option = None  # "dine_in" | "takeout"
        self.straw = None  # 포장일 때만: 빨대 등 일회용품
        self.carrier = None  # 포장일 때만: 캐리어
        self.discount = None  # "skt" | "kt"
        self.stamp = False  # 공차 멤버십 스탬프 적립
        self.benefits_asked = False  # 할인·적립 여부를 손님이 한 번이라도 정했는지 (결제 전에 꼭 묻는다)
        self.status = "ordering"  # 결제가 끝나면 "paid"
        self._shown = None  # 손님에게 최종 확인으로 보여 준 주문 내용 (그대로일 때만 결제한다)
        self.receipt = None

    # ------------------------------------------------------------ 옵션 검증

    def _size_choices(self, menu):
        return [{"size": s["size"], "name": SIZE_NAMES.get(s["size"], s["size"]), "price": s["price"]}
                for s in menu["sizes"]]

    def _validate(self, menu, size, temperature, sugar, ice, toppings, quantity):
        """손님이 고른 옵션을 메뉴판 규칙에 맞춰 확정한다. (확정 값, 기본값으로 채운 항목, 안내할 점) 을 돌려준다."""
        cat, name = self.catalog, menu["name"]
        if not cat.orderable(menu):
            reason = ("시연 매장(시흥배곧점) 판매 목록에 없는 메뉴" if menu["kind"] == "drink"
                      else "가격 정보가 없어 키오스크에서 주문할 수 없는 메뉴")
            raise OrderError("not_for_sale", f"'{name}'은(는) {reason}입니다. 직원에게 문의하도록 안내하거나 다른 메뉴를 제안하세요.")

        sizes = [s["size"] for s in menu["sizes"]]
        temps = menu["temperatures"]
        size, temperature = _word(size, _SIZE_WORDS), _word(temperature, _TEMP_WORDS)
        size_choices = self._size_choices(menu)
        if size and size not in sizes:
            raise OrderError("invalid_size", f"'{name}'은(는) {', '.join(sizes)} 사이즈만 있습니다. 손님에게 다시 골라 달라고 하세요.",
                             choices={"size": size_choices})
        if temperature and temperature not in temps:
            raise OrderError("invalid_temperature", f"'{name}'은(는) {'/'.join(temps)} 로만 주문할 수 있습니다.",
                             choices={"temperature": temps})
        need, choices = [], {}
        if not size:
            if len(sizes) == 1:
                size = sizes[0]
            else:
                need.append("size")
                choices["size"] = size_choices
        if not temperature:
            if len(temps) == 1:
                temperature = temps[0]
            else:
                need.append("temperature")
                choices["temperature"] = temps
        if need:
            raise OrderError("need_options", "아직 담지 않았습니다. 손님에게 빠진 항목을 한 번에 물어본 뒤 다시 호출하세요.",
                             menu=name, need=need, choices=choices)

        defaults, notes = [], []
        opt = menu["options"]

        sugar = self._parse_sugar(sugar)
        if opt["sugar"]["adjustable"] is False:
            if sugar is not None:
                notes.append("이 메뉴는 당도가 고정이라 당도를 바꿀 수 없습니다.")
            sugar = None
        elif sugar is None and opt["sugar"]["adjustable"]:
            sugar = opt["sugar"]["recommended"]
            defaults.append(f"당도 {sugar}%(추천 당도)")
        elif sugar is not None and opt["sugar"]["adjustable"] is None:
            notes.append("이 메뉴는 당도 조절 가능 여부가 확인되지 않았습니다. 요청은 주문에 표시했고 매장에서 확인이 필요합니다.")

        ice = self._parse_ice(ice)
        if temperature == "HOT":
            ice = None
        elif opt["ice"]["adjustable"] is False:
            if ice not in (None, "regular"):
                notes.append("이 메뉴는 얼음량이 고정이라 바꿀 수 없습니다.")
            ice = None
        elif ice is None and opt["ice"]["adjustable"]:
            ice = "regular"
            defaults.append("얼음 보통")
        elif ice is not None and opt["ice"]["adjustable"] is None:
            notes.append("이 메뉴는 얼음량 조절 가능 여부가 확인되지 않았습니다. 요청은 주문에 표시했고 매장에서 확인이 필요합니다.")

        toppings = self._parse_toppings(toppings)
        if len(toppings) > cat.max_toppings:
            raise OrderError("too_many_toppings", f"토핑은 최대 {cat.max_toppings}가지까지 추가할 수 있습니다.")

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise OrderError("invalid_quantity", "수량은 숫자로 알려 주세요.") from None
        if not 1 <= quantity <= MAX_QUANTITY:
            raise OrderError("invalid_quantity", f"수량은 1~{MAX_QUANTITY}잔 사이로 주문할 수 있습니다.")

        chosen = {"menu_id": menu["id"], "size": size, "temperature": temperature, "sugar": sugar, "ice": ice,
                  "toppings": toppings, "quantity": quantity}
        return chosen, defaults, notes

    def _parse_sugar(self, sugar):
        if sugar is None or sugar == "":
            return None
        digits = re.sub(r"\D", "", str(sugar))
        levels = self.catalog.sugar_levels
        if not digits or int(digits) not in levels:
            raise OrderError("invalid_sugar", "당도는 " + "/".join(f"{l}%" for l in levels) + " 중에서 고를 수 있습니다.",
                             choices={"sugar": levels})
        return int(digits)

    def _parse_ice(self, ice):
        if ice is None or ice == "":
            return None
        levels = self.catalog.ice_levels
        value = str(ice).strip().lower()
        by_label = {label.replace(" ", ""): key for key, label in levels.items()}
        value = by_label.get(value.replace(" ", ""), value)
        if value not in levels:
            raise OrderError("invalid_ice", "얼음량은 " + ", ".join(levels.values()) + " 중에서 고를 수 있습니다.",
                             choices={"ice": list(levels)})
        return value

    def _parse_toppings(self, toppings):
        by_name = {t["name"]: tid for tid, t in self.catalog.toppings.items()}
        result = []
        for t in toppings or []:
            tid = by_name.get(str(t).strip(), str(t).strip().lower())
            if tid not in self.catalog.toppings:
                raise OrderError("invalid_topping", f"'{t}' 토핑은 없습니다. 가능한 토핑: " +
                                 ", ".join(x["name"] for x in self.catalog.toppings.values()))
            if tid not in result:
                result.append(tid)
        return sorted(result, key=list(self.catalog.toppings).index)

    # ------------------------------------------------------------ 장바구니

    def _find(self, line_id):
        for line in self.lines:
            if line["line_id"] == line_id:
                return line
        raise OrderError("line_not_found", f"장바구니에 #{line_id} 줄이 없습니다. <주문상태>의 번호를 확인하세요.")

    def add(self, menu, size=None, temperature=None, sugar=None, ice=None, toppings=None, quantity=1, tumbler=False):
        chosen, defaults, notes = self._validate(menu, size, temperature, sugar, ice, toppings,
                                                 1 if quantity is None else quantity)
        chosen["tumbler"] = bool(tumbler)
        same = next((l for l in self.lines
                     if all(l[k] == chosen[k] for k in chosen if k != "quantity")), None)
        if same:
            if same["quantity"] + chosen["quantity"] > MAX_QUANTITY:
                raise OrderError("invalid_quantity", f"같은 메뉴는 한 번에 {MAX_QUANTITY}잔까지 담을 수 있습니다.")
            same["quantity"] += chosen["quantity"]
            line = same
        else:
            line = {"line_id": next(self._line_ids), **chosen}
            self.lines.append(line)
        return line, defaults, notes

    def update(self, line_id, **changes):
        """바꿀 항목만 넘긴다. 넘기지 않은(None) 항목은 그대로 둔다."""
        line = self._find(line_id)
        menu = self.catalog.by_id[line["menu_id"]]
        merged = {k: (changes[k] if changes.get(k) is not None else line[k])
                  for k in ("size", "temperature", "sugar", "ice", "toppings", "quantity")}
        new_temp = _word(changes.get("temperature"), _TEMP_WORDS)
        if new_temp and new_temp != line["temperature"] and changes.get("ice") is None:
            merged["ice"] = None  # HOT ↔ ICE 를 바꾸면 얼음은 기본값부터 다시 정한다
        chosen, defaults, notes = self._validate(menu, **merged)
        line.update(chosen)
        if changes.get("tumbler") is not None:
            line["tumbler"] = bool(changes["tumbler"])
        return line, defaults, notes

    def replace(self, line_id, size=None, temperature=None, sugar=None, ice=None, toppings=None, quantity=1, tumbler=False):
        """한 줄의 옵션을 통째로 다시 정한다. 화면의 옵션 선택 창은 모든 항목을 한꺼번에 보내기 때문이다."""
        line = self._find(line_id)
        menu = self.catalog.by_id[line["menu_id"]]
        chosen, defaults, notes = self._validate(menu, size, temperature, sugar, ice, toppings, quantity)
        line.update(chosen, tumbler=bool(tumbler))
        return line, defaults, notes

    def remove(self, line_id):
        line = self._find(line_id)
        self.lines.remove(line)
        return line

    def clear(self):
        self.lines.clear()

    # ------------------------------------------------------------ 주문 옵션·결제

    def set_options(self, dine_option=None, straw=None, carrier=None, discount=None, stamp=None):
        notes = []
        if dine_option is not None:
            value = _DINE_WORDS.get(str(dine_option).strip().lower())
            if not value:
                raise OrderError("invalid_dine_option", "이용 방식은 '매장' 또는 '포장' 중에서 고릅니다.")
            self.dine_option = value
            if value == "dine_in":
                self.straw = self.carrier = None
        if straw is not None or carrier is not None:
            if self.dine_option != "takeout":
                notes.append("빨대·캐리어는 포장 주문에서만 선택합니다. 먼저 포장 여부를 확인하세요.")
            else:
                self.straw = bool(straw) if straw is not None else self.straw
                self.carrier = bool(carrier) if carrier is not None else self.carrier
        if discount is not None or stamp is not None:
            self.benefits_asked = True
        if discount is not None:
            value = str(discount).strip().lower()
            if value not in (*DISCOUNTS, "none"):
                raise OrderError("invalid_discount", "할인은 skt, kt, none 중 하나입니다.")
            self.discount = None if value == "none" else value
        if stamp is not None:
            self.stamp = bool(stamp)
        return notes

    def checkout(self, payment_method, print_receipt):
        if self.status == "paid":
            raise OrderError("already_paid", "이미 결제가 끝난 주문입니다.")
        if not self.lines:
            raise OrderError("empty_cart", "장바구니가 비어 있어 결제할 수 없습니다. 먼저 메뉴를 담아야 합니다.")
        if not self.dine_option:
            raise OrderError("need_dine_option", "매장에서 먹고 가는지 포장인지 먼저 확인해야 합니다.", need=["dine_option"])
        if not self.benefits_asked:
            raise OrderError("need_benefits", "멤버십 스탬프 적립과 통신사(SKT/KT) 할인 여부를 아직 묻지 않았습니다. 손님에게 물어보고 "
                             "set_order_options 로 기록한 뒤(둘 다 없으면 discount=none, stamp=false) 다시 호출하세요.",
                             need=["discount", "stamp"])
        methods = self.catalog.payment_methods
        if payment_method not in methods:
            raise OrderError("invalid_payment_method", "결제 수단을 다시 확인해 주세요.", choices={"payment_method": methods})
        # 최종 확인: 같은 내용으로 두 번째 호출될 때 결제한다. 그 사이 주문이 바뀌면 다시 확인받는다.
        showing = json.dumps([self.lines, self.dine_option, self.straw, self.carrier, self.discount, self.stamp,
                              payment_method, bool(print_receipt)], ensure_ascii=False)
        if self._shown != showing:
            self._shown = showing
            raise OrderError("need_confirmation", "아직 결제하지 않았습니다. 아래 주문 내용(메뉴·옵션·수량·금액)과 결제 수단, 영수증 여부를 "
                             "손님에게 보여 주고 이대로 결제할지 확인받으세요. 손님이 동의하면 같은 인자로 다시 호출하면 결제됩니다.",
                             order=self.summary_text(), payment_method=methods[payment_method], print_receipt=bool(print_receipt))
        self.status = "paid"
        self.receipt = {
            "order_number": next(_order_numbers),
            "payment_method": {"id": payment_method, "name": methods[payment_method]},
            "print_receipt": bool(print_receipt),
            "paid_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        return self.receipt

    # ------------------------------------------------------------ 금액·표시

    def _prices(self, line):
        """(한 잔 가격, 그중 제휴 할인 대상 금액). 샷추가는 기본 토핑이 아니라 할인 대상에서 빠진다."""
        cat = self.catalog
        base = next(s["price"] for s in cat.by_id[line["menu_id"]]["sizes"] if s["size"] == line["size"])
        extra = sum(cat.toppings[t]["price"] for t in line["toppings"])
        basic = sum(cat.toppings[t]["price"] for t in line["toppings"] if cat.toppings[t]["basic"])
        return base + extra, base + basic

    def totals(self):
        subtotal = sum(self._prices(l)[0] * l["quantity"] for l in self.lines)
        eligible = sum(self._prices(l)[1] * l["quantity"] for l in self.lines)
        discount = min(int(eligible * DISCOUNT_RATE), DISCOUNT_CAP) if self.discount else 0
        count = sum(l["quantity"] for l in self.lines)
        return {"count": count, "subtotal": subtotal, "discount": discount, "total": subtotal - discount,
                "stamps": count if self.stamp else 0}

    def line_view(self, line):
        cat = self.catalog
        menu = cat.by_id[line["menu_id"]]
        opt = menu["options"]
        unit = self._prices(line)[0]
        sugar_note = None if line["sugar"] is not None else ("fixed" if opt["sugar"]["adjustable"] is False else "default")
        if line["temperature"] == "HOT":
            ice_note = "hot"
        elif line["ice"] is not None:
            ice_note = None
        else:
            ice_note = "fixed" if opt["ice"]["adjustable"] is False else "default"
        return {
            **line,
            "name": menu["name"],
            "image": f"/{menu['image']}" if menu["image"] else None,
            "toppings": [{"id": t, "name": cat.toppings[t]["name"], "price": cat.toppings[t]["price"]} for t in line["toppings"]],
            "sugar_note": sugar_note,
            "ice_note": ice_note,
            "unit_price": unit,
            "line_total": unit * line["quantity"],
        }

    def line_text(self, line):
        v = self.line_view(line)
        sugar = f"당도 {v['sugar']}%" if v["sugar"] is not None else {"fixed": "당도 고정", "default": "당도 기본"}[v["sugar_note"]]
        ice = (self.catalog.ice_levels[v["ice"]] if v["ice"] else
               {"hot": None, "fixed": "얼음 고정", "default": "얼음 기본"}[v["ice_note"]])
        parts = [v["size"], v["temperature"], sugar, ice,
                 "토핑: " + (", ".join(t["name"] for t in v["toppings"]) or "없음"),
                 "개인 텀블러" if v["tumbler"] else None]
        return (f"#{v['line_id']} {v['name']} | " + " | ".join(p for p in parts if p) +
                f" | {v['quantity']}잔 | {won(v['line_total'])}")

    def summary_text(self):
        """모델에게 매 턴 보여 주는 주문 상태."""
        t = self.totals()
        lines = ["장바구니: " + (f"{len(self.lines)}줄, {t['count']}잔" if self.lines else "비어 있음")]
        lines += ["  " + self.line_text(l) for l in self.lines]
        dine = DINE_OPTIONS.get(self.dine_option, "아직 선택 안 함")
        if self.dine_option == "takeout":
            ask = {None: "아직 안 물어봄", True: "필요", False: "필요 없음"}
            dine += f" (빨대: {ask[self.straw]}, 캐리어: {ask[self.carrier]})"
        lines.append(f"이용 방식: {dine}")
        lines.append("할인: " + (DISCOUNTS[self.discount] if self.discount else "없음") +
                     " / 멤버십 스탬프 적립: " + (f"예 ({t['stamps']}개)" if self.stamp else "아니오") +
                     ("" if self.benefits_asked else " (손님에게 아직 안 물어봄)"))
        if self.lines:
            lines.append(f"금액: 주문 {won(t['subtotal'])} - 할인 {won(t['discount'])} = 결제 {won(t['total'])}")
        if self.status == "paid":
            lines.append(f"결제 완료: 주문번호 {self.receipt['order_number']}")
        return "\n".join(lines)

    def to_dict(self):
        """화면에 그릴 주문 상태."""
        return {
            "status": self.status,
            "lines": [self.line_view(l) for l in self.lines],
            "totals": self.totals(),
            "dine_option": self.dine_option,
            "straw": self.straw,
            "carrier": self.carrier,
            "discount": self.discount,
            "stamp": self.stamp,
            "receipt": self.receipt,
        }
