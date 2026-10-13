"""RAG 인덱스: 문서(documents.jsonl) → 임베딩 → FAISS 저장·검색.

강의의 LOAD → SPLIT → EMBED → STORE 단계다. 문서를 만들 때(scripts/build_dataset.py) 이미
'메뉴 1개 = 문서 1개'처럼 검색 단위로 나눠 두었기 때문에 SPLIT(청크 분할)은 따로 하지 않는다.
"""
import hashlib
import json

import faiss
import numpy as np

from app.config import DOCS_PATH, EMBED_MODEL, INDEX_DIR

BATCH = 100  # 임베딩 API 한 번에 보내는 문서 수


def load_documents(path=DOCS_PATH):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def embedding_text(doc):
    """임베딩에 넣는 글. 모델에게 주는 본문(text)은 그대로 두고, 검색에 쓰는 글만 주제가 드러나게 줄인다.

    - 메뉴 문서: 영양정보 숫자 행을 뺀다.
    - 추천·메뉴판 문서: 제목과 첫 줄(무엇의 목록인지)만 쓴다. 메뉴 이름과 가격이 수십 줄 이어지면
      '달콤한 음료 추천' 같은 주제가 묻혀서, 목록이 긴 문서일수록 검색에서 밀린다.
    """
    lines = doc["text"].split("\n")
    if doc["type"] == "menu":
        lines = [l for l in lines if not l.startswith((" - ", "영양정보"))]
    elif doc["type"] in ("recommendation", "category"):
        lines = lines[:1]
    return doc["title"] + "\n" + "\n".join(lines)


class Embedder:
    def __init__(self, client, model=EMBED_MODEL):
        self.client = client
        self.model = model

    def embed(self, texts):
        """글 목록 → 길이를 1로 맞춘 벡터 행렬. 길이가 1이면 내적이 곧 코사인 유사도다."""
        vectors = []
        for i in range(0, len(texts), BATCH):
            response = self.client.embeddings.create(model=self.model, input=texts[i:i + BATCH])
            vectors += [d.embedding for d in response.data]
        matrix = np.array(vectors, dtype="float32")
        faiss.normalize_L2(matrix)
        return matrix


class VectorStore:
    def __init__(self, docs, index):
        self.docs = docs
        self.index = index
        self.position = {d["id"]: i for i, d in enumerate(docs)}

    @staticmethod
    def fingerprint(docs, model):
        """문서나 임베딩 모델이 바뀌면 값이 달라진다. 저장해 둔 인덱스를 다시 써도 되는지 확인하는 데 쓴다."""
        h = hashlib.sha256(model.encode())
        for d in docs:
            h.update(d["id"].encode())
            h.update(embedding_text(d).encode())
        return h.hexdigest()

    @classmethod
    def build(cls, docs, embedder):
        vectors = embedder.embed([embedding_text(d) for d in docs])
        index = faiss.IndexFlatIP(vectors.shape[1])  # 내적(= 코사인 유사도) 정확 검색
        index.add(vectors)
        return cls(docs, index)

    def save(self, model, folder=INDEX_DIR):
        folder.mkdir(parents=True, exist_ok=True)
        # faiss.write_index 는 윈도우에서 한글 경로를 못 열어서 바이트로 바꿔 직접 쓴다.
        (folder / "index.faiss").write_bytes(faiss.serialize_index(self.index).tobytes())
        (folder / "manifest.json").write_text(json.dumps({
            "embed_model": model,
            "count": len(self.docs),
            "dimension": self.index.d,
            "fingerprint": self.fingerprint(self.docs, model),
        }, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, docs, model, folder=INDEX_DIR):
        """저장된 인덱스를 읽는다. 없거나 문서·모델이 달라졌으면 None."""
        try:
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            if manifest["fingerprint"] != cls.fingerprint(docs, model):
                return None
            data = np.frombuffer((folder / "index.faiss").read_bytes(), dtype="uint8")
            return cls(docs, faiss.deserialize_index(data))
        except (OSError, KeyError, ValueError):
            return None

    def rank(self, vectors):
        """질의 벡터마다 모든 문서를 유사도 높은 순으로 줄 세운다. [(문서 위치, 코사인 유사도), ...]"""
        scores, positions = self.index.search(vectors, self.index.ntotal)
        return [list(zip(p.tolist(), s.tolist())) for p, s in zip(positions, scores)]


def open_store(embedder, docs=None, folder=INDEX_DIR):
    """저장된 인덱스가 최신이면 읽고, 아니면 임베딩을 새로 만들어 저장한다. (인덱스, 새로 만들었는지)"""
    docs = docs or load_documents()
    store = VectorStore.load(docs, embedder.model, folder)
    if store:
        return store, False
    store = VectorStore.build(docs, embedder)
    store.save(embedder.model, folder)
    return store, True
