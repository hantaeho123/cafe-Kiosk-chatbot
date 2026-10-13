"""RAG 인덱스를 만든다: data/rag/documents.jsonl → OpenAI 임베딩 → FAISS (data/rag/index/).

    python scripts/build_index.py            # 문서나 임베딩 모델이 바뀌었을 때만 다시 만든다
    python scripts/build_index.py --force    # 무조건 다시 만든다

서버(python -m app)도 시작할 때 인덱스가 없거나 오래됐으면 알아서 만든다.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from app.config import INDEX_DIR  # noqa: E402
from app.rag.store import Embedder, VectorStore, load_documents, open_store  # noqa: E402


def main():
    docs = load_documents()
    embedder = Embedder(OpenAI())
    started = time.time()
    if "--force" in sys.argv:
        store = VectorStore.build(docs, embedder)
        store.save(embedder.model)
        built = True
    else:
        store, built = open_store(embedder, docs)
    if built:
        print(f"문서 {len(docs)}개를 {embedder.model} 로 임베딩했습니다 ({store.index.d}차원, {time.time() - started:.1f}초)")
        print(f"-> {INDEX_DIR}")
    else:
        print(f"저장된 인덱스가 최신입니다 (문서 {len(docs)}개). 다시 만들려면 --force 를 붙이세요.")


if __name__ == "__main__":
    main()
