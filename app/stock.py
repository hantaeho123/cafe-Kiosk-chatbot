"""품절 상태. RAG 문서가 아니라 키오스크가 그때그때 넘기는 실시간 값이다. 직원 화면(/staff)에서 바꾼다."""
import json
import threading

from app.config import KIOSK_DIR


class Stock:
    def __init__(self, catalog, names=None):
        """names 를 주지 않으면 data/kiosk/stock.json 의 초깃값으로 시작한다."""
        if names is None:
            names = json.loads((KIOSK_DIR / "stock.json").read_text(encoding="utf-8"))["sold_out"]
        self.catalog = catalog
        self.version = 0  # 바뀔 때마다 올린다. 화면이 메뉴판을 다시 그릴지 정하는 데 쓴다.
        self._lock = threading.Lock()
        self._sold_out = {m["id"] for m in catalog.items if m["name"] in set(names)}

    def is_sold_out(self, menu_id):
        return menu_id in self._sold_out

    def set(self, menu_id, sold_out):
        if menu_id not in self.catalog.by_id:
            raise KeyError(menu_id)
        with self._lock:
            before = len(self._sold_out)
            (self._sold_out.add if sold_out else self._sold_out.discard)(menu_id)
            self.version += len(self._sold_out) != before

    def ids(self):
        return sorted(self._sold_out)

    def names(self):
        return [m["name"] for m in self.catalog.items if m["id"] in self._sold_out]

    def to_dict(self):
        return {"version": self.version, "sold_out": self.ids()}
