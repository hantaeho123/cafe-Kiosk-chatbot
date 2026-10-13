"""메뉴판(data/kiosk)을 읽어 이름으로 메뉴를 찾고, 화면에 보여 줄 카드 데이터를 만든다."""
import json
import re
from difflib import SequenceMatcher

from app.config import KIOSK_DIR

SIZE_NAMES = {"L": "라지", "J": "점보", "G": "기가"}
# 손님이 자주 쓰는 다른 표기를 메뉴판 표기로 맞춘다.
SPELLINGS = [("초콜릿", "초콜렛"), ("초코", "초콜렛"), ("쥬스", "주스"), ("라테", "라떼"), ("쟈스민", "자스민")]
# '아이스 아메리카노' 처럼 메뉴명 앞에 붙여 말하는 온도 표현. 메뉴명이 아니므로 떼고 찾는다.
TEMPERATURE_PREFIX = re.compile(r"^\s*(아이스|핫|따뜻한|뜨거운|차가운|시원한|iced?|hot)\s+", re.IGNORECASE)
DRINK_TABS = ["밀크티", "오리지널 티", "프룻티&모어", "스무디", "요거티", "커피"]


def normalize(text):
    """띄어쓰기·기호를 없애고 표기를 통일한다. '블랙밀크티+펄' 과 '블랙 밀크티 + 펄' 을 같게 본다."""
    text = re.sub(r"[\s+&()\[\]/,.·'\"!?~-]", "", text.lower())
    for src, dst in SPELLINGS:
        text = text.replace(src, dst)
    return text


class Catalog:
    def __init__(self, kiosk_dir=KIOSK_DIR):
        def load(name):
            return json.loads((kiosk_dir / name).read_text(encoding="utf-8"))

        self.items = load("menu.json")["items"]
        self.options = load("options.json")
        self.store = load("store.json")
        self.policies = load("policies.json")
        self.flow = self.policies["kiosk_flow"]  # 화면 흐름, 결제 수단, 기기 배치, 이용 안내
        self.by_id = {m["id"]: m for m in self.items}
        self.toppings = {t["id"]: t for t in self.options["toppings"]["items"]}
        self.max_toppings = self.options["toppings"]["max_count"]
        self.sugar_levels = [l["value"] for l in self.options["sugar"]["levels"]]
        self.ice_levels = {l["value"]: l["label"] for l in self.options["ice"]["levels"]}
        self.cups = {c["id"]: c for c in self.options["cups"]["items"]}
        self.payment_methods = {p["id"]: p["name"] for p in self.flow["payment_methods"]}
        self.allergens = sorted({a for m in self.items if m["kind"] == "drink" and self.orderable(m) for a in m["allergens"]})
        # 정규화한 이름·별칭 → 메뉴. 긴 이름부터 맞춰야 '블랙 밀크티 + 펄' 이 '블랙 밀크티' 로 잘리지 않는다.
        self._names = {}
        for m in self.items:
            for name in [m["name"], *m["aliases"], *([m["name_en"]] if m.get("name_en") else [])]:
                self._names.setdefault(normalize(name), m)
        self._by_length = sorted(self._names.items(), key=lambda kv: -len(kv[0]))

    @staticmethod
    def orderable(menu):
        """가격이 확인된 메뉴만 주문할 수 있다 (푸드·MD·미판매 음료는 가격이 없다)."""
        return bool(menu["sizes"])

    # ------------------------------------------------------------ 이름으로 찾기

    def resolve(self, name):
        """손님이 말한 이름으로 메뉴를 찾는다. (메뉴, []) 또는 (None, 후보 목록) 을 돌려준다."""
        if normalize(name) not in self._names:
            name = TEMPERATURE_PREFIX.sub("", name)
        key = normalize(name)
        if not key:
            return None, []
        if key in self._names:
            return self._names[key], []
        tokens = [normalize(t) for t in re.split(r"[\s+&]+", name) if normalize(t)]
        found = {}
        for norm, m in self._names.items():
            if key in norm or all(t in norm for t in tokens):
                found.setdefault(m["id"], m)
        if not found:
            scored = sorted(((SequenceMatcher(None, key, norm).ratio(), m) for norm, m in self._names.items()),
                            key=lambda x: -x[0])
            for score, m in scored[:12]:
                if score >= 0.6:
                    found.setdefault(m["id"], m)
        candidates = sorted(found.values(), key=lambda m: (not self.orderable(m), len(m["name"])))
        if len(candidates) == 1:
            return candidates[0], []
        return None, candidates[:8]

    def find_mentions(self, text, limit=6):
        """글 안에 이름이 그대로 들어 있는 메뉴를 등장 순서대로 찾는다."""
        norm = normalize(text)
        taken, found = [], []
        for key, m in self._by_length:
            start = norm.find(key)
            while start != -1:
                end = start + len(key)
                if not any(s < end and start < e for s, e in taken):
                    taken.append((start, end))
                    found.append((start, m))
                start = norm.find(key, end)
        seen, result = set(), []
        for _, m in sorted(found, key=lambda x: x[0]):
            if m["id"] not in seen:
                seen.add(m["id"])
                result.append(m)
        return result[:limit]

    def find_drinks(self, flavor=None, caffeine=None, no_milk=False, hot=False, iced=False, category=None,
                    max_price=None, max_kcal=None, low_sugar=False, sugar_adjustable=False,
                    exclude_allergens=(), exclude_ids=(), sort="popular"):
        """조건을 모두 만족하는 판매 중인 음료. 조건이 여러 개일 때 모델이 추천 목록들을 눈으로 겹쳐 보면
        틀리기 쉬워서(예: 무카페인 + 우유 없음) 메뉴판 데이터로 직접 고른다. exclude_ids 에는 품절 메뉴를 넣는다."""
        def matches(m):
            t = m["tags"]
            return (m["id"] not in exclude_ids
                    and (not flavor or flavor in t["flavors"])
                    and (caffeine != "none" or t["caffeine_level"] == "무카페인")
                    and (caffeine != "low" or t["caffeine_level"] in ("무카페인", "저카페인"))
                    and (not no_milk or not t["contains_milk"])
                    and (not hot or t["hot_available"])
                    and (not iced or "ICE" in m["temperatures"])
                    and (not category or m["category"] == category)
                    and (max_price is None or self.min_price(m) <= max_price)
                    and (max_kcal is None or (t["kcal_ref"] is not None and t["kcal_ref"] <= max_kcal))
                    and (not low_sugar or t["low_sugar_line"])
                    and (not sugar_adjustable or m["options"]["sugar"]["adjustable"] is True)
                    and not set(exclude_allergens) & set(m["allergens"]))

        def number(key):
            return lambda m: (m["tags"][key] is None, m["tags"][key] or 0, self.min_price(m))

        order = {"popular": self.popularity, "price": self.min_price, "kcal": number("kcal_ref"),
                 "caffeine": number("caffeine_mg_ref"), "sugar": number("sugar_g_ref")}
        found = [m for m in self.items if m["kind"] == "drink" and self.orderable(m) and matches(m)]
        return sorted(found, key=order.get(sort, self.popularity))

    def suggest(self, exclude_ids=(), limit=3):
        """추가 제안 창에 띄울 메뉴: 아직 담지 않았고 품절이 아닌 인기·베스트 음료."""
        picks = [m for m in self.items if m["kind"] == "drink" and self.orderable(m) and m["id"] not in exclude_ids
                 and (m["badges"] or m["is_best_seller"])]
        return sorted(picks, key=self.popularity)[:limit]

    @staticmethod
    def min_price(menu):
        return min(s["price"] for s in menu["sizes"])

    @staticmethod
    def rank(menu):
        """매장 인기 순위(1~5위). 순위에 없는 메뉴는 99."""
        return min((int(b[2]) for b in menu["badges"] if b.startswith("인기")), default=99)

    def popularity(self, menu):
        return (self.rank(menu), not menu["is_best_seller"], "사장님추천" not in menu["badges"], self.min_price(menu))

    # ------------------------------------------------------------ 화면용 데이터

    def card(self, m, sold_out=False):
        badges = [b for b in m["badges"] if b.startswith("인기")]
        if m["is_best_seller"]:
            badges.append("베스트")
        if m["is_new_season"]:
            badges.append("신메뉴")
        return {
            "id": m["id"],
            "name": m["name"],
            "name_en": m.get("name_en"),
            "kind": m["kind"],
            "category": m["category"],
            "image": f"/{m['image']}" if m["image"] else None,
            "orderable": self.orderable(m),
            "sold_out": bool(sold_out),
            "prices": [{"size": s["size"], "price": s["price"], "volume_ml": s.get("volume_ml")} for s in m["sizes"]],
            "temperatures": m.get("temperatures", []),
            "badges": badges,
        }

    def detail(self, m, sold_out=False):
        info = {**self.card(m, sold_out), "description": m["description"], "allergens": m["allergens"]}
        if m["kind"] != "drink":
            info["nutrition"] = [{"label": "1회 제공량 기준", "values": n} for n in m["nutrition"]]
            return info
        tags, opt = m["tags"], m["options"]
        info.update({
            "sugar": {"adjustable": opt["sugar"]["adjustable"], "recommended": opt["sugar"]["recommended"]},
            "ice": {"adjustable": opt["ice"]["adjustable"]},
            "included_toppings": m["included_toppings"],
            "flavors": tags["flavors"],
            "caffeine": tags["caffeine_level"],
            # 메뉴 화면에서 바로 보이는 대표 수치 (가장 작은 ICE 사이즈, 당도 0% 기준)
            "kcal": tags["kcal_ref"],
            "caffeine_mg": tags["caffeine_mg_ref"],
            "ref_basis": tags["ref_basis"],
            "nutrition": [{
                "label": f"{'ICE' if n['temperature'] == 'COLD' else 'HOT'} {n['size']}"
                         + (f" {n['variant']}" if n["variant"] else ""),
                "values": {"열량 (kcal)": n["kcal"], "당류 (g)": n["sugar_g"], "카페인 (mg)": n["caffeine_mg"]},
            } for n in m["nutrition"]],
            "nutrition_basis": m["nutrition_basis"],
        })
        return info

    def facts(self, m, sold_out=False):
        """두 메뉴를 나란히 비교할 때 쓰는 값. 설명은 공식 문구, 수치는 공식 영양정보에서 온다."""
        if m["kind"] != "drink":
            return {"name": m["name"], "category": m["category"], "description": m["description"], "orderable": False,
                    "price": "가격 정보 없음", "allergens": m["allergens"], "sold_out": False}
        tags, opt = m["tags"], m["options"]
        sugar = opt["sugar"]
        return {
            "name": m["name"], "category": m["category"], "description": m["description"],
            "orderable": self.orderable(m), "sold_out": bool(sold_out),
            "price": ", ".join(f"{s['size']} {s['price']:,}원" for s in m["sizes"]) or "가격 정보 없음",
            "flavors": tags["flavors"], "base_tea": tags["base_tea"],
            "caffeine_level": tags["caffeine_level"], "caffeine_mg": tags["caffeine_mg_ref"],
            "kcal": tags["kcal_ref"], "sugar_g": tags["sugar_g_ref"], "basis": f"{tags['ref_basis']}, 당도 0% 기준" if tags["ref_basis"] else None,
            "hot_available": tags["hot_available"],
            "sugar_option": ("고정" if sugar["adjustable"] is False else f"조절 가능(추천 {sugar['recommended']}%)"
                             if sugar["recommended"] is not None else "조절 가능" if sugar["adjustable"] else "매장 확인 필요"),
            "included_toppings": m["included_toppings"], "allergens": m["allergens"],
        }

    def board(self, sold_out=()):
        """메뉴판 탭: 추천(베스트·인기) · 신메뉴(시즌) · 음료 종류 6가지 · 디저트 · 기타."""
        drinks = [m for m in self.items if m["kind"] == "drink" and self.orderable(m)]

        picks = sorted((m for m in drinks if m["badges"] or m["is_best_seller"]), key=self.rank)
        tabs = [
            {"id": "recommend", "label": "추천", "items": picks},
            {"id": "new", "label": "신메뉴", "items": [m for m in drinks if m["is_new_season"]]},
            *({"id": cat, "label": cat, "items": [m for m in drinks if m["category"] == cat]} for cat in DRINK_TABS),
            {"id": "dessert", "label": "디저트", "items": [m for m in self.items if m["kind"] == "food"]},
            {"id": "etc", "label": "기타", "items": [m for m in self.items if m["kind"] == "md"]},
        ]
        return [{**t, "items": [self.card(m, m["id"] in sold_out) for m in t["items"]]} for t in tabs if t["items"]]
