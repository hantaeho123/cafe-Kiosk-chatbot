"""하이브리드 검색: 메뉴명 일치 + 임베딩 유사도. 강의의 RETRIEVE 단계다."""
from dataclasses import dataclass

from app.config import RAG_MIN_SCORE, RAG_TOP_K

MAX_NAME_HITS = 3
CONTEXT_EXTRA = 3  # 직전 대화에서 더 가져오는 문서 수


@dataclass
class Hit:
    doc: dict
    score: float  # 코사인 유사도 (1 에 가까울수록 비슷함)
    match: str  # "name": 메뉴명이 그대로 있음 / "vector": 임베딩 유사도 / "context": 직전 대화에서 가져옴


class Retriever:
    def __init__(self, store, embedder, catalog, top_k=RAG_TOP_K, min_score=RAG_MIN_SCORE):
        self.store = store
        self.embedder = embedder
        self.catalog = catalog
        self.top_k = top_k
        self.min_score = min_score

    def search(self, query, context=None, k=None, doc_types=None):
        """질의로 문서 k 개를 찾는다.

        1) 질의에 메뉴명이 그대로 있으면 그 메뉴 문서를 먼저 넣는다. '아메리카노' 처럼 이름이 다른 메뉴에도
           들어 있으면 임베딩만으로는 '얼그레이 아메리카노' 와 순위가 뒤바뀔 수 있어서다.
        2) 남은 자리는 임베딩 유사도 순으로 채운다.
        3) context(직전 대화)를 주면 거기서 문서를 몇 개 더 붙인다. "L로 주세요", "그건 얼마예요?" 같은
           짧은 후속 발화는 그것만으로는 무엇을 찾을지 알 수 없어서다. 질의 자체의 결과는 줄이지 않는다.
        """
        k = k or self.top_k
        docs = self.store.docs
        rankings = self.store.rank(self.embedder.embed([query] + ([f"{context} {query}"] if context else [])))
        score = dict(rankings[0])
        hits, picked = [], set()

        def take(pos, match):
            if pos is None or pos in picked or (doc_types and docs[pos]["type"] not in doc_types):
                return False
            picked.add(pos)
            hits.append(Hit(docs[pos], score[pos], match))
            return True

        def menu_positions(text):
            return [self.store.position.get(f"menu:{m['id']}") for m in self.catalog.find_mentions(text, limit=MAX_NAME_HITS)]

        for pos in menu_positions(query):
            take(pos, "name")
        for pos, value in rankings[0]:
            if len(hits) >= k or value < self.min_score:
                break
            take(pos, "vector")
        if context:
            limit = len(hits) + CONTEXT_EXTRA
            for pos in menu_positions(context):
                if len(hits) < limit:
                    take(pos, "context")
            if len(hits) == limit - CONTEXT_EXTRA:  # 직전 대화에 메뉴명이 없으면 합친 질의의 유사도로 찾는다
                for pos, value in rankings[1]:
                    if len(hits) >= limit - 1 or value < self.min_score:
                        break
                    take(pos, "context")
        return hits
