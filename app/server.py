"""웹 서버: 키오스크 화면(web/), 직원 화면(/staff), API.

    python -m app            # http://localhost:8000  (직원 화면은 http://localhost:8000/staff)

주문을 바꾸는 길은 둘이다. 손님이 화면 버튼으로 고치면 바로 반영되고(/api/order/*), 대화로 말하면 모델이 변경안을
올린 뒤 손님이 변경 확인 화면에서 확인해야 반영된다(/api/order/confirm). 결제(/api/pay/*, /api/scan)는 버튼으로만 한다.
"""
import base64
import json
import logging
import secrets
import threading
import time
from collections import deque
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field

from app import config, i18n
from app.catalog import Catalog
from app.chatbot.agent import KioskAgent
from app.order import DISCOUNTS, MAX_QUANTITY, OrderError
from app.pos import Barcodes, Pos
from app.rag.retriever import Retriever
from app.rag.store import Embedder, open_store
from app.session import SessionStore
from app.staff import StaffBoard
from app.stock import Stock

log = logging.getLogger("kiosk")


class SessionIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)


class ChatIn(SessionIn):
    message: str = Field(min_length=1, max_length=1000)


class LanguageIn(SessionIn):
    language: str


class LineIn(SessionIn):
    line_id: int
    quantity: int  # 0 이면 그 줄을 뺀다


class ItemIn(SessionIn):
    """옵션 화면에서 고른 값. menu_id 로 새로 담거나 line_id 로 담긴 줄을 바꾼다.
    draft 가 참이면 확인을 기다리는 변경안의 줄을 고친다(변경 확인 화면의 "옵션 직접 고치기")."""
    menu_id: Optional[str] = None
    line_id: Optional[int] = None
    draft: bool = False
    size: Optional[str] = None
    temperature: Optional[str] = None
    sugar: Optional[int] = None
    ice: Optional[str] = None
    toppings: list[str] = []
    quantity: int = 1
    cup: Optional[str] = None


class OptionsIn(SessionIn):
    dine_option: Optional[str] = None
    straw: Optional[bool] = None
    carrier: Optional[bool] = None


class NoteIn(SessionIn):
    note_id: int


class ScanIn(SessionIn):
    purpose: str
    code: str = Field(max_length=64)


class BenefitIn(SessionIn):
    kind: str


class TenderIn(SessionIn):
    method: str
    scenario: str = "ok"  # 시연용: 꽂은 카드의 상태


class ReceiptIn(SessionIn):
    print_receipt: bool


class StaffCallIn(SessionIn):
    reason: Optional[str] = Field(default=None, max_length=100)


class CallDoneIn(BaseModel):
    id: int


class OrderDoneIn(BaseModel):
    number: int


class StockIn(BaseModel):
    menu_id: str
    sold_out: bool


class RateLimiter:
    """정해진 시간 안에 받을 수 있는 횟수를 센다. 공개 주소로 올렸을 때 OpenAI 요금이 한없이 나가지 않게 하는 장치다."""

    def __init__(self, limit, seconds):
        self.limit, self.seconds = limit, seconds
        self._times = {}
        self._lock = threading.Lock()

    def allow(self, key="all"):
        if not self.limit:
            return True
        now = time.monotonic()
        with self._lock:
            times = self._times.setdefault(key, deque())
            while times and now - times[0] > self.seconds:
                times.popleft()
            if len(times) >= self.limit:
                return False
            times.append(now)
            if len(self._times) > 5000:  # 오래된 키가 쌓이지 않게
                self._times = {k: v for k, v in self._times.items() if v and now - v[-1] <= self.seconds}
            return True


class PasswordGate:
    """ACCESS_PASSWORD 가 있으면 모든 화면과 API 앞에서 브라우저 기본 로그인 창으로 비밀번호를 묻는다(아이디는 아무거나).
    배포 서버의 상태 확인에 쓰는 /api/config 만 열어 둔다."""

    def __init__(self, app, password):
        self.app, self.password = app, password

    def _ok(self, scope):
        header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        try:
            kind, _, value = header.partition(" ")
            given = base64.b64decode(value).decode("utf-8").partition(":")[2] if kind.lower() == "basic" else ""
        except ValueError:
            given = ""
        return secrets.compare_digest(given.encode(), self.password.encode())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/api/config" or self._ok(scope):
            return await self.app(scope, receive, send)
        denied = Response("비밀번호가 필요합니다.", status_code=401, media_type="text/plain; charset=utf-8",
                          headers={"WWW-Authenticate": 'Basic realm="kiosk", charset="UTF-8"'})
        await denied(scope, receive, send)


class Kiosk:
    """서버가 들고 있는 것들: 메뉴판, 품절 상태, POS, 직원 화면, 세션, 대화 에이전트(API 키가 있을 때만)."""

    def __init__(self, client=None, index_dir=config.INDEX_DIR):
        warm_up = client is None  # 테스트에서 넣은 가짜 클라이언트는 데우지 않는다
        self.catalog = Catalog()
        self.stock = Stock(self.catalog)
        self.board = StaffBoard()
        self.barcodes = Barcodes()
        self.pos = Pos(self.catalog, self.barcodes, self.board)
        self.sessions = SessionStore(self.catalog, self.stock, self.board)
        self.agent = None
        self.error = None
        try:
            client = client or OpenAI()
            embedder = Embedder(client)
            store, built = open_store(embedder, folder=index_dir)
            log.info("RAG 인덱스 %s: 문서 %d개 (%s)", "새로 생성" if built else "불러옴", len(store.docs), embedder.model)
            self.agent = KioskAgent(client, Retriever(store, embedder, self.catalog), self.catalog)
            if warm_up:
                threading.Thread(target=self.agent.warm_up, daemon=True).start()
        except OpenAIError as e:
            # 키가 없거나 틀려도 메뉴판과 장바구니 화면은 뜨게 하고, 대화만 막는다.
            self.error = f"OpenAI 연결에 실패했습니다. 프로젝트 루트의 .env 에 OPENAI_API_KEY 를 넣었는지 확인하세요. ({e})"
            log.error(self.error)


def fail(error, status=400):
    """OrderError 를 화면이 번역할 수 있게 코드와 함께 돌려준다."""
    return HTTPException(status, {"code": error.result["error"], "message": error.result["message"]})


def create_app(client=None, index_dir=config.INDEX_DIR):
    """client 와 index_dir 는 테스트에서 가짜 OpenAI 클라이언트와 임시 폴더를 넣을 때 쓴다."""
    kiosk = Kiosk(client, index_dir)
    catalog, stock, pos, board = kiosk.catalog, kiosk.stock, kiosk.pos, kiosk.board
    app = FastAPI(title="공차 키오스크 챗봇")
    app.state.kiosk = kiosk
    if config.ACCESS_PASSWORD:
        app.add_middleware(PasswordGate, password=config.ACCESS_PASSWORD)
    per_session = RateLimiter(config.CHAT_LIMIT_PER_SESSION, 600)
    per_hour = RateLimiter(config.CHAT_LIMIT_PER_HOUR, 3600)

    def opening(session, **extra):
        """세션 상태와 함께 화면이 처음 그릴 것들(인사말, 빠른 답변 버튼, 지난 대화)."""
        chips = kiosk.agent.chips(session) if kiosk.agent else i18n.chips("start", session.language)
        return {**session.state(), "history": session.history, "chips": chips,
                "greeting": i18n.text("greeting", session.language, store=catalog.store["name"]), **extra}

    def ordering(session_id):
        """화면 버튼으로 주문을 고칠 때: 결제 화면이 열려 있거나 결제가 끝난 주문은 건드리지 않는다."""
        session = kiosk.sessions.get(session_id)
        if session.order.status == "paid":
            raise HTTPException(409, {"code": "already_paid", "message": "이미 결제가 끝난 주문입니다."})
        if session.order.status == "paying":
            raise HTTPException(409, {"code": "payment_open", "message": "결제 화면이 열려 있는 동안에는 주문을 바꿀 수 없습니다."})
        return session

    def edit(session, action, remember=True):
        """손님이 버튼으로 주문을 직접 고친다. 실패하면 되돌리기 기록을 남기지 않는다."""
        if remember:
            session.touch()
        try:
            return action()
        except OrderError as e:
            if remember:
                session.undo_stack.pop()
            raise fail(e) from None

    def run(action):
        try:
            return action()
        except OrderError as e:
            raise fail(e) from None

    # ------------------------------------------------------------ 설정·메뉴판

    @app.get("/api/config")
    def get_config():
        flow, options = catalog.flow, catalog.options
        idle = dict(flow["idle"])
        if config.IDLE_SECONDS is not None:
            idle["idle_seconds"] = config.IDLE_SECONDS
        return {
            "store": catalog.store, "ready": kiosk.agent is not None, "error": kiosk.error,
            "model": config.CHAT_MODEL, "embed_model": config.EMBED_MODEL,
            "languages": i18n.LANGUAGES, "discounts": DISCOUNTS,
            # 옵션 화면의 선택지와 그 뜻
            "sugar_levels": catalog.sugar_levels,
            "sugar_help": {"meaning": options["sugar"]["meaning"],
                           "levels": {str(l["value"]): l["description"] for l in options["sugar"]["levels"]}},
            "ice_levels": list(catalog.ice_levels),
            "sizes": {s["id"]: {"name": s["name"], "name_en": s["name_en"]} for s in options["sizes"]["items"]},
            "toppings": [{"id": tid, "name": t["name"], "name_en": t["name_en"], "price": t["price"], "basic": t["basic"],
                          "description": t["description"]} for tid, t in catalog.toppings.items()],
            "cups": [{"id": cid, "name": c["name"], "name_en": c["name_en"], "description": c["description"],
                      "dine_in_only": c.get("dine_in_only", False)} for cid, c in catalog.cups.items()],
            "max_toppings": catalog.max_toppings, "max_quantity": MAX_QUANTITY,
            "requests": {k: flow["requests"][k] for k in ("max_notes", "max_length")},
            # 결제 화면
            "payment_methods": [{k: p[k] for k in ("id", "name", "group", "device")} for p in flow["payment_methods"]],
            "devices": {k: v for k, v in flow["devices"].items() if isinstance(v, dict)},
            "idle": {"idle_seconds": idle["idle_seconds"], "warning_seconds": idle["warning_seconds"]},
            "demo": kiosk.barcodes.demo(),  # 실제 기기 대신 쓰는 시연용 바코드와 카드
        }

    @app.get("/api/menu")
    def get_menu():
        return {"tabs": catalog.board(stock.ids()), "stock": stock.to_dict()}

    @app.get("/api/menu/{menu_id}")
    def get_menu_item(menu_id: str):
        if menu_id not in catalog.by_id:
            raise HTTPException(404, "없는 메뉴입니다.")
        return catalog.detail(catalog.by_id[menu_id], stock.is_sold_out(menu_id))

    @app.get("/api/live")
    def live(session_id: str):
        """화면이 몇 초마다 묻는 실시간 값: 품절 상태와 직원 호출 진행."""
        session = kiosk.sessions.get(session_id)
        return {"stock": stock.to_dict(), "staff_call": session.staff_call}

    # ------------------------------------------------------------ 세션

    @app.post("/api/session")
    def open_session(body: SessionIn):
        return opening(kiosk.sessions.get(body.session_id))

    @app.post("/api/session/reset")
    def reset_session(body: SessionIn):
        session = kiosk.sessions.get(body.session_id)
        session.new_order()
        return opening(session)

    @app.post("/api/session/language")
    def set_language(body: LanguageIn):
        session = kiosk.sessions.get(body.session_id)
        try:
            session.set_language(body.language)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return opening(session)

    # ------------------------------------------------------------ 손님이 버튼으로 주문 고치기

    @app.post("/api/order/line")
    def change_line(body: LineIn):
        session = ordering(body.session_id)
        order = session.order
        edit(session, lambda: order.remove(body.line_id) if body.quantity <= 0 else order.update(body.line_id, quantity=body.quantity))
        return opening(session)

    @app.post("/api/order/item")
    def put_item(body: ItemIn):
        """옵션 화면의 "담기"(menu_id) 와 주문 내역의 "옵션 변경"(line_id)."""
        session = ordering(body.session_id)
        options = body.model_dump(exclude={"session_id", "menu_id", "line_id", "draft"})
        if body.draft:  # 변경안의 줄을 손님이 직접 고친다. 그래도 반영은 변경 확인 화면의 [확인]으로 한다.
            if session.draft is None or body.line_id is None:
                raise HTTPException(409, {"code": "nothing_pending", "message": "확인할 변경이 없습니다."})
            line, _, notes = run(lambda: session.draft.replace(body.line_id, **options))
            return opening(session, line_id=line["line_id"], notes=notes)
        if body.line_id is None and body.menu_id not in catalog.by_id:
            raise HTTPException(404, "없는 메뉴입니다.")
        order = session.order
        if body.line_id is not None:
            line, _, notes = edit(session, lambda: order.replace(body.line_id, **options))
            return opening(session, line_id=line["line_id"], notes=notes)
        line, _, notes = edit(session, lambda: order.add(catalog.by_id[body.menu_id], **options))
        session.log("손님이 메뉴판에서 직접 담았다: " + order.line_text(line))
        # 추가 제안 창: 아직 담지 않은 인기 메뉴 (품절 제외)
        skip = {l["menu_id"] for l in order.lines} | set(stock.ids())
        suggestions = [catalog.card(m) for m in catalog.suggest(skip)]
        return opening(session, line_id=line["line_id"], notes=notes, suggestions=suggestions)

    @app.post("/api/order/options")
    def change_options(body: OptionsIn):
        session = ordering(body.session_id)
        # 빈 주문에서 매장/포장만 고르는 것(시작 화면)은 되돌릴 일이 아니다.
        edit(session, lambda: session.order.set_options(body.dine_option, body.straw, body.carrier),
             remember=bool(session.order.lines or session.draft))
        return opening(session)

    @app.post("/api/order/clear")
    def clear_order(body: SessionIn):
        session = ordering(body.session_id)
        edit(session, session.order.clear)
        session.log("손님이 [전체 취소]로 주문을 모두 비웠다")
        return opening(session)

    @app.post("/api/order/note")
    def remove_note(body: NoteIn):
        session = ordering(body.session_id)
        edit(session, lambda: session.order.remove_note(body.note_id))
        return opening(session)

    @app.post("/api/order/undo")
    def undo(body: SessionIn):
        session = ordering(body.session_id)
        run(session.undo)
        session.log("손님이 [되돌리기]로 직전 변경을 되돌렸다")
        return opening(session)

    # ------------------------------------------------------------ 변경 확인 화면

    @app.post("/api/order/confirm")
    def confirm(body: SessionIn):
        session = ordering(body.session_id)
        applied = run(session.confirm)
        return opening(session, applied=applied)

    @app.post("/api/order/reject")
    def reject(body: SessionIn):
        session = kiosk.sessions.get(body.session_id)
        session.reject()
        return opening(session)

    # ------------------------------------------------------------ 바코드 리더·결제 (손님만 할 수 있다)

    @app.post("/api/scan")
    def scan(body: ScanIn):
        session = kiosk.sessions.get(body.session_id)
        order = session.order
        if body.purpose == "menu_voucher":  # 교환권은 주문 화면에서 메뉴를 담는다
            ordering(body.session_id)
            result = edit(session, lambda: pos.scan(order, body.purpose, body.code))
            session.log(f"손님이 교환권 바코드를 찍어 {result['menu']}을(를) 담았다")
        else:
            result = run(lambda: pos.scan(order, body.purpose, body.code))
        return opening(session, scan=result)

    @app.post("/api/pay/start")
    def pay_start(body: SessionIn):
        session = kiosk.sessions.get(body.session_id)
        if session.pending():
            raise HTTPException(409, {"code": "pending_changes", "message": "확인을 기다리는 변경이 있습니다. 먼저 확인하거나 취소해 주세요."})
        if session.order.status != "paying":
            run(lambda: pos.start(session.order))
            session.log("손님이 [결제하기]를 눌러 결제 화면을 열었다")
        return opening(session)

    @app.post("/api/pay/cancel")
    def pay_cancel(body: SessionIn):
        session = kiosk.sessions.get(body.session_id)
        if session.order.status == "paying":
            pos.cancel(session.order)
            session.log("손님이 결제 화면에서 [이전]을 눌러 주문 화면으로 돌아왔다")
        return opening(session)

    @app.post("/api/pay/benefit")
    def pay_benefit(body: BenefitIn):
        session = kiosk.sessions.get(body.session_id)
        run(lambda: pos.remove_benefit(session.order, body.kind))
        return opening(session)

    @app.post("/api/pay/tender")
    def pay_tender(body: TenderIn):
        session = kiosk.sessions.get(body.session_id)
        approved = run(lambda: pos.pay(session.order, body.method, body.scenario))
        if not approved:
            session.log("결제가 승인되지 않았다: " + session.order.failure["reason_text"])
        return opening(session, approved=approved)

    @app.post("/api/pay/receipt")
    def pay_receipt(body: ReceiptIn):
        session = kiosk.sessions.get(body.session_id)
        run(lambda: pos.set_receipt(session.order, body.print_receipt))
        return opening(session)

    # ------------------------------------------------------------ 직원

    @app.post("/api/staff")
    def call_staff(body: StaffCallIn):
        session = kiosk.sessions.get(body.session_id)
        session.call_staff(body.reason or "화면의 직원 호출 버튼", "button")
        return session.state()

    @app.get("/api/staff/board")
    def staff_board():
        drinks = [m for m in catalog.items if m["kind"] == "drink" and catalog.orderable(m)]
        return {**board.to_dict(),
                "menus": [{"id": m["id"], "name": m["name"], "category": m["category"], "sold_out": stock.is_sold_out(m["id"])}
                          for m in drinks]}

    @app.post("/api/staff/call")
    def staff_answer(body: CallDoneIn):
        try:
            return board.answer(body.id)
        except KeyError:
            raise HTTPException(404, "없는 호출입니다.") from None

    @app.post("/api/staff/order")
    def staff_order_done(body: OrderDoneIn):
        try:
            return board.done(body.number)
        except KeyError:
            raise HTTPException(404, "없는 주문입니다.") from None

    @app.post("/api/staff/stock")
    def staff_stock(body: StockIn):
        try:
            stock.set(body.menu_id, body.sold_out)
        except KeyError:
            raise HTTPException(404, "없는 메뉴입니다.") from None
        return stock.to_dict()

    # ------------------------------------------------------------ 대화

    @app.post("/api/chat")
    def chat(body: ChatIn):
        session = kiosk.sessions.get(body.session_id)
        message = body.message.strip()
        if not message:
            raise HTTPException(400, "메시지가 비어 있습니다.")

        def events():
            if kiosk.agent is None:
                yield {"type": "error", "message": kiosk.error}
                yield {"type": "done"}
            elif not session.lock.acquire(blocking=False):
                yield {"type": "error", "message": i18n.text("busy", session.language)}
                yield {"type": "done"}
            elif not (per_session.allow(body.session_id) and per_hour.allow()):
                session.lock.release()
                yield {"type": "error", "message": i18n.text("rate_limited", session.language)}
                yield {"type": "done"}
            else:
                try:
                    yield from kiosk.agent.respond(session, message)
                finally:
                    session.lock.release()

        # Server-Sent Events: 답변을 만드는 대로 한 줄씩 화면에 흘려 보낸다.
        stream = (f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events())
        return StreamingResponse(stream, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/")
    def index():
        return FileResponse(config.WEB_DIR / "index.html")

    @app.get("/staff")
    def staff_page():
        return FileResponse(config.WEB_DIR / "staff.html")

    app.mount("/images", StaticFiles(directory=config.IMAGES_DIR), name="images")
    app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")
    return app
