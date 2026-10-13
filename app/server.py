"""웹 서버: 키오스크 화면(web/)과 API.

    python -m app            # http://localhost:8000
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field

from app import config, i18n
from app.catalog import Catalog
from app.chatbot.agent import KioskAgent
from app.order import DISCOUNTS, MAX_QUANTITY, OrderError
from app.rag.retriever import Retriever
from app.rag.store import Embedder, open_store
from app.session import SessionStore

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
    """메뉴 상세 창에서 고른 옵션. menu_id 로 새로 담거나 line_id 로 담긴 줄을 바꾼다."""
    menu_id: Optional[str] = None
    line_id: Optional[int] = None
    size: Optional[str] = None
    temperature: Optional[str] = None
    sugar: Optional[int] = None
    ice: Optional[str] = None
    toppings: list[str] = []
    quantity: int = 1
    tumbler: bool = False


class OptionsIn(SessionIn):
    dine_option: Optional[str] = None
    straw: Optional[bool] = None
    carrier: Optional[bool] = None
    discount: Optional[str] = None
    stamp: Optional[bool] = None


class Kiosk:
    """서버가 들고 있는 것들: 메뉴판, 세션, 대화 에이전트(API 키가 있을 때만)."""

    def __init__(self, client=None, index_dir=config.INDEX_DIR):
        self.catalog = Catalog()
        self.sessions = SessionStore(self.catalog)
        self.agent = None
        self.error = None
        try:
            client = client or OpenAI()
            embedder = Embedder(client)
            store, built = open_store(embedder, folder=index_dir)
            log.info("RAG 인덱스 %s: 문서 %d개 (%s)", "새로 생성" if built else "불러옴", len(store.docs), embedder.model)
            self.agent = KioskAgent(client, Retriever(store, embedder, self.catalog), self.catalog)
        except OpenAIError as e:
            # 키가 없거나 틀려도 메뉴판과 장바구니 화면은 뜨게 하고, 대화만 막는다.
            self.error = f"OpenAI 연결에 실패했습니다. 프로젝트 루트의 .env 에 OPENAI_API_KEY 를 넣었는지 확인하세요. ({e})"
            log.error(self.error)


def create_app(client=None, index_dir=config.INDEX_DIR):
    """client 와 index_dir 는 테스트에서 가짜 OpenAI 클라이언트와 임시 폴더를 넣을 때 쓴다."""
    kiosk = Kiosk(client, index_dir)
    catalog = kiosk.catalog
    app = FastAPI(title="공차 키오스크 챗봇")
    app.state.kiosk = kiosk

    def opening(session):
        """세션 상태와 함께 화면이 처음 그릴 것들(인사말, 빠른 답변 버튼, 지난 대화)."""
        chips = kiosk.agent.chips(session) if kiosk.agent else i18n.chips("start", session.language)
        return {**session.state(), "history": session.history, "chips": chips,
                "greeting": i18n.text("greeting", session.language, store=catalog.store["name"])}

    def ordering(session_id):
        """화면 버튼으로 주문을 고칠 때: 결제가 끝난 주문은 건드리지 않는다."""
        session = kiosk.sessions.get(session_id)
        if session.order.status == "paid":
            raise HTTPException(409, "이미 결제가 끝난 주문입니다.")
        return session

    @app.get("/api/config")
    def get_config():
        return {
            "store": catalog.store, "ready": kiosk.agent is not None, "error": kiosk.error,
            "model": config.CHAT_MODEL, "embed_model": config.EMBED_MODEL,
            "languages": i18n.LANGUAGES, "discounts": DISCOUNTS,
            # 메뉴 상세 창의 옵션 선택지
            "sugar_levels": catalog.sugar_levels, "ice_levels": list(catalog.ice_levels),
            "toppings": [{"id": tid, "name": t["name"], "price": t["price"]} for tid, t in catalog.toppings.items()],
            "max_toppings": catalog.max_toppings, "max_quantity": MAX_QUANTITY,
        }

    @app.get("/api/menu")
    def get_menu():
        return {"tabs": catalog.board()}

    @app.get("/api/menu/{menu_id}")
    def get_menu_item(menu_id: str):
        if menu_id not in catalog.by_id:
            raise HTTPException(404, "없는 메뉴입니다.")
        return catalog.detail(catalog.by_id[menu_id])

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

    @app.post("/api/order/line")
    def change_line(body: LineIn):
        session = ordering(body.session_id)
        try:
            if body.quantity <= 0:
                session.order.remove(body.line_id)
            else:
                session.order.update(body.line_id, quantity=body.quantity)
        except OrderError as e:
            raise HTTPException(400, e.result["message"]) from None
        return opening(session)

    @app.post("/api/order/item")
    def put_item(body: ItemIn):
        """메뉴 상세 창의 "담기"(menu_id) 와 주문 내역의 "옵션 변경"(line_id)."""
        session = ordering(body.session_id)
        order = session.order
        options = body.model_dump(exclude={"session_id", "menu_id", "line_id"})
        try:
            if body.line_id is not None:
                line, _, notes = order.replace(body.line_id, **options)
            elif body.menu_id in catalog.by_id:
                line, _, notes = order.add(catalog.by_id[body.menu_id], **options)
            else:
                raise HTTPException(404, "없는 메뉴입니다.")
        except OrderError as e:
            raise HTTPException(400, e.result["message"]) from None
        return {**opening(session), "line_id": line["line_id"], "notes": notes}

    @app.post("/api/order/options")
    def change_options(body: OptionsIn):
        session = ordering(body.session_id)
        try:
            session.order.set_options(body.dine_option, body.straw, body.carrier, body.discount, body.stamp)
        except OrderError as e:
            raise HTTPException(400, e.result["message"]) from None
        return opening(session)

    @app.post("/api/staff")
    def call_staff(body: SessionIn):
        session = kiosk.sessions.get(body.session_id)
        session.staff_call = {"reason": "화면의 직원 호출 버튼", "time": datetime.now().strftime("%H:%M")}
        return session.state()

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

    app.mount("/images", StaticFiles(directory=config.IMAGES_DIR), name="images")
    app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")
    return app
