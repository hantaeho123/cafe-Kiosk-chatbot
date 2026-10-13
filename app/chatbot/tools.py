"""모델이 호출하는 도구(Function Calling). 정의(스키마)와 실제 실행을 함께 둔다.

주문을 바꾸는 도구는 진짜 주문이 아니라 '변경안'(주문의 복사본)만 고친다. 변경안은 변경 확인 화면에 뜨고,
손님이 [확인]을 눌러야 주문에 반영된다. 결제·할인·적립을 처리하는 도구는 없다. 그것은 손님이 화면과 단말기로 직접 한다.
"""
import inspect

from app.catalog import DRINK_TABS
from app.i18n import LANGUAGES
from app.order import Order, OrderError, won

SEARCH_TOOL = "search_knowledge"
# 주문(변경안)을 고치는 도구. 결제 화면이 열려 있으면 쓸 수 없다.
ORDER_TOOLS = {"add_to_cart", "update_cart_item", "remove_cart_item", "add_request_note", "remove_request_note",
               "set_order_options", "undo_last_change"}
NEXT_STEP = ("아직 주문에 반영되지 않았습니다. 변경 확인 화면을 띄웠으니, 바뀌는 내용과 금액을 한두 문장으로 알리고 "
             "화면에서 [확인]을 눌러 달라고 안내하세요. '담았습니다', '바꿨습니다'라고 말하지 않습니다.")


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
            "size": {"type": "string", "enum": ["L", "J"], "description": "사이즈. 손님이 말하지 않았으면 넣지 않는다."},
            "temperature": {"type": "string", "enum": ["ICE", "HOT"], "description": "손님이 말하지 않았으면 넣지 않는다."},
            "sugar": {"type": "integer", "enum": cat.sugar_levels, "description": "당도(%). 말하지 않았으면 넣지 않는다(추천 당도로 들어간다)."},
            "ice": {"type": "string", "enum": list(cat.ice_levels),
                    "description": "얼음량: " + ", ".join(f"{k}={v}" for k, v in cat.ice_levels.items())
                                   + ". 말하지 않았으면 넣지 않는다. '얼음 없이'는 고를 수 없는 값이다."},
            "toppings": {"type": "array", "items": {"type": "string", "enum": list(cat.toppings)},
                         "description": f"추가할 토핑 전체 목록(샷추가를 빼고 최대 {cat.max_toppings}가지). {toppings}"},
            "quantity": {"type": "integer", "minimum": 1, "description": "수량(잔)."},
            "cup": {"type": "string", "enum": list(cat.cups),
                    "description": "컵: " + ", ".join(f"{k}={c['name']}" for k, c in cat.cups.items())
                                   + ". 말하지 않았으면 넣지 않는다(매장이면 매장컵, 포장이면 일회용컵)."},
        }
        return [
            _fn(SEARCH_TOOL,
                "공차 지식 문서를 검색한다: 메뉴 설명·가격·영양정보·알레르기, 추천 목록, 옵션·토핑의 뜻, 매장, 멤버십·제휴, "
                "키오스크 사용법·결제 방법·기기 위치·요청 사항 처리 방침·직원 호출 방법. "
                "<참고문서>에 답이 없거나 부족할 때, 복합 질문의 하위 질문마다 호출한다. 검색어에는 한 가지 주제만 넣는다.",
                {"query": {"type": "string", "description": "검색어. 예: '카페인 없는 음료', '타로 밀크티 칼로리', '카드 투입구 위치'"},
                 "doc_type": {"type": "string",
                              "enum": ["menu", "recommendation", "option", "category", "policy", "guide", "store", "knowledge"],
                              "description": "특정 종류의 문서만 찾고 싶을 때. guide=사용법·결제·요청·직원 호출 안내. 보통은 넣지 않는다."}},
                ["query"]),
            _fn("find_menus",
                "조건에 맞는, 지금 주문할 수 있는(품절이 아닌) 음료를 메뉴판 데이터에서 정확하게 골라 준다. 조건이 두 개 이상이거나 "
                "가격·칼로리·카페인·예산처럼 수치로 따지는 추천에는 반드시 이 도구를 쓴다. 필요한 조건만 넣는다.",
                {"flavor": {"type": "string", "enum": flavors, "description": "맛 특징"},
                 "caffeine": {"type": "string", "enum": ["none", "low"], "description": "none=카페인 0mg, low=50mg 미만"},
                 "no_milk": {"type": "boolean", "description": "우유가 들어가지 않은 음료만"},
                 "exclude_allergens": {"type": "array", "items": {"type": "string", "enum": cat.allergens},
                                       "description": "알레르기 유발물질 표기에 이 성분이 있는 음료를 뺀다"},
                 "sugar_adjustable": {"type": "boolean", "description": "당도를 낮춰 주문할 수 있는 음료만 ('덜 단 거')"},
                 "low_sugar": {"type": "boolean", "description": "저당·제로 시리즈 메뉴만"},
                 "hot": {"type": "boolean", "description": "따뜻하게(HOT) 주문할 수 있는 음료만"},
                 "category": {"type": "string", "enum": DRINK_TABS},
                 "max_price": {"type": "integer", "description": "가장 작은 사이즈 가격이 이 금액(원) 이하"},
                 "total_budget": {"type": "integer",
                                  "description": "손님이 말한 전체 예산(원)을 그대로 넣는다. 인원으로 나누지 않는다(나누기는 이 도구가 한다). "
                                                 "예: '2만 원으로 셋이' → total_budget=20000, people=3"},
                 "people": {"type": "integer", "minimum": 1, "description": "인원 수(= 음료 잔 수). total_budget 과 함께 넣는다"},
                 "max_kcal": {"type": "integer", "description": "열량이 이 값(kcal) 이하"},
                 "sort": {"type": "string", "enum": ["popular", "price", "kcal", "caffeine"],
                          "description": "정렬: 인기순(기본), 가격·열량·카페인 낮은 순"}}),
            _fn("compare_menus",
                "메뉴 2~3개를 나란히 비교할 값(맛, 카페인, 열량, 당류, 가격, HOT 가능 여부, 알레르기)을 가져온다. "
                "손님이 메뉴끼리 차이를 물으면 호출한다. 화면에도 비교표가 뜬다.",
                {"menu_names": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3,
                                "description": "메뉴판의 공식 메뉴명"}},
                ["menu_names"]),
            _fn("menu_options",
                "한 메뉴에서 고를 수 있는 옵션(사이즈별 가격·용량, HOT/ICE, 당도, 얼음, 토핑과 추가 요금, 컵)과 고정된 옵션, 품절 여부를 알려 준다. "
                "손님이 '이 메뉴에 ○○ 돼요?'처럼 옵션이 되는지 물으면 호출해 확인한다.",
                {"menu_name": {"type": "string", "description": "메뉴판의 공식 메뉴명"}}, ["menu_name"]),
            _fn("calculate_price",
                "메뉴 조합의 금액을 POS 가 계산한다(주문은 바뀌지 않는다). 여러 잔의 합계, 예산 안에 드는지, 토핑을 넣은 가격처럼 "
                "더하거나 곱해야 하는 금액은 직접 계산하지 말고 반드시 이 도구로 구한다.",
                {"items": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
                    "menu_name": {"type": "string", "description": "메뉴판의 공식 메뉴명"},
                    "size": {"type": "string", "enum": ["L", "J"], "description": "넣지 않으면 가장 작은 사이즈"},
                    "toppings": item_options["toppings"],
                    "quantity": {"type": "integer", "minimum": 1}}, "required": ["menu_name"]}},
                 "budget": {"type": "integer", "description": "예산(원). 넣으면 예산 안에 드는지와 남는 금액을 알려 준다"}},
                ["items"]),
            _fn("add_to_cart",
                "메뉴를 담는 변경안을 변경 확인 화면에 올린다(손님이 [확인]을 눌러야 담긴다). 사이즈·HOT/ICE 가 빠졌고 선택지가 여러 개면 "
                "올리지 않고 need 로 알려 준다. 여러 메뉴는 메뉴마다 한 번씩 호출한다.",
                {"menu_name": {"type": "string", "description": "메뉴판의 공식 메뉴명. 예: '블랙 밀크티 + 펄'"}, **item_options,
                 "request_note": {"type": "string", "description": "이 메뉴에 붙일 요청 메모(옵션으로 고를 수 없는 요청만, 60자 이내)"}},
                ["menu_name"]),
            _fn("update_cart_item",
                "주문의 한 줄을 고치는 변경안을 올린다(수량, 사이즈, HOT/ICE, 당도, 얼음, 토핑, 컵). 바꿀 항목만 넣는다. "
                "toppings 는 바꾼 뒤의 전체 목록을 넣는다(모두 빼려면 빈 배열).",
                {"line_id": {"type": "integer", "description": "<실시간상태>의 #번호"}, **item_options},
                ["line_id"]),
            _fn("remove_cart_item", "주문에서 한 줄을 빼거나 전부 비우는 변경안을 올린다.",
                {"line_id": {"type": "integer", "description": "뺄 줄의 #번호"},
                 "clear_all": {"type": "boolean", "description": "주문을 전부 비우려면 true"}}),
            _fn("add_request_note",
                "화면에서 고를 수 없는 요청을 짧은 메모로 정리해 주문에 붙이는 변경안을 올린다(예: '얼음 빼 주세요', '두 잔 중 하나는 포장'). "
                "메모는 음료를 만드는 직원에게 전달될 뿐이며 들어준다고 약속하지 않는다. 옵션으로 고를 수 있는 것은 메모로 남기지 않는다.",
                {"note": {"type": "string", "description": "손님 말투 그대로가 아니라 직원이 읽기 쉽게 정리한 메모(60자 이내, 한국어)"},
                 "line_id": {"type": "integer", "description": "특정 메뉴에 대한 요청이면 그 줄의 #번호. 주문 전체에 대한 요청이면 넣지 않는다"}},
                ["note"]),
            _fn("remove_request_note", "붙여 둔 요청 메모를 빼는 변경안을 올린다.",
                {"note_id": {"type": "integer", "description": "<실시간상태>의 메모 번호"}}, ["note_id"]),
            _fn("set_order_options", "매장/포장(과 포장일 때 빨대·캐리어)을 바꾸는 변경안을 올린다. 손님이 정한 항목만 넣는다.",
                {"dine_option": {"type": "string", "enum": ["매장", "포장"]},
                 "straw": {"type": "boolean", "description": "포장일 때 빨대 등 일회용품이 필요한지"},
                 "carrier": {"type": "boolean", "description": "포장일 때 캐리어가 필요한지"}}),
            _fn("undo_last_change",
                "손님이 '아니 그거 말고', '방금 거 취소'처럼 직전 변경을 무르려 할 때 호출한다. 확인을 기다리는 변경안이 있으면 그것을 버리고, "
                "없으면 직전에 반영된 변경을 되돌리는 변경안을 올린다. 그다음 손님이 원한 것을 새로 올린다.", {}),
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
        except TypeError as e:  # find_menus 처럼 조건을 그대로 넘기는 도구에 모르는 조건이 왔을 때
            return {"ok": False, "error": "bad_arguments", "message": str(e)}

    def _sold_out(self, session, menu):
        return bool(session.stock and session.stock.is_sold_out(menu["id"]))

    def _find_menu(self, menu_name):
        menu, candidates = self.catalog.resolve(menu_name)
        if menu:
            return menu
        if candidates:
            raise OrderError("ambiguous_menu", f"'{menu_name}'에 해당하는 메뉴가 여러 개입니다. 손님에게 어느 것인지 물어보세요.",
                             candidates=[{"name": m["name"], "orderable": self.catalog.orderable(m),
                                          "prices": {s["size"]: s["price"] for s in m["sizes"]}} for m in candidates])
        raise OrderError("menu_not_found", f"'{menu_name}'은(는) 메뉴판에 없습니다. search_knowledge 로 비슷한 메뉴를 찾아 제안하세요.")

    # ---- 알아보기

    def _search_knowledge(self, session, query, doc_type=None):
        hits = self.retriever.search(query, doc_types=[doc_type] if doc_type else None)
        sold_out = set(session.stock.ids()) if session.stock else set()
        return {"ok": True, "hits": hits,  # hits 는 에이전트가 화면 표시용으로 꺼내 쓰고 모델에게는 results 만 보낸다
                "results": [{"title": h.doc["title"], "type": h.doc["type"], "text": h.doc["text"],
                             **({"sold_out": True} if h.doc["metadata"].get("menu_id") in sold_out else {})} for h in hits]}

    def _find_menus(self, session, limit=8, total_budget=None, people=None, **conditions):
        result = {"ok": True}
        if total_budget is not None:
            people = max(int(people or 1), 1)
            share = int(total_budget) // people  # 나누기도 모델이 아니라 코드가 한다
            conditions["max_price"] = min(share, conditions.get("max_price") or share)
            result["budget"] = {"total": won(int(total_budget)), "people": people, "per_person": won(share),
                                "note": "한 사람 몫 안에서 고른 것입니다. 실제 조합의 합계는 calculate_price 로 확인하세요."}
        sold_out = session.stock.ids() if session.stock else []
        found = self.catalog.find_drinks(exclude_ids=sold_out, **conditions)
        if not found:
            return {**result, "total": 0, "message": "조건을 모두 만족하는 음료가 없습니다. 손님에게 알리고 조건을 하나 줄여 다시 찾아보세요."}
        return {**result, "total": len(found), "basis": "카페인·열량은 가장 작은 ICE 사이즈, 당도 0% 기준. 품절 메뉴는 빠져 있음",
                "menus": [{
                    "name": m["name"], "category": m["category"],
                    "price": ", ".join(f"{s['size']} {s['price']:,}원" for s in m["sizes"]),
                    "flavors": m["tags"]["flavors"], "caffeine_mg": m["tags"]["caffeine_mg_ref"], "kcal": m["tags"]["kcal_ref"],
                    "hot_available": m["tags"]["hot_available"], "contains_milk": m["tags"]["contains_milk"],
                    "sugar_adjustable": m["options"]["sugar"]["adjustable"], "allergens": m["allergens"],
                    "badges": self.catalog.card(m)["badges"],
                } for m in found[:limit]]}

    def _compare_menus(self, session, menu_names):
        menus = list({m["id"]: m for m in map(self._find_menu, menu_names)}.values())
        if len(menus) < 2:
            raise OrderError("bad_arguments", "서로 다른 메뉴가 두 개 이상 필요합니다.")
        facts = [self.catalog.facts(m, self._sold_out(session, m)) for m in menus]
        cards = [self.catalog.card(m) for m in menus]
        view = [{**f, "id": m["id"], "image": c["image"], "prices": c["prices"],
                 "sugar": m["options"]["sugar"] if m["kind"] == "drink" else {"adjustable": None, "recommended": None}}
                for f, m, c in zip(facts, menus, cards)]
        return {"ok": True, "menus": facts, "view": {"items": view},
                "note": "이 값만 근거로 차이를 설명하세요. 수치는 가장 작은 ICE 사이즈, 당도 0% 기준입니다."}

    def _menu_options(self, session, menu_name):
        cat, menu = self.catalog, self._find_menu(menu_name)
        if not cat.orderable(menu):
            return {"ok": True, "menu": menu["name"], "orderable": False,
                    "message": "가격 정보가 없어 키오스크에서 주문할 수 없는 메뉴입니다. 직원에게 문의하도록 안내하세요."}
        sugar, ice = menu["options"]["sugar"], menu["options"]["ice"]
        adjustable = {True: "조절 가능", False: "고정(바꿀 수 없음)", None: "조절 가능 여부가 확인되지 않음(요청은 받되 매장 확인 필요)"}
        return {
            "ok": True, "menu": menu["name"], "orderable": True, "sold_out": self._sold_out(session, menu),
            "sizes": [{"size": s["size"], "price": won(s["price"]), "volume_ml": s.get("volume_ml")} for s in menu["sizes"]],
            "temperatures": menu["temperatures"],
            "sugar": {"status": adjustable[sugar["adjustable"]], "levels": cat.sugar_levels if sugar["adjustable"] is not False else [],
                      "recommended": sugar["recommended"]},
            "ice": {"status": adjustable[ice["adjustable"]], "levels": list(cat.ice_levels.values()) if ice["adjustable"] is not False else [],
                    "not_selectable": "얼음 없이 (요청 메모로만 전달 가능, 약속할 수 없음)", "hot": "HOT 에는 얼음 옵션이 없음"},
            "toppings": {"max": f"샷추가를 빼고 {cat.max_toppings}가지", "included": menu["included_toppings"],
                         "choices": [{"name": t["name"], "price": won(t["price"])} for t in cat.toppings.values()]},
            "cups": [c["name"] for c in cat.cups.values()],
            "note": "여기에 없는 옵션(시럽 추가, 샷 빼기 등)은 고를 수 없습니다. 가까운 대안을 제시하고, 원하면 요청 메모로 남길 수 있다고 안내하세요.",
        }

    def _calculate_price(self, session, items, budget=None):
        scratch = Order(self.catalog, session.stock)  # 계산만 하고 버리는 주문
        assumed = []
        for item in items:
            if not isinstance(item, dict) or "menu_name" not in item:
                raise OrderError("bad_arguments", "items 의 각 항목에는 menu_name 이 필요합니다.")
            menu = self._find_menu(item["menu_name"])
            size = item.get("size")
            if not size and menu["sizes"]:
                size = min(menu["sizes"], key=lambda s: s["price"])["size"]
                if len(menu["sizes"]) > 1:
                    assumed.append(f"{menu['name']}: 사이즈를 말하지 않아 가장 작은 {size} 로 계산")
            scratch.add(menu, size, (menu.get("temperatures") or [None])[0], None, None, item.get("toppings"), item.get("quantity", 1))
        total = scratch.totals()["subtotal"]

        def priced(line):
            v = scratch.line_view(line)
            extra = " + " + ", ".join(t["name"] for t in v["toppings"]) if v["toppings"] else ""
            return f"{v['name']} {v['size']}{extra}: {won(v['unit_price'])} × {v['quantity']}잔 = {won(v['line_total'])}"

        result = {"ok": True, "lines": [priced(l) for l in scratch.lines], "total": won(total), "assumed": assumed,
                  "basis": "할인·쿠폰을 적용하기 전 금액. 주문은 바뀌지 않았습니다."}
        if budget is not None:
            budget = int(budget)
            result["budget"] = {"budget": won(budget), "within_budget": total <= budget,
                                ("left" if total <= budget else "over"): won(abs(budget - total))}
        return result

    # ---- 주문 바꾸기 (변경안)

    def _proposed(self, session, **result):
        text = session.pending_text()
        if text is None:
            return {"ok": True, "applied": False, "no_change": True, "message": "지금 주문과 달라지는 것이 없어 올린 변경안이 없습니다."}
        return {"ok": True, "applied": False, **result, "waiting_for_confirmation": text, "next": NEXT_STEP}

    def _doubled(self, line):
        """'블랙 밀크티 + 펄'에 펄을 또 추가한 것처럼, 메뉴에 이미 든 토핑을 한 번 더 넣었을 때의 안내."""
        included = self.catalog.by_id[line["menu_id"]]["included_toppings"]
        doubled = [self.catalog.toppings[t]["name"] for t in line["toppings"] if self.catalog.toppings[t]["name"] in included]
        if not doubled:
            return []
        return [f"이 메뉴에는 {', '.join(doubled)}이(가) 이미 들어 있는데 같은 토핑을 한 번 더 추가했습니다(추가 요금이 붙음). "
                "손님이 '더 추가'를 원한 것이 아니면 update_cart_item 으로 그 토핑을 빼세요."]

    def _add_to_cart(self, session, menu_name, size=None, temperature=None, sugar=None, ice=None,
                     toppings=None, quantity=1, cup=None, request_note=None):
        menu = self._find_menu(menu_name)
        draft = session.propose()
        try:
            line, defaults, notes = draft.add(menu, size, temperature, sugar, ice, toppings, quantity, cup)
            if request_note:
                draft.add_note(request_note, line["line_id"])
        except OrderError:
            session.pending()  # 아무것도 바뀌지 않은 빈 변경안은 치운다
            raise
        return self._proposed(session, proposed=draft.line_text(line), defaults_applied=defaults, notes=notes + self._doubled(line))

    def _update_cart_item(self, session, line_id, **changes):
        draft = session.propose()
        try:
            line, defaults, notes = draft.update(line_id, **changes)
        except OrderError:
            session.pending()
            raise
        return self._proposed(session, proposed=draft.line_text(line), defaults_applied=defaults, notes=notes + self._doubled(line))

    def _remove_cart_item(self, session, line_id=None, clear_all=False):
        draft = session.propose()
        if clear_all:
            draft.clear()
        elif line_id is None:
            session.pending()
            raise OrderError("bad_arguments", "뺄 줄의 #번호(line_id)나 clear_all 중 하나가 필요합니다.")
        else:
            try:
                draft.remove(line_id)
            except OrderError:
                session.pending()
                raise
        return self._proposed(session)

    def _add_request_note(self, session, note, line_id=None):
        draft = session.propose()
        try:
            draft.add_note(note, line_id)
        except OrderError:
            session.pending()
            raise
        return self._proposed(session, promise="메모는 직원에게 전달될 뿐입니다. 매장에서 들어줄 수 있는지는 약속하지 말고 '전달해 드릴게요'라고만 하세요.")

    def _remove_request_note(self, session, note_id):
        draft = session.propose()
        try:
            draft.remove_note(note_id)
        except OrderError:
            session.pending()
            raise
        return self._proposed(session)

    def _set_order_options(self, session, dine_option=None, straw=None, carrier=None):
        draft = session.propose()
        try:
            notes = draft.set_options(dine_option, straw, carrier)
        except OrderError:
            session.pending()
            raise
        return self._proposed(session, notes=notes)

    def _undo_last_change(self, session):
        if session.pending() and not session.draft_undo:
            session.discard()
            return {"ok": True, "applied": False, "discarded": True,
                    "message": "확인을 기다리던 변경안을 버렸습니다. 주문은 그대로입니다. 손님이 원한 것을 새로 올리거나 무엇을 원하는지 물어보세요."}
        session.propose_undo()
        return self._proposed(session, undo=True)

    # ---- 그 밖

    def _call_staff(self, session, reason=""):
        session.call_staff(reason, "chat")
        return {"ok": True, "message": "직원 화면에 호출을 띄웠습니다. 직원이 올 때까지 잠시 기다려 달라고 안내하세요."}

    def _set_language(self, session, language):
        if language not in LANGUAGES:
            raise OrderError("invalid_language", "지원 언어: " + ", ".join(LANGUAGES))
        session.set_language(language)
        return {"ok": True, "language": LANGUAGES[language]}
