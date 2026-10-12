"""공차 공식 홈페이지(www.gong-cha.co.kr)에서 메뉴·안내·매장 정보를 수집한다.

    python3 scripts/scrape_official.py            # data/raw/official/*.json 생성
    python3 scripts/scrape_official.py --images   # 상품 이미지도 data/images/ 에 내려받기

수업 과제용 수집이며, 모든 레코드에 출처 URL(source_url)을 남긴다.
"""
import argparse
import html
import json
import re
import time
from datetime import date
from pathlib import Path

import requests

BASE = "https://www.gong-cha.co.kr"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "official"
IMG_DIR = ROOT / "data" / "images"
DELAY = 0.3

# 공식 메뉴 페이지의 탭 코드
CATEGORIES = {
    "drink": {
        "001001": "New 시즌 메뉴",
        "001002": "베스트셀러",
        "001006": "밀크티",
        "001010": "스무디",
        "001003": "오리지널 티",
        "001015": "프룻티&모어",
        "001011": "커피",
        "001017": "요거티",
        "001018": "1리터 배달 메뉴",
    },
    "food": {"002001": "베이커리", "002004": "스낵", "002006": "아이스크림"},
    "md": {"003001": "비식품", "003002": "식품"},
}

INFO_PAGES = {
    "order_guide": "/brand/html/11",
    "tea": "/brand/html/9",
    "about": "/brand/html/10",
    "membership_app": "/brand/html/14",
    "skt_membership": "/brand/store/skt_membership",
    "kt_membership": "/brand/store/kt_membership",
    "kakaopay": "/brand/store/kakao",
    "payco": "/brand/store/payco",
}

session = requests.Session()
session.headers["User-Agent"] = "Mozilla/5.0 (class project data collection)"


def get(path, **params):
    time.sleep(DELAY)
    r = session.get(BASE + path, params=params, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def clean(s):
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s).replace("\u200b", "")).strip()


def num(s):
    """'473' -> 473, '0.5 미만' 같은 값은 문자열 그대로 둔다."""
    s = s.strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s or None


def container_text(page):
    """본문 영역을 줄 단위 텍스트로 바꾼다 (헤더/푸터/스크립트 제거)."""
    start = page.find('id="container"')
    end = page.find('id="pageTopScrollBtn"')
    body = page[start:end if end > 0 else None]
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"<[^>]+>", "\n", body)
    lines = [re.sub(r"\s+", " ", html.unescape(l)).strip() for l in body.split("\n")]
    return [l for l in lines if l and not l.startswith("<")][1:]


# ---------------------------------------------------------------- 메뉴

def parse_list(fragment, code):
    """ajaxlist 조각에서 (시리즈명, 상품번호, 이름, 이미지) 를 순서대로 뽑는다."""
    items = []
    tokens = re.finditer(
        r"<h3[^>]*>(?P<series>.*?)</h3>"
        r"|product_detail\?category=\d+&no=(?P<no>\d+)[^>]*>.*?<img src=\"(?P<img>[^\"]+)\".*?<p>(?P<name>.*?)</p>",
        fragment,
        flags=re.S,
    )
    series = None
    for m in tokens:
        if m.group("series") is not None:
            series = clean(m.group("series"))
            continue
        items.append(
            {
                "no": int(m.group("no")),
                "name": clean(m.group("name")),
                "image_url": BASE + m.group("img"),
                "category_code": code,
                "series": series,
            }
        )
    return items


def parse_detail(page, kind):
    block = page[page.find("menu-detail-conts"):page.find("item-arrows")]
    name = re.search(r'<p class="t1[^"]*">(.*?)</p>', block, flags=re.S)
    desc = re.search(r'<p class="t2[^"]*">(.*?)</p>', block, flags=re.S)
    img = re.search(r'<div class="picture[^"]*">\s*<img src="([^"]+)"', block)
    caution = re.search(r"알레르기 유발물질\s*:\s*(.*?)</p>", block, flags=re.S)
    nutrition = []
    # 커피류는 표가 (ESP)/(DRIP) 두 개로 나뉜다 -> variant 로 구분
    tables = re.findall(
        r'<div class="table-title">(.*?)</div>\s*<table>(.*?)</table>', block, flags=re.S
    )
    for title, table in tables:
        variant = clean(title).strip("()") or None
        headers = [clean(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", table, flags=re.S)]
        temp = None
        for row in re.findall(r"<tr>(.*?)</tr>", table.split("<tbody>")[-1], flags=re.S):
            cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
            if kind == "drink":
                # 열: [Cold/Hot(rowspan 이면 생략)], 사이즈, 용량, 열량, 당류, 단백질, 포화지방, 나트륨, 카페인
                if len(cells) == 9:
                    temp, cells = cells[0], cells[1:]
                if len(cells) != 8:
                    continue
                nutrition.append(
                    {
                        "variant": variant,
                        "temperature": (temp or "").upper() or None,
                        "size": cells[0],
                        "volume_ml": num(cells[1]),
                        "kcal": num(cells[2]),
                        "sugar_g": num(cells[3]),
                        "protein_g": num(cells[4]),
                        "saturated_fat_g": num(cells[5]),
                        "sodium_mg": num(cells[6]),
                        "caffeine_mg": num(cells[7]),
                    }
                )
            elif cells and len(cells) == len(headers):
                # 푸드/MD 는 표 머리글이 제각각이라 머리글 그대로 보존한다.
                nutrition.append({h: num(c) for h, c in zip(headers, cells)})

    allergens = []
    if caution:
        allergens = [a.strip() for a in re.split(r"[,/]", clean(caution.group(1))) if a.strip()]
    return {
        "name": clean(name.group(1)) if name else None,
        "description": clean(desc.group(1)) if desc else None,
        "image_url": BASE + img.group(1) if img else None,
        "nutrition": nutrition,
        "allergens": allergens,
    }


def scrape_menu():
    products = {}
    for kind, cats in CATEGORIES.items():
        for code, cat_name in cats.items():
            listed = parse_list(get("/brand/menu/ajaxlist", category=code), code)
            print(f"  {cat_name}({code}): {len(listed)}개")
            for order, item in enumerate(listed):
                p = products.get(item["no"])
                if p is None:
                    detail = parse_detail(get("/brand/menu/product_detail", category=code, no=item["no"]), kind)
                    p = products[item["no"]] = {
                        "no": item["no"],
                        "kind": kind,
                        "name": detail["name"] or item["name"],
                        "description": detail["description"],
                        "image_url": detail["image_url"] or item["image_url"],
                        "categories": [],
                        "nutrition": detail["nutrition"],
                        "allergens": detail["allergens"],
                        "source_url": f"{BASE}/brand/menu/product_detail?category={code}&no={item['no']}",
                    }
                p["categories"].append(
                    {"code": code, "name": cat_name, "series": item["series"], "order": order}
                )
    return sorted(products.values(), key=lambda p: p["no"])


def scrape_new_menus():
    page = get("/brand/menu/new_menu")
    out = []
    for seq in dict.fromkeys(re.findall(r"new_menu_detail\?seq=(\d+)", page)):
        detail = get("/brand/menu/new_menu_detail", seq=seq)
        body = detail[detail.find('id="container"'):detail.find('id="pageTopScrollBtn"')]
        out.append(
            {
                "seq": int(seq),
                "poster_images": [BASE + u for u in re.findall(r'<img[^>]+src="(/upload/new/[^"]+)"', body)],
                "text": [l for l in container_text(detail) if l not in ("Menu", "신메뉴", "목록")],
                "source_url": f"{BASE}/brand/menu/new_menu_detail?seq={seq}",
            }
        )
    return out


# ---------------------------------------------------------------- 매장

def scrape_stores(keywords):
    """매장 찾기에서 키워드로 검색하고 상세(전화·영업시간 등)를 가져온다."""
    stores = []
    for kw in keywords:
        page = get("/brand/store/list", keyword=kw)
        # CSRF 토큰은 <meta name="_csrf_header"> / <meta name="_csrf"> 에 실려 온다.
        csrf = re.search(r'name="_csrf_header" content="([^"]+)">\s*<meta name="_csrf" content="([^"]+)"', page)
        rows = re.findall(
            r'data-seq="(\d+)">\s*<div class="cell name">(.*?)<div class="cell benefit">(.*?)'
            r'<div class="cell addr">(.*?)<div class="cell num">(.*?)</a>',
            page,
            flags=re.S,
        )
        for seq, title, benefit, addr, tel in rows:
            title = clean(title)
            if kw not in title:
                continue
            store = {
                "seq": int(seq),
                "name": title,
                "address": clean(addr),
                "tel": clean(tel),
                "facilities": re.findall(r'<span class="(\w+)">', benefit),
                "search_keyword": kw,
            }
            data = {"seq": seq}
            if csrf:
                data[csrf.group(1)] = csrf.group(2)
            time.sleep(DELAY)
            r = session.post(
                BASE + "/brand/store/store_detail",
                data=data,
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/brand/store/list"},
                timeout=30,
            )
            try:
                d = r.json().get("data") or {}
                store["detail"] = {
                    k: d.get(k)
                    for k in ("sido", "gugun", "lat", "lng", "time", "holiday", "station", "convenience", "new_yn")
                }
            except ValueError:
                store["detail"] = None
                store["detail_error"] = f"HTTP {r.status_code}"
            store["source_url"] = f"{BASE}/brand/store/list?keyword={kw}"
            stores.append(store)
    return stores


# ---------------------------------------------------------------- 실행

def save(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"collected_at": date.today().isoformat(), "source": BASE, "data": data}
    (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> data/raw/official/{name}.json")


def download_images(products):
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for p in products:
        url = p["image_url"]
        if not url:
            continue
        dest = IMG_DIR / f"{p['no']}{Path(url).suffix}"
        if dest.exists():
            continue
        time.sleep(DELAY)
        r = session.get(url, timeout=30)
        if r.ok:
            dest.write_bytes(r.content)
    print(f"-> data/images/ ({len(list(IMG_DIR.iterdir()))}개)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", action="store_true", help="상품 이미지 내려받기")
    ap.add_argument("--stores", nargs="*", default=["시흥배곧"], help="매장 검색 키워드")
    args = ap.parse_args()

    print("메뉴 수집")
    products = scrape_menu()
    save("products", products)
    save("categories", CATEGORIES)

    print("신메뉴 수집")
    save("new_menus", scrape_new_menus())

    print("안내 페이지 수집")
    pages = {}
    for key, path in INFO_PAGES.items():
        pages[key] = {"source_url": BASE + path, "lines": container_text(get(path))}
    save("info_pages", pages)

    print("매장 수집")
    save("stores", scrape_stores(args.stores))

    if args.images:
        download_images(products)


if __name__ == "__main__":
    main()
