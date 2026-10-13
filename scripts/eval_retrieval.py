"""검색 품질 점검: 최종 HTI 의 과업별 질문을 검색해 기대한 문서가 상위 k 개 안에 드는지 본다.

    python scripts/eval_retrieval.py          # 요약 표
    python scripts/eval_retrieval.py -v       # 질문마다 상위 문서와 유사도까지

RAG_TOP_K, RAG_MIN_SCORE 를 정할 때 여기 나오는 유사도를 참고한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from app.catalog import Catalog  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402
from app.rag.store import Embedder, open_store  # noqa: E402

# (HTI 과업, 질문, 기대 문서). 기대 문서는 문서 id 이거나 메뉴명이며, 하나라도 찾으면 성공이다.
# 기대 문서가 비어 있으면 카페와 상관없는 질문이다(유사도가 얼마나 낮게 나오는지 본다).
# 예산·인원 추천, 메뉴 담기·바꾸기, 되돌리기는 검색이 아니라 도구(find_menus, calculate_price, add_to_cart 등)가 처리해서 여기에 없다.
CASES = [
    ("메뉴 찾기", "신메뉴 뭐 있어요?", ["recommend:season"]),
    ("메뉴 찾기", "밀크티 종류 보여 주세요", ["category:밀크티"]),
    ("메뉴 찾기", "디저트는 뭐가 있어요?", ["recommend:dessert", "category:베이커리", "category:스낵", "category:아이스크림"]),
    ("메뉴 찾기", "요즘 제일 잘 나가는 메뉴가 뭐예요?", ["recommend:popular", "recommend:best"]),
    ("취향 추천", "달콤한 음료 추천해 주세요", ["recommend:flavor-달콤한"]),
    ("취향 추천", "카페인 없는 음료 있어요?", ["recommend:no-caffeine"]),
    ("취향 추천", "칼로리 낮은 거 추천해줘", ["recommend:low-kcal"]),
    ("취향 추천", "우유 안 들어간 음료 뭐 있어요?", ["recommend:no-milk"]),
    ("취향 추천", "따뜻하게 마실 수 있는 거 추천해줘", ["recommend:hot"]),
    ("취향 추천", "음료랑 같이 먹을 만한 거 있어요?", ["recommend:dessert"]),
    ("메뉴 묻기", "아메리카노 중 사이즈 얼마인가요?", ["아메리카노"]),
    ("메뉴 묻기", "타로 밀크티 칼로리 알려줘", ["타로 밀크티"]),
    ("알레르기", "블랙 밀크티에 알레르기 성분 있어요?", ["블랙 밀크티"]),
    ("알레르기", "우유 알레르기가 있는데 마셔도 괜찮을까요?", ["guide:allergy"]),
    ("메뉴 비교", "블랙 밀크티랑 타로 밀크티 중에 뭐가 더 달아요?", ["블랙 밀크티", "타로 밀크티"]),
    ("옵션 뜻", "당도 30%가 얼마나 달아?", ["option:sugar"]),
    ("옵션 뜻", "J 사이즈가 뭐예요?", ["option:size"]),
    ("옵션 뜻", "펄이 뭐예요?", ["option:topping:pearl", "option:toppings"]),
    ("옵션 가능", "얼음 없이 돼요?", ["option:ice"]),
    ("옵션 가능", "라떼에 바닐라 시럽 추가할 수 있나요?", ["바닐라 카페라떼", "카페라떼"]),
    ("옵션 가능", "샷 추가하면 얼마예요?", ["option:topping:extra_shot", "option:toppings"]),
    ("옵션 가능", "개인 텀블러에 담아 주실 수 있어요?", ["option:cup"]),
    ("요청 사항", "요청 사항은 어떻게 남겨요?", ["guide:requests"]),
    ("할인·적립", "SKT 멤버십 할인 되나요?", ["policy:skt", "guide:benefits"]),
    ("할인·적립", "스탬프 적립은 어떻게 해요?", ["guide:benefits", "policy:membership"]),
    ("할인·적립", "쿠폰은 어디서 써요?", ["guide:benefits"]),
    ("교환권", "선물 받은 교환권은 어떻게 써요?", ["guide:voucher"]),
    ("결제", "카카오페이로 결제할 수 있어요?", ["policy:kakaopay", "guide:payment"]),
    ("결제", "카드는 어디에 꽂아요?", ["guide:payment"]),
    ("결제", "결제가 안 돼요", ["guide:payment"]),
    ("결제", "금액권으로 결제할 수 있어요?", ["guide:voucher", "guide:payment"]),
    ("사용법", "주문은 어떻게 하는 거예요?", ["guide:order", "guide:kiosk"]),
    ("사용법", "이 키오스크 어떻게 써요?", ["guide:kiosk"]),
    ("사용법", "시간이 모자라요. 연장할 수 있어요?", ["guide:kiosk"]),
    ("직원 호출", "직원은 어떻게 불러요?", ["guide:staff"]),
    ("매장", "매장 몇 시까지 해요?", ["store:info"]),
    ("복합", "아메리카노랑 카푸치노 중에 뭐가 더 진해요? 그리고 가격은 어떻게 되나요?", ["아메리카노"]),
    ("복합", "얼그레이티가 뭐예요? 그리고 따뜻하게도 되나요?", ["얼그레이티", "knowledge:tea:얼그레이티"]),
    ("범위 밖", "오늘 날씨 어때?", []),
    ("범위 밖", "파이썬으로 피보나치 짜줘", []),
]


def main():
    verbose = "-v" in sys.argv
    catalog = Catalog()
    embedder = Embedder(OpenAI())
    store, _ = open_store(embedder)
    retriever = Retriever(store, embedder, catalog, min_score=0)

    def doc_id(expected):
        if ":" in expected:
            return expected
        menu, _ = catalog.resolve(expected)
        return f"menu:{menu['id']}"

    passed = total = 0
    print(f"검색 문서 수 k = {retriever.top_k}, 문서 {len(store.docs)}개\n")
    for hti, question, expected in CASES:
        hits = retriever.search(question)
        ids = [h.doc["id"] for h in hits]
        wanted = [doc_id(e) for e in expected]
        rank = min((ids.index(w) + 1 for w in wanted if w in ids), default=None)
        top = hits[0].score if hits else 0.0
        if expected:
            total += 1
            passed += rank is not None
            mark = f"{'O' if rank else 'X'} 순위 {rank or '-'}"
        else:
            mark = "- (범위 밖)"
        print(f"[{hti}] {mark:<12} 최고 유사도 {top:.2f}  {question}")
        if verbose or (expected and rank is None):
            for h in hits:
                print(f"          {h.score:.2f} {'[이름]' if h.match == 'name' else '      '} {h.doc['id']}  {h.doc['title']}")
    print(f"\n기대 문서를 찾은 질문: {passed}/{total}")


if __name__ == "__main__":
    main()
