// 공차 키오스크 화면. 서버(app/server.py)의 API 를 부르고, 대화 답변은 SSE 로 받아 흘려 보여 준다.
// 문구는 i18n.js, 결제 화면과 바코드 리더는 pay.js 에 있다.
//
// 주문을 바꾸는 길은 둘이다.
//  - 손님이 버튼으로 고치면 바로 반영된다.
//  - 대화로 말하면 모델이 변경안을 올리고, 변경 확인 화면에서 [확인]을 눌러야 반영된다.

// 변경안을 올리는 도구들 (진행 중 문구를 하나로 보여 준다)
const PROPOSE_TOOLS = ["add_to_cart", "update_cart_item", "remove_cart_item", "add_request_note", "remove_request_note",
  "set_order_options", "undo_last_change"];

const state = {
  sessionId: null, lang: "ko", config: null, tabs: [], activeTab: null,
  order: null, pending: null, canUndo: false, busy: false, started: false,
  staffCall: null, stockVersion: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function t(key, vars = {}) {
  const text = (I18N[state.lang] || I18N.ko)[key] ?? I18N.ko[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? "");
}

// 다른 언어 화면에서는 번역한 메뉴명을 보여 준다 (번역 데이터: data/curated/menu_i18n.json).
function menuName(item) {
  return (state.lang !== "ko" && item.names && (item.names[state.lang] || item.names.en)) || item.name;
}

// 메뉴 데이터에 든 한국어 낱말(카테고리, 맛, 알레르기 성분, 영양 항목 등)을 화면 언어로 바꾼다. 없는 낱말은 그대로 둔다.
function word(text) {
  return (VOCAB[state.lang] && VOCAB[state.lang][text]) || text;
}

function words(list) {
  return list.map(word).join(", ");
}

function money(amount) {
  return state.lang === "ko" ? `${amount.toLocaleString("ko-KR")}원` : `₩${amount.toLocaleString("en-US")}`;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat(Infinity).filter((child) => child != null && child !== false));
  return node;
}

class ApiError extends Error {
  constructor(message, code) {
    super(message);
    this.code = code;
  }
}

async function api(path, body) {
  const options = body && {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, ...body }),
  };
  const response = await fetch(path, options || undefined);
  if (!response.ok) {
    const { detail } = await response.json().catch(() => ({}));
    if (detail && typeof detail === "object" && !Array.isArray(detail)) throw new ApiError(detail.message, detail.code);
    throw new ApiError(typeof detail === "string" ? detail : response.statusText);
  }
  return response.json();
}

// 서버가 준 오류 코드에 맞는 문구가 있으면 화면 언어로, 없으면 서버 문구 그대로.
function errorText(error) {
  return error.code && I18N.ko[`err_${error.code}`] ? t(`err_${error.code}`) : error.message;
}

// node 의 내용을 통째로 바꾼다. 조건에 따라 빠진(null) 자리는 건너뛴다.
function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((child) => child != null && child !== false));
}

function showToast(text) {
  const toast = $("#toast");
  toast.textContent = text;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => (toast.hidden = true), 4000);
}

// ------------------------------------------------------------ 글 표시

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// 모델 답변에서 쓰는 최소한의 서식만 그린다: **굵게**, "- " 목록, 줄바꿈.
function renderText(raw) {
  const bold = (line) => line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  let html = "";
  let inList = false;
  for (const line of escapeHtml(raw).split("\n")) {
    const item = line.match(/^\s*[-•*]\s+(.*)$/);
    if (item) {
      html += (inList ? "" : "<ul>") + `<li>${bold(item[1])}</li>`;
      inList = true;
    } else {
      html += inList ? "</ul>" : "";
      inList = false;
      if (line.trim()) html += `<p>${bold(line)}</p>`;
    }
  }
  return html + (inList ? "</ul>" : "");
}

// ------------------------------------------------------------ 겹쳐 뜨는 화면 (시작·옵션·추가 제안·변경 확인·결제)
// 화면들은 상단 바 아래에 뜬다. 그래서 [이전] [처음으로] [시간 연장]은 어느 화면에서나 누를 수 있다.
// screen = { name, render(): Node, back }  back: 함수면 [이전]을 눌렀을 때 부르고, false 면 [이전]을 막고, 없으면 그냥 닫는다.

const screens = [];

const topScreen = () => screens[screens.length - 1];

function renderLayer() {
  const top = topScreen();
  $("#layer").hidden = !top;
  $("#main").inert = Boolean(top);
  $(".bottom-nav").inert = Boolean(top);
  $("#layer").replaceChildren(...(top ? [top.render()] : []));
  updateNav();
}

function openScreen(screen) {
  const at = screens.findIndex((s) => s.name === screen.name);
  if (at >= 0) screens.splice(at, 1);
  screens.push(screen);
  renderLayer();
}

function closeScreen(name) {
  const at = screens.findIndex((s) => s.name === name);
  if (at < 0) return;
  screens.splice(at, 1);
  renderLayer();
}

function goBack() {
  const top = topScreen();
  if (!top) openStart();  // 주문 화면의 앞 화면은 시작 화면이다
  else if (typeof top.back === "function") top.back();
  else if (top.back !== false) closeScreen(top.name);
}

function updateNav() {
  const top = topScreen();
  const paid = state.order && state.order.status === "paid";
  $("#back-button").disabled = paid || Boolean(top && top.back === false);
}

// 화면 하나의 틀: 제목, [닫기], 내용, 아래 버튼.
function sheet({ title, sub, body, actions, close, wide, cls }) {
  return el("section", { class: `sheet${wide ? " wide" : ""}${cls ? ` ${cls}` : ""}`, role: "dialog", "aria-modal": "true" },
    el("header", { class: "sheet-head" },
      el("div", {}, el("h3", {}, title), sub && el("p", { class: "muted" }, sub)),
      close && el("button", { class: "secondary small", type: "button", onclick: close }, t("close"))),
    el("div", { class: "sheet-body" }, body),
    actions && el("footer", { class: "sheet-actions" }, actions));
}

// "정말 할까요?"를 묻는 작은 화면.
function ask(name, { title, body, yes, onYes }) {
  const close = () => closeScreen(name);
  openScreen({ name, render: () => sheet({ title, sub: body, close,
    actions: [el("button", { class: "secondary", type: "button", onclick: close }, t("keep_ordering")),
      el("button", { class: "primary", type: "button", onclick: () => { close(); onYes(); } }, yes)] }) });
}

// ------------------------------------------------------------ 시작 화면: 언어, 큰 글씨·낮은 화면, 매장·포장

function languageButtons() {
  return el("div", { class: "segmented" }, Object.entries(state.config.languages).map(([code, name]) =>
    el("button", { type: "button", "aria-pressed": String(code === state.lang), onclick: () => changeLanguage(code) }, name)));
}

function setDisplay(option, on) {
  if (option === "big") document.documentElement.classList.toggle("big-text", on);
  else document.body.classList.toggle("low-screen", on);
  $("#big-text-toggle").setAttribute("aria-pressed", String(document.documentElement.classList.contains("big-text")));
  $("#low-screen-toggle").setAttribute("aria-pressed", String(document.body.classList.contains("low-screen")));
  if (topScreen() && topScreen().name === "start") renderLayer();
}

function openStart() {
  const choose = async (dine) => {
    try {
      const session = await api("/api/order/options", { dine_option: dine });
      state.started = true;
      closeScreen("start");
      applyState(session);
      renderChips(session.chips);
      $("#input").focus();
    } catch (error) {
      showToast(errorText(error));
    }
  };
  const toggle = (option, label, on) =>
    el("button", { type: "button", "aria-pressed": String(on), onclick: () => setDisplay(option, !on) }, label);
  openScreen({ name: "start", back: false, render: () => {
    const { order } = state;
    const dine = (key) => el("button", { class: "tile" + (order.dine_option === key ? " selected" : ""), type: "button",
      onclick: () => choose(key) }, t(`${key}_long`));
    return el("section", { class: "sheet start", role: "dialog", "aria-modal": "true" },
      el("div", { class: "sheet-body" },
        el("p", { class: "start-store" }, storeName()),
        el("h3", {}, t("start_title")),
        el("p", { class: "muted" }, t("start_sub")),
        el("div", { class: "start-row" }, el("span", {}, t("start_language")), languageButtons()),
        el("div", { class: "start-row" }, el("span", {}, t("start_screen")),
          el("div", { class: "segmented" },
            toggle("big", t("big_text"), document.documentElement.classList.contains("big-text")),
            toggle("low", t("low_screen"), document.body.classList.contains("low-screen")))),
        el("h4", {}, t("start_where")),
        el("div", { class: "tiles" }, dine("dine_in"), dine("takeout")),
        order.dine_option && el("button", { class: "secondary wide-button", type: "button",
          onclick: () => { state.started = true; closeScreen("start"); } }, t("start_continue"))));
  } });
}

// ------------------------------------------------------------ 메뉴판

function badgeLabel(badge) {
  const rank = badge.match(/^인기(\d)위$/);
  if (rank) return state.lang === "ko" ? badge : `No.${rank[1]}`;
  return { 베스트: t("badge_best"), 신메뉴: t("badge_new") }[badge] || badge;
}

function menuCard(item, onPick) {
  const prices = item.prices.map((p) => p.price);
  const price = prices.length ? money(Math.min(...prices)) + (prices.length > 1 ? "~" : "") : item.price ? money(item.price) : t("price_ask");
  const cls = "card" + (item.orderable ? "" : " unavailable") + (item.sold_out ? " sold-out" : "");
  return el("button", { class: cls, type: "button", onclick: () => (onPick ? onPick(item) : openDetail(item.id)) },
    el("div", { class: "badges" }, item.badges.map((b) => el("span", { class: "badge" + (b.startsWith("인기") ? "" : " soft") }, badgeLabel(b)))),
    el("div", { class: "photo" }, item.image && el("img", { src: item.image, alt: "", loading: "lazy" }),
      item.sold_out && el("span", { class: "sold-out-mark" }, t("sold_out"))),
    el("div", { class: "info" }, el("div", { class: "name" }, menuName(item)), el("div", { class: "price" }, price)));
}

function renderBoard() {
  const tabLabel = (tab) => (I18N.ko[`tab_${tab.id}`] ? t(`tab_${tab.id}`) : tab.label);
  $("#tabs").replaceChildren(...state.tabs.map((tab) =>
    el("button", { type: "button", role: "tab", "aria-selected": String(tab.id === state.activeTab),
      onclick: () => { state.activeTab = tab.id; renderBoard(); $("#grid").scrollTop = 0; } }, tabLabel(tab))));
  const active = state.tabs.find((tab) => tab.id === state.activeTab);
  $("#grid").replaceChildren(...(active ? active.items.map((item) => menuCard(item)) : []));
}

// 품절은 실시간 값이라 바뀌면 메뉴판을 다시 받아 온다.
async function reloadMenu() {
  const menu = await api("/api/menu");
  state.tabs = menu.tabs;
  state.stockVersion = menu.stock.version;
  renderBoard();
}

// ------------------------------------------------------------ 메뉴 상세 · 옵션 화면

const toppingName = (x) => (I18N[state.lang] || {})[`topping_${x.id}`] || x.name;
const toppingDescription = (x) => (I18N[state.lang] || {})[`topping_desc_${x.id}`] || x.description;
const sizeName = (size) => {
  const names = state.config.sizes[size];
  return names ? (state.lang === "ko" ? names.name : names.name_en) : size;
};

function optionGroup(title, badge, ...children) {
  return el("section", { class: "option-group" },
    el("div", { class: "option-head" }, el("span", {}, title),
      badge && el("span", { class: "pill" + (badge === "optional" ? " optional" : "") }, t(badge))),
    children);
}

// choices: [{ value, label, extra(오른쪽 금액), tag(추천 표시), desc(뜻 설명), disabled }]
function radioOptions(name, choices, selected, onPick) {
  return choices.map((c) => el("label", { class: "option" },
    el("input", { type: "radio", name, checked: c.value === selected, disabled: c.disabled, onchange: () => onPick(c.value) }),
    el("span", { class: "label" }, c.tag && el("small", { class: "pick" }, c.tag), c.label, c.desc && el("small", { class: "desc" }, c.desc)),
    c.extra && el("span", { class: "extra" }, c.extra)));
}

function menuFacts(item) {
  const facts = [];
  if (!item.orderable) facts.push([t("price"), item.price ? money(item.price) : t("price_ask")]);
  if (item.kind === "drink") {
    if (item.included_toppings.length) facts.push([t("toppings_included"), words(item.included_toppings)]);
    if (item.flavors.length) facts.push([t("flavor"), words(item.flavors)]);
    if (item.caffeine) facts.push([t("caffeine"), word(item.caffeine)]);
  }
  facts.push([t("allergens"), item.allergens.length ? words(item.allergens) : t("none")]);
  let nutrition = null;
  if (item.nutrition && item.nutrition.length) {
    const columns = Object.keys(item.nutrition[0].values);
    nutrition = el("table", { class: "nutrition" },
      el("thead", {}, el("tr", {}, el("th", {}, ""), columns.map((c) => el("th", {}, word(c))))),
      el("tbody", {}, item.nutrition.map((row) => el("tr", {}, el("td", {}, word(row.label)), columns.map((c) => el("td", {}, String(row.values[c] ?? "-")))))));
  }
  return el("details", { class: "more", open: !item.orderable },
    el("summary", {}, t("more_info")),
    el("dl", { class: "facts" }, facts.map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
    nutrition,
    nutrition && item.nutrition_basis && el("div", { class: "muted" }, word(item.nutrition_basis)),
    el("button", { class: "link", type: "button",
      onclick: () => { closeScreen("detail"); send(t("ask_allergy_template", { name: item.name })); } }, t("ask_allergy")));
}

// 옵션 선택지를 그리고, 지금 고른 값(choice)을 돌려준다. line 이 있으면 담긴 줄의 값으로 채운다.
function optionForm(item, line, onChange) {
  const { config, order } = state;
  const takeout = order.dine_option === "takeout";
  const locked = Boolean(line && line.voucher);  // 교환권 메뉴는 사이즈와 수량이 고정이다
  const choice = line
    ? { size: line.size, temperature: line.temperature, sugar: line.sugar,
        ice: line.ice ?? (item.ice.adjustable ? "regular" : null),
        toppings: new Set(line.toppings.map((x) => x.id)), quantity: line.quantity, cup: line.cup }
    : { size: item.prices[0].size, temperature: item.temperatures[0],
        sugar: item.sugar.adjustable ? item.sugar.recommended : null, ice: item.ice.adjustable ? "regular" : null,
        toppings: new Set(), quantity: 1, cup: order.dine_option === "dine_in" ? "store" : "disposable" };
  choice.price = () => {
    const base = item.prices.find((p) => p.size === choice.size).price;
    const extra = config.toppings.filter((x) => choice.toppings.has(x.id)).reduce((sum, x) => sum + x.price, 0);
    return (base + extra) * choice.quantity - (locked ? base : 0);
  };

  const quantity = el("span", {}, String(choice.quantity));
  const boxes = [];
  let iceSection = null;
  const basicCount = () => config.toppings.filter((x) => x.basic && choice.toppings.has(x.id)).length;
  const refresh = () => {
    quantity.textContent = String(choice.quantity);
    if (iceSection) iceSection.hidden = choice.temperature !== "ICE";
    for (const box of boxes) box.disabled = !box.checked && basicCount() >= config.max_toppings;
    onChange(choice);
  };
  const pick = (key) => (value) => { choice[key] = value; refresh(); };
  const step = (delta) => () => {
    choice.quantity = Math.min(config.max_quantity, Math.max(1, choice.quantity + delta));
    refresh();
  };

  // 조절 가능 여부: true 면 단계 선택, false 면 고정, null(미확인)이면 '기본'을 기본값으로 두고 단계도 고를 수 있게 한다.
  const levels = (name, adjustable, options) => {
    if (adjustable === false) return [el("p", { class: "muted" }, t("fixed_note"))];
    const unknown = adjustable == null;
    return [
      radioOptions(name, unknown ? [{ value: null, label: t("default_option") }, ...options] : options, choice[name], pick(name)),
      unknown && el("p", { class: "muted" }, t("unknown_hint")),
    ];
  };
  // 당도 단계마다 뜻을 적어 둔다 (기존 키오스크의 막힘: 당도 % 의 뜻을 알 방법이 없다)
  const sugarOptions = config.sugar_levels.map((n) => (
    { value: n, label: `${n}%`, desc: t(`sugar_desc_${n}`), tag: n === item.sugar.recommended ? t("recommended_tag") : null }));
  const iceOptions = config.ice_levels.map((key) => ({ value: key, label: t(`ice_${key}`) }));
  iceSection = optionGroup(t("ice"), item.ice.adjustable === false ? null : "required",
    levels("ice", item.ice.adjustable, iceOptions), item.ice.adjustable !== false && el("p", { class: "muted" }, t("ice_none_hint")));

  const topping = (x) => {
    const box = el("input", { type: "checkbox", checked: choice.toppings.has(x.id), onchange: () => {
      if (box.checked) choice.toppings.add(x.id); else choice.toppings.delete(x.id);
      refresh();
    } });
    if (x.basic) boxes.push(box);  // 샷추가는 최대 개수에 세지 않는다
    return el("label", { class: "option" }, box,
      el("span", { class: "label" }, toppingName(x), el("small", { class: "desc" }, toppingDescription(x))),
      el("span", { class: "extra" }, `+${money(x.price)}`));
  };
  const sizes = item.prices.map((p) => {
    const ml = p.volume_ml && (p.volume_ml.ICE || p.volume_ml.HOT);
    return { value: p.size, label: p.size, desc: ml ? t("size_volume", { name: sizeName(p.size), ml }) : sizeName(p.size),
      extra: money(p.price), disabled: locked && p.size !== choice.size };
  });
  const cups = config.cups.map((c) => (
    { value: c.id, label: t(`cup_${c.id}`), desc: t(`cup_desc_${c.id}`), disabled: c.dine_in_only && takeout }));

  const node = el("div", { class: "option-form" },
    locked && el("p", { class: "notice-line" }, t("voucher_locked")),
    el("div", { class: "quantity-row" }, el("strong", {}, t("quantity")),
      el("span", { class: "stepper" },
        el("button", { type: "button", "aria-label": "-", disabled: locked, onclick: step(-1) }, "−"), quantity,
        el("button", { type: "button", "aria-label": "+", disabled: locked, onclick: step(1) }, "+"))),
    optionGroup(t("size"), "required", radioOptions("size", sizes, choice.size, pick("size"))),
    optionGroup("HOT / ICE", "required",
      radioOptions("temperature", item.temperatures.map((v) => ({ value: v, label: v })), choice.temperature, pick("temperature"))),
    optionGroup(t("sugar"), item.sugar.adjustable === false ? null : "required",
      item.sugar.adjustable !== false && el("p", { class: "muted" }, t("sugar_meaning")),
      levels("sugar", item.sugar.adjustable, sugarOptions)),
    iceSection,
    optionGroup(t("toppings_choice"), "optional",
      el("p", { class: "muted" }, t("toppings_max", { n: config.max_toppings })), config.toppings.filter((x) => x.basic).map(topping)),
    optionGroup(t("shot_group"), "optional", config.toppings.filter((x) => !x.basic).map(topping)),
    optionGroup(t("cup"), "required", radioOptions("cup", cups, choice.cup, pick("cup")),
      takeout && el("p", { class: "muted" }, t("cup_store_takeout"))));
  refresh();
  return { node, choice };
}

// 메뉴 카드를 누르면 열린다. line 을 주면 담긴 줄의 옵션을 바꾸는 화면이 된다.
// draft: 변경 확인 화면에서 온 것(변경안의 줄을 고친다) / fromSuggestion: 추가 제안 창에서 고른 메뉴
async function openDetail(id, line = null, { draft = false, fromSuggestion = false } = {}) {
  let item;
  try {
    item = await api(`/api/menu/${encodeURIComponent(id)}`);
  } catch (error) {
    showToast(errorText(error));
    return;
  }
  const close = () => closeScreen("detail");
  const closeButton = el("button", { class: "secondary", type: "button", onclick: close }, t("close"));
  const askButton = el("button", { class: "secondary", type: "button",
    onclick: () => { close(); send(t("ask_template", { name: item.name })); } }, t("ask_menu"));
  const header = [
    el("div", { class: "photo" }, item.image && el("img", { src: item.image, alt: item.name })),
    el("h3", {}, menuName(item)),
    el("div", { class: "muted" }, word(item.category)),
    item.description && el("p", { class: "description" }, (state.lang !== "ko" && item.descriptions[state.lang]) || item.description),
    // 가격·칼로리·카페인은 펼치지 않아도 바로 보인다
    item.kind === "drink" && item.kcal != null && el("p", { class: "quick-facts" },
      t("quick_facts", { kcal: item.kcal, mg: item.caffeine_mg ?? "-" }), el("small", {}, t("quick_basis", { basis: item.ref_basis }))),
  ];
  let node;
  if (!item.orderable || (item.sold_out && !line)) {
    node = el("section", { class: "sheet", role: "dialog", "aria-modal": "true" },
      el("div", { class: "sheet-body" }, header,
        el("p", { class: "notice-line" }, t(item.sold_out ? "sold_out_notice" : "not_orderable")), menuFacts(item)),
      el("footer", { class: "sheet-actions" }, closeButton, askButton));
  } else {
    const submit = el("button", { class: "primary", type: "button" });
    const form = optionForm(item, line, (choice) => {
      submit.textContent = t(draft ? "draft_button" : line ? "save_button" : "add_button", { price: money(choice.price()) });
    });
    submit.addEventListener("click", async () => {
      const { choice } = form;
      submit.disabled = true;
      try {
        const session = await api("/api/order/item", {
          ...(line ? { line_id: line.line_id } : { menu_id: item.id }), draft,
          size: choice.size, temperature: choice.temperature, sugar: choice.sugar,
          ice: choice.temperature === "ICE" ? choice.ice : null,
          toppings: [...choice.toppings], quantity: choice.quantity, cup: choice.cup,
        });
        close();
        applyState(session);
        renderChips(session.chips);
        if (draft) return;  // 변경 확인 화면으로 돌아가 [확인]을 받는다
        state.started = true;
        showToast([t(line ? "saved" : "added", { name: menuName(item) }), ...session.notes].join(" "));
        if (!line && !fromSuggestion && session.suggestions && session.suggestions.length) openSuggest(item, session);
        else showLine(session.line_id);
      } catch (error) {
        showToast(errorText(error));
        submit.disabled = false;
      }
    });
    // 옵션을 한 문장으로 말하기: 여기에 적으면 대화로 넘어가 옵션 값으로 바뀌고, 변경 확인 화면에서 확인한다
    const sayInput = el("input", { type: "text", maxlength: "200", placeholder: t("say_placeholder"), "aria-label": t("say_options") });
    const say = (event) => {
      event.preventDefault();
      const text = sayInput.value.trim();
      if (!text) return;
      close();
      send(line ? t("say_edit", { n: line.line_id, name: item.name, text }) : t("say_new", { name: item.name, text }));
    };
    const talk = !draft && el("section", { class: "option-group talk" },
      el("div", { class: "option-head" }, el("span", {}, t("say_options"))),
      el("form", { class: "say", onsubmit: say }, sayInput, el("button", { class: "secondary", type: "submit" }, t("say_send"))),
      el("button", { class: "link", type: "button",
        onclick: () => { close(); send(t("ask_options_template", { name: item.name })); } }, t("ask_options")));
    node = el("section", { class: "sheet", role: "dialog", "aria-modal": "true" },
      el("div", { class: "sheet-body" }, header, form.node, talk, menuFacts(item)),
      el("footer", { class: "sheet-actions" }, closeButton, line || draft ? null : askButton, submit));
  }
  openScreen({ name: "detail", render: () => node });
}

// ------------------------------------------------------------ 추가 제안 창
// 담았다는 것을 먼저 알리고(기존 키오스크: 담자마자 창이 떠서 담겼는지 헷갈림), 함께 살 만한 메뉴를 보여 준다.

function openSuggest(item, session) {
  const close = () => { closeScreen("suggest"); showLine(session.line_id); };
  openScreen({ name: "suggest", back: close, render: () => sheet({
    title: `✓ ${t("suggest_title", { name: menuName(item) })}`, sub: t("suggest_sub"), close,
    body: el("div", { class: "suggest-cards" }, session.suggestions.map((card) =>
      menuCard(card, () => { closeScreen("suggest"); openDetail(card.id, null, { fromSuggestion: true }); }))),
    actions: [el("button", { class: "primary", type: "button", onclick: close }, t("suggest_no"))],
  }) });
}

// ------------------------------------------------------------ 주문 내역

function optionText(line) {
  const parts = [line.size, line.temperature];
  parts.push(line.sugar != null ? `${t("sugar")} ${line.sugar}%` : t(`sugar_${line.sugar_note}`));
  if (line.ice) parts.push(t(`ice_${line.ice}`));
  else if (line.ice_note !== "hot") parts.push(t(`ice_${line.ice_note}`));
  if (line.toppings.length) parts.push("+ " + line.toppings.map(toppingName).join(", "));
  parts.push(t(`cup_${line.cup}`));
  return parts.join(" · ");
}

function noteRow(note, removable) {
  return el("li", { class: "note" }, el("span", {}, note.text),
    removable && el("button", { class: "link", type: "button", "aria-label": t("remove"),
      onclick: () => update("/api/order/note", { note_id: note.note_id }) }, "✕"));
}

// 담은 뒤에는 주문 내역을 보여 주고, 방금 담은 줄을 잠깐 강조한다.
function showLine(lineId) {
  document.body.dataset.view = "cart";
  const row = $(`#lines .line[data-line="${lineId}"]`);
  if (row) {
    row.classList.add("flash");
    row.scrollIntoView({ block: "nearest" });
  }
}

function renderCart() {
  const { order } = state;
  if (!order) return;
  const { totals } = order;
  const editable = order.status === "ordering";
  $("#cart-count").textContent = totals.count || "";
  $("#nav-count").textContent = totals.count || "";
  $("#pending-banner").hidden = !state.pending;
  $("#paying-banner").hidden = order.status !== "paying";
  for (const button of $$("#dine button")) {
    button.setAttribute("aria-pressed", String(button.dataset.dine === order.dine_option));
    button.disabled = !editable;
  }
  $("#takeout-extras").hidden = order.dine_option !== "takeout";
  $("#straw").checked = Boolean(order.straw);
  $("#carrier").checked = Boolean(order.carrier);
  $("#straw").disabled = $("#carrier").disabled = !editable;

  const changeLine = (line, quantity) => update("/api/order/line", { line_id: line.line_id, quantity });
  $("#lines").replaceChildren(...(order.lines.length ? order.lines.map((line) =>
    el("div", { class: "line" + (line.sold_out ? " sold-out" : ""), "data-line": line.line_id },
      line.image ? el("img", { src: line.image, alt: "" }) : el("span"),
      el("div", {},
        el("div", { class: "name" }, menuName(line),
          line.voucher && el("span", { class: "tag" }, t("voucher_tag")),
          line.sold_out && el("span", { class: "tag warn" }, t("line_sold_out"))),
        el("div", { class: "options" }, optionText(line)),
        line.notes.length ? el("ul", { class: "notes" }, line.notes.map((n) => noteRow(n, editable))) : null,
        el("div", { class: "bottom" },
          el("span", { class: "stepper" },
            el("button", { type: "button", "aria-label": "-", disabled: !editable || line.voucher, onclick: () => changeLine(line, line.quantity - 1) }, "−"),
            el("span", {}, String(line.quantity)),
            el("button", { type: "button", "aria-label": "+", disabled: !editable || line.voucher, onclick: () => changeLine(line, line.quantity + 1) }, "+")),
          el("span", { class: "amount" }, money(line.line_total - line.covered))),
        el("div", { class: "line-actions" },
          el("button", { class: "link", type: "button", disabled: !editable, onclick: () => openDetail(line.menu_id, line) }, t("edit")),
          el("button", { class: "link", type: "button", disabled: !editable, onclick: () => changeLine(line, 0) }, t("remove"))))))
    : [el("div", { class: "empty" }, t("cart_empty"))]));

  // 요청 사항: 주문 전체에 대한 메모. 남기는 것은 대화로 한다(모델이 짧은 메모로 정리해 변경 확인 화면에 올린다).
  fill($("#requests"),
    el("div", { class: "requests-head" }, el("strong", {}, t("requests_title")),
      el("button", { class: "link", type: "button", disabled: !editable, onclick: () => {
        document.body.dataset.view = "chat";
        $("#input").value = t("request_prefill");
        $("#input").focus();
      } }, t("request_add"))),
    order.notes.length ? el("ul", { class: "notes" }, order.notes.map((n) => noteRow(n, editable))) : null,
    order.notes.length || order.lines.some((l) => l.notes.length) ? el("p", { class: "muted" }, t("request_caveat")) : null);

  const row = (label, value, cls) => el("div", { class: cls }, el("dt", {}, label), el("dd", {}, value));
  fill($("#totals"),
    row(t("subtotal"), money(totals.subtotal)),
    totals.voucher ? row(t("voucher_amount"), `−${money(totals.voucher)}`) : null,
    totals.coupon ? row(t("coupon_amount"), `−${money(totals.coupon)}`) : null,
    totals.discount ? row(t("discount_amount"), `−${money(totals.discount)}`) : null,
    row(t("total"), money(totals.total), "grand"));
  $("#clear-button").disabled = !editable || !order.lines.length;
  $("#undo-button").disabled = !state.canUndo;
  $("#pay").disabled = !editable || !order.lines.length || state.busy;
}

// 서버가 돌려준 상태(언어·주문·변경안·직원 호출·품절)를 화면에 반영한다.
function applyState(next) {
  state.order = next.order;
  state.pending = next.pending;
  state.canUndo = next.can_undo;
  if (next.language !== state.lang) {
    state.lang = next.language;
    relabel();
  }
  renderCart();
  showStaffCall(next.staff_call);
  if (next.stock && next.stock.version !== state.stockVersion) reloadMenu();
  const top = topScreen();
  if (top && top.name === "confirm" && !state.pending) closeScreen("confirm");
  else if (top && (top.name === "confirm" || top.name === "pay")) renderLayer();
  updateNav();
}

function showStaffCall(call) {
  const before = state.staffCall;
  if (call && (!before || before.id !== call.id || before.time !== call.time)) showToast(t("staff_called"));
  else if (call && before && before.status !== call.status && call.status === "coming") showToast(t("staff_coming"));
  state.staffCall = call && { ...call };
}

async function update(path, body) {
  try {
    const session = await api(path, body);
    applyState(session);
    renderChips(session.chips);
    return session;
  } catch (error) {
    showToast(errorText(error));
    renderCart();
    return null;
  }
}

async function callStaff(reason) {
  try {
    applyState({ ...(await api("/api/staff", reason ? { reason } : {})) });
  } catch (error) {
    showToast(errorText(error));
  }
}

// ------------------------------------------------------------ 변경 확인 화면
// 모델이 올린 변경안(메뉴·옵션·수량·금액)을 손님이 눌러 확인한다. [확인]을 누르기 전에는 주문이 바뀌지 않는다.

function fieldChange(field, before, after) {
  const value = (line) => ({
    size: line.size, temperature: line.temperature,
    sugar: line.sugar != null ? `${line.sugar}%` : t(`sugar_${line.sugar_note}`),
    ice: line.ice ? t(`ice_${line.ice}`) : line.ice_note === "hot" ? "HOT" : t(`ice_${line.ice_note}`),
    toppings: line.toppings.length ? line.toppings.map(toppingName).join(", ") : t("no_toppings"),
    quantity: t("cups_unit", { n: line.quantity }), cup: t(`cup_${line.cup}`),
  })[field];
  return el("li", {}, el("span", { class: "field" }, t(`field_${field}`)),
    el("s", {}, value(before)), " → ", el("strong", {}, value(after)));
}

function changeCard(change) {
  const head = (kind, label, name, amount) => el("div", { class: "change-head" },
    el("span", { class: `change-kind ${kind}` }, label), el("strong", {}, name), amount && el("span", { class: "amount" }, amount));
  const lineNotes = (line) => line.notes.length ? el("ul", { class: "notes" }, line.notes.map((n) => noteRow(n, false))) : null;
  const editButton = (line) => el("button", { class: "link", type: "button",
    onclick: () => openDetail(line.menu_id, line, { draft: true }) }, t("confirm_edit"));
  const who = (note) => note.line_name || t("request_for_order");
  switch (change.type) {
    case "add":
      return el("li", { class: "change" }, head("add", t("change_add"), menuName(change.after), money(change.after.line_total - change.after.covered)),
        el("div", { class: "options" }, optionText(change.after), " · ", t("cups_unit", { n: change.after.quantity })),
        lineNotes(change.after), editButton(change.after));
    case "update":
      return el("li", { class: "change" },
        head("update", t("change_update"), menuName(change.after),
          change.before.line_total !== change.after.line_total ? `${money(change.before.line_total)} → ${money(change.after.line_total)}` : money(change.after.line_total)),
        el("ul", { class: "fields" }, change.fields.map((f) => fieldChange(f, change.before, change.after))),
        el("div", { class: "options muted" }, optionText(change.after), " · ", t("cups_unit", { n: change.after.quantity })),
        editButton(change.after));
    case "remove":
      return el("li", { class: "change removed" }, head("remove", t("change_remove"), menuName(change.before), `−${money(change.before.line_total - change.before.covered)}`),
        el("div", { class: "options" }, optionText(change.before), " · ", t("cups_unit", { n: change.before.quantity })));
    case "note_add":
    case "note_remove":
      return el("li", { class: "change" + (change.type === "note_remove" ? " removed" : "") },
        head("note", t(`change_${change.type}`), who(change.note)),
        el("div", { class: "options" }, `“${change.note.text}”`),
        change.type === "note_add" && el("div", { class: "muted" }, t("request_caveat")));
    case "dine":
      return el("li", { class: "change" }, head("update", t("change_dine"),
        `${change.before ? t(change.before) : t("not_chosen")} → ${t(change.after)}`));
    default: {  // 빨대·캐리어
      const word = (v) => (v == null ? t("not_chosen") : t(v ? "need_yes" : "need_no"));
      return el("li", { class: "change" }, head("update", t(change.name), `${word(change.before)} → ${word(change.after)}`));
    }
  }
}

function openConfirm() {
  if (!state.pending) return;
  let working = false;
  const respeak = () => {  // 고쳐서 다시 말하기: 변경안은 그대로 두고 대화로 돌아간다
    closeScreen("confirm");
    document.body.dataset.view = "chat";
    $("#input").placeholder = t("respeak_hint");
    $("#input").focus();
  };
  const settle = async (path, note) => {
    if (working) return;
    working = true;
    try {
      const session = await api(path, {});
      closeScreen("confirm");
      applyState(session);
      renderChips(session.chips);
      addNote(t(note));
      if (session.applied) state.started = true;
    } catch (error) {
      showToast(errorText(error));
      working = false;
      api("/api/session", {}).then(applyState);
    }
  };
  openScreen({ name: "confirm", back: respeak, render: () => {
    const { pending } = state;
    if (!pending) return el("div");
    return sheet({
      title: t(pending.undo ? "confirm_title_undo" : "confirm_title"), sub: t("confirm_sub"), wide: true, cls: "confirm", close: respeak,
      body: [
        el("ul", { class: "changes" }, pending.changes.map(changeCard)),
        el("dl", { class: "totals" }, el("div", { class: "grand" }, el("dt", {}, t("total")),
          el("dd", {}, pending.before.total !== pending.after.total
            ? [el("s", {}, money(pending.before.total)), " → ", money(pending.after.total)] : money(pending.after.total)))),
      ],
      actions: [
        el("button", { class: "secondary", type: "button", onclick: () => settle("/api/order/reject", "note_rejected") }, t("confirm_no")),
        el("button", { class: "secondary", type: "button", onclick: respeak }, t("confirm_respeak")),
        el("button", { class: "primary", type: "button", onclick: () => settle("/api/order/confirm", "note_confirmed") }, t("confirm_yes")),
      ],
    });
  } });
}

// ------------------------------------------------------------ 대화

function paint(message) {
  const status = message.status ? `<p class="status">${escapeHtml(message.status)}</p>` : "";
  message.bubble.innerHTML = renderText(message.raw) + status;
  const list = $("#messages");
  list.scrollTop = list.scrollHeight;
}

function addMessage(role, text = "") {
  const bubble = el("div", { class: "bubble" });
  const message = el("div", { class: `message ${role}` }, bubble);
  Object.assign(message, { bubble, raw: text, status: null, sources: [], tools: [] });
  $("#messages").append(message);
  if (role === "user") bubble.textContent = text;
  else paint(message);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return message;
}

// 손님이 버튼으로 한 일을 대화 흐름에 한 줄로 남긴다 (모델의 말이 아니다).
function addNote(text) {
  $("#messages").append(el("div", { class: "message system" }, text));
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

// 누르면 화면이 바로 동작하는 버튼. 결제와 변경 확인은 손님이 직접 눌러야 한다.
const ACTIONS = {
  pay: () => startPayment(),
  resume_pay: () => openPayment(),
  confirm: () => openConfirm(),
  reject: async () => { if (await update("/api/order/reject", {})) addNote(t("note_rejected")); },
  undo: () => undo(),
  dine_in: () => update("/api/order/options", { dine_option: "dine_in" }),
  takeout: () => update("/api/order/options", { dine_option: "takeout" }),
};

async function undo() {
  if (await update("/api/order/undo", {})) addNote(t("note_undone"));
}

function renderChips(chips) {
  $("#chips").replaceChildren(...(chips || []).map((chip) =>
    el("button", { type: "button", class: chip.action ? "action" : "", disabled: state.busy,
      onclick: () => (chip.action ? ACTIONS[chip.action]() : send(chip.text)) }, chip.label)));
}

// "근거 보기"를 켜면 답변마다 어떤 문서를 찾았고 어떤 도구를 불렀는지 보여 준다.
// 답변을 코드로 다시 확인한 결과(금액·알레르기 안내·요청 메모 안내)를 한 줄로.
function verifiedRow(v) {
  if (!v) return null;
  const parts = [v.rewritten ? t("verify_rewritten") : v.amounts === "ok" ? t("verify_amounts_ok")
    : t("verify_amounts_unknown", { amounts: v.amounts.map(money).join(", ") })];
  if (v.allergy_notice) parts.push(t(v.allergy_notice === "added" ? "verify_allergy_added" : "verify_allergy_ok"));
  if (v.note_notice) parts.push(t(v.note_notice === "added" ? "verify_note_added" : "verify_note_ok"));
  return el("li", {}, el("span", { class: "tag" }, t("verify_tag")), ` ${parts.join(" · ")}`);
}

function evidence(message) {
  if (!message.sources.length && !message.tools.length && !message.verified) return null;
  const sourceRow = (s) => el("li", {},
    el("span", { class: "tag" }, (s.match === "vector" ? "" : `${t(`match_${s.match}`)} `) + s.score.toFixed(2)),
    ` ${s.title} (${s.type})${s.query ? ` ← "${s.query}"` : ""}`);
  const toolRow = (c) => el("li", {}, el("code", {}, `${c.ok ? "✓" : "✗"} ${c.name}(${JSON.stringify(c.args)})`));
  return el("details", { class: "evidence" },
    el("summary", {}, t("evidence_summary", { docs: message.sources.length, tools: message.tools.length })),
    el("ul", {}, message.sources.map(sourceRow), message.tools.map(toolRow), verifiedRow(message.verified)));
}

// 메뉴끼리 비교해 물으면 답변 아래에 나란히 놓은 표가 붙는다.
function compareTable(items) {
  const row = (label, cell) => el("tr", {}, el("th", {}, label), items.map((m) => el("td", {}, cell(m))));
  const sugar = (m) => (m.sugar.adjustable === false ? t("fixed") : m.sugar.adjustable == null ? t("unknown")
    : t("adjustable") + (m.sugar.recommended != null ? ` (${t("recommended_tag")} ${m.sugar.recommended}%)` : ""));
  const number = (value, unit) => (value == null ? "-" : `${value}${unit}`);
  const basis = items.map((m) => m.basis).find(Boolean);
  return el("div", { class: "compare" },
    el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}), items.map((m) => el("th", {},
        m.image && el("img", { src: m.image, alt: "" }), el("div", {}, menuName(m)), m.sold_out && el("span", { class: "tag warn" }, t("sold_out")))))),
      el("tbody", {},
        row(t("cmp_price"), (m) => (m.prices.length ? m.prices.map((p) => `${p.size} ${money(p.price)}`).join(" / ") : m.price ? money(m.price) : t("price_ask"))),
        row(t("cmp_flavor"), (m) => words(m.flavors || []) || "-"),
        row(t("cmp_caffeine"), (m) => number(m.caffeine_mg, "mg")),
        row(t("cmp_kcal"), (m) => number(m.kcal, "kcal")),
        row(t("cmp_sugar_g"), (m) => number(m.sugar_g, "g")),
        row(t("cmp_hot"), (m) => t(m.hot_available ? "yes" : "no")),
        row(t("cmp_sugar_option"), sugar),
        row(t("cmp_allergens"), (m) => words(m.allergens) || t("none")))),
    basis && el("p", { class: "muted" }, t("cmp_basis", { basis })));
}

function handleEvent(event, message) {
  switch (event.type) {
    case "delta":
      message.raw += event.text;
      message.status = null;
      paint(message);
      break;
    case "replace":  // 답변 검증에 걸려 서버가 다시 쓴 답변
      message.raw = event.text;
      paint(message);
      break;
    case "verified":
      message.verified = event;
      break;
    case "status": {
      const key = PROPOSE_TOOLS.includes(event.name) ? "status_propose" : `status_${event.name}`;
      message.status = t(I18N.ko[key] ? key : "status_thinking");
      paint(message);
      break;
    }
    case "sources":
      message.sources.push(...event.items);
      break;
    case "tool":
      message.tools.push(event);
      if (event.sources) message.sources.push(...event.sources.map((s) => ({ ...s, query: event.args.query })));
      break;
    case "state":
      applyState(event);
      break;
    case "cards":
      message.append(el("div", { class: "message-cards" }, event.items.map((item) => menuCard(item))));
      break;
    case "compare":
      message.append(compareTable(event.items));
      break;
    case "proposal":
      message.proposal = event.changed;  // 이번 답변에서 변경안이 새로 올라오거나 달라졌을 때만 확인 화면을 띄운다
      break;
    case "chips":
      message.chips = event.items;
      break;
    case "error":
      message.raw = event.message;
      message.bubble.classList.add("error");
      if (event.detail) message.tools.push({ ok: false, name: "error", args: event.detail });
      break;
  }
}

function setBusy(busy) {
  state.busy = busy;
  $("#send").disabled = busy;
  for (const button of $$("#chips button")) button.disabled = busy;
  renderCart();
}

async function send(text) {
  text = text.trim();
  if (!text || state.busy) return;
  state.started = true;
  document.body.dataset.view = "chat";
  $("#input").placeholder = t("placeholder");
  setBusy(true);
  addMessage("user", text);
  const message = addMessage("bot");
  message.status = t("status_thinking");
  paint(message);
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, message: text }),
    });
    if (!response.ok) throw new Error(response.statusText);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        if (line.startsWith("data: ")) handleEvent(JSON.parse(line.slice(6)), message);
      }
    }
  } catch (error) {
    message.raw = t("network_error");
    message.bubble.classList.add("error");
  }
  message.status = null;
  if (!message.raw.trim()) message.raw = t("empty_reply");
  paint(message);
  const details = evidence(message);
  if (details) message.append(details);
  setBusy(false);
  renderChips(message.chips || []);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  // 모델이 변경안을 올렸으면 변경 확인 화면을 띄운다. 손님이 눌러 확인해야 반영된다.
  // 변경안은 그대로인데 다른 것을 물었을 때는 답변을 가리지 않는다([변경 확인하기] 버튼과 주문 내역의 안내로 다시 연다).
  if (message.proposal && state.pending) openConfirm();
  else $("#input").focus();
}

// ------------------------------------------------------------ 언어·세션

// 브랜드와 매장 이름을 화면 언어로. 예: 공차 시흥배곧점 / Gong Cha Siheung Baegot
function storeName() {
  const { store, store_names: names } = state.config;
  return `${t("brand")} ${(state.lang !== "ko" && names[state.lang]) || store.name}`.replace("贡茶 ", "贡茶");
}

function relabel() {
  $("#store-name").textContent = storeName();
  document.documentElement.lang = state.lang;
  for (const node of $$("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const node of $$("[data-i18n-placeholder]")) node.placeholder = t(node.dataset.i18nPlaceholder);
  $("#languages").replaceChildren(...languageButtons().children);
  renderBoard();
  if (state.order) renderCart();
  showTimer();
  if (topScreen()) renderLayer();
}

// 세션을 열거나 초기화했을 때: 대화창을 비우고 인사말과 지난 대화를 다시 그린다.
function openConversation(session) {
  screens.length = 0;
  $("#messages").replaceChildren();
  state.staffCall = session.staff_call && { ...session.staff_call };
  applyState(session);
  addMessage("bot", session.greeting).classList.add("greeting");
  for (const turn of session.history) addMessage(turn.role === "user" ? "user" : "bot", turn.content);
  renderChips(session.chips);
  state.started = Boolean(session.history.length || session.order.lines.length);
  renderLayer();
  if (session.order.status === "paid") openPayment();  // 새로 고쳐도 주문번호 화면으로 돌아온다
  else if (!session.order.dine_option) openStart();
}

// 처음으로 가기: 주문과 대화를 비우고 시작 화면으로. 다음 손님을 위해 큰 글씨·낮은 화면도 끈다.
async function restart() {
  const session = await api("/api/session/reset", {});
  setDisplay("big", false);
  setDisplay("low", false);
  resetIdle();
  openConversation(session);
}

function askRestart() {
  if (!state.started || (state.order && state.order.status === "paid")) restart();
  else ask("restart", { title: t("restart_title"), body: t("restart_body"), yes: t("restart_yes"), onYes: restart });
}

async function changeLanguage(language) {
  const session = await api("/api/session/language", { language });
  applyState(session);
  // 아직 대화를 시작하지 않았다면 인사말도 새 언어로 바꾼다.
  const messages = $$("#messages .message");
  if (messages.length === 1 && messages[0].classList.contains("greeting")) {
    messages[0].raw = session.greeting;
    paint(messages[0]);
  }
  renderChips(session.chips);
}

// ------------------------------------------------------------ 시간 연장
// 조작이 없으면 남은 시간을 보여 주다가 안내 창을 띄운다. [시간 연장]을 누르면 처음부터 다시 센다.
// 답변을 만드는 동안과, 아직 아무것도 하지 않은 시작 화면에서는 세지 않는다.

const idle = { left: 0, total: 0, warning: 0 };

function resetIdle() {
  idle.left = idle.total;
  if (topScreen() && topScreen().name === "idle") closeScreen("idle");
  showTimer();
}

function showTimer() {
  const button = $("#timer-button");
  button.hidden = !idle.total || !state.started;
  const minutes = Math.floor(idle.left / 60);
  $("#timer-text").textContent = t("timer_left", { time: `${minutes}:${String(idle.left % 60).padStart(2, "0")}` });
  button.classList.toggle("urgent", idle.left <= idle.warning);
}

function tickIdle() {
  if (!idle.total || !state.started || state.busy) return;
  idle.left -= 1;
  showTimer();
  if (idle.left <= 0) {
    restart().then(() => showToast(t("idle_reset")));
  } else if (idle.left <= idle.warning) {
    // 1초마다 화면을 다시 그리면 누르는 순간 버튼이 바뀔 수 있어서, 남은 초만 고쳐 쓴다.
    if (topScreen() && topScreen().name === "idle") {
      $("#layer .idle .muted").textContent = t("idle_body", { n: idle.left });
      return;
    }
    openScreen({ name: "idle", back: resetIdle, render: () => sheet({
      title: t("idle_title"), sub: t("idle_body", { n: idle.left }), cls: "idle",
      actions: [el("button", { class: "secondary", type: "button", onclick: restart }, t("idle_restart")),
        el("button", { class: "primary", type: "button", onclick: resetIdle }, t("idle_extend"))] }) });
  }
}

// ------------------------------------------------------------ 시작

function newSessionId() {
  // crypto.randomUUID 는 https 나 localhost 에서만 쓸 수 있어서, 다른 주소로 열었을 때를 대비한다.
  return crypto.randomUUID ? crypto.randomUUID() : `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

async function init() {
  state.sessionId = sessionStorage.getItem("kioskSession") || newSessionId();
  sessionStorage.setItem("kioskSession", state.sessionId);
  const [config, menu, session] = await Promise.all([api("/api/config"), api("/api/menu"), api("/api/session", {})]);
  state.config = config;
  state.tabs = menu.tabs;
  state.stockVersion = menu.stock.version;
  state.activeTab = menu.tabs[0] && menu.tabs[0].id;
  state.lang = session.language;
  idle.total = config.idle.idle_seconds ? config.idle.idle_seconds + config.idle.warning_seconds : 0;
  idle.warning = config.idle.warning_seconds;
  idle.left = idle.total;
  if (!config.ready) {
    $("#notice").textContent = config.error;
    $("#notice").hidden = false;
  }
  relabel();
  openConversation(session);

  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#input");
    const text = input.value;
    input.value = "";
    send(text);
  });
  // 모든 화면 공통 행동
  $("#back-button").addEventListener("click", goBack);
  $("#restart-button").addEventListener("click", askRestart);
  $("#timer-button").addEventListener("click", resetIdle);
  $("#big-text-toggle").addEventListener("click", () => setDisplay("big", !document.documentElement.classList.contains("big-text")));
  $("#low-screen-toggle").addEventListener("click", () => setDisplay("low", !document.body.classList.contains("low-screen")));
  $("#help-button").addEventListener("click", () => { screens.length = 0; renderLayer(); send(t("help_message")); });
  $("#staff-button").addEventListener("click", () => callStaff());
  $("#voucher-button").addEventListener("click", () => openVoucherScanner());

  $("#pay").addEventListener("click", () => startPayment());
  $("#pending-open").addEventListener("click", openConfirm);
  $("#paying-open").addEventListener("click", () => openPayment());
  $("#undo-button").addEventListener("click", undo);
  $("#clear-button").addEventListener("click", () => ask("clear", {
    title: t("clear_title"), body: t("clear_body"), yes: t("clear_yes"), onYes: () => update("/api/order/clear", {}) }));
  for (const button of $$("#dine button")) {
    button.addEventListener("click", () => update("/api/order/options", { dine_option: button.dataset.dine }));
  }
  $("#straw").addEventListener("change", (event) => update("/api/order/options", { straw: event.target.checked }));
  $("#carrier").addEventListener("change", (event) => update("/api/order/options", { carrier: event.target.checked }));

  const toggle = $("#evidence-toggle");
  const showEvidence = (on) => {
    document.body.classList.toggle("show-evidence", on);
    toggle.setAttribute("aria-pressed", String(on));
    localStorage.setItem("kioskEvidence", on ? "1" : "");
  };
  showEvidence(localStorage.getItem("kioskEvidence") === "1");
  toggle.addEventListener("click", () => showEvidence(!document.body.classList.contains("show-evidence")));

  for (const button of $$(".bottom-nav button")) {
    button.addEventListener("click", () => (document.body.dataset.view = button.dataset.view));
  }

  // 손님이 화면을 만질 때마다 남은 시간을 처음으로 돌린다. 안내 창이 떠 있을 때는 버튼으로만 연장한다.
  for (const type of ["pointerdown", "keydown"]) {
    document.addEventListener(type, () => {
      if (!(topScreen() && topScreen().name === "idle")) { idle.left = idle.total; showTimer(); }
    }, true);
  }
  setInterval(tickIdle, 1000);
  // 품절과 직원 호출 진행은 실시간 값이라 몇 초마다 묻는다.
  setInterval(async () => {
    try {
      const live = await api(`/api/live?session_id=${encodeURIComponent(state.sessionId)}`);
      showStaffCall(live.staff_call);
      if (live.stock.version !== state.stockVersion) {
        await reloadMenu();
        applyState(await api("/api/session", {}));
      }
    } catch (error) { /* 다음 번에 다시 묻는다 */ }
  }, 4000);
  $("#input").focus();
}

init().catch((error) => {
  $("#notice").textContent = `${I18N.ko.network_error} (${error.message})`;
  $("#notice").hidden = false;
});
