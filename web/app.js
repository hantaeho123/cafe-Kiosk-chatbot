// 공차 키오스크 화면. 서버(app/server.py)의 API 를 부르고, 대화 답변은 SSE 로 받아 흘려 보여 준다.

const I18N = {
  ko: {
    match_context: "직전 대화",
    quantity: "수량", size: "사이즈", required: "필수", optional: "선택", toppings_choice: "토핑 선택", toppings_max: "최대 {n}개 선택",
    recommended_tag: "추천", default_option: "기본", fixed_note: "이 메뉴는 고정이라 바꿀 수 없어요.",
    unknown_hint: "조절 가능 여부가 확인되지 않은 메뉴예요. 고르시면 주문에 표시해 두고 매장에서 확인해요.",
    tumbler_use: "개인 텀블러에 담기", add_button: "{price} 담기", save_button: "{price} · 변경 저장",
    added: "{name} 담았어요.", saved: "{name} 옵션을 바꿨어요.", edit: "옵션 변경", more_info: "영양정보 · 알레르기",
    brand_sub: "AI 주문 키오스크", evidence: "근거 보기", call_staff: "직원 호출", restart: "처음으로",
    menu_title: "메뉴판", chat_title: "대화로 주문하기", placeholder: "메뉴를 물어보거나 주문해 보세요", send: "보내기",
    cart_title: "주문 내역", cart_empty: "아직 담은 메뉴가 없어요. 대화로 주문하거나 메뉴판에서 골라 보세요.",
    dine_in: "매장", takeout: "포장", straw: "빨대", carrier: "캐리어",
    discount: "제휴 할인", discount_none: "없음", discount_skt: "SKT T멤버십 10%", discount_kt: "KT 멤버십 10%", stamp: "멤버십 스탬프 적립",
    subtotal: "주문 금액", discount_amount: "할인", total: "결제 금액", pay: "결제하기", pay_message: "결제할게요",
    nav_menu: "메뉴", nav_chat: "대화", nav_cart: "주문",
    tab_recommend: "추천", tab_new: "신메뉴", tab_dessert: "디저트", tab_etc: "기타", badge_best: "베스트", badge_new: "신메뉴",
    price_ask: "가격 매장 문의", ask_menu: "물어보기",
    ask_template: "{name}에 대해 알려주세요",
    not_orderable: "키오스크에서 주문할 수 없는 메뉴예요. 직원에게 문의해 주세요.",
    price: "가격", temperature: "온도", allergens: "알레르기", toppings_included: "기본 토핑", flavor: "맛", caffeine: "카페인", none: "없음",
    sugar: "당도", sugar_fixed: "당도 고정", sugar_default: "당도 기본", sugar_recommended: "추천 {n}%",
    adjustable: "조절 가능", fixed: "고정", unknown: "매장 확인 필요",
    ice: "얼음", ice_none: "얼음 없이", ice_less: "얼음 적게", ice_regular: "얼음 보통", ice_full: "얼음 많이", ice_fixed: "얼음 고정", ice_default: "얼음 기본",
    tumbler: "개인 텀블러", remove: "삭제",
    order_done: "주문이 완료되었습니다", order_number: "주문번호", payment: "결제 수단",
    receipt_yes: "영수증이 출력됩니다", receipt_no: "영수증 미출력", stamps: "스탬프 {n}개 적립", new_order: "새 주문 시작", close: "닫기",
    staff_called: "직원을 호출했습니다. 잠시만 기다려 주세요.",
    status_search_knowledge: "메뉴 정보를 찾고 있어요", status_add_to_cart: "장바구니에 담고 있어요", status_checkout: "결제를 진행하고 있어요",
    status_call_staff: "직원을 호출하고 있어요", status_edit: "주문을 수정하고 있어요", status_thinking: "생각하고 있어요",
    evidence_summary: "근거 · 문서 {docs}개 · 도구 {tools}회", match_name: "메뉴명 일치",
    network_error: "서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.", empty_reply: "죄송해요, 답변을 만들지 못했어요. 다시 말씀해 주세요.",
  },
  en: {
    match_context: "previous turn",
    quantity: "Quantity", size: "Size", required: "Required", optional: "Optional", toppings_choice: "Toppings", toppings_max: "Choose up to {n}",
    recommended_tag: "Recommended", default_option: "Default", fixed_note: "This is fixed for this drink.",
    unknown_hint: "We couldn't confirm whether this can be adjusted. Your choice will be noted and checked by staff.",
    tumbler_use: "Use my own tumbler", add_button: "Add · {price}", save_button: "Save · {price}",
    added: "Added {name}.", saved: "Updated {name}.", edit: "Edit", more_info: "Nutrition · Allergens",
    brand_sub: "AI ordering kiosk", evidence: "Show sources", call_staff: "Call staff", restart: "Start over",
    menu_title: "Menu", chat_title: "Order by chat", placeholder: "Ask about the menu or place an order", send: "Send",
    cart_title: "Your order", cart_empty: "Nothing here yet. Order by chat or pick from the menu.",
    dine_in: "For here", takeout: "To go", straw: "Straw", carrier: "Carrier",
    discount: "Partner discount", discount_none: "None", discount_skt: "SKT T Membership 10%", discount_kt: "KT Membership 10%", stamp: "Collect membership stamps",
    subtotal: "Subtotal", discount_amount: "Discount", total: "Total", pay: "Pay", pay_message: "I'd like to pay",
    nav_menu: "Menu", nav_chat: "Chat", nav_cart: "Order",
    tab_recommend: "Picks", tab_new: "New", tab_dessert: "Desserts", tab_etc: "Other", badge_best: "Best", badge_new: "New",
    price_ask: "Ask staff for price", ask_menu: "Ask about it",
    ask_template: "Tell me about {name}",
    not_orderable: "This item can't be ordered at the kiosk. Please ask a staff member.",
    price: "Price", temperature: "Hot / Iced", allergens: "Allergens", toppings_included: "Included toppings", flavor: "Flavor", caffeine: "Caffeine", none: "None",
    sugar: "Sugar", sugar_fixed: "Fixed sugar", sugar_default: "Default sugar", sugar_recommended: "{n}% recommended",
    adjustable: "Adjustable", fixed: "Fixed", unknown: "Ask staff",
    ice: "Ice", ice_none: "No ice", ice_less: "Less ice", ice_regular: "Regular ice", ice_full: "Extra ice", ice_fixed: "Fixed ice", ice_default: "Default ice",
    tumbler: "Own tumbler", remove: "Remove",
    order_done: "Your order is complete", order_number: "Order number", payment: "Payment",
    receipt_yes: "Your receipt is printing", receipt_no: "No receipt", stamps: "{n} stamp(s) collected", new_order: "Start a new order", close: "Close",
    staff_called: "A staff member has been called. One moment, please.",
    status_search_knowledge: "Looking up the menu", status_add_to_cart: "Adding to your order", status_checkout: "Processing payment",
    status_call_staff: "Calling a staff member", status_edit: "Updating your order", status_thinking: "Thinking",
    evidence_summary: "Sources · {docs} docs · {tools} tool calls", match_name: "name match",
    network_error: "Couldn't reach the server. Please try again in a moment.", empty_reply: "Sorry, I couldn't come up with an answer. Please try again.",
    topping_pearl: "Pearl", topping_mini_pearl: "Mini Pearl", topping_white_pearl: "White Pearl", topping_aloe: "Aloe",
    topping_coconut: "Coconut", topping_milk_foam: "Milk Foam", topping_cheese_foam: "Cheese Foam", topping_extra_shot: "Extra Shot",
  },
  zh: {
    match_context: "上一轮对话",
    quantity: "数量", size: "杯型", required: "必选", optional: "可选", toppings_choice: "加料", toppings_max: "最多选 {n} 种",
    recommended_tag: "推荐", default_option: "默认", fixed_note: "该饮品此项固定，无法更改。",
    unknown_hint: "尚未确认此饮品能否调整。您的选择会标注在订单上，由店员确认。",
    tumbler_use: "使用自带杯", add_button: "加入订单 · {price}", save_button: "保存修改 · {price}",
    added: "已加入：{name}", saved: "已修改：{name}", edit: "修改选项", more_info: "营养成分 · 过敏原",
    brand_sub: "AI 点单机", evidence: "查看依据", call_staff: "呼叫店员", restart: "重新开始",
    menu_title: "菜单", chat_title: "对话点单", placeholder: "可以询问菜单或直接点单", send: "发送",
    cart_title: "订单明细", cart_empty: "还没有选择饮品。可以通过对话点单，或从菜单中选择。",
    dine_in: "店内", takeout: "外带", straw: "吸管", carrier: "提袋",
    discount: "合作折扣", discount_none: "无", discount_skt: "SKT T会员 10%", discount_kt: "KT 会员 10%", stamp: "累积会员印章",
    subtotal: "订单金额", discount_amount: "折扣", total: "应付金额", pay: "结账", pay_message: "我要结账",
    nav_menu: "菜单", nav_chat: "对话", nav_cart: "订单",
    tab_recommend: "推荐", tab_new: "新品", tab_dessert: "甜点", tab_etc: "其他", badge_best: "热销", badge_new: "新品",
    price_ask: "价格请咨询店员", ask_menu: "询问",
    ask_template: "请介绍一下{name}",
    not_orderable: "此商品无法在点单机上购买，请咨询店员。",
    price: "价格", temperature: "冷 / 热", allergens: "过敏原", toppings_included: "已含配料", flavor: "口味", caffeine: "咖啡因", none: "无",
    sugar: "甜度", sugar_fixed: "固定甜度", sugar_default: "默认甜度", sugar_recommended: "推荐 {n}%",
    adjustable: "可调整", fixed: "固定", unknown: "请咨询店员",
    ice: "冰量", ice_none: "去冰", ice_less: "少冰", ice_regular: "正常冰", ice_full: "多冰", ice_fixed: "固定冰量", ice_default: "默认冰量",
    tumbler: "自带杯", remove: "删除",
    order_done: "订单已完成", order_number: "取餐号", payment: "支付方式",
    receipt_yes: "正在打印小票", receipt_no: "不打印小票", stamps: "累积 {n} 个印章", new_order: "开始新订单", close: "关闭",
    staff_called: "已呼叫店员，请稍候。",
    status_search_knowledge: "正在查询菜单信息", status_add_to_cart: "正在加入订单", status_checkout: "正在结账",
    status_call_staff: "正在呼叫店员", status_edit: "正在修改订单", status_thinking: "正在思考",
    evidence_summary: "依据 · 文档 {docs} 篇 · 工具 {tools} 次", match_name: "名称匹配",
    network_error: "无法连接服务器，请稍后再试。", empty_reply: "抱歉，未能生成回答，请再说一次。",
    topping_pearl: "珍珠", topping_mini_pearl: "小珍珠", topping_white_pearl: "白珍珠", topping_aloe: "芦荟",
    topping_coconut: "椰果", topping_milk_foam: "奶盖", topping_cheese_foam: "芝士奶盖", topping_extra_shot: "加浓缩",
  },
  ja: {
    match_context: "直前の会話",
    quantity: "数量", size: "サイズ", required: "必須", optional: "任意", toppings_choice: "トッピング", toppings_max: "最大 {n} 個まで",
    recommended_tag: "おすすめ", default_option: "標準", fixed_note: "このメニューは固定のため変更できません。",
    unknown_hint: "調整できるか確認できていないメニューです。選択内容は注文に記載し、店舗で確認します。",
    tumbler_use: "マイタンブラーを使う", add_button: "{price} カートに入れる", save_button: "{price} · 変更を保存",
    added: "{name} を追加しました。", saved: "{name} を変更しました。", edit: "オプション変更", more_info: "栄養成分 · アレルギー",
    brand_sub: "AI 注文キオスク", evidence: "根拠を表示", call_staff: "スタッフを呼ぶ", restart: "最初から",
    menu_title: "メニュー", chat_title: "チャットで注文", placeholder: "メニューの質問やご注文をどうぞ", send: "送信",
    cart_title: "ご注文内容", cart_empty: "まだ商品がありません。チャットで注文するか、メニューからお選びください。",
    dine_in: "店内", takeout: "持ち帰り", straw: "ストロー", carrier: "キャリア",
    discount: "提携割引", discount_none: "なし", discount_skt: "SKT Tメンバーシップ 10%", discount_kt: "KT メンバーシップ 10%", stamp: "スタンプを貯める",
    subtotal: "注文金額", discount_amount: "割引", total: "お支払い金額", pay: "お会計", pay_message: "お会計をお願いします",
    nav_menu: "メニュー", nav_chat: "チャット", nav_cart: "注文",
    tab_recommend: "おすすめ", tab_new: "新メニュー", tab_dessert: "デザート", tab_etc: "その他", badge_best: "人気", badge_new: "新作",
    price_ask: "価格はスタッフまで", ask_menu: "質問する",
    ask_template: "{name}について教えてください",
    not_orderable: "この商品はキオスクでは注文できません。スタッフにお尋ねください。",
    price: "価格", temperature: "ホット / アイス", allergens: "アレルギー", toppings_included: "基本トッピング", flavor: "味", caffeine: "カフェイン", none: "なし",
    sugar: "甘さ", sugar_fixed: "甘さ固定", sugar_default: "甘さ標準", sugar_recommended: "おすすめ {n}%",
    adjustable: "調整可", fixed: "固定", unknown: "スタッフに確認",
    ice: "氷", ice_none: "氷なし", ice_less: "氷少なめ", ice_regular: "氷ふつう", ice_full: "氷多め", ice_fixed: "氷固定", ice_default: "氷標準",
    tumbler: "マイタンブラー", remove: "削除",
    order_done: "ご注文が完了しました", order_number: "注文番号", payment: "お支払い方法",
    receipt_yes: "レシートを印刷します", receipt_no: "レシートなし", stamps: "スタンプ {n} 個獲得", new_order: "新しい注文を始める", close: "閉じる",
    staff_called: "スタッフを呼びました。少々お待ちください。",
    status_search_knowledge: "メニュー情報を検索中", status_add_to_cart: "カートに追加中", status_checkout: "お会計を処理中",
    status_call_staff: "スタッフを呼んでいます", status_edit: "注文を変更中", status_thinking: "考え中",
    evidence_summary: "根拠 · 文書 {docs} 件 · ツール {tools} 回", match_name: "名前一致",
    network_error: "サーバーに接続できませんでした。少し待ってからもう一度お試しください。", empty_reply: "申し訳ございません、回答を作成できませんでした。もう一度お願いします。",
    topping_pearl: "パール", topping_mini_pearl: "ミニパール", topping_white_pearl: "ホワイトパール", topping_aloe: "アロエ",
    topping_coconut: "ココナッツ", topping_milk_foam: "ミルクフォーム", topping_cheese_foam: "チーズフォーム", topping_extra_shot: "ショット追加",
  },
};
const EDIT_TOOLS = ["update_cart_item", "remove_cart_item", "set_order_options"];

const state = { sessionId: null, lang: "ko", config: null, tabs: [], activeTab: null, order: null, busy: false, receiptShown: null, staffKey: null };

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function t(key, vars = {}) {
  const text = (I18N[state.lang] || I18N.ko)[key] ?? I18N.ko[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? "");
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

async function api(path, body) {
  const options = body && {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, ...body }),
  };
  const response = await fetch(path, options || undefined);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : response.statusText);
  }
  return response.json();
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

function showToast(text) {
  const toast = $("#toast");
  toast.textContent = text;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => (toast.hidden = true), 4000);
}

// ------------------------------------------------------------ 메뉴판

function badgeLabel(badge) {
  const rank = badge.match(/^인기(\d)위$/);
  if (rank) return state.lang === "ko" ? badge : `No.${rank[1]}`;
  return { 베스트: t("badge_best"), 신메뉴: t("badge_new") }[badge] || badge;
}

function menuCard(item) {
  const prices = item.prices.map((p) => p.price);
  const price = prices.length ? money(Math.min(...prices)) + (prices.length > 1 ? "~" : "") : t("price_ask");
  return el("button", { class: "card" + (item.orderable ? "" : " unavailable"), type: "button", onclick: () => openDetail(item.id) },
    el("div", { class: "badges" }, item.badges.map((b) => el("span", { class: "badge" + (b.startsWith("인기") ? "" : " soft") }, badgeLabel(b)))),
    el("div", { class: "photo" }, item.image && el("img", { src: item.image, alt: "", loading: "lazy" })),
    el("div", { class: "info" }, el("div", { class: "name" }, item.name), el("div", { class: "price" }, price)));
}

function renderBoard() {
  const tabLabel = (tab) => (I18N.ko[`tab_${tab.id}`] ? t(`tab_${tab.id}`) : tab.label);
  $("#tabs").replaceChildren(...state.tabs.map((tab) =>
    el("button", { type: "button", role: "tab", "aria-selected": String(tab.id === state.activeTab),
      onclick: () => { state.activeTab = tab.id; renderBoard(); $("#grid").scrollTop = 0; } }, tabLabel(tab))));
  const active = state.tabs.find((tab) => tab.id === state.activeTab);
  $("#grid").replaceChildren(...(active ? active.items.map(menuCard) : []));
}

// ------------------------------------------------------------ 메뉴 상세 · 옵션 선택

function optionGroup(title, badge, ...children) {
  return el("section", { class: "option-group" },
    el("div", { class: "option-head" }, el("span", {}, title),
      badge && el("span", { class: "pill" + (badge === "optional" ? " optional" : "") }, t(badge))),
    children);
}

// choices: [{ value, label, extra(오른쪽 금액), tag(추천 표시) }]
function radioOptions(name, choices, selected, onPick) {
  return choices.map((c) => el("label", { class: "option" },
    el("input", { type: "radio", name, checked: c.value === selected, onchange: () => onPick(c.value) }),
    el("span", { class: "label" }, c.tag && el("small", { class: "pick" }, c.tag), c.label),
    c.extra && el("span", { class: "extra" }, c.extra)));
}

function menuFacts(item) {
  const facts = [];
  if (!item.orderable) facts.push([t("price"), t("price_ask")]);
  if (item.kind === "drink") {
    if (item.included_toppings.length) facts.push([t("toppings_included"), item.included_toppings.join(", ")]);
    if (item.flavors.length) facts.push([t("flavor"), item.flavors.join(", ")]);
    if (item.caffeine) facts.push([t("caffeine"), item.caffeine]);
  }
  facts.push([t("allergens"), item.allergens.length ? item.allergens.join(", ") : t("none")]);
  let nutrition = null;
  if (item.nutrition && item.nutrition.length) {
    const columns = Object.keys(item.nutrition[0].values);
    nutrition = el("table", { class: "nutrition" },
      el("thead", {}, el("tr", {}, el("th", {}, ""), columns.map((c) => el("th", {}, c)))),
      el("tbody", {}, item.nutrition.map((row) => el("tr", {}, el("td", {}, row.label), columns.map((c) => el("td", {}, String(row.values[c] ?? "-")))))));
  }
  return el("details", { class: "more", open: !item.orderable },
    el("summary", {}, t("more_info")),
    el("dl", { class: "facts" }, facts.map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
    nutrition,
    nutrition && item.nutrition_basis && el("div", { class: "muted" }, item.nutrition_basis));
}

// 옵션 선택지를 그리고, 지금 고른 값(choice)을 돌려준다. line 이 있으면 담긴 줄의 값으로 채운다.
function optionForm(item, line, onChange) {
  const { config } = state;
  const choice = line
    ? { size: line.size, temperature: line.temperature, sugar: line.sugar,
        ice: line.ice ?? (item.ice.adjustable ? "regular" : null),
        toppings: new Set(line.toppings.map((x) => x.id)), quantity: line.quantity, tumbler: line.tumbler }
    : { size: item.prices[0].size, temperature: item.temperatures[0],
        sugar: item.sugar.adjustable ? item.sugar.recommended : null, ice: item.ice.adjustable ? "regular" : null,
        toppings: new Set(), quantity: 1, tumbler: false };
  choice.price = () => {
    const base = item.prices.find((p) => p.size === choice.size).price;
    const extra = config.toppings.filter((x) => choice.toppings.has(x.id)).reduce((sum, x) => sum + x.price, 0);
    return (base + extra) * choice.quantity;
  };

  const quantity = el("span", {}, String(choice.quantity));
  const boxes = [];
  let iceSection = null;
  const refresh = () => {
    quantity.textContent = String(choice.quantity);
    if (iceSection) iceSection.hidden = choice.temperature !== "ICE";
    for (const box of boxes) box.disabled = !box.checked && choice.toppings.size >= config.max_toppings;
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
  const sugarOptions = config.sugar_levels.map((n) => (
    { value: n, label: `${n}%`, tag: n === item.sugar.recommended ? t("recommended_tag") : null }));
  const iceOptions = config.ice_levels.map((key) => ({ value: key, label: t(`ice_${key}`) }));
  iceSection = optionGroup(t("ice"), item.ice.adjustable === false ? null : "required", levels("ice", item.ice.adjustable, iceOptions));

  const topping = (x) => {
    const box = el("input", { type: "checkbox", checked: choice.toppings.has(x.id), onchange: () => {
      if (box.checked) choice.toppings.add(x.id); else choice.toppings.delete(x.id);
      refresh();
    } });
    boxes.push(box);
    return el("label", { class: "option" }, box,
      el("span", { class: "label" }, (I18N[state.lang] || {})[`topping_${x.id}`] || x.name),
      el("span", { class: "extra" }, `+${money(x.price)}`));
  };
  const tumbler = el("input", { type: "checkbox", checked: choice.tumbler, onchange: () => pick("tumbler")(tumbler.checked) });

  const node = el("div", { class: "option-form" },
    el("div", { class: "quantity-row" }, el("strong", {}, t("quantity")),
      el("span", { class: "stepper" },
        el("button", { type: "button", "aria-label": "-", onclick: step(-1) }, "−"), quantity,
        el("button", { type: "button", "aria-label": "+", onclick: step(1) }, "+"))),
    optionGroup(t("size"), "required",
      radioOptions("size", item.prices.map((p) => ({ value: p.size, label: p.size, extra: money(p.price) })), choice.size, pick("size"))),
    optionGroup("HOT / ICE", "required",
      radioOptions("temperature", item.temperatures.map((v) => ({ value: v, label: v })), choice.temperature, pick("temperature"))),
    optionGroup(t("sugar"), item.sugar.adjustable === false ? null : "required", levels("sugar", item.sugar.adjustable, sugarOptions)),
    iceSection,
    optionGroup(t("toppings_choice"), "optional",
      el("p", { class: "muted" }, t("toppings_max", { n: config.max_toppings })), config.toppings.map(topping)),
    optionGroup(t("tumbler"), "optional", el("label", { class: "option" }, tumbler, el("span", { class: "label" }, t("tumbler_use")))));
  refresh();
  return { node, choice };
}

// 메뉴 카드를 누르면 열린다. line 을 주면 주문 내역에 담긴 줄의 옵션을 바꾸는 창이 된다.
async function openDetail(id, line = null) {
  const item = await api(`/api/menu/${encodeURIComponent(id)}`);
  const dialog = $("#detail");
  const close = el("button", { class: "secondary", type: "button", onclick: () => dialog.close() }, t("close"));
  const ask = el("button", { class: "secondary", type: "button",
    onclick: () => { dialog.close(); send(t("ask_template", { name: item.name })); } }, t("ask_menu"));
  const header = [
    el("div", { class: "photo" }, item.image && el("img", { src: item.image, alt: item.name })),
    el("h3", {}, item.name),
    el("div", { class: "muted" }, item.category),
    item.description && el("p", { class: "description" }, item.description),
  ];
  if (!item.orderable) {
    dialog.replaceChildren(
      el("div", { class: "modal-scroll" }, header, el("p", { class: "muted" }, t("not_orderable")), menuFacts(item)),
      el("div", { class: "modal-actions" }, close, ask));
    dialog.showModal();
    return;
  }

  const submit = el("button", { class: "primary", type: "button" });
  const form = optionForm(item, line, (choice) => {
    submit.textContent = t(line ? "save_button" : "add_button", { price: money(choice.price()) });
  });
  submit.addEventListener("click", async () => {
    const { choice } = form;
    submit.disabled = true;
    try {
      const session = await api("/api/order/item", {
        ...(line ? { line_id: line.line_id } : { menu_id: item.id }),
        size: choice.size, temperature: choice.temperature, sugar: choice.sugar,
        ice: choice.temperature === "ICE" ? choice.ice : null,
        toppings: [...choice.toppings], quantity: choice.quantity, tumbler: choice.tumbler,
      });
      dialog.close();
      applyState(session);
      renderChips(session.chips);
      showToast([t(line ? "saved" : "added", { name: item.name }), ...session.notes].join(" "));
      // 담은 뒤에는 주문 내역을 보여 주고, 방금 담은 줄을 잠깐 강조한다.
      document.body.dataset.view = "cart";
      const row = $(`#lines .line[data-line="${session.line_id}"]`);
      if (row) {
        row.classList.add("flash");
        row.scrollIntoView({ block: "nearest" });
      }
    } catch (error) {
      showToast(error.message);
      submit.disabled = false;
    }
  });
  dialog.replaceChildren(
    el("div", { class: "modal-scroll" }, header, form.node, menuFacts(item)),
    el("div", { class: "modal-actions" }, close, line ? null : ask, submit));
  dialog.showModal();
}

// ------------------------------------------------------------ 주문 내역

function optionText(line) {
  const parts = [line.size, line.temperature];
  parts.push(line.sugar != null ? `${t("sugar")} ${line.sugar}%` : t(`sugar_${line.sugar_note}`));
  if (line.ice) parts.push(t(`ice_${line.ice}`));
  else if (line.ice_note !== "hot") parts.push(t(`ice_${line.ice_note}`));
  if (line.toppings.length) parts.push("+ " + line.toppings.map((x) => (I18N[state.lang] || {})[`topping_${x.id}`] || x.name).join(", "));
  if (line.tumbler) parts.push(t("tumbler"));
  return parts.join(" · ");
}

function receiptLine(line) {
  return el("li", {},
    el("span", {}, `${line.name} × ${line.quantity}`, el("small", {}, optionText(line))),
    el("span", {}, money(line.line_total)));
}

function renderCart() {
  const order = state.order;
  if (!order) return;
  const { totals } = order;
  $("#cart-count").textContent = totals.count || "";
  $("#nav-count").textContent = totals.count || "";
  for (const button of $$("#dine button")) button.setAttribute("aria-pressed", String(button.dataset.dine === order.dine_option));
  $("#takeout-extras").hidden = order.dine_option !== "takeout";
  $("#straw").checked = Boolean(order.straw);
  $("#carrier").checked = Boolean(order.carrier);
  $("#discount").replaceChildren(
    el("option", { value: "none" }, t("discount_none")),
    ...Object.keys(state.config.discounts).map((key) => el("option", { value: key }, t(`discount_${key}`))));
  $("#discount").value = order.discount || "none";
  $("#stamp").checked = order.stamp;

  const changeLine = (line, quantity) => update("/api/order/line", { line_id: line.line_id, quantity });
  $("#lines").replaceChildren(...(order.lines.length ? order.lines.map((line) =>
    el("div", { class: "line", "data-line": line.line_id },
      line.image ? el("img", { src: line.image, alt: "" }) : el("span"),
      el("div", {},
        el("div", { class: "name" }, line.name),
        el("div", { class: "options" }, optionText(line)),
        el("div", { class: "bottom" },
          el("span", { class: "stepper" },
            el("button", { type: "button", "aria-label": "-", onclick: () => changeLine(line, line.quantity - 1) }, "−"),
            el("span", {}, String(line.quantity)),
            el("button", { type: "button", "aria-label": "+", onclick: () => changeLine(line, line.quantity + 1) }, "+")),
          el("span", { class: "amount" }, money(line.line_total))),
        el("div", { class: "line-actions" },
          el("button", { class: "remove", type: "button", onclick: () => openDetail(line.menu_id, line) }, t("edit")),
          el("button", { class: "remove", type: "button", onclick: () => changeLine(line, 0) }, t("remove"))))))
    : [el("div", { class: "empty" }, t("cart_empty"))]));

  $("#subtotal").textContent = money(totals.subtotal);
  $("#discount-row").hidden = !totals.discount;
  $("#discount-amount").textContent = `−${money(totals.discount)}`;
  $("#total").textContent = money(totals.total);
  $("#pay").disabled = !order.lines.length || state.busy;
}

function showReceipt() {
  const order = state.order;
  const { receipt, totals } = order;
  const dialog = $("#receipt");
  dialog.replaceChildren(el("div", { class: "modal-body" },
    el("h3", {}, t("order_done")),
    el("div", { class: "order-number" }, t("order_number"), el("strong", {}, String(receipt.order_number))),
    el("ul", { class: "receipt-lines" }, order.lines.map(receiptLine)),
    el("dl", { class: "totals" },
      el("div", {}, el("dt", {}, t("subtotal")), el("dd", {}, money(totals.subtotal))),
      totals.discount ? el("div", {}, el("dt", {}, t("discount_amount")), el("dd", {}, `−${money(totals.discount)}`)) : null,
      el("div", { class: "grand" }, el("dt", {}, t("total")), el("dd", {}, money(totals.total)))),
    el("p", { class: "muted" },
      [`${t(order.dine_option)} · ${t("payment")}: ${receipt.payment_method.name}`,
        receipt.print_receipt ? t("receipt_yes") : t("receipt_no"),
        totals.stamps ? t("stamps", { n: totals.stamps }) : null].filter(Boolean).join(" · ")),
    el("div", { class: "modal-actions" }, el("button", { class: "primary", type: "button", onclick: restart }, t("new_order")))));
  if (!dialog.open) dialog.showModal();
}

// 서버가 돌려준 상태(언어·주문·직원 호출)를 화면에 반영한다.
function applyState(next) {
  state.order = next.order;
  if (next.language !== state.lang) {
    state.lang = next.language;
    relabel();
  }
  renderCart();
  const staffKey = next.staff_call && `${next.staff_call.time} ${next.staff_call.reason}`;
  if (staffKey && staffKey !== state.staffKey) showToast(t("staff_called"));
  state.staffKey = staffKey;
  if (next.order.status === "paid" && state.receiptShown !== next.order.receipt.order_number) {
    state.receiptShown = next.order.receipt.order_number;
    showReceipt();
  }
}

async function update(path, body) {
  try {
    const session = await api(path, body);
    applyState(session);
    renderChips(session.chips);
  } catch (error) {
    showToast(error.message);
    renderCart();
  }
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

function renderChips(chips) {
  $("#chips").replaceChildren(...(chips || []).map((chip) =>
    el("button", { type: "button", disabled: state.busy, onclick: () => send(chip.text) }, chip.label)));
}

// "근거 보기"를 켜면 답변마다 어떤 문서를 찾았고 어떤 도구를 불렀는지 보여 준다.
function evidence(message) {
  if (!message.sources.length && !message.tools.length) return null;
  const sourceRow = (s) => el("li", {},
    el("span", { class: "tag" }, (s.match === "vector" ? "" : `${t(`match_${s.match}`)} `) + s.score.toFixed(2)),
    ` ${s.title} (${s.type})${s.query ? ` ← "${s.query}"` : ""}`);
  const toolRow = (c) => el("li", {}, el("code", {}, `${c.ok ? "✓" : "✗"} ${c.name}(${JSON.stringify(c.args)})`));
  return el("details", { class: "evidence" },
    el("summary", {}, t("evidence_summary", { docs: message.sources.length, tools: message.tools.length })),
    el("ul", {}, message.sources.map(sourceRow), message.tools.map(toolRow)));
}

function handleEvent(event, message) {
  switch (event.type) {
    case "delta":
      message.raw += event.text;
      message.status = null;
      paint(message);
      break;
    case "status": {
      const key = EDIT_TOOLS.includes(event.name) ? "status_edit" : `status_${event.name}`;
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
      message.append(el("div", { class: "message-cards" }, event.items.map(menuCard)));
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
  $("#pay").disabled = busy || !state.order || !state.order.lines.length;
}

async function send(text) {
  text = text.trim();
  if (!text || state.busy) return;
  document.body.dataset.view = "chat";
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
  $("#input").focus();
}

// ------------------------------------------------------------ 언어·세션

function relabel() {
  document.documentElement.lang = state.lang;
  for (const node of $$("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const node of $$("[data-i18n-placeholder]")) node.placeholder = t(node.dataset.i18nPlaceholder);
  for (const button of $$("#languages button")) button.setAttribute("aria-pressed", String(button.dataset.lang === state.lang));
  renderBoard();
  if (state.order) renderCart();
}

// 세션을 열거나 초기화했을 때: 대화창을 비우고 인사말과 지난 대화를 다시 그린다.
function openConversation(session) {
  $("#receipt").close();
  $("#messages").replaceChildren();
  state.receiptShown = null;
  applyState(session);
  addMessage("bot", session.greeting).classList.add("greeting");
  for (const turn of session.history) addMessage(turn.role === "user" ? "user" : "bot", turn.content);
  renderChips(session.chips);
}

async function restart() {
  openConversation(await api("/api/session/reset", {}));
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
  state.activeTab = menu.tabs[0] && menu.tabs[0].id;
  state.lang = session.language;
  $("#store-name").textContent = config.store.name;
  $("#languages").replaceChildren(...Object.entries(config.languages).map(([code, name]) =>
    el("button", { type: "button", "data-lang": code, onclick: () => changeLanguage(code) }, name)));
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
  $("#pay").addEventListener("click", () => send(t("pay_message")));
  $("#restart-button").addEventListener("click", restart);
  $("#staff-button").addEventListener("click", async () => applyState(await api("/api/staff", {})));
  for (const button of $$("#dine button")) {
    button.addEventListener("click", () => update("/api/order/options", { dine_option: button.dataset.dine }));
  }
  $("#straw").addEventListener("change", (event) => update("/api/order/options", { straw: event.target.checked }));
  $("#carrier").addEventListener("change", (event) => update("/api/order/options", { carrier: event.target.checked }));
  $("#discount").addEventListener("change", (event) => update("/api/order/options", { discount: event.target.value }));
  $("#stamp").addEventListener("change", (event) => update("/api/order/options", { stamp: event.target.checked }));

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
  // 메뉴 상세 팝업은 바깥을 누르면 닫힌다. 주문 완료 팝업은 "새 주문 시작"으로만 닫는다.
  $("#detail").addEventListener("click", (event) => event.target === $("#detail") && $("#detail").close());
  $("#receipt").addEventListener("cancel", (event) => event.preventDefault());
  $("#input").focus();
}

init().catch((error) => {
  $("#notice").textContent = `${I18N.ko.network_error} (${error.message})`;
  $("#notice").hidden = false;
});
