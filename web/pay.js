// 결제 화면과 바코드 리더·결제 단말기(시연용).
// 결제는 손님이 화면과 단말기로 직접 한다: 할인·적립(바코드) → 결제수단 → 돈 내기 → 영수증 → 주문번호.
// 모델에게는 결제를 시작하거나 승인하는 도구가 없고, 여기 있는 버튼만 서버의 /api/pay, /api/scan 을 부른다.
// 실제 기기가 없으므로 "카드 꽂기", "바코드 찍기" 같은 손님의 행동을 버튼으로 흉내 낸다.

const pay = {
  step: "benefits",  // benefits → (scan) → method → (easy) → terminal → receipt → done
  method: null,  // card | nfc | barcode | gift_card
  scanFor: null,  // 할인·적립 단계에서 찍으려는 바코드: stamp | membership | coupon
  scenario: "ok",  // 시연용: 꽂은 카드의 상태
  approved: false,  // 카드가 승인되어 빼기를 기다리는 중
  error: null,  // 바코드를 읽지 못했을 때의 안내
  message: null,
  receiptTaken: false,
  working: false,
};

const BARCODE_KIND = { stamp: "app", membership: "membership", coupon: "coupon", menu_voucher: "menu_voucher", gift_card: "gift_card", pay: "pay" };

function demoLabel(item) {
  return state.lang === "ko" ? item.label : item.label_en || item.label;
}

// 손님이 [결제하기]를 눌렀다.
async function startPayment() {
  if (state.pending) {  // 확인을 기다리는 변경안이 있으면 결제로 넘어가지 않는다
    openConfirm();
    showToast(t("err_pending_changes"));
    return;
  }
  try {
    const session = await api("/api/pay/start", {});
    Object.assign(pay, { step: "benefits", method: null, scanFor: null, scenario: "ok", approved: false, error: null, message: null, receiptTaken: false });
    applyState(session);
    renderChips(session.chips);
    openPayment();
  } catch (error) {
    showToast(errorText(error));
    if (error.code === "need_dine_option") openStart();
  }
}

function openPayment() {
  const { order } = state;
  if (order.status === "ordering") {
    startPayment();
    return;
  }
  if (order.status === "paid" && pay.step !== "done") pay.step = order.receipt.print_receipt == null ? "receipt" : "done";
  openScreen({
    name: "pay",
    render: renderPay,
    // [이전]: 한 단계 앞으로. 첫 단계에서는 결제 화면을 닫고 주문 화면으로 돌아간다. 결제가 끝난 뒤에는 돌아갈 수 없다.
    get back() {
      if (state.order.status === "paid" || pay.approved) return false;
      return payBack;
    },
  });
}

async function payBack() {
  const previous = { scan: "benefits", method: "benefits", easy: "method", terminal: ["nfc", "barcode"].includes(pay.method) ? "easy" : "method" };
  pay.error = null;
  pay.message = null;
  if (previous[pay.step]) {
    pay.step = previous[pay.step];
    renderLayer();
    return;
  }
  const session = await api("/api/pay/cancel", {});
  closeScreen("pay");
  applyState(session);
  renderChips(session.chips);
}

function payStep(step, changes = {}) {
  Object.assign(pay, { step, error: null, ...changes });
  renderLayer();
}

// ------------------------------------------------------------ 바코드 리더 (시연)

async function scanBarcode(purpose, code) {
  if (pay.working) return null;
  pay.working = true;
  try {
    const session = await api("/api/scan", { purpose, code });
    pay.error = null;
    return session;
  } catch (error) {
    pay.error = errorText(error);
    renderLayer();
    return null;
  } finally {
    pay.working = false;
  }
}

// onRead(code): 바코드를 리더에 비췄을 때. 실제로는 손님이 휴대폰을 리더에 갖다 대는 행동이다.
function scannerPanel(purpose, onRead) {
  const all = state.config.demo.barcodes;
  const shown = (b) => b.kind === BARCODE_KIND[purpose] || b.kind === "unreadable";  // 인식 실패 시연용은 늘 보여 준다
  const fit = all.filter(shown);
  const others = all.filter((b) => !shown(b));
  const button = (b) => el("button", { class: "demo-code", type: "button", onclick: () => onRead(b.code) },
    el("span", { class: "bars", "aria-hidden": "true" }), el("span", {}, demoLabel(b)), el("small", {}, b.code));
  const input = el("input", { type: "text", maxlength: "64", placeholder: t("scan_manual"), "aria-label": t("scan_manual") });
  return el("div", { class: "scanner" },
    el("h4", {}, t(`scan_title_${purpose}`)),
    el("p", { class: "muted" }, t("scan_guide")),
    pay.error && el("div", { class: "alert" }, el("strong", {}, pay.error), el("span", {}, t("scan_help")),
      el("button", { class: "secondary small", type: "button", onclick: () => callStaff(t("staff_reason_barcode")) }, t("call_staff"))),
    el("div", { class: "demo" },
      el("p", { class: "demo-title" }, t("scan_demo"), el("small", {}, t("demo_label"))),
      el("div", { class: "demo-codes" }, fit.map(button)),
      el("details", {}, el("summary", {}, t("scan_other")), el("div", { class: "demo-codes" }, others.map(button))),
      el("form", { class: "say", onsubmit: (event) => { event.preventDefault(); if (input.value.trim()) onRead(input.value.trim()); } },
        input, el("button", { class: "secondary", type: "submit" }, t("scan_read")))));
}

// 교환권으로 메뉴 고르기: 주문 화면에서 교환권 바코드를 찍으면 그 메뉴가 주문 내역에 담긴다.
function openVoucherScanner() {
  if (state.order.status !== "ordering") {
    showToast(t("err_payment_open"));
    return;
  }
  pay.error = null;
  const close = () => closeScreen("voucher");
  const read = async (code) => {
    const session = await scanBarcode("menu_voucher", code);
    if (!session) return;
    state.started = true;
    close();
    applyState(session);
    renderChips(session.chips);
    showToast(t("voucher_added", { name: session.scan.menu }));
    showLine(session.scan.line_id);
  };
  openScreen({ name: "voucher", render: () => sheet({ title: t("use_voucher"), close, body: scannerPanel("menu_voucher", read) }) });
}

// ------------------------------------------------------------ 결제 단말기 그림
// 카드 투입구·바코드 리더가 어디 있는지 보여 준다. 지금 써야 하는 곳이 강조된다.

function terminalFigure(active, screenText) {
  const part = (key) => "part " + key + (active === key ? " active" : "");
  const label = (key) => el("li", { class: active === key ? "active" : "" }, el("span", { class: `dot ${key}` }), t(`dev_${key}`));
  return el("aside", { class: "terminal-box" },
    el("strong", {}, t("terminal_title")), el("small", { class: "muted" }, t("terminal_where")),
    el("div", { class: "terminal", "aria-hidden": "true" },
      el("div", { class: part("nfc") }, ")))"),
      el("div", { class: "screen" }, screenText || ""),
      el("div", { class: part("barcode") }),
      el("div", { class: "keys" }, Array.from({ length: 9 }, () => el("i"))),
      el("div", { class: part("card_slot") }),
      el("div", { class: part("receipt") })),
    el("ul", { class: "terminal-legend" }, ["nfc", "barcode", "card_slot", "receipt"].map(label)));
}

// ------------------------------------------------------------ 단계별 화면

function orderSummary(order) {
  const { totals } = order;
  const row = (label, value, cls) => el("div", { class: cls }, el("dt", {}, label), el("dd", {}, value));
  return [
    el("ul", { class: "receipt-lines" }, order.lines.map((line) => el("li", {},
      el("span", {}, `${menuName(line)} × ${line.quantity}`, line.voucher && el("span", { class: "tag" }, t("voucher_tag")),
        el("small", {}, optionText(line)), line.notes.map((n) => el("small", { class: "memo" }, `“${n.text}”`))),
      el("span", {}, money(line.line_total - line.covered))))),
    order.notes.length ? el("ul", { class: "notes" }, order.notes.map((n) => el("li", { class: "note" }, n.text))) : null,
    el("dl", { class: "totals" },
      row(t("subtotal"), money(totals.subtotal)),
      totals.voucher ? row(t("voucher_amount"), `−${money(totals.voucher)}`) : null,
      totals.coupon ? row(t("coupon_amount"), `−${money(totals.coupon)}`) : null,
      totals.discount ? row(t("discount_amount"), `−${money(totals.discount)}`) : null,
      row(t("total"), money(totals.total), "grand"),
      totals.paid ? row(t("paid_so_far"), `−${money(totals.paid)}`) : null,
      totals.paid && totals.due ? row(t("due"), money(totals.due), "grand") : null),
  ];
}

async function removeBenefit(kind) {
  try {
    applyState(await api("/api/pay/benefit", { kind }));
  } catch (error) {
    showToast(errorText(error));
  }
}

function benefitsStep(order) {
  const { totals } = order;
  const benefit = (kind, purpose, title, hint, applied) => el("div", { class: "benefit" + (applied ? " on" : "") },
    el("div", {}, el("strong", {}, title), el("small", {}, applied || hint)),
    applied
      ? el("button", { class: "secondary small", type: "button", onclick: () => removeBenefit(kind) }, t("benefit_remove"))
      : el("button", { class: "secondary small", type: "button", onclick: () => payStep("scan", { scanFor: purpose }) }, t("scan_button")));
  const next = totals.due > 0
    ? el("button", { class: "primary wide-button", type: "button", onclick: () => payStep("method") }, t("next_method"))
    : el("button", { class: "primary wide-button", type: "button", onclick: () => tender("none") }, t("finish_free"));
  return [
    el("h4", {}, t("pay_summary")), orderSummary(order),
    el("h4", {}, t("pay_step_benefits")),
    benefit("stamp", "stamp", t("benefit_stamp"), t("benefit_stamp_hint"), order.stamp && t("benefit_stamp_on", { n: totals.stamps })),
    benefit("discount", "membership", t("benefit_discount"), t("benefit_discount_hint"),
      order.discount && `${t(`discount_${order.discount}`)} · −${money(totals.discount)}`),
    benefit("coupon", "coupon", t("benefit_coupon"), t("benefit_coupon_hint"), order.coupon && `${order.coupon.label} · −${money(totals.coupon)}`),
    next,
  ];
}

function scanStep() {
  const purpose = pay.scanFor;
  return scannerPanel(purpose, async (code) => {
    const session = await scanBarcode(purpose, code);
    if (!session) return;
    pay.step = "benefits";
    showToast(t("scan_ok", { label: demoLabel(session.scan) }));
    applyState(session);
  });
}

function dueLine(order) {
  return el("p", { class: "due" }, t("due"), el("strong", {}, money(order.totals.due)));
}

function methodStep(order) {
  const tile = (title, desc, onclick) => el("button", { class: "tile", type: "button", onclick }, el("strong", {}, title), el("small", {}, desc));
  return [
    el("h4", {}, t("method_title")), dueLine(order),
    pay.message && el("p", { class: "notice-line" }, pay.message),
    order.tenders.length ? el("ul", { class: "notes" }, order.tenders.map((x) =>
      el("li", { class: "note" }, t("gift_used", { label: x.label || x.name, amount: money(x.amount) })))) : null,
    el("div", { class: "tiles three" },
      tile(t("method_card"), t("method_card_desc"), () => payStep("terminal", { method: "card", scenario: "ok" })),
      tile(t("method_easy"), t("method_easy_desc"), () => payStep("easy")),
      tile(t("method_gift"), t("method_gift_desc"), () => payStep("terminal", { method: "gift_card" }))),
  ];
}

function easyStep(order) {
  const tile = (title, desc, method) => el("button", { class: "tile", type: "button",
    onclick: () => payStep("terminal", { method, scenario: "ok" }) }, el("strong", {}, title), el("small", {}, desc));
  return [el("h4", {}, t("easy_title")), dueLine(order),
    el("div", { class: "tiles" }, tile(t("easy_tap"), t("easy_tap_desc"), "nfc"), tile(t("easy_barcode"), t("easy_barcode_desc"), "barcode"))];
}

// 카드를 꽂거나 휴대폰을 갖다 댔다. 결과(승인·거절)는 단말기(서버)가 정한다.
async function tender(method) {
  if (pay.working) return;
  pay.working = true;
  try {
    const session = await api("/api/pay/tender", { method, scenario: pay.scenario });
    if (session.approved) {
      if (method === "card") pay.approved = true;  // 카드를 빼야 다음으로 넘어간다
      else pay.step = "receipt";
    }
    applyState(session);
    renderChips(session.chips);
  } catch (error) {
    showToast(errorText(error));
  } finally {
    pay.working = false;
  }
}

function failureBox(order) {
  const { failure } = order;
  if (!failure) return null;
  return el("div", { class: "alert" },
    el("strong", {}, t("fail_title")), el("span", {}, t(`fail_${failure.reason}`)),
    failure.attempts >= 2 && el("span", {}, t("fail_many")),
    el("div", { class: "alert-actions" },
      el("button", { class: "secondary small", type: "button", onclick: () => payStep("method") }, t("other_method")),
      el("button", { class: "secondary small", type: "button", onclick: () => callStaff(t("staff_reason_payment")) }, t("call_staff"))));
}

// 시연용: 손님이 어떤 카드를 꽂는지 고른다. 승인 거절과 다시 시도를 보여 주기 위한 것이다.
function scenarioPicker(kind, title) {
  return el("div", { class: "demo" },
    el("p", { class: "demo-title" }, title, el("small", {}, t("demo_label"))),
    state.config.demo.scenarios[kind].map((s) => el("label", { class: "option" },
      el("input", { type: "radio", name: "scenario", checked: s.id === pay.scenario, onchange: () => { pay.scenario = s.id; } }),
      el("span", { class: "label" }, demoLabel(s)))));
}

function terminalStep(order) {
  const retry = Boolean(order.failure);
  if (pay.method === "card") {
    if (pay.approved) {
      return [el("h4", {}, t("card_approved")),
        el("button", { class: "primary wide-button", type: "button", onclick: () => payStep("receipt", { approved: false }) }, t("card_remove"))];
    }
    return [el("h4", {}, t("method_card")), dueLine(order), el("p", {}, t("card_guide")), failureBox(order),
      scenarioPicker("card", t("demo_card")),
      el("button", { class: "primary wide-button", type: "button", onclick: () => tender("card") }, t(retry ? "retry" : "card_insert"))];
  }
  if (pay.method === "nfc") {
    return [el("h4", {}, t("easy_tap")), dueLine(order), el("p", {}, t("nfc_guide")), failureBox(order),
      scenarioPicker("nfc", t("demo_phone")),
      el("button", { class: "primary wide-button", type: "button", onclick: () => tender("nfc") }, t(retry ? "retry" : "nfc_tap"))];
  }
  const purpose = pay.method === "gift_card" ? "gift_card" : "pay";
  return [dueLine(order), scannerPanel(purpose, async (code) => {
    const session = await scanBarcode(purpose, code);
    if (!session) return;
    if (session.order.status === "paid") pay.step = "receipt";
    else {  // 금액권 잔액이 모자라면 나머지를 다른 수단으로 낸다
      pay.step = "method";
      pay.message = t("gift_more");
    }
    if (purpose === "gift_card") showToast(t("gift_used", { label: demoLabel(session.scan), amount: money(session.scan.amount) }));
    applyState(session);
    renderChips(session.chips);
  })];
}

async function chooseReceipt(print) {
  try {
    const session = await api("/api/pay/receipt", { print_receipt: print });
    pay.step = "done";
    applyState(session);
  } catch (error) {
    showToast(errorText(error));
  }
}

function receiptStep() {
  return [el("h4", {}, t("receipt_question")),
    el("div", { class: "tiles" },
      el("button", { class: "tile", type: "button", onclick: () => chooseReceipt(true) }, el("strong", {}, t("receipt_print"))),
      el("button", { class: "tile", type: "button", onclick: () => chooseReceipt(false) }, el("strong", {}, t("receipt_skip"))))];
}

function receiptPaper(order) {
  const { receipt, totals } = order;
  return el("div", { class: "paper" },
    el("strong", {}, t("receipt_paper_title")), el("small", {}, `${state.config.store.name} · ${receipt.paid_at}`),
    el("div", { class: "paper-number" }, `${t("order_number")} ${receipt.order_number}`),
    order.lines.map((line) => el("div", { class: "paper-row" }, el("span", {}, `${menuName(line)} × ${line.quantity}`), el("span", {}, money(line.line_total)))),
    totals.voucher ? el("div", { class: "paper-row" }, el("span", {}, t("voucher_amount")), el("span", {}, `−${money(totals.voucher)}`)) : null,
    totals.coupon ? el("div", { class: "paper-row" }, el("span", {}, t("coupon_amount")), el("span", {}, `−${money(totals.coupon)}`)) : null,
    totals.discount ? el("div", { class: "paper-row" }, el("span", {}, t("discount_amount")), el("span", {}, `−${money(totals.discount)}`)) : null,
    el("div", { class: "paper-row total" }, el("span", {}, t("total")), el("span", {}, money(totals.total))),
    receipt.payments.map((p) => el("div", { class: "paper-row" }, el("span", {}, p.name), el("span", {}, money(p.amount)))));
}

function doneStep(order) {
  const { receipt, totals } = order;
  let paper = el("p", { class: "muted" }, t("receipt_none"));
  if (receipt.print_receipt && !pay.receiptTaken) {
    paper = [el("p", { class: "muted" }, t("receipt_printing")), receiptPaper(order),
      el("button", { class: "secondary wide-button", type: "button", onclick: () => payStep("done", { receiptTaken: true }) }, t("take_receipt"))];
  } else if (receipt.print_receipt) {
    paper = el("p", { class: "muted" }, t("receipt_taken"));
  }
  return [
    el("h4", {}, t("order_done")),
    el("div", { class: "order-number" }, t("order_number"), el("strong", {}, String(receipt.order_number)), el("small", {}, t("order_number_hint"))),
    orderSummary(order),
    el("p", { class: "muted" }, [`${t(order.dine_option)} · ${t("payment")}: ${receipt.payments.map((p) => p.name).join(" + ") || "-"}`,
      totals.stamps ? t("stamps", { n: totals.stamps }) : null].filter(Boolean).join(" · ")),
    paper,
    el("button", { class: "primary wide-button", type: "button", onclick: restart }, t("new_order")),
  ];
}

function renderPay() {
  const { order } = state;
  const steps = ["benefits", "method", "pay", "receipt"];
  const at = { benefits: 0, scan: 0, method: 1, easy: 1, terminal: 2, receipt: 3, done: 3 }[pay.step];
  const content = {
    benefits: () => benefitsStep(order), scan: scanStep, method: () => methodStep(order), easy: () => easyStep(order),
    terminal: () => terminalStep(order), receipt: receiptStep, done: () => doneStep(order),
  }[pay.step]();
  // 지금 단계에서 써야 하는 기기를 단말기 그림에서 강조한다
  let device = null;
  if (pay.step === "scan" || (pay.step === "terminal" && ["barcode", "gift_card"].includes(pay.method))) device = "barcode";
  else if (pay.step === "terminal") device = pay.method === "card" ? "card_slot" : "nfc";
  else if (pay.step === "done" && order.receipt.print_receipt && !pay.receiptTaken) device = "receipt";
  const screenText = order.status === "paid" ? money(order.totals.total) : pay.step === "terminal" || pay.step === "method" ? money(order.totals.due) : "";
  const paid = order.status === "paid";
  return el("section", { class: "sheet wide pay", role: "dialog", "aria-modal": "true" },
    el("header", { class: "sheet-head" },
      el("ol", { class: "steps" }, steps.map((s, i) => el("li", { class: i === at ? "now" : i < at ? "done" : "" }, `${i + 1} ${t(`pay_step_${s}`)}`)))),
    el("div", { class: "sheet-body pay-body" }, el("div", { class: "pay-main" }, content), terminalFigure(device, screenText)),
    !paid && el("footer", { class: "sheet-actions help-row" },
      el("button", { class: "secondary", type: "button", disabled: pay.approved, onclick: goBack }, t("back")),
      el("span", { class: "muted" }, t("need_help")),
      // 결제 화면을 잠시 접고 대화로 물어본다(결제는 그대로 진행 중이고, 주문 내역의 안내로 돌아온다)
      el("button", { class: "secondary", type: "button", disabled: pay.approved, onclick: () => { closeScreen("pay"); $("#input").focus(); } }, t("ask_chat")),
      el("button", { class: "secondary", type: "button", onclick: () => callStaff(t("staff_reason_payment")) }, t("call_staff"))));
}
