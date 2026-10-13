"""메뉴 이름과 설명의 번역을 만든다: 공식 메뉴(한글) → data/curated/menu_i18n.json (영어·중국어·일본어).

    python scripts/translate_menu.py        # 아직 번역이 없는 항목만 번역해 덧붙인다
    python scripts/build_dataset.py         # 그다음 kiosk/, rag/ 에 반영

공식 사이트에 외국어 메뉴가 없어서 모델로 번역한 값이다(검수 전). 파일을 직접 고치면 그 값이 유지된다.
외국어 화면의 메뉴판·상세 화면·주문 내역과, 메뉴 문서의 외국어 이름에 쓰인다.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from app.config import CHAT_MODEL, DATA_DIR  # noqa: E402

OUT = DATA_DIR / "curated" / "menu_i18n.json"
BATCH = 30
HANGUL = re.compile("[가-힣]")
LANGUAGES = {"en": "영어", "zh": "중국어(간체)", "ja": "일본어"}
GLOSSARY = {
    "en": "펄=Pearls, 미니펄=Mini Pearls, 화이트펄=White Pearls, 코코넛=Coconut Jelly, 알로에=Aloe, 밀크폼=Milk Foam, 치즈폼=Cheese Foam, "
          "쥬얼리=Jewelry Pearls, 크러쉬=Crush, 저당=Low Sugar, 제로=Zero Sugar, 제주 그린=Jeju Green Tea, 납작복숭아=Flat Peach, "
          "공차슈페너=Gong Cha Einspanner, 샷=Espresso Shot, 쑥=Mugwort, 미숫가루=Misugaru (Roasted Grain), 펄볶이=Pearl-bokki",
    "zh": "공차=贡茶, 펄=珍珠, 미니펄=小珍珠, 화이트펄=白珍珠, 코코넛=椰果, 알로에=芦荟, 밀크폼=奶盖, 치즈폼=芝士奶盖, 쥬얼리=宝石珍珠, "
          "밀크티=奶茶, 스무디=冰沙, 크러쉬=碎冰沙, 요구르트=酸奶, 저당=低糖, 제로=零糖, 시그니처=招牌, 브라운슈가=黑糖, 제주 그린=济州绿茶, "
          "말차=抹茶, 얼그레이=伯爵, 우롱=乌龙, 자스민=茉莉, 블랙티=红茶, 타로=芋头, 히비스커스=洛神花, 납작복숭아=蟠桃, 샷=浓缩咖啡, "
          "아메리카노=美式咖啡, 카페라떼=拿铁, 쑥=艾草, 밤=栗子, 오미자=五味子, 미숫가루=韩式谷物粉",
    "ja": "공차=ゴンチャ, 펄=パール, 미니펄=ミニパール, 화이트펄=ホワイトパール, 코코넛=ナタデココ, 알로에=アロエ, 밀크폼=ミルクフォーム, "
          "치즈폼=チーズフォーム, 쥬얼리=ジュエリーパール, 밀크티=ミルクティー, 스무디=スムージー, 크러쉬=クラッシュ, 요구르트=ヨーグルト, "
          "저당=低糖, 제로=ゼロシュガー, 시그니처=シグネチャー, 브라운슈가=黒糖, 제주 그린=済州グリーンティー, 말차=抹茶, 얼그레이=アールグレイ, "
          "우롱=ウーロン, 자스민=ジャスミン, 블랙티=ブラックティー, 타로=タロ, 히비스커스=ハイビスカス, 납작복숭아=蟠桃, 샷=エスプレッソショット, "
          "쑥=よもぎ, 밤=栗, 오미자=オミジャ(五味子), 미숫가루=ミスカル(穀物パウダー)",
}
GUIDE = """\
버블티 카페 '공차(Gong Cha)' 한국 매장의 메뉴를 {language} 메뉴판에 쓸 글로 옮긴다.
- name: 메뉴판에 적는 이름답게 짧게 {language}로 쓴다. 이름에 든 재료와 토핑을 빠뜨리거나 보태지 않는다.
  '+' 는 영어에서는 'with', 중국어와 일본어에서는 기호 '+' 그대로 쓴다('추가' 같은 말을 붙이지 않는다).
- description: 한국어 원문 설명을 {language}로 번역한다. 한글을 남기지 않는다. 원문에 없는 재료나 효능을 보태지 않는다.
  원문 설명이 없으면(null) null 로 둔다.
- 이미 정해진 영문명(name_en)이 있으면 그 뜻에 맞춘다.
- 한국어로만 통하는 이름은 소리 나는 대로 적고 괄호로 뜻을 짧게 붙인다.
- 용어: {glossary}
JSON 객체로만 답한다. 키는 받은 한글 메뉴명 그대로, 값은 {{"name": ..., "description": ...}}.
"""


def main():
    products = json.loads((DATA_DIR / "raw/official/products.json").read_text(encoding="utf-8"))["data"]
    menus = {p["name"]: p["description"] for p in reversed(products)}  # 같은 이름이 여러 번 있으면 앞의 것
    names = list(dict.fromkeys(p["name"] for p in products))
    saved = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {
        "_note": "공식 사이트에 외국어 메뉴가 없어 모델로 번역한 값이다. 눈에 띄는 오역만 손으로 고쳤고 전체를 검수하지는 않았다(verified=false). "
                 "직접 고친 값은 scripts/translate_menu.py 를 다시 돌려도 유지된다.",
        "verified": False, "source": f"scripts/translate_menu.py ({CHAT_MODEL})", "menus": {}}
    table = saved["menus"]
    client = OpenAI()
    for lang, language in LANGUAGES.items():
        def done(name):
            entry = table.get(name, {}).get(lang, {})
            return bool(entry.get("name")) and (not menus[name] or bool(entry.get("description")))

        for name in names:  # 한글이 남은 번역은 번역되지 않은 것으로 보고 다시 한다
            entry = table.get(name, {}).get(lang, {})
            for field in ("name", "description"):
                if entry.get(field) and HANGUL.search(entry[field]) and not (lang == "en" and field == "name"):
                    entry[field] = None

        todo = [n for n in names if not done(n)]
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            payload = [{"name": n, "name_en": table.get(n, {}).get("en", {}).get("name"), "description": menus[n]} for n in batch]
            response = client.chat.completions.create(
                model=CHAT_MODEL, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": GUIDE.format(language=language, glossary=GLOSSARY[lang])},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
            translated = json.loads(response.choices[0].message.content)
            for name in batch:
                got = translated.get(name)
                if not isinstance(got, dict):
                    continue
                entry = table.setdefault(name, {}).setdefault(lang, {})
                if not entry.get("name") and isinstance(got.get("name"), str):  # 손으로 고친 이름은 덮어쓰지 않는다
                    entry["name"] = got["name"].strip()
                if not entry.get("description"):
                    entry["description"] = got["description"].strip() if menus[name] and isinstance(got.get("description"), str) else None
            print(f"{language}: {min(i + BATCH, len(todo))}/{len(todo)}")
    saved["menus"] = {n: {lang: table[n][lang] for lang in LANGUAGES if lang in table[n]} for n in names if n in table}
    OUT.write_text(json.dumps(saved, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    missing = [(n, lang) for n in names for lang in LANGUAGES if not saved["menus"].get(n, {}).get(lang, {}).get("name")]
    print(f"-> {OUT.relative_to(DATA_DIR.parent)} (메뉴 {len(saved['menus'])}개 × {len(LANGUAGES)}개 언어)" + (f", 빠진 것: {missing}" if missing else ""))


if __name__ == "__main__":
    main()
