"""수집한 원본(data/raw, data/curated)을 합쳐 키오스크용·RAG용 데이터를 만든다.

    python3 scripts/build_dataset.py

    data/kiosk/menu.json        키오스크 화면·장바구니 계산용 (구조화)
    data/kiosk/options.json     사이즈·당도·얼음·토핑
    data/kiosk/store.json       시연 매장
    data/kiosk/policies.json    멤버십·제휴·고객센터·주문 흐름·기기 배치·이용 안내
    data/kiosk/barcodes.json    시연용 바코드(멤버십·쿠폰·교환권·금액권·결제)와 단말기 시나리오
    data/kiosk/stock.json       서버를 켰을 때의 품절 상태
    data/rag/documents.jsonl    RAG 검색용 문서 (한 줄에 문서 1개)
"""
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
# 가격은 매장 판매가로 통일한다(2026-10-14 팀 결정). 배민 가격은 추천 당도·얼음 고정·인기 순위를 읽는 데만 쓴다.
PRICE_SOURCE = "공차 시흥배곧점 매장 판매가 (매장 메뉴판·키오스크 사진, 다이닝코드 매장 메뉴정보 — 갱신일 미상, 2026-10-14 확인)"
DELIVERY_SOURCE = "배달의민족 공차 시흥배곧점 (2026-10-12 캡처, 배달앱 판매가)"
NUTRITION_BASIS = "기본 레시피(당도 0%, 스무디는 당도 고정) 및 컵 사이즈 기준"

# 재료·맛 태그는 공식 값이 아니라 메뉴명/설명에서 규칙으로 뽑은 파생 값이다.
BASE_TEAS = [("얼그레이", "얼그레이티"), ("우롱", "우롱티"), ("자스민", "자스민티"), ("히비스커스", "히비스커스"),
             ("말차", "말차"), ("제주 그린", "말차"), ("제주그린", "말차"), ("블랙", "블랙티"), ("홍차", "블랙티")]
TOPPINGS_IN_NAME = [("미니펄", "미니펄"), ("화이트펄", "화이트펄"), ("코코넛", "코코넛"), ("알로에", "알로에"),
                    ("밀크폼", "밀크폼"), ("치즈폼", "치즈폼"), ("쥬얼리", "쥬얼리"), ("샷", "에스프레소 샷")]
FLAVORS = {
    "달콤한": ["달콤", "초콜", "초코", "브라운슈가", "카라멜", "바닐라", "허니", "쿠키"],
    "상큼한": ["상큼", "새콤", "자몽", "레몬", "청포도", "패션", "히비스커스", "요구르트", "요거", "오미자", "구아바", "트로피컬"],
    "고소한": ["고소", "타로", "피스타치오", "미숫가루", "밤 크림", "라떼", "우롱"],
    "쌉싸름한": ["쌉싸름", "말차", "제주 그린", "제주그린", "아메리카노", "슈페너"],
    "향긋한": ["향긋", "자스민", "얼그레이", "꽃"],
    "진한": ["진한", "진하", "묵직", "깊은"],
    "쫀득한": ["쫀득", "펄", "쥬얼리", "모찌"],
    "시원한": ["스무디", "크러쉬", "빙수", "아이스티", "쉐이크"],
}
# 공식 탭이 '베스트셀러'/'New 시즌 메뉴' 뿐인 음료는 이름(정규식)으로 음료 종류를 정한다 (위에서부터 먼저 맞는 것).
DRINK_TYPES = [
    ("요거티", ["그릭요거티"]),
    ("커피", ["아메리카노", "카페", "슈페너"]),
    ("스무디", ["스무디", "크러쉬", "쉐이크", "빙수"]),
    ("밀크티", ["밀크티", "밀크(?!폼)"]),
    ("오리지널 티", ["오리지널 티", "^우롱티"]),
    ("프룻티&모어", [""]),
]
FRUITS = ["망고", "딸기", "자몽", "레몬", "청포도", "포도", "패션 프룻", "복숭아", "수박", "참외", "사과", "애플", "구아바"]


def load(path):
    return json.loads((DATA / path).read_text(encoding="utf-8"))


def dump(path, obj):
    out = DATA / path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> data/{path}")


def won(n):
    return f"{n:,}원"


def caffeine_level(mg):
    if mg is None:
        return None
    if mg == 0:
        return "무카페인"
    if mg < 50:
        return "저카페인"
    if mg <= 150:
        return "보통"
    return "고카페인"


def pick_nutrition(rows, size=None, temp="COLD"):
    """대표 영양정보 한 줄: ICE·가장 작은 사이즈·(커피는 ESP) 우선."""
    for want_temp in (temp, None):
        for r in rows:
            if (want_temp is None or r["temperature"] == want_temp) and (size is None or r["size"] == size):
                return r
    return rows[0] if rows else None


def build_menu():
    products = load("raw/official/products.json")["data"]
    # 외국어 이름·설명: 공식 값이 없어 번역해 둔 것(scripts/translate_menu.py). 파일이 없으면 비워 둔다.
    translations = load("curated/menu_i18n.json")["menus"] if (DATA / "curated/menu_i18n.json").exists() else {}
    prices = defaultdict(list)
    with open(DATA / "raw/baemin/prices.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            prices[int(row["official_no"])].append(row)

    # 매장 판매가(raw/store/prices.csv: 메뉴판·키오스크 사진, 다이닝코드). 음료 가격은 모두 이것으로 쓴다.
    # 배민에도 있는 음료는 배민 행의 추천 당도·얼음·배지는 그대로 두고 가격만 매장가로 바꾼다. 매장가가 없는 음료는 판매하지 않는 것으로 본다.
    store_prices = defaultdict(list)
    with open(DATA / "raw/store/prices.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["official_no"]:
                store_prices[int(row["official_no"])].append(row)
    # 1리터 배달 메뉴는 다이닝코드에만 가격이 있고 매장 메뉴판에는 없다. 배달 전용으로 보고 키오스크에서는 팔지 않는다.
    delivery_only = {p["no"] for p in products if any(c["name"] == "1리터 배달 메뉴" for c in p["categories"])}
    drink_nos = {p["no"] for p in products if p["kind"] == "drink"}
    for no in drink_nos:
        store = [r for r in store_prices.get(no, []) if r["kind"] == "drink" and no not in delivery_only]
        if not store:
            prices.pop(no, None)
            continue
        base = prices.get(no) or [{"section": "매장 메뉴판", "app_name": store[0]["name"], "rec_sugar": "", "ice": "", "badge": ""}]
        prices[no] = [{**base[0], "size": r["size"], "price": r["price"], "store": True} for r in store]

    # 공식 사이트에 같은 메뉴가 탭별로 따로 등록된 경우 하나로 합친다.
    by_name = {}
    merged = []
    for p in products:
        twin = by_name.get((p["kind"], p["name"]))
        if twin:
            twin["categories"] += p["categories"]
            twin["merged_official_nos"].append(p["no"])
            prices[twin["no"]] += prices.pop(p["no"], [])
            continue
        p["merged_official_nos"] = [p["no"]]
        by_name[(p["kind"], p["name"])] = p
        merged.append(p)

    menu = []
    for p in merged:
        rows = prices.get(p["no"], [])
        cats = p["categories"]
        tabs = [c["name"] for c in cats]
        regular = [t for t in tabs if t not in ("New 시즌 메뉴", "베스트셀러")]
        series = next((c["series"] for c in cats if c["series"]), None)
        app_section = rows[0]["section"] if rows else None
        text = f"{p['name']} {p['description'] or ''}"
        item = {
            "id": f"{p['kind']}-{p['no']}",
            "official_no": p["no"],
            "kind": p["kind"],
            "name": p["name"],
            "name_en": translations.get(p["name"], {}).get("en", {}).get("name"),
            "i18n": translations.get(p["name"], {}),  # {"en" | "zh" | "ja": {"name", "description"}}
            "aliases": sorted({re.sub(r"\s*[LJG]$", "", r["app_name"]) for r in rows} - {p["name"]}),
            "description": p["description"],
            "category": regular[0] if regular else next(t for t, keys in DRINK_TYPES if any(re.search(k, p["name"]) for k in keys)),
            "official_tabs": tabs,
            "series": series,
            "app_section": app_section,
            "is_new_season": "New 시즌 메뉴" in tabs,
            "is_best_seller": "베스트셀러" in tabs,
            "badges": sorted({b for r in rows for b in r["badge"].split("|") if b}),
            "image": f"images/{p['no']}{Path(p['image_url']).suffix}" if p["image_url"] else None,
            "image_url": p["image_url"],
            "on_sale": bool(rows),
            "price_status": "확인됨" if rows else "가격 미확인",
            "price_source": None if not rows else "store" if rows[0].get("store") else "baemin",  # 매장 판매가 / 배달앱 판매가
            "allergens": p["allergens"],
            "source_url": p["source_url"],
        }

        if p["kind"] != "drink":
            item["nutrition"] = p["nutrition"]
            item["sizes"] = []
            # 푸드·MD 는 단품 가격만 있다. 키오스크의 주문(사이즈·옵션 선택)은 음료만 받으므로 가격을 알아도 on_sale 은 그대로 둔다.
            store = store_prices.get(p["no"])
            item["price"] = int(store[0]["price"]) if store else None
            if store:
                item.update(price_status="확인됨", price_source="store")
            menu.append(item)
            continue

        nut = p["nutrition"]
        temps = sorted({"ICE" if n["temperature"] == "COLD" else "HOT" for n in nut}, reverse=True)
        official_sizes = list(dict.fromkeys(n["size"] for n in nut))
        sizes = []
        for size in dict.fromkeys(r["size"] for r in rows):
            r = next(x for x in rows if x["size"] == size)
            cold = pick_nutrition(nut, size, "COLD")
            hot = next((n for n in nut if n["size"] == size and n["temperature"] == "HOT"), None)
            in_official = any(n["size"] == size for n in nut)
            sizes.append({
                "size": size,
                "price": int(r["price"]),
                "volume_ml": {"ICE": cold["volume_ml"] if in_official else None, "HOT": hot["volume_ml"] if hot else None},
                "size_in_official_table": in_official,
            })
        rec = rows[0]["rec_sugar"] if rows else ""
        ice = rows[0]["ice"] if rows else ""
        option_source = "baemin" if rec or ice else None
        if not rec and not ice and re.search("스무디|크러쉬|쉐이크", p["name"]):
            # 공식 영양정보 안내의 '스무디 당도 고정' + 배민의 다른 스무디가 모두 Sugar고정/Ice고정인 점으로 추정
            rec = ice = "fixed"
            option_source = "inferred"
        rep = pick_nutrition(nut, official_sizes[0] if official_sizes else None)
        included = [label for key, label in TOPPINGS_IN_NAME if key in p["name"]]
        if re.search(r"(?<!미니)(?<!화이트)펄", p["name"]):
            included.insert(0, "펄")
        teas = list(dict.fromkeys(label for key, label in BASE_TEAS if key in text))
        item.update({
            "sizes": sizes,
            "official_sizes": official_sizes,
            "temperatures": temps,
            "options": {
                "sugar": {
                    "adjustable": None if not rec else rec != "fixed",
                    "recommended": int(rec) if rec.isdigit() else None,
                },
                "ice": {"adjustable": None if not ice else ice == "regular", "default": "regular" if ice == "regular" else None},
                "source": option_source,
            },
            "included_toppings": included,
            "nutrition_basis": NUTRITION_BASIS,
            "nutrition": nut,
            "tags": {
                "_derived": True,
                "base_tea": teas,
                "flavors": [f for f, keys in FLAVORS.items() if any(k in text for k in keys)],
                "fruits": [f for f in FRUITS if f in text],
                "low_sugar_line": "저당" in p["name"] or "제로" in p["name"],
                "contains_milk": "우유" in p["allergens"],
                "contains_coffee": any(n["variant"] for n in nut),
                "hot_available": "HOT" in temps,
                "caffeine_level": caffeine_level(rep["caffeine_mg"]) if rep else None,
                "kcal_ref": rep["kcal"] if rep else None,
                "sugar_g_ref": rep["sugar_g"] if rep else None,
                "caffeine_mg_ref": rep["caffeine_mg"] if rep else None,
                "ref_basis": f"ICE {rep['size']}" + (f" {rep['variant']}" if rep and rep["variant"] else "") if rep else None,
            },
        })
        menu.append(item)
    return menu


# ---------------------------------------------------------------- RAG 문서

def nutrition_sentence(n):
    temp = "ICE" if n["temperature"] == "COLD" else "HOT"
    variant = {"ESP": " 에스프레소", "DRIP": " 드립"}.get(n["variant"], "")
    return (f"{temp} {n['size']}{variant}({n['volume_ml']}ml): 열량 {n['kcal']}kcal, 당류 {n['sugar_g']}g, "
            f"단백질 {n['protein_g']}g, 포화지방 {n['saturated_fat_g']}g, 나트륨 {n['sodium_mg']}mg, 카페인 {n['caffeine_mg']}mg")


def size_gap_text(drinks):
    """J 가 L 보다 얼마나 비싼지(매장가 기준)를 데이터에서 센다."""
    gaps = Counter(next(s["price"] for s in m["sizes"] if s["size"] == "J") - next(s["price"] for s in m["sizes"] if s["size"] == "L")
                   for m in drinks if {"L", "J"} <= {s["size"] for s in m["sizes"]})
    common = gaps.most_common(1)[0][0]
    others = sorted(g for g in gaps if g != common)
    return (f"J 는 L 보다 대부분 {won(common)} 비싸고, 메뉴에 따라 " + ", ".join(won(g) for g in others) + " 차이가 나기도 합니다.")


def menu_doc(m, options):
    lines = [f"메뉴명: {m['name']}"]
    foreign = [f"{label} {m['i18n'][lang]['name']}" for lang, label in (("en", "영어"), ("zh", "중국어"), ("ja", "일본어"))
               if m["i18n"].get(lang, {}).get("name")]
    if foreign:
        lines.append("외국어 이름: " + " / ".join(foreign))
    if m["aliases"]:
        lines.append(f"다른 표기: {', '.join(m['aliases'])}")
    lines.append(f"분류: {m['category']}" + (f" / {m['series']}" if m["series"] else ""))
    if m["description"]:
        lines.append(f"설명: {m['description']}")
    if m["kind"] != "drink":
        lines.append(f"가격: {won(m['price'])} (키오스크 대화로는 주문할 수 없고 카운터에서 주문합니다)" if m["price"]
                     else "가격: 가격 정보가 수집되지 않았습니다(매장 확인 필요).")
        for n in m["nutrition"]:
            lines.append("영양정보: " + ", ".join(f"{k} {v}" for k, v in n.items()))
    else:
        if m["sizes"]:
            lines.append("가격: " + ", ".join(f"{s['size']} 사이즈 {won(s['price'])}" for s in m["sizes"]))
        else:
            lines.append("가격: 시연 매장(시흥배곧점) 판매 목록에 없어 가격이 확인되지 않은 메뉴입니다.")
        lines.append("HOT/ICE: " + ("HOT, ICE 모두 가능" if "HOT" in m["temperatures"] else "ICE 전용 (HOT 불가)"))
        sugar, ice = m["options"]["sugar"], m["options"]["ice"]
        if sugar["adjustable"] is False:
            lines.append("당도: 고정 (조절 불가)")
        elif sugar["recommended"] is not None:
            lines.append(f"당도: 0/30/50/70/100% 중 선택, 추천 당도 {sugar['recommended']}%")
        if ice["adjustable"] is False:
            lines.append("얼음량: 고정 (조절 불가)")
        elif ice["adjustable"]:
            lines.append("얼음량: 조절 가능 (기본 얼음 보통)")
        if m["included_toppings"]:
            lines.append(f"기본 포함 토핑: {', '.join(m['included_toppings'])}")
        t = m["tags"]
        if t["base_tea"]:
            lines.append(f"베이스: {', '.join(t['base_tea'])}")
        if t["flavors"]:
            lines.append(f"맛 특징: {', '.join(t['flavors'])}")
        if t["caffeine_level"]:
            lines.append(f"카페인: {t['caffeine_level']} ({t['ref_basis']} 기준 {t['caffeine_mg_ref']}mg)")
        if m["nutrition"]:
            lines.append(f"영양정보 [{NUTRITION_BASIS}]")
            lines += [" - " + nutrition_sentence(n) for n in m["nutrition"]]
    lines.append("알레르기 유발물질: " + (", ".join(m["allergens"]) if m["allergens"] else "표기 없음"))
    badges = m["badges"] + (["베스트셀러"] if m["is_best_seller"] else []) + (["시즌 메뉴"] if m["is_new_season"] else [])
    if badges:
        lines.append(f"특이사항: {', '.join(badges)}")
    return {
        "id": f"menu:{m['id']}",
        "type": "menu",
        "title": m["name"],
        "text": "\n".join(lines),
        "metadata": {
            "menu_id": m["id"], "kind": m["kind"], "category": m["category"], "series": m["series"],
            "on_sale": m["on_sale"], "min_price": min((s["price"] for s in m["sizes"]), default=None),
        },
        "source": [m["source_url"]] + ([PRICE_SOURCE] if m["price_source"] == "store" else []),
    }


def build_documents(menu, options, store, policies, flow, info):
    docs = [menu_doc(m, options) for m in menu]
    drinks = [m for m in menu if m["kind"] == "drink" and m["on_sale"]]

    def add(doc_id, doc_type, title, text, source, **meta):
        docs.append({"id": doc_id, "type": doc_type, "title": title, "text": text, "metadata": meta, "source": source})

    def price_line(m):
        return f"- {m['name']}: " + ", ".join(f"{s['size']} {won(s['price'])}" for s in m["sizes"])

    # 카테고리별 메뉴판
    by_cat = defaultdict(list)
    for m in drinks:
        by_cat[m["category"]].append(m)
    for cat, items in by_cat.items():
        add(f"category:{cat}", "category", f"{cat} 메뉴판",
            f"{cat} 카테고리에서 판매 중인 메뉴와 가격입니다.\n" + "\n".join(price_line(m) for m in items),
            [PRICE_SOURCE], category=cat)
    food = [m for m in menu if m["kind"] == "food"]
    for cat in dict.fromkeys(m["category"] for m in food):
        items = [m for m in food if m["category"] == cat]
        add(f"category:{cat}", "category", f"푸드 - {cat}",
            # 첫 줄은 검색(임베딩)에 쓰이므로 무엇의 목록인지만 짧게 적는다.
            f"공차 푸드({cat}) 메뉴와 확인된 가격입니다.\n"
            "괄호 안은 매장 키오스크 화면에서 확인한 가격이고, 가격이 없는 것은 매장에서 확인해야 합니다. 푸드는 카운터에서 주문합니다.\n" +
            "\n".join(f"- {m['name']}" + (f" ({won(m['price'])})" if m["price"] else "") +
                      (f": {m['description']}" if m["description"] else "") for m in items),
            ["https://www.gong-cha.co.kr/brand/menu/product?category=002001", PRICE_SOURCE], category=cat)

    # 추천용 문서 (태그·영양정보에서 파생)
    def rec(doc_id, title, intro, items, fmt=price_line):
        if items:
            add(f"recommend:{doc_id}", "recommendation", title, intro + "\n" + "\n".join(fmt(m) for m in items),
                ["build_dataset.py 파생 (공식 영양정보·메뉴 설명 기반)"])

    ranked = sorted((m for m in drinks if any(b.startswith("인기") for b in m["badges"])),
                    key=lambda m: min(b for b in m["badges"] if b.startswith("인기")))
    rec("popular", "인기 메뉴 순위", "시흥배곧점에서 한 달간 주문이 많은 인기 메뉴 순위입니다. 요즘 가장 잘 나가는(많이 팔리는) 메뉴입니다.", ranked,
        lambda m: f"- {next(b for b in m['badges'] if b.startswith('인기'))}: {m['name']} ({won(m['sizes'][0]['price'])})")
    rec("best", "베스트셀러", "공차 고객들이 즐겨 찾는 티와 토핑 조합(베스트셀러)입니다.", [m for m in drinks if m["is_best_seller"]])
    rec("owner", "사장님 추천 메뉴", "매장에서 추천하는 메뉴입니다.", [m for m in drinks if "사장님추천" in m["badges"]])
    for flavor in FLAVORS:
        rec(f"flavor-{flavor}", f"{flavor} 음료 추천", f"'{flavor}' 맛 특징을 가진 음료입니다.",
            [m for m in drinks if flavor in m["tags"]["flavors"]])
    rec("no-caffeine", "카페인 없는 음료", "카페인이 0mg 인 음료입니다(공식 영양정보 기준).",
        [m for m in drinks if m["tags"]["caffeine_level"] == "무카페인"])
    rec("low-caffeine", "카페인 적은 음료", "카페인이 50mg 미만인 음료입니다(공식 영양정보 기준).",
        [m for m in drinks if m["tags"]["caffeine_level"] == "저카페인"],
        lambda m: f"- {m['name']}: 카페인 {m['tags']['caffeine_mg_ref']}mg ({m['tags']['ref_basis']})")
    rec("high-caffeine", "카페인 많은 음료", "카페인이 150mg 을 넘는 음료입니다(공식 영양정보 기준).",
        [m for m in drinks if m["tags"]["caffeine_level"] == "고카페인"],
        lambda m: f"- {m['name']}: 카페인 {m['tags']['caffeine_mg_ref']}mg ({m['tags']['ref_basis']})")
    low_kcal = sorted((m for m in drinks if m["tags"]["kcal_ref"] is not None), key=lambda m: m["tags"]["kcal_ref"])[:15]
    rec("low-kcal", "칼로리 낮은 음료", f"열량이 낮은 순서입니다. [{NUTRITION_BASIS}]", low_kcal,
        lambda m: f"- {m['name']}: {m['tags']['kcal_ref']}kcal ({m['tags']['ref_basis']})")
    rec("low-sugar", "저당·제로 메뉴", "당을 줄인 저당 시리즈 메뉴입니다.", [m for m in drinks if m["tags"]["low_sugar_line"]])
    rec("hot", "따뜻하게(HOT) 마실 수 있는 음료", "HOT 주문이 가능한 음료입니다.", [m for m in drinks if m["tags"]["hot_available"]])
    rec("no-milk", "우유가 들어가지 않는 음료", "알레르기 유발물질에 우유 표기가 없는 음료입니다.",
        [m for m in drinks if not m["tags"]["contains_milk"]])
    rec("cheapest", "가격이 저렴한 음료", "가격이 낮은 순서입니다.",
        sorted(drinks, key=lambda m: m["sizes"][0]["price"])[:15])
    rec("season", "신메뉴·시즌 메뉴", "지금 판매 중인 신메뉴(시즌 한정) 음료입니다.", [m for m in drinks if m["is_new_season"]],
        lambda m: price_line(m) + (f" ({m['series']})" if m["series"] else ""))
    add("recommend:dessert", "recommendation", "음료와 곁들이기 좋은 디저트·푸드",
        "음료와 함께 곁들일 수 있는 디저트(푸드) 메뉴입니다.\n"
        "괄호 안은 매장 키오스크 화면에서 확인한 가격이고, 가격이 없는 것은 매장에서 확인해야 합니다. 푸드는 카운터에서 주문합니다.\n" +
        "\n".join(f"- {cat}: " + ", ".join(m["name"] + (f"({won(m['price'])})" if m["price"] else "") for m in food if m["category"] == cat)
                  for cat in dict.fromkeys(m["category"] for m in food)),
        ["https://www.gong-cha.co.kr/brand/menu/product?category=002001", PRICE_SOURCE])

    # 옵션
    sugar, ice, top = options["sugar"], options["ice"], options["toppings"]
    add("option:sugar", "option", "당도 선택과 단계별 뜻",
        "당도는 " + ", ".join(l["label"] for l in sugar["levels"]) + " 5단계 중에서 고를 수 있고 추가 요금은 없습니다.\n" +
        sugar["meaning"] + "\n" + "\n".join(f"- {l['label']}: {l['description']}" for l in sugar["levels"]) + "\n" +
        sugar["rule"] + "\n영양정보의 당류와 열량은 당도 0% 기준이라, 당도를 올리면 실제 값은 더 높습니다.",
        [sugar["source"], sugar["meaning_note"]])
    add("option:ice", "option", "얼음량 선택",
        "얼음량은 " + ", ".join(l["label"] for l in ice["levels"]) + " 중에서 고를 수 있고 추가 요금은 없습니다. 기본은 얼음 보통입니다.\n" +
        f"'{ice['not_selectable']['label']}'는 화면에서 고를 수 없습니다. " + ice["not_selectable"]["handling"] + "\n" + ice["rule"],
        [ice["source"]])
    add("option:toppings", "option", "토핑 종류와 가격",
        f"토핑은 최대 {top['max_count']}가지까지 추가할 수 있습니다. 샷추가는 토핑 개수에 세지 않습니다.\n" +
        "\n".join(f"- {t['name']}({t['name_en']}) " + " / ".join(f"{s} +{won(p)}" for s, p in t["price_by_size"].items())
                  + f": {t['description']}" for t in top["items"]) +
        "\n음료 이름에 이미 '+펄' 처럼 토핑이 적힌 메뉴는 그 토핑이 기본 포함된 가격입니다.",
        [top["source"]])
    cups = options["cups"]
    add("option:cup", "option", "컵 선택 (매장컵·일회용컵·개인컵·텀블러)",
        "컵은 " + ", ".join(c["name"] for c in cups["items"]) + " 중에서 고릅니다. "
        "개인 텀블러를 가져왔으면 개인컵을 골라 텀블러에 담아 달라고 할 수 있습니다.\n" +
        "\n".join(f"- {c['name']}({c['name_en']}): {c['description']}" for c in cups["items"]) + "\n" + cups["rule"] +
        "\n개인컵(텀블러) 할인이 있는지는 확인되지 않아 매장에 물어봐야 합니다.",
        [cups["source"]])
    for t in top["items"]:
        add(f"option:topping:{t['id']}", "option", f"토핑 - {t['name']}",
            f"{t['name']}({t['name_en']}) 토핑은 {t['description']}입니다. 추가 요금은 "
            + ", ".join(f"{s} 사이즈 {won(p)}" for s, p in t["price_by_size"].items()) + "입니다.", [top["source"]])
    on_sale_sizes = [s["id"] for s in options["sizes"]["items"] if any(x["size"] == s["id"] for m in drinks for x in m["sizes"])]
    add("option:size", "option", "사이즈 안내",
        "공차 음료 사이즈는 L(라지), J(점보), G(기가)가 있습니다. 이 매장에서는 " + ", ".join(on_sale_sizes) + " 사이즈를 판매합니다. "
        "J(점보)는 L(라지)보다 큰 사이즈입니다. ICE 기준 L 473ml, J 651ml, G 946ml 이고 HOT L 은 414ml 입니다. "
        "메뉴마다 주문 가능한 사이즈가 다르며, 스무디·크러쉬·쥬얼리 음료는 대부분 L 사이즈만 있습니다. " + size_gap_text(drinks),
        [options["sizes"]["source"], PRICE_SOURCE])
    add("option:temperature", "option", "HOT / ICE 안내", options["temperature"]["rule"], [options["temperature"]["source"]])

    # 매장·정책·안내
    add("store:info", "store", f"공차 {store['name']} 매장 정보",
        f"매장명: 공차 {store['name']}\n주소: {store['address']}\n전화번호: {store['tel']}\n영업시간: {store['hours']}\n"
        f"휴무일: {store['holiday']}\n가까운 곳: {store['nearby']}\n편의시설: {', '.join(store['facilities'])}\n"
        f"이용 가능한 제휴: {', '.join(k for k, v in store['partners'].items() if v)}",
        [store["source_url"]])
    mem = policies["membership"]
    add("policy:membership", "policy", mem["title"], "공차 멤버십 앱 혜택: " + " / ".join(mem["benefits"]), [mem["source_url"]])
    for p in policies["telecom"] + policies["payment_partners"]:
        text = f"{p['title']} 혜택: " + " / ".join(p["benefits"]) + "\n이용안내:\n" + "\n".join(f"- {r}" for r in p["rules"])
        add(f"policy:{p['id']}", "policy", f"{p['title']} 제휴", text, [p["source_url"]])
    cc = policies["customer_center"]
    add("policy:customer_center", "policy", "고객센터",
        f"공차 고객센터 전화번호는 {cc['tel']} 이고 운영시간은 {cc['hours']} 입니다. 이메일: {cc['email']}", [cc["source_url"]])
    add("guide:order", "guide", "주문 방법",
        "공차는 오리지널 티, 밀크티, 스무디, 과일믹스 등의 메뉴를 베이스로 5가지 당도와 얼음량, 토핑까지 선택해 취향대로 주문할 수 있습니다.\n"
        "STEP 1 음료 선택: 취향에 맞게 공차의 음료를 고릅니다.\nSTEP 2 얼음량·당도: 얼음량과 당도를 원하는 대로 조절합니다.\n"
        "STEP 3 토핑: 7가지 토핑 중 원하는 토핑을 선택합니다(최대 3가지).",
        [info["order_guide"]["source_url"]])
    # 이용 안내: 사용법, 결제 방법과 기기 위치, 적립·할인, 교환권·금액권, 요청 사항 처리 방침, 직원 호출, 알레르기
    for g in flow["guides"]:
        add(f"guide:{g['id']}", "guide", g["title"], g["text"], ["data/curated/kiosk_flow.json (화면 흐름과 기기 배치는 가정값)"])

    # 차(茶) 지식: 공식 Tea 페이지의 찻잎 소개 4종
    tea_lines = info["tea"]["lines"]
    for name in ("진하고 풍부한 블랙티", "가볍고 은은한 플로럴 자스민티", "구수하고 달콤한 우롱티", "진하고 상큼한 얼그레이티"):
        if name in tea_lines:
            add(f"knowledge:tea:{name.split()[-1]}", "knowledge", name,
                f"{name}: {tea_lines[tea_lines.index(name) + 1]}", [info["tea"]["source_url"]])
    add("knowledge:brand", "knowledge", "공차 브랜드 소개",
        "공차(貢茶)는 '황제에게 올리는 차'에서 유래한 이름의 프리미엄 티 브랜드입니다. 1996년 대만에서 창립자 슈퍼 우(Super Wu)가 "
        "첫 티샵을 열고 시그니처 메뉴 '밀크폼'을 선보였고, 대만 가오슝에서 브랜드로 공식 출범했습니다. 현재 전 세계 2,100개가 넘는 매장을 "
        "운영합니다. 엄선한 찻잎을 매장에서 직접 우려냅니다.",
        [info["about"]["source_url"], info["tea"]["source_url"]])
    return docs


def main():
    menu = build_menu()
    options = load("curated/options.json")
    policies = load("curated/policies.json")
    flow = load("curated/kiosk_flow.json")
    info = load("raw/official/info_pages.json")["data"]

    # 사이즈별 용량을 공식 영양정보 표에서 채운다.
    volumes = defaultdict(set)
    for m in menu:
        for n in m.get("nutrition", []) if m["kind"] == "drink" else []:
            volumes[(n["size"], "ICE" if n["temperature"] == "COLD" else "HOT")].add(n["volume_ml"])
    for s in options["sizes"]["items"]:
        s["volume_ml"] = {t: sorted(volumes[(s["id"], t)]) for t in ("ICE", "HOT") if volumes[(s["id"], t)]}

    raw_store = load("raw/official/stores.json")["data"][0]
    partner_pages = {"SKT T멤버십": "skt_membership", "KT 멤버십": "kt_membership", "카카오페이": "kakaopay", "페이코": "payco"}
    facility_names = {"seat": "좌석", "toilet": "화장실", "parking": "주차", "wifi": "와이파이"}
    store = {
        "name": raw_store["name"],
        "address": raw_store["address"],
        "tel": raw_store["tel"],
        "hours": raw_store["detail"]["time"],
        "holiday": raw_store["detail"]["holiday"],
        "nearby": raw_store["detail"]["station"],
        "lat": float(raw_store["detail"]["lat"]),
        "lng": float(raw_store["detail"]["lng"]),
        "facilities": [facility_names.get(f, f) for f in raw_store["facilities"]],
        "partners": {label: any(raw_store["name"] in l for l in info[key]["lines"]) for label, key in partner_pages.items()},
        "source_url": raw_store["source_url"],
    }

    priced = [m for m in menu if m["on_sale"]]
    dump("kiosk/menu.json", {
        "price_source": PRICE_SOURCE,
        "nutrition_basis": NUTRITION_BASIS,
        "counts": {"total": len(menu), "on_sale": len(priced)},
        "items": menu,
    })
    dump("kiosk/options.json", options)
    dump("kiosk/store.json", store)
    dump("kiosk/policies.json", {**policies, "kiosk_flow": flow})
    dump("kiosk/barcodes.json", load("curated/barcodes.json"))
    dump("kiosk/stock.json", load("curated/stock.json"))

    docs = build_documents(menu, options, store, policies, flow, info)
    out = DATA / "rag" / "documents.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in docs) + "\n", encoding="utf-8")
    print(f"-> data/rag/documents.jsonl ({len(docs)}개 문서)")


if __name__ == "__main__":
    main()
