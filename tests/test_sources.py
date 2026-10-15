import pytest

from app.chatbot.sources import SourceFooter, source_report
from app.rag.retriever import Hit


def parse(parts):
    footer = SourceFooter()
    visible = "".join(footer.feed(part) for part in parts)
    tail, report = footer.finish()
    return visible + tail, report


def test_footer_never_leaks_at_any_stream_boundary():
    answer = '따뜻한 타로 밀크티를 추천해요.'
    raw = answer + '<answer_sources>["menu:taro","menu:taro"]</answer_sources>'
    for cut in range(len(raw) + 1):
        visible, report = parse([raw[:cut], raw[cut:]])
        assert visible == answer
        assert report == {"status": "reported", "ids": ["menu:taro"]}
    assert parse(list(raw))[0] == answer


@pytest.mark.parametrize('footer', [
    '<answer_sources>not json</answer_sources>',
    '<answer_sources>{"ids": []}</answer_sources>',
    '<answer_sources>[null]</answer_sources>',
    '<answer_sources>["menu:taro"]',
    '<answer_sour',
])
def test_bad_or_truncated_metadata_does_not_become_answer_text(footer):
    visible, report = parse(list('안녕하세요.' + footer))
    assert visible == '안녕하세요.'
    assert report == {"status": "invalid", "ids": []}


def test_missing_and_explicitly_empty_references_are_distinct():
    assert parse(['안녕하세요.']) == ('안녕하세요.', {"status": "missing", "ids": []})
    assert parse(['1 < 2, 2 <']) == ('1 < 2, 2 <', {"status": "missing", "ids": []})
    assert parse(['안녕하세요.<answer_sources>[]</answer_sources>']) == (
        '안녕하세요.', {"status": "reported", "ids": []})


def test_unknown_ids_are_excluded_without_ranking_by_similarity():
    hits = [Hit({"id": "guide:voucher"}, .30, "vector"),
            Hit({"id": "menu:taro"}, .20, "context"), Hit({"id": "menu:black"}, .19, "context")]
    report = source_report({"status": "reported", "ids": ["menu:black", "menu:taro", "invented"]}, hits)
    assert report == {"type": "attribution", "status": "invalid", "ids": ["menu:black", "menu:taro"]}
