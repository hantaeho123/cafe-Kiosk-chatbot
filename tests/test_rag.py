from app.rag.store import Embedder, VectorStore, embedding_text, load_documents, open_store
from tests.conftest import FakeClient


def test_documents_cover_what_the_kiosk_must_answer():
    docs = load_documents()
    types = {d["type"] for d in docs}
    assert {"menu", "recommendation", "option", "category", "policy", "guide", "store", "knowledge"} <= types
    ids = {d["id"] for d in docs}
    assert {"recommend:season", "recommend:dessert", "recommend:popular", "option:toppings", "store:info"} <= ids


def test_embedding_text_drops_nutrition_rows_only():
    doc = next(d for d in load_documents() if d["title"] == "초콜렛 밀크티")
    text = embedding_text(doc)
    assert "가격: L 사이즈 5,000원" in text and "kcal" not in text
    assert "kcal" in doc["text"]  # 모델에게 주는 본문에는 영양정보가 남아 있다


def test_index_is_saved_and_reused(tmp_path):
    docs = load_documents()[:20]
    embedder = Embedder(FakeClient())
    store, built = open_store(embedder, docs, tmp_path)
    assert built and store.index.ntotal == 20
    _, built_again = open_store(embedder, docs, tmp_path)
    assert not built_again
    # 문서가 바뀌면 저장해 둔 인덱스를 쓰지 않는다
    changed = [{**docs[0], "text": docs[0]["text"] + " 변경"}, *docs[1:]]
    assert VectorStore.load(changed, embedder.model, tmp_path) is None


def test_menu_name_in_query_comes_first(retriever):
    hits = retriever.search("아메리카노 중 사이즈 얼마인가요?")
    assert hits[0].doc["title"] == "아메리카노" and hits[0].match == "name"
    assert len(hits) == 6 and len({h.doc["id"] for h in hits}) == 6


def test_compound_query_gets_both_menus(retriever):
    titles = [h.doc["title"] for h in retriever.search("블랙 밀크티랑 타로 밀크티 중에 뭐가 더 달아요?")]
    assert titles[:2] == ["블랙 밀크티", "타로 밀크티"]


def test_doc_type_filter(retriever):
    hits = retriever.search("펄 추가 가격", doc_types=["option"])
    assert hits and all(h.doc["type"] == "option" for h in hits)


def test_follow_up_adds_documents_from_the_previous_turn(retriever):
    alone = retriever.search("L로 주세요")
    hits = retriever.search("L로 주세요", context="**타로 밀크티**는 L 4,900원이에요. 타로 밀크티 한 잔 주세요")
    assert [h.doc["id"] for h in hits[:6]] == [h.doc["id"] for h in alone]  # 질의 자체의 결과는 그대로다
    assert [(h.doc["title"], h.match) for h in hits[6:]] == [("타로 밀크티", "context")]


def test_context_without_menu_names_falls_back_to_similarity(retriever):
    hits = retriever.search("그건 뭐예요?", context="멤버십 스탬프 적립은 어떻게 해요?")
    assert len(hits) == 8 and [h.match for h in hits[6:]] == ["context", "context"]


def test_min_score_drops_weak_matches(catalog, store):
    from app.rag.retriever import Retriever
    strict = Retriever(store, Embedder(FakeClient()), catalog, top_k=6, min_score=0.99)
    assert strict.search("오늘 날씨 어때?") == []


def test_list_documents_are_embedded_by_topic_only():
    doc = next(d for d in load_documents() if d["id"] == "recommend:flavor-달콤한")
    assert embedding_text(doc) == "달콤한 음료 추천\n'달콤한' 맛 특징을 가진 음료입니다."
    assert "초콜렛 밀크티" in doc["text"]  # 메뉴 목록은 모델에게 주는 본문에 남아 있다
