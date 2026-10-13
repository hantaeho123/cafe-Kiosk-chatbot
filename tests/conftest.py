"""테스트용 가짜 OpenAI 클라이언트. API 키 없이 검색·대화 흐름을 확인한다."""
import json
import zlib
from types import SimpleNamespace as NS

import pytest

from app.catalog import Catalog
from app.rag.retriever import Retriever
from app.rag.store import Embedder, VectorStore, load_documents


def fake_vector(text, size=512):
    """글자 두 개 묶음을 세어 만든 벡터. 같은 낱말이 많이 겹칠수록 가깝게 나온다."""
    vector = [0.0] * size
    compact = text.replace(" ", "")
    for i in range(len(compact) - 1):
        vector[zlib.crc32(compact[i:i + 2].encode()) % size] += 1.0
    return vector


def text_chunks(text):
    """모델이 글로 답할 때의 스트림 조각."""
    return [NS(choices=[NS(delta=NS(content=piece, tool_calls=None))]) for piece in (text[:3], text[3:]) if piece]


def tool_chunks(*calls):
    """모델이 도구를 부를 때의 스트림 조각. 실제처럼 인자를 두 조각으로 나눠 보낸다."""
    chunks = []
    for index, (name, args) in enumerate(calls):
        raw = json.dumps(args, ensure_ascii=False)
        half = len(raw) // 2
        chunks.append(NS(choices=[NS(delta=NS(content=None, tool_calls=[
            NS(index=index, id=f"call_{index}", function=NS(name=name, arguments=raw[:half]))]))]))
        chunks.append(NS(choices=[NS(delta=NS(content=None, tool_calls=[
            NS(index=index, id=None, function=NS(name=None, arguments=raw[half:]))]))]))
    return chunks + [NS(choices=[])]  # 실제 스트림 끝에 오는 사용량 조각


class FakeClient:
    """script 에 넣어 둔 응답을 차례로 돌려준다. 받은 요청은 requests 에 남긴다."""

    def __init__(self, script=()):
        self.script = list(script)
        self.requests = []
        self.embeddings = NS(create=self._embed)
        self.moderations = NS(create=self._moderate)
        self.chat = NS(completions=NS(create=self._complete))

    def _embed(self, model, input):
        return NS(data=[NS(embedding=fake_vector(text)) for text in input])

    def _moderate(self, model, input):
        return NS(results=[NS(flagged="폭탄" in input)])

    def _complete(self, **params):
        # 에이전트는 같은 messages 목록에 도구 결과를 계속 덧붙이므로, 요청할 때의 모습을 따로 남겨 둔다.
        self.requests.append({**params, "messages": list(params["messages"])})
        if not self.script:
            raise RuntimeError("준비한 응답이 없습니다")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return iter(step)


@pytest.fixture(scope="session")
def catalog():
    return Catalog()


@pytest.fixture(scope="session")
def store():
    return VectorStore.build(load_documents(), Embedder(FakeClient()))


@pytest.fixture
def retriever(catalog, store):
    return Retriever(store, Embedder(FakeClient()), catalog, top_k=6, min_score=0)
