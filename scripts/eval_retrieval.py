"""검색 품질 점검: HTI 의 task 별 질문을 검색해 기대한 문서가 상위 k 개 안에 드는지 본다.

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

# (HTI 번호, 질문, 기대 문서). 기대 문서는 문서 id 이거나 메뉴명이며, 하나라도 찾으면 성공이다.
# 기대 문서가 비어 있으면 카페와 상관없는 질문이다(유사도가 얼마나 낮게 나오는지 본다).
CASES = [
    ("2.2", "매장 몇 시까지 해요?", ["store:info"]),
    ("2.3", "주문은 어떻게 하는 거예요?", ["guide:order"]),
    ("3.1", "신메뉴 뭐 있어요?", ["recommend:season"]),
    ("3.2", "아메리카노 중 사이즈 얼마인가요?", ["아메리카노"]),
    ("3.3", "밀크티 종류 보여 주세요", ["category:밀크티"]),
    ("3.3", "디저트는 뭐가 있어요?", ["recommend:dessert", "category:베이커리", "category:스낵", "category:아이스크림"]),
    ("4.1", "달콤한 음료 추천해 주세요", ["recommend:flavor-달콤한"]),
    ("4.1", "카페인 없는 음료 있어요?", ["recommend:no-caffeine"]),
    ("4.1", "칼로리 낮은 거 추천해줘", ["recommend:low-kcal"]),
    ("4.1", "우유 안 들어간 음료 뭐 있어요?", ["recommend:no-milk"]),
    ("4.1", "따뜻하게 마실 수 있는 거 추천해줘", ["recommend:hot"]),
    ("4.2", "음료랑 같이 먹을 만한 거 있어요?", ["recommend:dessert"]),
    ("4.3", "요즘 제일 잘 나가는 메뉴가 뭐예요?", ["recommend:popular", "recommend:best"]),
    ("5.1", "타로 밀크티 칼로리 알려줘", ["타로 밀크티"]),
    ("5.1", "블랙 밀크티에 알레르기 성분 있어요?", ["블랙 밀크티"]),
    ("5.2.2", "샷 추가하면 얼마예요?", ["option:topping:extra_shot", "option:toppings"]),
    ("5.2.3", "라떼에 바닐라 시럽 추가할 수 있나요?", ["바닐라 카페라떼", "카페라떼"]),
    ("5.2.5", "당도 조절 되나요?", ["option:sugar"]),
    ("5.2.5", "얼음 적게 넣을 수 있어요?", ["option:ice"]),
    ("5.2.6", "펄 추가하면 얼마예요?", ["option:topping:pearl", "option:toppings"]),
    ("5.2.7", "사이즈는 뭐가 있어요?", ["option:size"]),
    ("7.4", "SKT 멤버십 할인 되나요?", ["policy:skt"]),
    ("7.4", "스탬프 적립은 어떻게 해요?", ["policy:membership"]),
    ("7.5", "카카오페이로 결제할 수 있어요?", ["policy:kakaopay", "guide:payment"]),
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
        print(f"[{hti:>5}] {mark:<12} 최고 유사도 {top:.2f}  {question}")
        if verbose or (expected and rank is None):
            for h in hits:
                print(f"          {h.score:.2f} {'[이름]' if h.match == 'name' else '      '} {h.doc['id']}  {h.doc['title']}")
    print(f"\n기대 문서를 찾은 질문: {passed}/{total}")


if __name__ == "__main__":
    main()
