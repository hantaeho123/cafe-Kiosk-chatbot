"""손님 한 명(브라우저 탭 하나)의 대화·주문 상태. 시연용이라 서버 메모리에만 둔다."""
import threading

from app.i18n import LANGUAGES
from app.order import Order


class Session:
    def __init__(self, catalog, language="ko"):
        self.catalog = catalog
        self.language = language
        self.lock = threading.Lock()  # 한 세션에서 답변을 동시에 두 개 만들지 않도록
        self.new_order()

    def new_order(self):
        """다음 손님을 위해 주문과 대화를 비운다. 화면 언어는 그대로 둔다."""
        self.order = Order(self.catalog)
        self.history = []  # [{"role": "user" | "assistant", "content": str}]
        self.staff_call = None

    def set_language(self, language):
        if language not in LANGUAGES:
            raise ValueError(f"지원하지 않는 언어: {language}")
        self.language = language

    def state(self):
        return {"language": self.language, "order": self.order.to_dict(), "staff_call": self.staff_call}


class SessionStore:
    def __init__(self, catalog):
        self.catalog = catalog
        self._sessions = {}
        self._lock = threading.Lock()

    def get(self, session_id):
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Session(self.catalog)
            return self._sessions[session_id]
