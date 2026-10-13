"""대화 에이전트. 한 턴을 검색(RAG) → 프롬프트 구성 → 모델 호출(스트리밍) → 도구 실행 순서로 처리한다."""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from app import config, i18n
from app.chatbot.prompts import system_prompt, turn_message
from app.chatbot.tools import SEARCH_TOOL, ToolBox

log = logging.getLogger("kiosk")


def _sources(hits):
    return [{"title": h.doc["title"], "type": h.doc["type"], "score": round(h.score, 3), "match": h.match} for h in hits]


class KioskAgent:
    def __init__(self, client, retriever, catalog, model=config.CHAT_MODEL, temperature=config.TEMPERATURE,
                 moderation=config.USE_MODERATION):
        self.client = client
        self.retriever = retriever
        self.catalog = catalog
        self.tools = ToolBox(catalog, retriever)
        self.specs = self.tools.specs()
        self.system = system_prompt(catalog)
        self.model = model
        self.temperature = temperature
        self.moderation = moderation
        self._pool = ThreadPoolExecutor(max_workers=4)

    def respond(self, session, user_text):
        """손님의 한마디에 대한 답을 이벤트(dict)로 하나씩 내보낸다.

        delta: 답변 글 조각 / status: 실행할 도구 / tool: 도구 실행 결과 / sources: 검색된 문서
        state: 주문 상태 / cards: 답변에 나온 메뉴 / chips: 빠른 답변 버튼 / error / done
        """
        try:
            yield from self._respond(session, user_text)
        except Exception as e:  # 모델·네트워크 오류가 나도 화면에는 안내 문구를 보여 준다
            log.exception("답변 생성 실패")
            yield {"type": "error", "message": i18n.text("error", session.language), "detail": f"{type(e).__name__}: {e}"}
            yield {"type": "state", **session.state()}
        yield {"type": "done"}

    def _respond(self, session, user_text):
        if session.order.status == "paid":  # 결제가 끝난 뒤의 첫 마디는 새 주문으로 본다
            session.new_order()
        order = session.order
        language = i18n.detect_language(user_text)
        if language and language != session.language:  # 손님이 다른 언어로 말하면 화면과 답변 언어를 따라 바꾼다
            session.set_language(language)
            yield {"type": "state", **session.state()}

        flagged = self._pool.submit(self._flagged, user_text) if self.moderation else None
        hits = self.retriever.search(user_text, context=self._context(session.history, user_text))
        if flagged and flagged.result():
            reply = i18n.text("blocked", session.language)
            yield {"type": "delta", "text": reply}
            yield from self._finish(session, user_text, reply, need=None)
            return
        yield {"type": "sources", "items": _sources(hits)}

        messages = [
            {"role": "system", "content": self.system},
            *session.history[-2 * config.HISTORY_TURNS:],
            {"role": "user", "content": turn_message(order.summary_text(), hits, session.language, user_text)},
        ]
        parts, need = [], None
        for round_no in range(config.MAX_TOOL_ROUNDS + 1):
            # 마지막 라운드에서는 도구를 막아 반드시 글로 답하게 한다.
            text, calls = yield from self._stream(messages, allow_tools=round_no < config.MAX_TOOL_ROUNDS)
            if text.strip():
                parts.append(text.strip())
            if not calls:
                break
            if text.strip():
                yield {"type": "delta", "text": "\n\n"}
            messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in calls]})
            for call in calls:
                yield {"type": "status", "name": call["name"]}
                args, result = self._run_tool(call, session)
                found = result.pop("hits", None)
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
                event = {"type": "tool", "name": call["name"], "args": args, "ok": bool(result.get("ok"))}
                if call["name"] == SEARCH_TOOL:
                    event["sources"] = _sources(found or [])
                else:
                    event["result"] = {k: v for k, v in result.items() if k != "order"}
                    yield {"type": "state", **session.state()}
                if call["name"] == "add_to_cart":
                    need = result if result.get("error") == "need_options" else None
                yield event
        yield from self._finish(session, user_text, "\n\n".join(parts), need)

    def _finish(self, session, user_text, reply, need):
        if reply:  # 모델이 아무 글도 내지 않은 턴은 기록하지 않는다
            session.history += [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}]
        # "블랙 밀크티 하나 주세요처럼 말씀해 주세요" 같은 예시 문장 속 메뉴는 카드로 띄우지 않는다.
        spoken = re.sub(r'"[^"\n]*"|“[^”\n]*”', " ", reply)
        cards = [self.catalog.card(m) for m in self.catalog.find_mentions(spoken, limit=4)]
        if cards:
            yield {"type": "cards", "items": cards}
        yield {"type": "chips", "items": self.chips(session, need)}
        yield {"type": "state", **session.state()}

    # ------------------------------------------------------------ 검색·안전

    @staticmethod
    def _context(history, user_text):
        """짧은 후속 발화("L로 주세요", "그건 얼마예요?")일 때 검색에 함께 쓸 직전 대화(키오스크의 답 + 손님의 말)."""
        if len(user_text) >= 20 or not history:
            return None
        return " ".join(m["content"] for m in reversed(history[-2:]))

    def _flagged(self, text):
        """OpenAI Moderation 으로 유해한 입력인지 확인한다. 확인에 실패하면 막지 않는다."""
        try:
            result = self.client.moderations.create(model=config.MODERATION_MODEL, input=text)
            return result.results[0].flagged
        except Exception:
            log.warning("모더레이션 호출 실패 (건너뜀)", exc_info=True)
            return False

    # ------------------------------------------------------------ 모델 호출·도구

    def _stream(self, messages, allow_tools):
        """모델을 스트리밍으로 호출한다. 글 조각은 바로 내보내고, (전체 글, 도구 호출 목록) 을 돌려준다."""
        params = {"model": self.model, "messages": messages, "tools": self.specs, "stream": True}
        if not allow_tools:
            params["tool_choice"] = "none"
        if self.temperature is not None:
            params["temperature"] = self.temperature
        text, calls = "", {}
        for chunk in self.client.chat.completions.create(**params):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                text += delta.content
                yield {"type": "delta", "text": delta.content}
            for piece in delta.tool_calls or []:  # 도구 호출은 이름·인자가 조각나서 온다
                call = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                call["id"] = piece.id or call["id"]
                if piece.function:
                    call["name"] += piece.function.name or ""
                    call["arguments"] += piece.function.arguments or ""
        return text, [calls[i] for i in sorted(calls)]

    def _run_tool(self, call, session):
        try:
            args = json.loads(call["arguments"] or "{}")
            if not isinstance(args, dict):
                raise ValueError("인자가 객체가 아님")
        except ValueError:
            return {}, {"ok": False, "error": "bad_arguments", "message": "도구 인자를 JSON 객체로 다시 보내세요."}
        result = self.tools.run(call["name"], args, session)
        log.info("도구 %s(%s) → ok=%s", call["name"], call["arguments"], result.get("ok"))
        return args, result

    # ------------------------------------------------------------ 빠른 답변 버튼

    def chips(self, session, need=None):
        lang, order = session.language, session.order
        if need:
            menu = need["menu"]
            sizes, temps = need["choices"].get("size"), need["choices"].get("temperature")
            if sizes and temps:
                return [{"label": f"{t} · {s['size']} {i18n.money(s['price'], lang)}",
                         "text": i18n.text("pick_size_temp", lang, menu=menu, temp=t, size=s["size"])}
                        for t in temps for s in sizes]
            if sizes:
                return [{"label": f"{s['size']} {i18n.money(s['price'], lang)}",
                         "text": i18n.text("pick_size", lang, menu=menu, size=s["size"])} for s in sizes]
            return [{"label": t, "text": i18n.text("pick_temp", lang, menu=menu, temp=t)} for t in temps]
        if order.status == "paid":
            return []
        if not order.lines:
            return i18n.chips("start", lang)
        if not order.dine_option:
            return i18n.chips("dine", lang) + i18n.chips("cart", lang)[:1]
        return i18n.chips("cart", lang)
