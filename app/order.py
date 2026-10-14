"""주문 상태(장바구니·요청 메모·할인·결제 내역). 가격 계산과 옵션 검증은 LLM 이 아니라 이 코드(POS)가 한다.

결제 절차(바코드 리더, 결제 단말기)는 app/pos.py 에 있다.
"""
import copy
import re

from app.catalog import SIZE_NAMES

MAX_QUANTITY = 20
# 통신사 제휴 할인 (data/kiosk/policies.json 의 telecom): 제조 음료와 기본 토핑에 10%, 최대 2만원.
DISCOUNTS = {"skt": "SKT T멤버십 10% 할인", "kt": "KT 멤버십 10% 할인"}
DISCOUNT_RATE = 0.10
DISCOUNT_CAP = 20000
DINE_OPTIONS = {"dine_in": "매장에서 먹고 가기", "takeout": "포장하기"}
LINE_FIELDS = ("size", "temperature", "sugar", "ice", "toppings", "quantity", "cup")

_SIZE_WORDS = {"라지": "L", "large": "L", "점보": "J", "jumbo": "J", "기가": "G", "giga": "G"}
_TEMP_WORDS = {"ice": "ICE", "iced": "ICE", "cold": "ICE", "아이스": "ICE", "차가운": "ICE",
               "hot": "HOT", "핫": "HOT", "따뜻한": "HOT", "뜨거운": "HOT"}
_DINE_WORDS = {"매장": "dine_in", "dine_in": "dine_in", "포장": "takeout", "takeout": "takeout"}
_CUP_WORDS = {"store": "store", "매장컵": "store", "머그": "store", "머그컵": "store",
              "disposable": "disposable", "일회용컵": "disposable", "일회용": "disposable", "테이크아웃컵": "disposable",
              "personal": "personal", "개인컵": "personal", "텀블러": "personal", "개인텀블러": "personal", "tumbler": "personal"}
_NO_ICE_WORDS = {"none", "no", "noice", "얼음없이", "얼음빼고", "얼음제외", "없이"}
# 되돌리기(직전 변경 취소)와 변경 확인 화면이 다루는 범위. 할인·결제 내역은 여기에 들지 않는다.
_CART_FIELDS = ("lines", "notes", "dine_option", "straw", "carrier")


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
    def __init__(self, catalog, stock=None):
        self.catalog = catalog
        self.stock = stock  # 품절 상태. 없으면 품절을 확인하지 않는다.
        self.lines = []
        self.notes = []  # 요청 메모: {"note_id", "text", "line_id"(주문 전체에 대한 메모면 None)}
        self._next_line = 1
        self._next_note = 1
        self.dine_option = None  # "dine_in" | "takeout"
        self.straw = None  # 포장일 때만: 빨대 등 일회용품
        self.carrier = None  # 포장일 때만: 캐리어
        # 아래는 손님이 결제 화면에서 바코드를 찍거나 단말기를 써야만 바뀐다 (app/pos.py)
        self.discount = None  # "skt" | "kt"
        self.stamp = None  # 공차 멤버십 앱 바코드를 찍었으면 {"code", "label"}
        self.coupon = None  # {"code", "label", "benefit": "amount" | "free_drink", "amount"}
        self.tenders = []  # 낸 돈: {"method", "name", "amount", ...}
        self.scanned = set()  # 이 주문에서 이미 쓴 바코드
        self.failure = None  # 직전 결제 실패: {"method", "reason", "attempts"}
        self.status = "ordering"  # "ordering" → "paying"(결제 화면) → "paid"
        self.receipt = None

    # ------------------------------------------------------------ 옵션 검증

    def _size_choices(self, menu):
        return [{"size": s["size"], "name": SIZE_NAMES.get(s["size"], s["size"]), "price": s["price"]}
                for s in menu["sizes"]]

    def _validate(self, menu, size, temperature, sugar, ice, toppings, quantity, cup):
        """손님이 고른 옵션을 메뉴판 규칙에 맞춰 확정한다. (확정 값, 기본값으로 채운 항목, 안내할 점) 을 돌려준다."""
        cat, name = self.catalog, menu["name"]
        if not cat.orderable(menu):
            reason = ("시연 매장(시흥배곧점) 판매 목록에 없는 메뉴" if menu["kind"] == "drink"
                      else "음료가 아니어서 키오스크에서 주문할 수 없고 카운터에서 주문하는 메뉴"
                      + (f"(가격 {won(menu['price'])})" if menu.get("price") else "(가격 정보 없음)"))
            raise OrderError("not_for_sale", f"'{name}'은(는) {reason}입니다. 직원에게 문의하도록 안내하거나 다른 메뉴를 제안하세요.")
        if self.stock and self.stock.is_sold_out(menu["id"]):
            raise OrderError("sold_out", f"'{name}'은(는) 지금 품절이라 주문할 수 없습니다. 품절이라고 알리고 비슷한 다른 메뉴를 제안하세요.")

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

        ice = None if temperature == "HOT" else self._parse_ice(ice)
        if temperature == "HOT":
            pass
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
        # 공식 안내의 '7가지 토핑 중 최대 3가지'. 샷추가는 그 7가지에 들지 않아 세지 않는다.
        if sum(cat.toppings[t]["basic"] for t in toppings) > cat.max_toppings:
            raise OrderError("too_many_toppings", f"토핑은 최대 {cat.max_toppings}가지까지 추가할 수 있습니다.")

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise OrderError("invalid_quantity", "수량은 숫자로 알려 주세요.") from None
        if not 1 <= quantity <= MAX_QUANTITY:
            raise OrderError("invalid_quantity", f"수량은 1~{MAX_QUANTITY}잔 사이로 주문할 수 있습니다.")

        cup, cup_default = self._parse_cup(cup)
        if cup_default:
            defaults.append(cat.cups[cup]["name"])
        chosen = {"menu_id": menu["id"], "size": size, "temperature": temperature, "sugar": sugar, "ice": ice,
                  "toppings": toppings, "quantity": quantity, "cup": cup}
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
        value = str(ice).strip().lower().replace(" ", "")
        value = {label.replace(" ", ""): key for key, label in levels.items()}.get(value, value)
        if value in _NO_ICE_WORDS:
            raise OrderError("ice_not_selectable",
                             "'얼음 없이'는 화면에서 고를 수 없는 값이라 반영하지 않았습니다. 가장 가까운 선택은 '얼음 적게'입니다. "
                             "손님에게 얼음 적게로 할지 묻고, 원하면 '얼음 빼 주세요'를 요청 메모로 남길 수 있다고 안내하세요. "
                             "메모는 직원에게 전달될 뿐이며 들어준다고 약속하지 않습니다.",
                             choices={"ice": list(levels)}, closest="less")
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

    def _parse_cup(self, cup):
        """(컵, 기본값으로 정했는지). 말하지 않으면 매장에서는 매장컵, 그 밖에는 일회용컵이다."""
        cups = self.catalog.cups
        if cup is None or cup == "":
            return ("store" if self.dine_option == "dine_in" else "disposable"), True
        value = _CUP_WORDS.get(str(cup).strip().lower().replace(" ", ""))
        if value not in cups:
            raise OrderError("invalid_cup", "컵은 " + ", ".join(c["name"] for c in cups.values()) + " 중에서 고를 수 있습니다.",
                             choices={"cup": list(cups)})
        if value == "store" and self.dine_option == "takeout":
            raise OrderError("invalid_cup", "포장 주문은 매장컵에 담을 수 없습니다. 일회용컵이나 개인컵 중에서 고르게 하세요.",
                             choices={"cup": [c for c in cups if c != "store"]})
        return value, False

    # ------------------------------------------------------------ 장바구니

    def _find(self, line_id):
        for line in self.lines:
            if line["line_id"] == line_id:
                return line
        raise OrderError("line_not_found", f"장바구니에 #{line_id} 줄이 없습니다. <실시간상태>의 번호를 확인하세요.")

    def add(self, menu, size=None, temperature=None, sugar=None, ice=None, toppings=None, quantity=1, cup=None, voucher=None):
        chosen, defaults, notes = self._validate(menu, size, temperature, sugar, ice, toppings,
                                                 1 if quantity is None else quantity, cup)
        chosen["voucher"] = voucher  # 교환권으로 담은 줄이면 {"code", "label"}
        same = None if voucher else next((l for l in self.lines if all(l[k] == chosen[k] for k in chosen if k != "quantity")), None)
        if same:
            if same["quantity"] + chosen["quantity"] > MAX_QUANTITY:
                raise OrderError("invalid_quantity", f"같은 메뉴는 한 번에 {MAX_QUANTITY}잔까지 담을 수 있습니다.")
            same["quantity"] += chosen["quantity"]
            line = same
        else:
            line = {"line_id": self._next_line, **chosen}
            self._next_line += 1
            self.lines.append(line)
        return line, defaults, notes

    def _change(self, line, menu, **options):
        chosen, defaults, notes = self._validate(menu, **options)
        if line["voucher"] and (chosen["size"] != line["size"] or chosen["quantity"] != 1):
            raise OrderError("voucher_locked", "교환권으로 담은 메뉴는 사이즈와 수량을 바꿀 수 없습니다. 당도·얼음·토핑·컵만 바꿀 수 있습니다.")
        line.update(chosen)
        return line, defaults, notes

    def update(self, line_id, **changes):
        """바꿀 항목만 넘긴다. 넘기지 않은(None) 항목은 그대로 둔다."""
        line = self._find(line_id)
        merged = {k: (changes[k] if changes.get(k) is not None else line[k]) for k in LINE_FIELDS}
        new_temp = _word(changes.get("temperature"), _TEMP_WORDS)
        if new_temp and new_temp != line["temperature"] and changes.get("ice") is None:
            merged["ice"] = None  # HOT ↔ ICE 를 바꾸면 얼음은 기본값부터 다시 정한다
        return self._change(line, self.catalog.by_id[line["menu_id"]], **merged)

    def replace(self, line_id, size=None, temperature=None, sugar=None, ice=None, toppings=None, quantity=1, cup=None):
        """한 줄의 옵션을 통째로 다시 정한다. 화면의 옵션 선택 창은 모든 항목을 한꺼번에 보내기 때문이다."""
        line = self._find(line_id)
        return self._change(line, self.catalog.by_id[line["menu_id"]], size=size, temperature=temperature, sugar=sugar,
                            ice=ice, toppings=toppings, quantity=quantity, cup=cup)

    def remove(self, line_id):
        line = self._find(line_id)
        self.lines.remove(line)
        self.notes = [n for n in self.notes if n["line_id"] != line_id]
        return line

    def clear(self):
        self.lines.clear()
        self.notes.clear()

    # ------------------------------------------------------------ 요청 메모

    def add_note(self, text, line_id=None):
        """화면에서 고를 수 없는 요청을 주문(또는 한 줄)에 메모로 붙인다. 들어줄지는 매장이 정한다."""
        limits = self.catalog.flow["requests"]
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            raise OrderError("empty_note", "메모 내용이 비어 있습니다.")
        if len(text) > limits["max_length"]:
            raise OrderError("note_too_long", f"요청 메모는 {limits['max_length']}자까지 남길 수 있습니다. 더 짧게 정리해서 다시 호출하세요.")
        if line_id is not None:
            self._find(line_id)
        same = next((n for n in self.notes if n["text"] == text and n["line_id"] == line_id), None)
        if same:
            return same
        if len(self.notes) >= limits["max_notes"]:
            raise OrderError("too_many_notes", f"요청 메모는 주문당 {limits['max_notes']}개까지 남길 수 있습니다.")
        note = {"note_id": self._next_note, "text": text, "line_id": line_id}
        self._next_note += 1
        self.notes.append(note)
        return note

    def remove_note(self, note_id):
        note = next((n for n in self.notes if n["note_id"] == note_id), None)
        if not note:
            raise OrderError("note_not_found", f"요청 메모 {note_id}번이 없습니다. <실시간상태>의 메모 번호를 확인하세요.")
        self.notes.remove(note)
        return note

    # ------------------------------------------------------------ 이용 방식

    def set_options(self, dine_option=None, straw=None, carrier=None):
        notes = []
        if dine_option is not None:
            value = _DINE_WORDS.get(str(dine_option).strip().lower())
            if not value:
                raise OrderError("invalid_dine_option", "이용 방식은 '매장' 또는 '포장' 중에서 고릅니다.")
            self.dine_option = value
            # 기본 컵은 이용 방식을 따른다: 매장이면 매장컵, 포장이면 일회용컵. 개인컵은 그대로 둔다.
            swap = {"takeout": ("store", "disposable"), "dine_in": ("disposable", "store")}[value]
            for line in self.lines:
                if line["cup"] == swap[0]:
                    line["cup"] = swap[1]
            if value == "dine_in":
                self.straw = self.carrier = None
        if straw is not None or carrier is not None:
            if self.dine_option != "takeout":
                notes.append("빨대·캐리어는 포장 주문에서만 선택합니다. 먼저 포장 여부를 확인하세요.")
            else:
                self.straw = bool(straw) if straw is not None else self.straw
                self.carrier = bool(carrier) if carrier is not None else self.carrier
        return notes

    # ------------------------------------------------------------ 복사·되돌리기

    def snapshot(self):
        return {k: copy.deepcopy(getattr(self, k)) for k in _CART_FIELDS}

    def restore(self, snapshot):
        """장바구니를 snapshot 때로 되돌린다. 줄 번호는 다시 쓰지 않도록 번호표는 되감지 않는다."""
        for key, value in snapshot.items():
            setattr(self, key, copy.deepcopy(value))

    def clone(self):
        """모델이 올리는 변경안을 담을 복사본. 가격과 옵션 검증이 진짜 주문과 똑같이 돈다."""
        other = copy.copy(self)
        other.restore(self.snapshot())
        other.tenders = copy.deepcopy(self.tenders)
        other.scanned = set(self.scanned)
        return other

    def adopt(self, draft):
        """손님이 확인한 변경안을 받아들인다."""
        self.restore(draft.snapshot())
        self._next_line = max(self._next_line, draft._next_line)
        self._next_note = max(self._next_note, draft._next_note)

    # ------------------------------------------------------------ 금액·표시

    def _base(self, line):
        return next(s["price"] for s in self.catalog.by_id[line["menu_id"]]["sizes"] if s["size"] == line["size"])

    def _prices(self, line):
        """(한 잔 가격, 그중 제휴 할인 대상 금액). 샷추가는 기본 토핑이 아니라 할인 대상에서 빠진다."""
        cat = self.catalog
        base = self._base(line)
        extra = sum(cat.topping_price(t, line["size"]) for t in line["toppings"])
        basic = sum(cat.topping_price(t, line["size"]) for t in line["toppings"] if cat.toppings[t]["basic"])
        return base + extra, base + basic

    def _coupon_amount(self, remaining):
        if not self.coupon:
            return 0
        if self.coupon["benefit"] == "free_drink":  # 교환권으로 받지 않은 음료 중 가장 비싼 한 잔의 메뉴 가격
            return max((self._base(l) for l in self.lines if not l["voucher"]), default=0)
        return min(self.coupon["amount"], max(remaining, 0))

    def totals(self):
        count = sum(l["quantity"] for l in self.lines)
        subtotal = sum(self._prices(l)[0] * l["quantity"] for l in self.lines)
        voucher = sum(self._base(l) for l in self.lines if l["voucher"])  # 교환권은 메뉴 가격만 대신 낸다
        coupon = self._coupon_amount(subtotal - voucher)
        eligible = sum(self._prices(l)[1] * l["quantity"] for l in self.lines if not l["voucher"])
        discount = min(int(eligible * DISCOUNT_RATE), DISCOUNT_CAP) if self.discount else 0
        total = subtotal - voucher - coupon - discount
        paid = sum(t["amount"] for t in self.tenders)
        return {"count": count, "subtotal": subtotal, "voucher": voucher, "coupon": coupon, "discount": discount,
                "total": total, "paid": paid, "due": total - paid, "stamps": count if self.stamp else 0}

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
            "names": self.catalog.names(menu),
            "image": f"/{menu['image']}" if menu["image"] else None,
            "toppings": [{"id": t, "name": cat.toppings[t]["name"], "price": cat.topping_price(t, line["size"])} for t in line["toppings"]],
            "sugar_note": sugar_note,
            "ice_note": ice_note,
            "cup_name": cat.cups[line["cup"]]["name"],
            "unit_price": unit,
            "line_total": unit * line["quantity"],
            "covered": self._base(line) if line["voucher"] else 0,  # 교환권이 대신 내는 금액
            "sold_out": bool(self.stock and self.stock.is_sold_out(line["menu_id"])),
            "notes": [n for n in self.notes if n["line_id"] == line["line_id"]],
        }

    def option_parts(self, line):
        """한 줄의 옵션을 한국어 낱말로: 사이즈, 온도, 당도, 얼음, 토핑, 컵."""
        v = self.line_view(line)
        sugar = f"당도 {v['sugar']}%" if v["sugar"] is not None else {"fixed": "당도 고정", "default": "당도 기본"}[v["sugar_note"]]
        ice = (self.catalog.ice_levels[v["ice"]] if v["ice"] else
               {"hot": None, "fixed": "얼음 고정", "default": "얼음 기본"}[v["ice_note"]])
        parts = [v["size"], v["temperature"], sugar, ice,
                 "토핑: " + (", ".join(t["name"] for t in v["toppings"]) or "없음"), v["cup_name"]]
        return [p for p in parts if p]

    def line_text(self, line):
        v = self.line_view(line)
        text = (f"#{v['line_id']} {v['name']} | " + " | ".join(self.option_parts(line)) +
                f" | {v['quantity']}잔 | {won(v['line_total'])}")
        if v["voucher"]:
            text += f" | 교환권 사용(메뉴 가격 {won(v['covered'])} 처리)"
        if v["sold_out"]:
            text += " | 지금 품절(결제 전에 빼야 함)"
        for note in v["notes"]:
            text += f" | 요청 메모 {note['note_id']}번: {note['text']}"
        return text

    def amount_text(self):
        t = self.totals()
        text = f"주문 {won(t['subtotal'])}"
        for key, label in (("voucher", "교환권"), ("coupon", "쿠폰"), ("discount", "제휴 할인")):
            if t[key]:
                text += f" - {label} {won(t[key])}"
        text += f" = 결제 금액 {won(t['total'])}"
        if t["paid"]:
            text += f" (이미 낸 금액 {won(t['paid'])}, 남은 금액 {won(t['due'])})"
        return text

    def summary_text(self):
        """모델에게 매 턴 보여 주는 주문 상태. 금액은 모두 여기(POS)에서 계산한 값이다."""
        t = self.totals()
        lines = ["장바구니: " + (f"{len(self.lines)}줄, {t['count']}잔" if self.lines else "비어 있음")]
        lines += ["  " + self.line_text(l) for l in self.lines]
        for note in self.notes:
            if note["line_id"] is None:
                lines.append(f"  주문 전체 요청 메모 {note['note_id']}번: {note['text']}")
        dine = DINE_OPTIONS.get(self.dine_option, "아직 선택 안 함")
        if self.dine_option == "takeout":
            ask = {None: "선택 안 함", True: "필요", False: "필요 없음"}
            dine += f" (빨대: {ask[self.straw]}, 캐리어: {ask[self.carrier]})"
        lines.append(f"이용 방식: {dine}")
        if self.lines:
            lines.append(f"금액(POS 계산): {self.amount_text()}")
        benefits = [DISCOUNTS[self.discount]] if self.discount else []
        benefits += [f"쿠폰 '{self.coupon['label']}'"] if self.coupon else []
        benefits += [f"멤버십 스탬프 {t['stamps']}개 적립"] if self.stamp else []
        lines.append("할인·적립(손님이 결제 화면에서 바코드를 찍어야 적용된다): " + (", ".join(benefits) or "아직 없음"))
        if self.status == "paid":
            lines.append(f"결제 완료: 주문번호 {self.receipt['order_number']}")
        elif self.status == "paying":
            lines.append("결제 화면: 손님이 진행 중 (이 동안에는 주문을 바꿀 수 없다)")
            if self.failure:
                lines.append(f"  직전 결제 실패: {self.failure['reason_text']} ({self.failure['attempts']}번째)")
        else:
            lines.append("결제 화면: 아직 열지 않음")
        return "\n".join(lines)

    def to_dict(self):
        """화면에 그릴 주문 상태."""
        return {
            "status": self.status,
            "lines": [self.line_view(l) for l in self.lines],
            "notes": [n for n in self.notes if n["line_id"] is None],
            "totals": self.totals(),
            "dine_option": self.dine_option,
            "straw": self.straw,
            "carrier": self.carrier,
            "discount": self.discount,
            "stamp": bool(self.stamp),
            "coupon": self.coupon and {"label": self.coupon["label"], "benefit": self.coupon["benefit"]},
            "tenders": self.tenders,
            "failure": self.failure,
            "receipt": self.receipt,
        }


# ------------------------------------------------------------ 변경 내용 (변경 확인 화면)

def changes(before, after):
    """두 주문 상태의 차이. 변경 확인 화면에 그리고 모델에게도 글로 알려 준다."""
    result = []
    old = {l["line_id"]: l for l in before.lines}
    new = {l["line_id"]: l for l in after.lines}
    for line_id, line in new.items():
        if line_id not in old:
            result.append({"type": "add", "line_id": line_id, "after": after.line_view(line)})
        elif line != old[line_id]:
            result.append({"type": "update", "line_id": line_id, "fields": [f for f in LINE_FIELDS if line[f] != old[line_id][f]],
                           "before": before.line_view(old[line_id]), "after": after.line_view(line)})
    for line_id, line in old.items():
        if line_id not in new:
            result.append({"type": "remove", "line_id": line_id, "before": before.line_view(line)})

    def with_name(order, note):
        line = next((l for l in order.lines if l["line_id"] == note["line_id"]), None)
        return {**note, "line_name": order.catalog.by_id[line["menu_id"]]["name"] if line else None}

    old_notes = {n["note_id"]: n for n in before.notes}
    new_notes = {n["note_id"]: n for n in after.notes}
    # 새로 담는 줄에 붙은 메모는 그 줄과 함께 보이므로 따로 적지 않는다.
    result += [{"type": "note_add", "note": with_name(after, n)} for i, n in new_notes.items()
               if i not in old_notes and (n["line_id"] is None or n["line_id"] in old)]
    # 줄을 빼서 함께 사라지는 메모는 따로 적지 않는다.
    result += [{"type": "note_remove", "note": with_name(before, n)} for i, n in old_notes.items()
               if i not in new_notes and (n["line_id"] is None or n["line_id"] in new)]
    if before.dine_option != after.dine_option:
        result.append({"type": "dine", "before": before.dine_option, "after": after.dine_option})
    for key in ("straw", "carrier"):
        if getattr(before, key) != getattr(after, key):
            result.append({"type": "option", "name": key, "before": getattr(before, key), "after": getattr(after, key)})
    return result


def change_text(before, after, change):
    """변경 한 건을 모델이 읽을 한 줄로."""
    kind = change["type"]
    if kind == "add":
        return "추가: " + after.line_text(after._find(change["line_id"]))
    if kind == "remove":
        return "삭제: " + before.line_text(before._find(change["line_id"]))
    if kind == "update":
        return ("변경: " + before.line_text(before._find(change["line_id"])) +
                "\n      → " + after.line_text(after._find(change["line_id"])))
    if kind in ("note_add", "note_remove"):
        note = change["note"]
        where = f"{note['line_name']}(#{note['line_id']})" if note["line_id"] else "주문 전체"
        return f"요청 메모 {'추가' if kind == 'note_add' else '삭제'} ({where}): {note['text']}"
    if kind == "dine":
        return f"이용 방식: {DINE_OPTIONS.get(change['before'], '선택 안 함')} → {DINE_OPTIONS.get(change['after'], '선택 안 함')}"
    label = {"straw": "빨대", "carrier": "캐리어"}[change["name"]]
    word = {None: "선택 안 함", True: "필요", False: "필요 없음"}
    return f"{label}: {word[change['before']]} → {word[change['after']]}"
