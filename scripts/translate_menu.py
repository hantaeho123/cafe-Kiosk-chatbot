"""메뉴 영문명을 만든다: 공식 메뉴명(한글) → data/curated/menu_names_en.json.

    python scripts/translate_menu.py        # 아직 영문명이 없는 메뉴만 번역해 덧붙인다
    python scripts/build_dataset.py         # 그다음 kiosk/, rag/ 에 반영

공식 사이트에 영문 메뉴명이 없어서 모델로 번역한 값이다(검수 전). 파일을 직접 고치면 그 값이 유지된다.
외국어 화면의 메뉴판과 메뉴 문서의 '영문명'에 쓰인다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from app.config import CHAT_MODEL, DATA_DIR  # noqa: E402

OUT = DATA_DIR / "curated" / "menu_names_en.json"
BATCH = 60
GUIDE = """\
버블티 카페 '공차(Gong Cha)' 한국 매장의 메뉴명을 영어 메뉴판에 쓸 이름으로 옮긴다.
- 메뉴판에 적는 이름답게 짧게, 낱말 첫 글자는 대문자로 쓴다. 설명을 덧붙이지 않는다.
- 이름에 든 재료와 토핑을 빠뜨리거나 보태지 않는다. '+' 는 'with' 로 쓴다.
- 용어: 펄=Pearls, 미니펄=Mini Pearls, 화이트펄=White Pearls, 코코넛=Coconut Jelly, 알로에=Aloe, 밀크폼=Milk Foam,
  치즈폼=Cheese Foam, 쥬얼리=Jewelry Pearls, 크러쉬=Crush, 스무디=Smoothie, 밀크티=Milk Tea, 요구르트=Yogurt,
  그릭요거티=Greek Yogurt Tea, 저당=Low Sugar, 제로=Zero Sugar, 시그니처=Signature, 브라운슈가=Brown Sugar,
  제주 그린=Jeju Green Tea, 말차=Matcha, 얼그레이=Earl Grey, 우롱=Oolong, 자스민=Jasmine, 블랙=Black,
  히비스커스=Hibiscus, 납작복숭아=Flat Peach, 공차슈페너=Gong Cha Einspanner, 샷=Espresso Shot, 쑥=Mugwort, 밤=Chestnut,
  오미자=Omija (Schisandra), 미숫가루=Misugaru (Roasted Grain), 펄볶이=Pearl-bokki (Tteokbokki-Style Pearls).
- 한국어로만 통하는 이름은 소리 나는 대로 적고 괄호로 뜻을 짧게 붙인다.
JSON 객체로만 답한다. 키는 받은 한글 메뉴명 그대로, 값은 영문명.
"""


def main():
    products = json.loads((DATA_DIR / "raw/official/products.json").read_text(encoding="utf-8"))["data"]
    names = list(dict.fromkeys(p["name"] for p in products))
    saved = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {
        "_note": "공식 사이트에 영문 메뉴명이 없어 모델로 번역한 값이다. 검수하지 않았다(verified=false). "
                 "직접 고친 값은 scripts/translate_menu.py 를 다시 돌려도 유지된다.",
        "verified": False, "source": f"scripts/translate_menu.py ({CHAT_MODEL})", "names": {}}
    todo = [n for n in names if n not in saved["names"]]
    client = OpenAI()
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        response = client.chat.completions.create(
            model=CHAT_MODEL, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": GUIDE}, {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}])
        translated = json.loads(response.choices[0].message.content)
        for name in batch:
            if isinstance(translated.get(name), str) and translated[name].strip():
                saved["names"][name] = translated[name].strip()
        print(f"{min(i + BATCH, len(todo))}/{len(todo)} 번역")
    saved["names"] = {n: saved["names"][n] for n in names if n in saved["names"]}  # 메뉴 순서대로, 없어진 메뉴는 뺀다
    OUT.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    missing = [n for n in names if n not in saved["names"]]
    print(f"-> {OUT.relative_to(DATA_DIR.parent)} (영문명 {len(saved['names'])}/{len(names)}개)" + (f", 빠진 메뉴: {missing}" if missing else ""))


if __name__ == "__main__":
    main()
