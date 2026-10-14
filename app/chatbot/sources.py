"""모델이 답변 끝에 표시한 참고 문서 ID를 본문 스트림과 분리한다.

ID가 검색 결과에 있는지 검증할 뿐, 모델 내부의 기여도나 사실성을 증명하지는 않는다.
"""
import json


class SourceFooter:
    OPEN = "<answer_sources>"
    CLOSE = "</answer_sources>"

    def __init__(self):
        self.pending = ""
        self.footer = None

    def feed(self, text):
        if self.footer is not None:
            self.footer += text
            return ""
        self.pending += text
        if self.OPEN in self.pending:
            visible, self.footer = self.pending.split(self.OPEN, 1)
            self.pending = ""
            return visible
        # 태그가 여러 스트림 조각에 걸쳐 와도 화면에 잠깐 노출하지 않는다.
        keep = next((n for n in range(len(self.OPEN) - 1, 0, -1)
                     if self.pending.endswith(self.OPEN[:n])), 0)
        visible = self.pending[:-keep] if keep else self.pending
        self.pending = self.pending[-keep:] if keep else ""
        return visible

    def finish(self):
        if self.footer is None:
            # 태그를 쓰다가 끊긴 응답도 내부 표식을 노출하지 않는다.
            if self.pending == "<":
                return self.pending, {"status": "missing", "ids": []}
            return "", {"status": "invalid" if self.pending else "missing", "ids": []}
        body, closing, tail = self.footer.partition(self.CLOSE)
        try:
            ids = json.loads(body)
            if not closing or tail.strip() or not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError("잘못된 문서 ID 목록")
            return "", {"status": "reported", "ids": list(dict.fromkeys(ids))}
        except (ValueError, TypeError):
            return "", {"status": "invalid", "ids": []}


def source_report(attribution, hits):
    available = {h.doc["id"] for h in hits}
    ids = attribution.get("ids", [])
    valid = [i for i in ids if i in available]
    return {"type": "attribution", "ids": valid,
            "status": "invalid" if len(valid) != len(ids) else attribution.get("status", "missing")}
