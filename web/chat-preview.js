// Opt-in localhost UI fixtures. No AI requests or simulated order mutations.
(() => {
  if (!["localhost", "127.0.0.1", "[::1]"].includes(location.hostname) ||
      new URLSearchParams(location.search).get("preview") !== "chat") return;
  const items = [...new Map(state.tabs.flatMap(tab => tab.items).map(item => [item.id, item])).values()];
  const taro = items.find(item => item.name === "타로 밀크티 + 펄") || items[0];
  const other = items.find(item => item.name === "블랙 밀크티 + 펄") || items[1];
  if (!taro) return;
  const price = item => money(Math.min(...item.prices.map(p => p.price)));
  const samples = {
    detail: {
      question: `${taro.name}에 대해 알려줘`,
      text: `**${taro.name}**의 메뉴 안내 예시예요.\n- 가격: ${price(taro)}부터\n- 사이즈와 옵션은 메뉴 카드에서 확인할 수 있어요.\n\n어떤 옵션으로 드릴까요?`,
      cards: [taro],
    },
    recommend: {
      question: "메뉴 두 가지 추천해줘",
      text: `두 메뉴를 비교해 보세요.\n- **${taro.name}** · ${price(taro)}부터\n- **${other.name}** · ${price(other)}부터\n\n어떤 메뉴가 마음에 드세요?`,
      cards: [taro, other],
    },
    confirm: {
      question: `${taro.name} 한 잔 담아줘`,
      text: `**${taro.name} 1잔**의 주문 확인 예시예요.\n- 표시 금액: ${price(taro)}\n\n아직 주문에 담기지 않았어요. 아래에서 확인 화면을 살펴보세요.`,
      cards: [],
    },
  };
  const chips = () => $("#chips").replaceChildren(...[
    ["detail", "메뉴 설명"], ["recommend", "메뉴 추천"], ["confirm", "주문 확인"],
  ].map(([key, label]) => el("button", { type: "button", disabled: state.busy,
    onclick: () => reply(samples[key].question, key) }, label)));
  function confirmSample() {
    const close = () => closeScreen("preview-confirm");
    openScreen({ name: "preview-confirm", render: () => sheet({
      title: "주문 확인 · 샘플", sub: "화면 예시이며 실제 주문은 바뀌지 않아요.", close,
      body: el("div", { class: "change" }, el("strong", {}, `${taro.name} 1잔`),
        el("p", { class: "amount" }, price(taro))),
      actions: [el("button", { class: "secondary", type: "button", onclick: close }, "돌아가기"),
        el("button", { class: "primary", type: "button", onclick: () => {
          close(); addNote("샘플 확인 완료 · 실제 주문은 변경되지 않았어요.");
        } }, "샘플 확인")],
    }) });
  }
  async function reply(text, key) {
    if (state.busy) return;
    key ||= /담|주문|확인/.test(text) ? "confirm" : /추천|비교/.test(text) ? "recommend" : "detail";
    const sample = samples[key];
    screens.length = 0;
    renderLayer();
    document.body.dataset.view = "chat";
    addMessage("user", text);
    const message = addMessage("bot");
    setBusy(true);
    try {
      for (let i = 0; i < sample.text.length; i += 8) {
        handleEvent({ type: "delta", text: sample.text.slice(i, i + 8) }, message);
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      if (sample.cards.length) handleEvent({ type: "cards", items: sample.cards }, message);
      if (key === "confirm") message.append(el("button", {
        class: "secondary wide-button", type: "button", onclick: confirmSample,
      }, "확인 화면 보기 · 샘플"));
      message.scrollIntoView({ block: "start", behavior: "smooth" });
    } finally {
      setBusy(false);
      chips();
    }
  }
  window.kioskChatPreview = { reply };
  screens.length = 0;
  renderLayer();
  idle.total = 0;
  showTimer();
  $("#notice").replaceChildren(el("strong", {}, "샘플 대화 미리보기"),
    " · 실제 AI 응답이 아닙니다. 아래 예시를 누르거나 입력해 보세요. ",
    el("a", { href: "/" }, "일반 화면"));
  $("#notice").hidden = false;
  $("#messages").replaceChildren();
  chips();
  reply(samples.detail.question, "detail");
})();
