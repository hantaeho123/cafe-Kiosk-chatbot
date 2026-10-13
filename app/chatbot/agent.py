"""대화 에이전트. 한 턴을 검색(RAG) → 프롬프트 구성 → 모델 호출(스트리밍) → 도구 실행 순서로 처리한다.

모델은 주문을 직접 바꾸지 못한다. 도구가 올린 변경안은 턴이 끝날 때 proposal 이벤트로 화면에 보내고,
손님이 변경 확인 화면에서 [확인]을 눌러야 반영된다. 결제는 화면과 단말기에서 손님이 직접 한다.
"""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from app import config, i18n
from app.chatbot import checks
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

    def warm_up(self):
        """서버를 켠 직후 첫 손님의 답이 느리지 않게 임베딩과 모델을 한 번씩 미리 부른다(연결과 프롬프트 캐시를 데운다)."""
        try:
            self.retriever.search("안녕하세요")
            self.client.chat.completions.create(
                model=self.model, tools=self.specs, max_completion_tokens=16,
                messages=[{"role": "system", "content": self.system}, {"role": "user", "content": "안녕하세요"}])
            log.info("모델 연결 준비 완료")
        except Exception:
            log.warning("미리 데우기 실패 (첫 답변이 조금 느릴 수 있음)", exc_info=True)

    def respond(self, session, user_text):
        """손님의 한마디에 대한 답을 이벤트(dict)로 하나씩 내보낸다.

        delta: 답변 글 조각 / replace: 검증에 걸려 다시 쓴 답변 전체 / verified: 검증 결과
        status: 실행할 도구 / tool: 도구 실행 결과 / sources: 검색된 문서
        state: 주문 상태 / cards: 답변에 나온 메뉴 / compare: 메뉴 비교표 / proposal: 확인을 기다리는 변경안
        chips: 빠른 답변 버튼 / error / done
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
        language = i18n.detect_language(user_text)
        if language and language != session.language:  # 손님이 다른 언어로 말하면 화면과 답변 언어를 따라 바꾼다
            session.set_language(language)
            yield {"type": "state", **session.state()}

        if session.pending() and checks.is_bare_yes(user_text):
            # 말로 한 동의는 확인이 아니다. 모델에게 맡기면 가끔 같은 변경을 다시 올리므로 코드가 바로 답하고 확인 화면을 다시 띄운다.
            reply = i18n.text("press_confirm", session.language)
            yield {"type": "delta", "text": reply}
            yield from self._finish(session, user_text, reply, need=None, waiting=None)
            return

        waiting = session.pending_text()  # 이번 턴에 변경안이 달라졌는지 보려고 처음 모습을 남겨 둔다
        conflict, warnings = checks.request_warnings(user_text)  # 서로 부딪치는 말, 없는 사이즈
        warnings += self._fixed_options(user_text)
        flagged = self._pool.submit(self._flagged, user_text) if self.moderation else None
        hits = self.retriever.search(user_text, context=self._context(session.history, user_text))
        if flagged and flagged.result():
            reply = i18n.text("blocked", session.language)
            yield {"type": "delta", "text": reply}
            yield from self._finish(session, user_text, reply, need=None, waiting=waiting)
            return
        yield {"type": "sources", "items": _sources(hits)}

        messages = [
            {"role": "system", "content": self.system},
            *session.history[-2 * config.HISTORY_TURNS:],
            {"role": "user", "content": turn_message(session, hits, user_text, [conflict, *warnings] if conflict else warnings)},
        ]
        unapplied = []
        parts, need = [], None
        notes_before = self._note_ids(session)
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
            # 부딪치는 말이 한 음료에 대한 것일 때만 막는다. 여러 잔을 한 번에 말한 것("아이스 하나, 따뜻한 것 하나")은 그대로 둔다.
            lines_touched = sum(c["name"] in ("add_to_cart", "update_cart_item") for c in calls)
            session.ask_first = conflict if lines_touched == 1 else None
            for call in calls:
                yield {"type": "status", "name": call["name"]}
                args, result = self._run_tool(call, session)
                unapplied += [u for u in result.get("unapplied", []) if u not in unapplied]
                found = result.pop("hits", None)
                view = result.pop("view", None)  # 화면에만 쓰는 값 (비교표의 사진 등)
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
                event = {"type": "tool", "name": call["name"], "args": args, "ok": bool(result.get("ok"))}
                if call["name"] == SEARCH_TOOL:
                    event["sources"] = _sources(found or [])
                else:
                    event["result"] = result
                    yield {"type": "state", **session.state()}
                if call["name"] == "compare_menus" and view:
                    yield {"type": "compare", **view}
                if call["name"] == "add_to_cart":  # 고를 것이 남았으면 선택지 버튼을 띄운다
                    asking = result.get("error") == "need_options" or (result.get("error") == "conflicting_request" and result["choices"])
                    need = result if asking else None
                yield event
        session.ask_first = None
        new_note = bool(self._note_ids(session) - notes_before)
        written = "\n\n".join(parts)
        reply = yield from self._verify(session, user_text, written, messages, new_note, unapplied)
        yield from self._finish(session, user_text, reply, need, waiting, written)

    def _fixed_options(self, user_text):
        """손님이 말한 얼음량·당도가 그 메뉴에서는 고정일 때 미리 알려 준다. 모델이 도구를 부르기 전에 '가능해요'라고 답하지 않게."""
        found = []
        for menu in self.catalog.find_mentions(user_text, limit=3):
            if menu["kind"] != "drink" or not self.catalog.orderable(menu):
                continue
            for asked, key, label in ((checks.mentions_ice_amount(user_text), "ice", "얼음량"), (checks.mentions_sugar(user_text), "sugar", "당도")):
                if asked and menu["options"][key]["adjustable"] is False:
                    found.append(f"'{menu['name']}'은(는) {label}이 고정이라 바꿀 수 없습니다. 손님이 말한 {label}은 반영되지 않으니 "
                                 "된다고 하지 말고 고정이라고 알리세요.")
        return found

    @staticmethod
    def _note_ids(session):
        return {n["note_id"] for n in (session.draft or session.order).notes}

    def _verify(self, session, user_text, reply, messages, new_note=False, unapplied=()):
        """답변을 코드로 다시 확인한다(checks.py). 근거 없는 금액이 있으면 한 번 다시 쓰게 하고,
        알레르기 안내나 요청 메모 안내가 빠졌으면 정해 둔 문구를 붙인다."""
        report = {"type": "verified", "amounts": "ok", "rewritten": False, "allergy_notice": None, "note_notice": None, "unapplied": None}
        context = checks.message_text(messages)
        unknown = checks.ungrounded_amounts(reply, context)
        if unknown:
            log.warning("답변에 근거 없는 금액 %s", unknown)
            fixed = self._rewrite(messages, reply, unknown)
            if fixed and not checks.ungrounded_amounts(fixed, context):
                reply, report["rewritten"] = fixed, True
                yield {"type": "replace", "text": reply}
            else:
                caution = i18n.text("amount_caution", session.language)
                reply += "\n\n" + caution
                report["amounts"] = unknown
                yield {"type": "delta", "text": "\n\n" + caution}
        if checks.asks_about_allergy(user_text) and reply:
            report["allergy_notice"] = "model"
            if not checks.has_allergy_notice(reply, session.language):
                notice = i18n.text("allergy_notice", session.language)
                reply += "\n\n" + notice
                report["allergy_notice"] = "added"
                yield {"type": "delta", "text": "\n\n" + notice}
        if session.language != "ko":  # 외국어 답변: "5,500원" → ₩5,500, 메뉴 이름 뒤의 한글 괄호는 뺀다 (화면과 같은 표기)
            cleaned = checks.without_korean_parens(checks.foreign_prices(reply))
            if cleaned != reply:
                reply = cleaned
                yield {"type": "replace", "text": reply}
        if unapplied and session.pending() and reply:  # 손님이 말한 옵션을 반영하지 못했는데 답변이 밝히지 않았을 때
            report["unapplied"] = "model"
            if not checks.mentions_fixed(reply, session.language):
                items = ", ".join(i18n.text(f"unapplied_{u}", session.language) for u in unapplied)
                notice = i18n.text("unapplied_notice", session.language, items=items)
                reply += "\n\n" + notice
                report["unapplied"] = "added"
                yield {"type": "delta", "text": "\n\n" + notice}
        if new_note and reply:
            report["note_notice"] = "model"
            if not checks.has_note_caveat(reply, session.language):
                notice = i18n.text("note_notice", session.language)
                reply += "\n\n" + notice
                report["note_notice"] = "added"
                yield {"type": "delta", "text": "\n\n" + notice}
        yield report
        return reply

    def _rewrite(self, messages, reply, unknown):
        """근거 없는 금액이 든 답변을 모델에게 한 번 다시 쓰게 한다. 실패하면 None."""
        note = ("방금 답변에 근거 없는 금액이 있습니다: " + ", ".join(f"{n:,}원" for n in unknown) + ". 이 금액은 실시간 상태, 참고문서, "
                "도구 결과 어디에도 없습니다. 직접 계산하거나 어림하지 말고 근거에 적힌 금액만 그대로 써서 같은 내용의 답변을 다시 쓰세요. "
                "필요한 합계가 근거에 없으면 그 금액은 빼고, 정확한 금액은 화면의 주문 내역이나 결제 화면에서 확인해 달라고 안내하세요. "
                "사과나 설명 없이 고친 답변만 씁니다.")
        stream = self._stream([*messages, {"role": "assistant", "content": reply}, {"role": "system", "content": note}], allow_tools=False)
        try:
            while True:
                next(stream)  # 글 조각은 화면에 흘리지 않고 다 쓴 뒤에 한 번에 바꾼다
        except StopIteration as done:
            return done.value[0].strip()
        except Exception:
            log.warning("답변 다시 쓰기 실패", exc_info=True)
            return None

    def _finish(self, session, user_text, reply, need, waiting=None, written=None):
        if reply:  # 모델이 아무 글도 내지 않은 턴은 기록하지 않는다
            session.history += [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}]
            session.events.clear()  # 화면 조작 내역은 이번 턴에 모델에게 전했다
        # "블랙 밀크티 하나 주세요처럼 말씀해 주세요" 같은 예시 문장 속 메뉴는 카드로 띄우지 않는다.
        # 어떤 메뉴를 말했는지는 다듬기 전의 글(written)에서 찾는다. 한글 괄호를 뺀 뒤에는 모델이 제멋대로 번역한 이름을 알아볼 수 없다.
        spoken = re.sub(r'"[^"\n]*"|“[^”\n]*”', " ", f"{written or ''}\n{reply}")
        mentioned = self.catalog.find_mentions(spoken, limit=5)
        if mentioned:
            session.shown = [m["name"] for m in mentioned]
        cards = [self.catalog.card(m, bool(session.stock and session.stock.is_sold_out(m["id"]))) for m in mentioned[:4]]
        if cards:
            yield {"type": "cards", "items": cards}
        pending = session.pending()
        if pending:
            # changed 가 참이면 화면이 변경 확인 화면을 띄운다. 변경안은 그대로 두고 다른 것을 물었을 때는
            # 답변을 가리지 않도록 띄우지 않고, 손님이 [변경 확인하기]로 다시 연다.
            yield {"type": "proposal", **pending, "changed": session.pending_text() != waiting}
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
        """상황에 맞는 버튼. text 가 있으면 그 문장을 보내고, action 이 있으면 화면이 바로 동작한다.
        결제와 변경 확인은 손님이 직접 눌러야 해서 action 버튼으로 준다."""
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
        if order.status == "paying":
            return [i18n.action("resume_pay", lang)] + i18n.chips("paying", lang)
        if session.pending():
            return [i18n.action("confirm", lang), i18n.action("reject", lang)]
        undo = [i18n.action("undo", lang)] if session.undo_stack else []
        if not order.lines:
            return i18n.chips("start", lang) if not undo else undo + i18n.chips("start", lang)[:3]
        if not order.dine_option:
            return [i18n.action("dine_in", lang), i18n.action("takeout", lang)] + undo
        return [i18n.action("pay", lang)] + i18n.chips("cart", lang) + undo
