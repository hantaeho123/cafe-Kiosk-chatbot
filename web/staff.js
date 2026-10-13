// 직원 화면: 직원 호출, 결제가 끝난 주문(요청 메모 포함), 품절 관리. 2초마다 서버에 다시 묻는다.
// 키오스크의 "직원 전달" 기능이 닿는 곳이다: 직원 호출과 요청 메모가 여기에 뜬다.

const $ = (selector) => document.querySelector(selector);
let board = { calls: [], orders: [], menus: [] };

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

async function post(path, body) {
  await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  await refresh();
}

const SOURCES = { button: "화면 버튼", chat: "대화" };

function render() {
  const waiting = board.calls.filter((c) => c.status === "waiting");
  $("#call-count").textContent = waiting.length || "";
  $("#calls").replaceChildren(...(board.calls.length ? board.calls.map((call) =>
    el("div", { class: "ticket" + (call.status === "waiting" ? " fresh" : " done") },
      el("div", { class: "ticket-head" }, el("span", {}, call.reason), el("small", {}, `${call.time} · ${SOURCES[call.source] || call.source}`)),
      call.status === "waiting"
        ? el("button", { class: "secondary small", type: "button", onclick: () => post("/api/staff/call", { id: call.id }) }, "확인 (가는 중)")
        : el("small", {}, "확인함")))
    : [el("div", { class: "empty" }, "호출이 없습니다.")]));

  const fresh = board.orders.filter((o) => o.status === "new");
  $("#order-count").textContent = fresh.length || "";
  $("#orders").replaceChildren(...(board.orders.length ? board.orders.map((order) =>
    el("div", { class: "ticket" + (order.status === "new" ? "" : " done") },
      el("div", { class: "ticket-head" }, el("span", {}, `주문번호 ${order.number}`),
        el("small", {}, [order.time, order.dine_option, ...order.extras].filter(Boolean).join(" · "))),
      el("ul", {}, order.lines.map((line) => el("li", {},
        el("strong", {}, `${line.name} × ${line.quantity}`), line.voucher && " (교환권)",
        el("div", { class: "muted" }, line.options),
        line.notes.map((note) => el("div", { class: "memo" }, `요청: ${note}`))))),
      order.notes.map((note) => el("div", { class: "memo" }, `요청: ${note}`)),
      order.status === "new"
        ? el("button", { class: "secondary small", type: "button", onclick: () => post("/api/staff/order", { number: order.number }) }, "제조 완료")
        : el("small", {}, "제조 완료")))
    : [el("div", { class: "empty" }, "아직 결제된 주문이 없습니다.")]));

  const keyword = $("#search").value.trim();
  const menus = board.menus.filter((m) => !keyword || m.name.includes(keyword))
    .sort((a, b) => Number(b.sold_out) - Number(a.sold_out));  // 품절인 메뉴를 위로
  $("#stock-count").textContent = board.menus.filter((m) => m.sold_out).length || "";
  $("#menus").replaceChildren(...menus.map((menu) =>
    el("div", { class: "stock-row" + (menu.sold_out ? " out" : "") },
      el("div", {}, el("span", {}, menu.name), " ", el("small", {}, menu.category)),
      el("button", { class: menu.sold_out ? "outline" : "secondary small", type: "button",
        onclick: () => post("/api/staff/stock", { menu_id: menu.id, sold_out: !menu.sold_out }) }, menu.sold_out ? "품절 해제" : "품절 처리"))));
}

async function refresh() {
  try {
    board = await (await fetch("/api/staff/board")).json();
    render();
  } catch (error) { /* 다음 번에 다시 묻는다 */ }
}

$("#search").addEventListener("input", render);
refresh();
setInterval(refresh, 2000);
