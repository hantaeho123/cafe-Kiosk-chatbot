"""손님 한 명(브라우저 탭 하나)의 대화·주문 상태. 시연용이라 서버 메모리에만 둔다."""
import threading

from app.i18n import LANGUAGES
from app.order import Order, OrderError, change_text, changes, won

UNDO_DEPTH = 10


class Session:
    def __init__(self, catalog, stock=None, board=None, language="ko"):
        self.catalog = catalog
        self.stock = stock
        self.board = board
        self.language = language
        self.lock = threading.Lock()  # 한 세션에서 답변을 동시에 두 개 만들지 않도록
        self.new_order()

    def new_order(self):
        """다음 손님을 위해 주문과 대화를 비운다. 화면 언어는 그대로 둔다."""
        self.order = Order(self.catalog, self.stock)
        # 모델이 올린 변경안(주문의 복사본). 손님이 변경 확인 화면에서 [확인]을 눌러야 order 에 반영된다.
        self.draft = None
        self.draft_undo = 0  # 변경안이 '되돌리기'일 때 몇 단계를 되돌리는지
        self.undo_stack = []  # 되돌리기용: 바뀌기 전 장바구니들
        self.history = []  # [{"role": "user" | "assistant", "content": str}]
        self.staff_call = None
        self.events = []  # 지난 답변 뒤에 손님이 화면 버튼으로 한 일. 다음 턴에 모델에게 알려 준다.

    def set_language(self, language):
        if language not in LANGUAGES:
            raise ValueError(f"지원하지 않는 언어: {language}")
        self.language = language

    def log(self, text):
        self.events.append(text)
        del self.events[:-8]

    # ------------------------------------------------------------ 손님이 버튼으로 직접 고칠 때

    def touch(self):
        """손님이 화면 버튼으로 주문을 고치기 직전에 부른다. 되돌릴 수 있게 지금 상태를 남기고,
        확인을 기다리던 변경안은 바탕이 달라지므로 버린다."""
        self._remember()
        if self.draft is not None:
            self.draft, self.draft_undo = None, 0
            self.log("확인을 기다리던 변경안은 손님이 주문을 직접 고쳐서 취소되었다")

    def _remember(self):
        self.undo_stack.append(self.order.snapshot())
        del self.undo_stack[:-UNDO_DEPTH]

    def undo(self):
        """손님이 [되돌리기] 버튼을 눌렀을 때: 직전 변경 하나를 바로 되돌린다."""
        if not self.undo_stack:
            raise OrderError("nothing_to_undo", "되돌릴 변경이 없습니다.")
        self.draft, self.draft_undo = None, 0
        self.order.restore(self.undo_stack.pop())

    # ------------------------------------------------------------ 모델이 올리는 변경안

    def _ordering(self):
        if self.order.status != "ordering":
            raise OrderError("payment_open", "손님이 결제 화면을 진행 중이어서 주문을 바꿀 수 없습니다. 주문을 바꾸려면 손님이 "
                             "결제 화면에서 [이전]을 눌러 주문 화면으로 돌아와야 한다고 안내하세요.")

    def propose(self):
        """변경안을 꺼낸다(없으면 지금 주문을 복사해 만든다). 모델의 도구는 이 복사본만 고친다."""
        self._ordering()
        if self.draft is None:
            self.draft = self.order.clone()
        return self.draft

    def propose_undo(self):
        """직전에 반영된 변경을 되돌리는 변경안을 만든다. 손님이 확인해야 되돌려진다."""
        self._ordering()
        if self.draft_undo >= len(self.undo_stack):
            raise OrderError("nothing_to_undo", "되돌릴 변경이 없습니다. 손님에게 무엇을 바꾸고 싶은지 다시 물어보세요.")
        self.draft_undo += 1
        self.draft = self.order.clone()
        self.draft.restore(self.undo_stack[-self.draft_undo])
        return self.draft

    def discard(self):
        had = self.draft is not None
        self.draft, self.draft_undo = None, 0
        return had

    def pending(self):
        """확인을 기다리는 변경 내용. 변경 확인 화면이 이것을 그린다."""
        if self.draft is None:
            return None
        diff = changes(self.order, self.draft)
        if not diff:
            self.draft, self.draft_undo = None, 0
            return None
        return {"changes": diff, "before": self.order.totals(), "after": self.draft.totals(), "undo": bool(self.draft_undo)}

    def pending_text(self):
        pending = self.pending()
        if not pending:
            return None
        lines = ["  " + change_text(self.order, self.draft, c) for c in pending["changes"]]
        lines.append(f"  확인하면 결제 금액(POS 계산): {won(pending['before']['total'])} → {won(pending['after']['total'])}")
        return "\n".join(lines)

    def confirm(self):
        """손님이 변경 확인 화면에서 [확인]을 눌렀다. 반영한 변경 내용을 돌려준다."""
        pending = self.pending()
        if not pending:
            raise OrderError("nothing_pending", "확인할 변경이 없습니다.")
        if self.order.status != "ordering":
            raise OrderError("payment_open", "결제 화면이 열려 있는 동안에는 주문을 바꿀 수 없습니다.")
        for change in pending["changes"]:  # 변경안을 올린 뒤에 품절된 메뉴
            if change["type"] in ("add", "update") and change["after"]["sold_out"]:
                raise OrderError("sold_out", f"'{change['after']['name']}'은(는) 방금 품절되어 반영할 수 없습니다.")
        if self.draft_undo:
            del self.undo_stack[-self.draft_undo:]
        else:
            self._remember()
        text = self.pending_text()
        self.order.adopt(self.draft)
        self.draft, self.draft_undo = None, 0
        self.log("손님이 변경 확인 화면에서 [확인]을 눌러 주문에 반영되었다:\n" + text)
        return pending["changes"]

    def reject(self):
        if self.discard():
            self.log("손님이 변경 확인 화면에서 [취소]를 눌렀다. 올렸던 변경안은 반영되지 않았다")

    # ------------------------------------------------------------ 직원 호출·화면 상태

    def call_staff(self, reason, source):
        self.staff_call = self.board.call(reason, source) if self.board else {"reason": reason, "source": source, "status": "waiting"}
        return self.staff_call

    def state(self):
        return {
            "language": self.language,
            "order": self.order.to_dict(),
            "pending": self.pending(),
            "can_undo": bool(self.undo_stack) and self.order.status == "ordering",
            "staff_call": self.staff_call,
            "stock": self.stock.to_dict() if self.stock else {"version": 0, "sold_out": []},
        }


class SessionStore:
    def __init__(self, catalog, stock=None, board=None):
        self.catalog = catalog
        self.stock = stock
        self.board = board
        self._sessions = {}
        self._lock = threading.Lock()

    def get(self, session_id):
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Session(self.catalog, self.stock, self.board)
            return self._sessions[session_id]
