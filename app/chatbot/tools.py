"""모델이 호출하는 도구(Function Calling). 정의(스키마)와 실제 실행을 함께 둔다."""
import inspect
from datetime import datetime

from app.catalog import DRINK_TABS
from app.i18n import LANGUAGES
from app.order import DISCOUNTS, OrderError

SEARCH_TOOL = "search_knowledge"


def _fn(name, description, properties, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required)},
    }}


class ToolBox:
    def __init__(self, catalog, retriever):
        self.catalog = catalog
        self.retriever = retriever

    # ------------------------------------------------------------ 정의

    def specs(self):
        cat = self.catalog
        toppings = ", ".join(f"{tid}={t['name']}" for tid, t in cat.toppings.items())
        flavors = sorted({f for m in cat.items if m["kind"] == "drink" for f in m["tags"]["flavors"]})
        item_options = {
            "size": {"type": "string", "enum": ["L", "J", "G"], "description": "사이즈. 손님이 말하지 않았으면 넣지 않는다."},
            "temperature": {"type": "string", "enum": ["ICE", "HOT"], "description": "손님이 말하지 않았으면 넣지 않는다."},
            "sugar": {"type": "integer", "enum": cat.sugar_levels, "description": "당도(%). 말하지 않았으면 넣지 않는다(추천 당도로 담김)."},
            "ice": {"type": "string", "enum": list(cat.ice_levels),
                    "description": "얼음량: " + ", ".join(f"{k}={v}" for k, v in cat.ice_levels.items()) + ". 말하지 않았으면 넣지 않는다."},
            "toppings": {"type": "array", "items": {"type": "string", "enum": list(cat.toppings)},
                         "description": f"추가할 토핑 전체 목록(최대 {cat.max_toppings}가지). {toppings}"},
            "quantity": {"type": "integer", "minimum": 1, "description": "수량(잔)."},
            "tumbler": {"type": "boolean", "description": "개인 텀블러에 담아 달라고 하면 true."},
        }
        return [
            _fn(SEARCH_TOOL,
                "공차 지식 문서를 검색한다: 메뉴 설명·가격·영양정보·알레르기, 추천 목록, 옵션·토핑, 매장, 멤버십·제휴. "
                "<참고문서>에 답이 없거나 부족할 때, 복합 질문의 하위 질문마다 호출한다. 검색어에는 한 가지 주제만 넣는다.",
                {"query": {"type": "string", "description": "검색어. 예: '카페인 없는 음료', '타로 밀크티 칼로리', '토핑 가격'"},
                 "doc_type": {"type": "string",
                              "enum": ["menu", "recommendation", "option", "category", "policy", "guide", "store", "knowledge"],
                              "description": "특정 종류의 문서만 찾고 싶을 때. 보통은 넣지 않는다."}},
                ["query"]),
            _fn("find_menus",
                "조건에 맞는 판매 중인 음료를 메뉴판 데이터에서 정확하게 골라 준다. 조건이 두 개 이상이거나 "
                "가격·칼로리·카페인처럼 수치로 따지는 추천에는 반드시 이 도구를 쓴다. 필요한 조건만 넣는다.",
                {"flavor": {"type": "string", "enum": flavors, "description": "맛 특징"},
                 "caffeine": {"type": "string", "enum": ["none", "low"], "description": "none=카페인 0mg, low=50mg 미만"},
                 "no_milk": {"type": "boolean", "description": "우유가 들어가지 않은 음료만"},
                 "hot": {"type": "boolean", "description": "따뜻하게(HOT) 주문할 수 있는 음료만"},
                 "category": {"type": "string", "enum": DRINK_TABS},
                 "max_price": {"type": "integer", "description": "가장 작은 사이즈 가격이 이 금액(원) 이하"},
                 "max_kcal": {"type": "integer", "description": "열량이 이 값(kcal) 이하"},
                 "low_sugar": {"type": "boolean", "description": "저당·제로 메뉴만"},
                 "sort": {"type": "string", "enum": ["popular", "price", "kcal", "caffeine"],
                          "description": "정렬: 인기순(기본), 가격·열량·카페인 낮은 순"}}),
            _fn("add_to_cart",
                "메뉴를 장바구니에 담는다. 사이즈·HOT/ICE 가 빠졌고 선택지가 여러 개면 담지 않고 need 로 알려 준다.",
                {"menu_name": {"type": "string", "description": "메뉴판의 공식 메뉴명. 예: '블랙 밀크티 + 펄'"}, **item_options},
                ["menu_name"]),
            _fn("update_cart_item",
                "장바구니의 한 줄을 고친다(수량, 사이즈, HOT/ICE, 당도, 얼음, 토핑, 텀블러). 바꿀 항목만 넣는다. "
                "toppings 는 바꾼 뒤의 전체 목록을 넣는다(모두 빼려면 빈 배열).",
                {"line_id": {"type": "integer", "description": "<주문상태>의 #번호"}, **item_options},
                ["line_id"]),
            _fn("remove_cart_item", "장바구니에서 한 줄을 빼거나 전부 비운다.",
                {"line_id": {"type": "integer", "description": "뺄 줄의 #번호"},
                 "clear_all": {"type": "boolean", "description": "장바구니를 전부 비우려면 true"}}),
            _fn("set_order_options", "주문 전체에 대한 선택을 기록한다. 손님이 정한 항목만 넣는다.",
                {"dine_option": {"type": "string", "enum": ["매장", "포장"]},
                 "straw": {"type": "boolean", "description": "포장일 때 빨대 등 일회용품이 필요한지"},
                 "carrier": {"type": "boolean", "description": "포장일 때 캐리어가 필요한지"},
                 "discount": {"type": "string", "enum": [*DISCOUNTS, "none"], "description": "통신사 제휴 할인. 취소하려면 none"},
                 "stamp": {"type": "boolean", "description": "공차 멤버십 스탬프를 적립할지"}}),
            _fn("checkout",
                "결제를 진행하고 주문번호를 받는다. 손님이 주문 내용을 확인하고 결제 수단과 영수증 여부까지 정한 뒤에만 호출한다.",
                {"payment_method": {"type": "string", "enum": list(cat.payment_methods),
                                    "description": ", ".join(f"{k}={v}" for k, v in cat.payment_methods.items())},
                 "print_receipt": {"type": "boolean", "description": "영수증을 출력할지"}},
                ["payment_method", "print_receipt"]),
            _fn("call_staff", "직원을 호출한다. 손님이 직원을 찾거나 키오스크로 해결할 수 없는 일일 때.",
                {"reason": {"type": "string", "description": "직원에게 전달할 호출 사유(한국어, 한 문장)"}}, ["reason"]),
            _fn("set_language", "화면과 대화 언어를 바꾼다.",
                {"language": {"type": "string", "enum": list(LANGUAGES)}}, ["language"]),
        ]

    # ------------------------------------------------------------ 실행

    def run(self, name, args, session):
        """도구를 실행하고 모델에게 돌려줄 결과(dict)를 만든다."""
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return {"ok": False, "error": "unknown_tool", "message": f"'{name}' 도구는 없습니다."}
        try:
            inspect.signature(handler).bind(session, **args)
        except TypeError as e:  # 모델이 정의에 없는 인자를 보냈거나 필수 인자를 빠뜨렸을 때
            return {"ok": False, "error": "bad_arguments", "message": str(e)}
        try:
            return handler(session, **args)
        except OrderError as e:
            return e.result

    def _search_knowledge(self, session, query, doc_type=None):
        hits = self.retriever.search(query, doc_types=[doc_type] if doc_type else None)
        return {"ok": True, "hits": hits,  # hits 는 에이전트가 화면 표시용으로 꺼내 쓰고 모델에게는 results 만 보낸다
                "results": [{"title": h.doc["title"], "type": h.doc["type"], "text": h.doc["text"]} for h in hits]}

    def _find_menus(self, session, limit=8, **conditions):
        found = self.catalog.find_drinks(**conditions)
        if not found:
            return {"ok": True, "total": 0, "message": "조건을 모두 만족하는 음료가 없습니다. 손님에게 알리고 조건을 하나 줄여 다시 찾아보세요."}
        return {"ok": True, "total": len(found), "basis": "카페인·열량은 가장 작은 ICE 사이즈, 당도 0% 기준",
                "menus": [{
                    "name": m["name"], "category": m["category"],
                    "price": ", ".join(f"{s['size']} {s['price']:,}원" for s in m["sizes"]),
                    "flavors": m["tags"]["flavors"], "caffeine_mg": m["tags"]["caffeine_mg_ref"], "kcal": m["tags"]["kcal_ref"],
                    "hot_available": m["tags"]["hot_available"], "contains_milk": m["tags"]["contains_milk"],
                    "badges": self.catalog.card(m)["badges"],
                } for m in found[:limit]]}

    def _find_menu(self, menu_name):
        menu, candidates = self.catalog.resolve(menu_name)
        if menu:
            return menu
        if candidates:
            raise OrderError("ambiguous_menu", f"'{menu_name}'에 해당하는 메뉴가 여러 개입니다. 손님에게 어느 것인지 물어보세요.",
                             candidates=[{"name": m["name"], "orderable": self.catalog.orderable(m),
                                          "prices": {s["size"]: s["price"] for s in m["sizes"]}} for m in candidates])
        raise OrderError("menu_not_found", f"'{menu_name}'은(는) 메뉴판에 없습니다. search_knowledge 로 비슷한 메뉴를 찾아 제안하세요.")

    def _add_to_cart(self, session, menu_name, size=None, temperature=None, sugar=None, ice=None,
                     toppings=None, quantity=1, tumbler=False):
        order = session.order
        menu = self._find_menu(menu_name)
        line, defaults, notes = order.add(menu, size, temperature, sugar, ice, toppings, quantity, tumbler)
        return {"ok": True, "added": order.line_text(line), "defaults_applied": defaults, "notes": notes,
                "order": order.summary_text()}

    def _update_cart_item(self, session, line_id, **changes):
        order = session.order
        line, defaults, notes = order.update(line_id, **changes)
        return {"ok": True, "updated": order.line_text(line), "defaults_applied": defaults, "notes": notes,
                "order": order.summary_text()}

    def _remove_cart_item(self, session, line_id=None, clear_all=False):
        order = session.order
        if clear_all:
            order.clear()
            return {"ok": True, "removed": "전체", "order": order.summary_text()}
        if line_id is None:
            raise OrderError("bad_arguments", "뺄 줄의 #번호(line_id)나 clear_all 중 하나가 필요합니다.")
        line_text = order.line_text(order.remove(line_id))
        return {"ok": True, "removed": line_text, "order": order.summary_text()}

    def _set_order_options(self, session, dine_option=None, straw=None, carrier=None, discount=None, stamp=None):
        order = session.order
        notes = order.set_options(dine_option, straw, carrier, discount, stamp)
        return {"ok": True, "notes": notes, "order": order.summary_text()}

    def _checkout(self, session, payment_method=None, print_receipt=False):
        order = session.order
        receipt = order.checkout(payment_method, print_receipt)
        totals = order.totals()
        return {"ok": True, "order_number": receipt["order_number"], "paid": totals["total"],
                "payment_method": receipt["payment_method"]["name"], "print_receipt": receipt["print_receipt"],
                "stamps": totals["stamps"], "order": order.summary_text()}

    def _call_staff(self, session, reason=""):
        session.staff_call = {"reason": reason, "time": datetime.now().strftime("%H:%M")}
        return {"ok": True, "message": "직원을 호출했습니다. 곧 직원이 올 것이라고 안내하세요."}

    def _set_language(self, session, language):
        if language not in LANGUAGES:
            raise OrderError("invalid_language", "지원 언어: " + ", ".join(LANGUAGES))
        session.set_language(language)
        return {"ok": True, "language": LANGUAGES[language]}
