/* Mesh-ПВЗ — единое приложение с ролями ПВЗ и продавца (vanilla JS, без сборки). */
const API = "/api/v1";

const state = {
  pvz: [],
  sellers: [],
  items: [],
  orders: [],
  edges: [],
  incidents: [],
  pvzId: 1,
  sellerId: 1,
  page: "dashboard",
  ledgerOrderId: null,
  compare: null,
  buyerCode: "",
  buyerData: null,
  buyerError: null,
};

const $ = (sel) => document.querySelector(sel);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const money = (value) =>
  value === null || value === undefined
    ? "—"
    : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(value) + " ₽";
const num = (value) => new Intl.NumberFormat("ru-RU").format(value ?? 0);
const dt = (iso) => (iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

const ROLE_KEYS = { admin: "demo-admin-key", seller: "demo-seller-key", pvz: "demo-pvz-key" };

function apiKey() {
  return ROLE_KEYS[localStorage.getItem("meshRole") || "admin"] || ROLE_KEYS.admin;
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: { "X-API-Key": apiKey() } };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(API + path, init);
  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    data = null;
  }
  if (!response.ok) {
    const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : response.statusText;
    throw new Error(detail);
  }
  return data;
}

let toastTimer = null;
function toast(message, kind = "") {
  const box = $("#toast");
  box.textContent = message;
  box.className = "toast " + kind;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.hidden = true), 6000);
}

async function guard(promise, okMessage) {
  try {
    const result = await promise;
    if (okMessage) toast(okMessage, "ok");
    return result;
  } catch (error) {
    toast(error.message, "error");
    return null;
  }
}

function pill(status) {
  const map = {
    stored: "Хранится", reserved: "Зарезервирован", assembled: "Собран", issued: "Выдан",
    returned: "Возвращён", created: "Создан", routed: "Маршрут", rerouted: "Перемаршрутизирован",
    in_transit: "В пути", open: "Открыт", resolved: "Решён", cancelled: "Отменён",
  };
  return `<span class="pill ${esc(status)}">${esc(map[status] || status)}</span>`;
}

const kindLabel = { mesh: "mesh (без хаба)", hub: "хаб + магистраль", local: "локальный", trunk: "магистраль" };

function segmentsText(route) {
  if (!route || !route.segments || !route.segments.length) return "—";
  return route.segments.map((seg) => `${seg.from_code} → ${seg.to_code}`).join("  ·  ");
}

function table(headers, rows, empty = "Нет данных") {
  if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
  return `<div class="table-wrap"><table><thead><tr>${headers
    .map((h) => `<th class="${h.num ? "num" : ""}">${esc(h.title)}</th>`)
    .join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

/* ---------- загрузка справочников ---------- */
async function loadCatalog() {
  const [pvz, sellers, items, orders, edges, incidents, clock] = await Promise.all([
    api("/pvz?include_hub=true"),
    api("/sellers"),
    api("/items"),
    api("/orders"),
    api("/edges"),
    api("/incidents"),
    api("/clock"),
  ]);
  state.pvz = pvz;
  state.sellers = sellers;
  state.items = items;
  state.orders = orders;
  state.edges = edges;
  state.incidents = incidents;
  $("#clock-badge").textContent = "Виртуальное время: " + dt(clock.virtual_now);
  if (!pvz.some((row) => row.id === state.pvzId && !row.is_hub)) state.pvzId = (pvz.find((row) => !row.is_hub) || pvz[0]).id;
  if (!sellers.some((row) => row.id === state.sellerId)) state.sellerId = sellers[0] ? sellers[0].id : 1;
}

const points = () => state.pvz.filter((row) => !row.is_hub);
const pvzOptions = (selected, includeHub = false) =>
  state.pvz
    .filter((row) => includeHub || !row.is_hub)
    .map((row) => `<option value="${row.id}" ${row.id === selected ? "selected" : ""}>${esc(row.code)} · ${esc(row.name)}</option>`)
    .join("");

/* ---------- страницы ---------- */
async function renderDashboard() {
  const summary = await api("/analytics/summary");
  const statuses = summary.orders.by_status || {};
  const cards = [
    { title: "Заказов всего", value: num(summary.orders.total), hint: "во всех статусах" },
    { title: "Логистика (внешние затраты)", value: money(summary.logistics_cost_rub), hint: "не прибыль цепочки" },
    { title: "Начислено за хранение", value: money(summary.storage_charged_rub), hint: "ПВЗ, ₽" },
    { title: "Открытых инцидентов", value: num(summary.incidents_open), hint: "требуют решения" },
  ];
  const partyRows = ["pvz", "marketplace", "seller", "buyer"].map((party) => {
    const value = summary.ledger[party] || 0;
    const label = { pvz: "ПВЗ", marketplace: "Маркетплейс", seller: "Продавец", buyer: "Покупатель" }[party];
    return `<tr><td>${label}</td><td class="num ${value >= 0 ? "good" : ""}" style="color:${value >= 0 ? "var(--good)" : "var(--bad)"}">${money(value)}</td></tr>`;
  });
  const statusRows = Object.entries(statuses).map(([status, count]) => `<tr><td>${pill(status)}</td><td class="num">${count}</td></tr>`);
  const pvzRows = points().map((row) => {
    const cls = row.utilization_pct >= 85 ? "warn" : "";
    return `<tr>
      <td><strong>${esc(row.code)}</strong><br><span class="muted small">${esc(row.name)}</span></td>
      <td>${esc(row.city)}</td>
      <td class="num">${row.used_cells} / ${row.capacity_cells}</td>
      <td style="min-width:120px"><div class="bar ${cls}"><span style="width:${Math.min(100, row.utilization_pct)}%"></span></div></td>
      <td class="num">${row.utilization_pct}%</td>
    </tr>`;
  });
  $("#page").innerHTML = `
    <div class="grid cols-4">${cards
      .map((c) => `<div class="card"><h3>${esc(c.title)}</h3><div class="metric small">${esc(c.value)}</div><div class="hint">${esc(c.hint)}</div></div>`)
      .join("")}</div>
    <div class="grid cols-2">
      <div class="card"><h2>Денежные потоки по сторонам</h2>
        ${table([{ title: "Сторона" }, { title: "Итог по ledger", num: true }], partyRows)}
        <div class="hint">Сумма по всем сторонам равна −внешним затратам логистики: переводы между участниками не создают прибыли цепочки.</div>
      </div>
      <div class="card"><h2>Заказы по статусам</h2>${table([{ title: "Статус" }, { title: "Количество", num: true }], statusRows)}</div>
    </div>
    <div class="card"><h2>Загрузка ПВЗ</h2>
      ${table([{ title: "ПВЗ" }, { title: "Город" }, { title: "Ячейки", num: true }, { title: "Загрузка" }, { title: "%", num: true }], pvzRows)}
      <div class="hint">Порог предупреждения — 85% (параметр модели). Хаб HUB-KZN — узел графа, не точка выдачи.</div>
    </div>`;
}

async function renderPvz() {
  const pvz = state.pvz.find((row) => row.id === state.pvzId) || points()[0];
  const [analytics, notes] = await Promise.all([
    api(`/analytics/pvz?pvz_id=${pvz.id}`),
    api("/notifications?audience=pvz"),
  ]);
  const stock = state.items.filter((row) => row.stored_pvz_id === pvz.id);
  const sellers = state.sellers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  const stockRows = stock.map((item) => `<tr>
      <td><strong>${esc(item.sku)}</strong><br><span class="muted small">${esc(item.title)}</span></td>
      <td class="num">${item.qty}</td>
      <td class="num">${item.reserved_qty}</td>
      <td class="num">${item.available_qty}</td>
      <td class="num">${item.cell_size}</td>
      <td>${pill(item.status)}</td>
      <td class="num">${money(item.price_rub)}</td>
      <td><button class="btn ghost small" data-action="relocate" data-id="${item.id}">Переместить</button></td>
    </tr>`);
  const revenueRows = Object.entries(analytics.revenue).map(([kind, value]) => {
    const label = { pickup_fee: "Выдача", storage: "Хранение", mesh_bonus: "mesh-бонус", relocation: "Перемещение" }[kind] || kind;
    return `<tr><td>${esc(label)}</td><td class="num">${money(value)}</td></tr>`;
  });
  const deductionRows = Object.entries(analytics.deductions).map(([kind, value]) => {
    const label = { penalty: "Удержания", return: "Возврат" }[kind] || kind;
    return `<tr><td>${esc(label)}</td><td class="num">${money(value)}</td></tr>`;
  });
  const noteRows = notes.slice(0, 12).map((note) => `<div class="list-item">
      <div class="row"><strong>${dt(note.ts)}</strong><span class="muted small">${esc(note.audience)}</span></div>
      <div>${esc(note.text)}</div></div>`);
  const alert = notes.find((note) => note.payload && note.payload.kind === "long_storage" && note.payload.offer);

  $("#page").innerHTML = `
    <div class="card">
      <div class="section-title">
        <h2>Кабинет ПВЗ</h2>
        <label>Точка выдачи<select id="pvz-select">${pvzOptions(pvz.id)}</select></label>
      </div>
      <div class="grid cols-4" style="margin-top:12px">
        <div><h3>Ёмкость</h3><div class="metric small">${pvz.used_cells} / ${pvz.capacity_cells}</div><div class="hint">свободно ${pvz.free_cells} ячеек</div></div>
        <div><h3>Загрузка</h3><div class="metric small">${pvz.utilization_pct}%</div><div class="bar ${pvz.utilization_pct >= 85 ? "warn" : ""}"><span style="width:${Math.min(100, pvz.utilization_pct)}%"></span></div></div>
        <div><h3>Прибыль за период</h3><div class="metric small ${analytics.profit_rub >= 0 ? "good" : "bad"}">${money(analytics.profit_rub)}</div><div class="hint">ledger ${money(analytics.ledger_net_rub)} − операции ${money(analytics.op_costs_modeled_rub)}</div></div>
        <div><h3>Хранение</h3><div class="metric small">${money(analytics.revenue.storage || 0)}</div><div class="hint">${analytics.storage_cell_days} ячейко-дней</div></div>
      </div>
      <div class="hint">${esc(pvz.code)} · ${esc(pvz.name)} · ${esc(pvz.city)} · тариф ${money(pvz.storage_rate_day)}/ячейка·день, выдача ${money(pvz.op_fee)}</div>
    </div>

    <div class="grid cols-2">
      <div class="card"><h2>Принять товар на хранение</h2>
        <form id="accept-form" class="form-row">
          <label>SKU<input name="sku" required placeholder="TD-2001" /></label>
          <label>Наименование<input name="title" required placeholder="Наушники" /></label>
          <label>Продавец<select name="seller_id">${sellers}</select></label>
          <label>Количество<input name="qty" type="number" min="1" value="1" required /></label>
          <label>Ячеек на шт.<input name="cell_size" type="number" min="1" max="10" value="1" required /></label>
          <label>Цена, ₽<input name="price_rub" type="number" min="1" value="2500" required /></label>
          <button class="btn" type="submit">Принять</button>
        </form>
        <div class="hint">Приёмка занимает ячейки и пишет начисление за операции ПВЗ (имитация).</div>
      </div>
      <div class="card"><h2>Доходы и удержания ПВЗ</h2>
        <div class="grid cols-2">
          <div>${table([{ title: "Доход" }, { title: "₽", num: true }], revenueRows, "Пока нет начислений")}</div>
          <div>${table([{ title: "Удержание" }, { title: "₽", num: true }], deductionRows, "Удержаний нет")}</div>
        </div>
        <div class="hint">Порог длительного хранения — 21 день / 600 ₽; при срабатывании предлагается перемещение за 60 ₽.
        ${alert ? `<br><strong>Активно предложение:</strong> ${esc(alert.payload.offer.suggested_code)} — ${alert.payload.offer.cells} яч., ${money(alert.payload.offer.cost_rub)}.` : ""}</div>
      </div>
    </div>

    <div class="card"><h2>Остатки на ${esc(pvz.code)}</h2>
      ${table(
        [{ title: "Товар" }, { title: "Всего", num: true }, { title: "Резерв", num: true }, { title: "Доступно", num: true },
         { title: "Ячеек/шт", num: true }, { title: "Статус" }, { title: "Цена", num: true }, { title: "" }],
        stockRows, "На этой точке пока нет товаров")}
    </div>

    <div class="card"><h2>Уведомления ПВЗ</h2>
      <div class="list">${noteRows.join("") || '<div class="empty">Уведомлений нет</div>'}</div>
    </div>`;

  $("#pvz-select").addEventListener("change", (event) => {
    state.pvzId = Number(event.target.value);
    render();
  });
  $("#accept-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = {
      sku: form.get("sku"),
      title: form.get("title"),
      seller_id: Number(form.get("seller_id")),
      qty: Number(form.get("qty")),
      cell_size: Number(form.get("cell_size")),
      price_rub: Number(form.get("price_rub")),
      pvz_id: pvz.id,
    };
    const result = await guard(api("/items", { method: "POST", body: payload }), "Товар принят на хранение");
    if (result) {
      await loadCatalog();
      render();
    }
  });
}

async function renderSeller() {
  const seller = state.sellers.find((row) => row.id === state.sellerId) || state.sellers[0];
  const analytics = await api(`/analytics/seller?seller_id=${seller.id}`);
  const stock = state.items.filter((row) => row.seller_id === seller.id);
  const stockRows = stock.map((item) => `<tr>
      <td><strong>${esc(item.sku)}</strong><br><span class="muted small">${esc(item.title)}</span></td>
      <td>${esc(item.stored_pvz_code)}</td>
      <td class="num">${item.qty}</td>
      <td class="num">${item.reserved_qty}</td>
      <td>${pill(item.status)}</td>
    </tr>`);
  const itemOptions = stock
    .filter((item) => item.available_qty > 0)
    .map((item) => `<option value="${item.id}">${esc(item.sku)} · ${esc(item.title)} (${item.available_qty} шт. на ${esc(item.stored_pvz_code)})</option>`)
    .join("");
  const orderRows = analytics.orders.map((order) => `<tr>
      <td>${esc(order.code)}<br><span class="muted small">${esc(order.title || "")}</span></td>
      <td>${pill(order.status)}</td>
      <td class="num">${money(order.goods_rub)}</td>
      <td class="num">${money(order.commission_rub)}</td>
      <td class="num">${money(order.net_rub)}</td>
    </tr>`);
  $("#page").innerHTML = `
    <div class="card">
      <div class="section-title">
        <h2>Кабинет продавца</h2>
        <label>Продавец<select id="seller-select">${state.sellers
          .map((s) => `<option value="${s.id}" ${s.id === seller.id ? "selected" : ""}>${esc(s.name)}</option>`)
          .join("")}</select></label>
      </div>
      <div class="grid cols-3" style="margin-top:12px">
        <div><h3>Выручка от товаров</h3><div class="metric small">${money(analytics.revenue.goods_rub)}</div></div>
        <div><h3>Комиссия маркетплейса</h3><div class="metric small bad">−${money(analytics.costs.commission_rub)}</div><div class="hint">${seller.commission_pct}% от товара</div></div>
        <div><h3>Прибыль продавца</h3><div class="metric small ${analytics.profit_rub >= 0 ? "good" : "bad"}">${money(analytics.profit_rub)}</div></div>
      </div>
    </div>

    <div class="grid cols-2">
      <div class="card"><h2>Оформить заказ</h2>
        <form id="order-form" class="form-row">
          <label>Товар<select name="item_id" required>${itemOptions || '<option value="">нет доступных остатков</option>'}</select></label>
          <label>Количество<input name="qty" type="number" min="1" value="1" required /></label>
          <label>Пункт выдачи<select name="to_pvz_id">${pvzOptions(state.pvzId)}</select></label>
          <label class="inline"><input type="checkbox" name="accept_latency" checked style="width:auto" /> Допустимо увеличение срока</label>
          <button class="btn" type="submit">Оформить</button>
        </form>
        <div class="hint">Заказ резервирует остаток и сразу строит маршрут. Стоимость для покупателя и срок показываются после оформления.</div>
      </div>
      <div class="card"><h2>Заказы продавца</h2>
        ${table([{ title: "Заказ" }, { title: "Статус" }, { title: "Товар", num: true }, { title: "Комиссия", num: true }, { title: "Нетто", num: true }], orderRows, "Заказов ещё нет")}
      </div>
    </div>

    <div class="card"><h2>Остатки продавца</h2>
      ${table([{ title: "Товар" }, { title: "ПВЗ" }, { title: "Всего", num: true }, { title: "Резерв", num: true }, { title: "Статус" }], stockRows, "Нет товаров")}
    </div>`;
  $("#seller-select").addEventListener("change", (event) => {
    state.sellerId = Number(event.target.value);
    render();
  });
  $("#order-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    if (!form.get("item_id")) return toast("Нет доступного остатка для заказа", "error");
    const payload = {
      item_id: Number(form.get("item_id")),
      qty: Number(form.get("qty")),
      to_pvz_id: Number(form.get("to_pvz_id")),
      accept_latency: form.get("accept_latency") === "on",
    };
    const result = await guard(api("/orders", { method: "POST", body: payload }));
    if (result) {
      toast(`Заказ ${result.order.code}: ${result.order.route.kind}, ${money(result.order.route.total_cost_rub)}, ${result.order.route.total_hours} ч`, "ok");
      await loadCatalog();
      render();
    }
  });
}

async function renderOrders() {
  const rows = state.orders.map((order) => {
    const actions = [];
    if (order.status === "routed" || order.status === "rerouted") actions.push(`<button class="btn small" data-action="assemble" data-id="${order.id}">Собрать</button>`);
    if (order.status === "assembled") actions.push(`<button class="btn ghost small" data-action="transit" data-id="${order.id}">В путь</button>`);
    if (order.status === "assembled" || order.status === "in_transit") actions.push(`<button class="btn good small" data-action="issue" data-id="${order.id}">Выдать</button>`);
    if (order.status === "issued") actions.push(`<button class="btn danger small" data-action="return" data-id="${order.id}">Возврат</button>`);
    if (["routed", "rerouted", "assembled"].includes(order.status)) actions.push(`<button class="btn danger small" data-action="cancel" data-id="${order.id}">Отменить</button>`);
    actions.push(`<button class="btn ghost small" data-action="ledger" data-id="${order.id}">Начисления</button>`);
    return `<tr>
      <td><strong>${esc(order.code)}</strong><br><span class="muted small">${esc(order.title || "")} × ${order.qty}</span></td>
      <td>${order.from_code} → ${order.to_code}</td>
      <td>${order.route ? kindLabel[order.route.kind] || order.route.kind : "—"}<br><span class="segments">${segmentsText(order.route)}</span></td>
      <td class="num">${order.route ? money(order.route.total_cost_rub) : "—"}</td>
      <td class="num">${order.route ? order.route.total_hours + " ч" : "—"}</td>
      <td class="num">${money(order.buyer_total_rub)}</td>
      <td>${pill(order.status)}<br><span class="muted small">${dt(order.created_at)}</span></td>
      <td class="inline">${actions.join(" ")}</td>
    </tr>`;
  });

  let ledgerBlock = "";
  if (state.ledgerOrderId) {
    const rowsLedger = await api(`/ledger?order_id=${state.ledgerOrderId}`);
    const body = rowsLedger.map((row) => `<tr><td>${esc(row.party)}</td><td>${esc(row.kind)}</td><td class="num" style="color:${row.amount_rub >= 0 ? "var(--good)" : "var(--bad)"}">${money(row.amount_rub)}</td><td class="muted small">${esc(row.note || "")}</td></tr>`);
    const total = rowsLedger.reduce((sum, row) => sum + row.amount_rub, 0);
    ledgerBlock = `<div class="card"><div class="section-title"><h2>Начисления по заказу #${state.ledgerOrderId}</h2>
      <button class="btn ghost small" data-action="close-ledger">Скрыть</button></div>
      ${table([{ title: "Сторона" }, { title: "Операция" }, { title: "Сумма", num: true }, { title: "Комментарий" }], body, "Начислений нет")}
      <div class="hint">Сумма по заказу: <strong>${money(total)}</strong> — это ровно внешние затраты логистики со знаком минус; переводы между участниками компенсируются.</div>
    </div>`;
  }

  $("#page").innerHTML = `
    <div class="card"><h2>Все заказы</h2>
      ${table(
        [{ title: "Заказ" }, { title: "Маршрут" }, { title: "Схема" }, { title: "Логистика", num: true },
         { title: "Срок", num: true }, { title: "Покупатель", num: true }, { title: "Статус" }, { title: "Действия" }],
        rows, "Заказов нет — создайте в кабинете продавца")}
      <div class="hint">Переходы статусов проверяются сервером: недопустимый переход вернёт 409 с объяснением.</div>
    </div>
    ${ledgerBlock}`;
}

async function renderRoutes() {
  const from = Number($("#from-select")?.value || state.pvzId || 1);
  const to = Number($("#to-select")?.value || 3);
  const accept = $("#accept-latency") ? $("#accept-latency").checked : true;
  const comparison = await guard(api(`/routes/compare?from=${from}&to=${to}&accept_latency=${accept}`));
  if (comparison) state.compare = comparison;

  const variant = (data, title) => `
    <div class="card"><h2>${title}</h2>
      <div class="metric small ${data.available ? "" : "bad"}">${data.available ? money(data.total_cost_rub) + " · " + data.total_hours + " ч" : "недоступен"}</div>
      <div class="segments">${segmentsText(data)}</div>
      ${data.segments && data.segments.length
        ? `<div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>Сегмент</th><th>Тип</th><th class="num">₽</th><th class="num">ч</th></tr></thead><tbody>${data.segments
            .map((seg) => `<tr><td>${esc(seg.from_code)} → ${esc(seg.to_code)}</td><td>${esc(kindLabel[seg.kind] || seg.kind)}</td><td class="num">${money(seg.cost_rub)}</td><td class="num">${seg.hours}</td></tr>`)
            .join("")}</tbody></table></div>`
        : `<div class="empty">${esc(data.reason || "")}</div>`}
    </div>`;

  const edgeRows = state.edges.map((edge) => `<tr>
      <td>${esc(edge.from_code)} → ${esc(edge.to_code)}</td>
      <td>${esc(kindLabel[edge.kind] || edge.kind)}</td>
      <td class="num">${money(edge.cost_rub)}</td>
      <td class="num">${edge.hours}</td>
      <td>${edge.is_active ? '<span class="pill issued">активен</span>' : '<span class="pill open">отключён</span>'}</td>
      <td><button class="btn ghost small" data-action="${edge.is_active ? "disable-edge" : "enable-edge"}" data-id="${edge.id}">${edge.is_active ? "Отключить" : "Включить"}</button></td>
    </tr>`);

  const cmp = state.compare;
  $("#page").innerHTML = `
    <div class="card"><div class="section-title"><h2>Сравнение маршрутов</h2>
      <div class="inline">
        <label>Откуда<select id="from-select">${pvzOptions(cmp ? cmp.from.id : 1)}</select></label>
        <label>Куда<select id="to-select">${pvzOptions(cmp ? cmp.to.id : 3)}</select></label>
        <label class="inline"><input type="checkbox" id="accept-latency" ${cmp && cmp.accept_latency ? "checked" : ""} style="width:auto" /> допустить рост срока</label>
        <button class="btn" id="compare-btn">Рассчитать</button>
      </div></div>
      ${cmp ? `<div class="hint"><strong>Выбрано: ${esc(cmp.chosen || "нет")}</strong> — ${esc(cmp.reason)}</div>` : ""}
    </div>
    <div class="grid cols-2">
      ${cmp ? variant(cmp.mesh, "mesh · без хаба") : ""}
      ${cmp ? variant(cmp.hub, "хаб + магистраль") : ""}
    </div>
    <div class="card"><h2>Сегменты сети</h2>
      <div class="hint">Отключение сегмента действующего заказа создаёт инцидент и запускает автоперевыбор маршрута.</div>
      ${table([{ title: "Сегмент" }, { title: "Тип" }, { title: "Стоимость", num: true }, { title: "Часы", num: true }, { title: "Состояние" }, { title: "" }], edgeRows)}
    </div>`;

  const recalc = () => {
    state.compare = null;
    renderRoutes();
  };
  $("#compare-btn").addEventListener("click", recalc);
  $("#from-select").addEventListener("change", recalc);
  $("#to-select").addEventListener("change", recalc);
  $("#accept-latency").addEventListener("change", recalc);
}

async function renderIncidents() {
  const rows = state.incidents.map((incident) => `<tr>
      <td>#${incident.id}<br><span class="muted small">${dt(incident.ts)}</span></td>
      <td>${esc(incident.type)}<br><span class="muted small">${esc(incident.description)}</span></td>
      <td>${incident.order_id ? "#" + incident.order_id : "—"}</td>
      <td>${pill(incident.status)}</td>
      <td>${incident.resolved_route_id ? "маршрут #" + incident.resolved_route_id : "—"}</td>
      <td>${incident.status === "open" ? `<button class="btn small" data-action="reroute" data-id="${incident.id}">Перевыбрать маршрут</button>` : ""}</td>
    </tr>`);
  $("#page").innerHTML = `
    <div class="card"><h2>Инциденты логистики</h2>
      ${table([{ title: "Инцидент" }, { title: "Причина" }, { title: "Заказ" }, { title: "Статус" }, { title: "Решение" }, { title: "" }], rows, "Инцидентов нет")}
      <div class="hint">Инцидент открывается при отключении сегмента, на котором стоит активный заказ. Если путь найден — заказ помечается «перемаршрутизирован», инцидент закрывается. Если пути нет — остаётся открытым до восстановления сегмента.</div>
    </div>`;
}

async function renderClock() {
  const clock = await api("/clock");
  const notes = await api("/notifications?limit=50");
  const noteRows = notes.map((note) => `<div class="list-item">
      <div class="row"><strong>${dt(note.ts)}</strong><span class="badge">${esc(note.audience)}${note.order_id ? " · заказ #" + note.order_id : ""}</span></div>
      <div>${esc(note.text)}</div>
      ${note.payload && note.payload.kind ? `<div class="muted small">тип: ${esc(note.payload.kind)}</div>` : ""}
    </div>`);
  $("#page").innerHTML = `
    <div class="grid cols-2">
      <div class="card"><h2>Виртуальные часы</h2>
        <div class="metric">${dt(clock.virtual_now)}</div>
        <p class="muted small">${esc(clock.timezone)}. Время виртуальное и одинаковое для всех расчётов — сценарии хранения воспроизводимы.</p>
        <div class="form-row">
          <label>Сдвинуть на дни<input id="days-input" type="number" min="1" max="365" value="7" /></label>
          <button class="btn" id="advance-btn">Сдвинуть время</button>
        </div>
        <div class="hint">Сдвиг начисляет хранение по всем остаткам и рассылает уведомления о длительном хранении с предложением перемещения.</div>
      </div>
      <div class="card"><h2>Сводка правил</h2>
        <div class="list">
          <div class="list-item">Хранение: 5 ₽ за ячейку в день — начисляется маркетплейсом в пользу ПВЗ</div>
          <div class="list-item">Длительное хранение: с 21 дня или 600 ₽ — уведомление и предложение переместить остаток за 60 ₽</div>
          <div class="list-item">Выдача: 25 ₽ ПВЗ + 15 ₽ бонус за заказ без хаба</div>
          <div class="list-item">Возврат: удержание 60 ₽ за обратную логистику, срок — 7 дней</div>
          <div class="list-item">Платёжный шлюз не подключён: все суммы — программные расчёты</div>
        </div>
      </div>
    </div>
    <div class="card"><h2>Лента уведомлений</h2>
      <div class="list">${noteRows.join("") || '<div class="empty">Уведомлений нет</div>'}</div>
    </div>`;
  $("#advance-btn").addEventListener("click", async () => {
    const days = Number($("#days-input").value || 1);
    const result = await guard(api(`/clock/advance?days=${days}`, { method: "POST" }));
    if (result) {
      toast(`Начислено за хранение: ${money(result.charged_total_rub)}, уведомлений: ${result.alerts}`, "ok");
      await loadCatalog();
      render();
    }
  });
}

async function renderBuyer() {
  let card = "";
  if (state.buyerError) {
    card = `<div class="card"><h2>Заказ не найден</h2><p>${esc(state.buyerError)}</p></div>`;
  }
  const data = state.buyerData;
  if (data) {
    const timeline = (data.timeline || [])
      .map((event) => `<li><strong>${esc(event.label)}</strong><br><span class="muted small">${dt(event.ts)}</span></li>`)
      .join("");
    card = `<div class="card">
      <div class="section-title"><h2>${esc(data.code)}</h2>${pill(data.status)}</div>
      <p>${esc(data.hint)}</p>
      <div class="grid cols-4">
        <div><span class="muted small">Куда</span><br><strong>${esc(data.destination.code || "—")}</strong> · ${esc(data.destination.city || "")}</div>
        <div><span class="muted small">Схема</span><br>${data.route ? esc(kindLabel[data.route.kind] || data.route.kind) : "—"}</div>
        <div><span class="muted small">К оплате</span><br><strong>${money(data.buyer_total_rub)}</strong></div>
        <div><span class="muted small">Возврат до</span><br>${data.return_deadline ? dt(data.return_deadline) : "после выдачи"}</div>
      </div>
      <h3>История</h3>
      <ul class="timeline">${timeline || "<li class=\"muted\">Событий пока нет</li>"}</ul>
    </div>`;
  }
  $("#page").innerHTML = `
    <div class="card"><h2>Отслеживание заказа</h2>
      <div class="inline">
        <label style="min-width:220px">Код заказа<input id="track-code" placeholder="ORD-00001" value="${esc(state.buyerCode)}"></label>
        <button class="btn" data-action="track">Показать</button>
      </div>
      <div class="hint">Страница покупателя публичная: код, статус, сроки и стоимость доставки. Комиссии
      маркетплейса и расчёты продавца здесь не показываются.</div>
    </div>${card}`;
}

/* ---------- роутинг ---------- */
const pages = {
  dashboard: { title: "Дашборд", subtitle: "Сводка по сети ПВЗ и денежным потокам", render: renderDashboard },
  pvz: { title: "Кабинет ПВЗ", subtitle: "Приёмка, остатки, хранение и доходы точки", render: renderPvz },
  seller: { title: "Кабинет продавца", subtitle: "Остатки, заказы и расчёты продавца", render: renderSeller },
  orders: { title: "Заказы", subtitle: "Маршрут, сборка, выдача, возврат", render: renderOrders },
  buyer: { title: "Покупатель", subtitle: "Отслеживание заказа по коду без входа в систему", render: renderBuyer },
  routes: { title: "Маршруты и сеть", subtitle: "mesh против хаба и управление сегментами", render: renderRoutes },
  incidents: { title: "Инциденты", subtitle: "Нештатные ситуации и перевыбор маршрута", render: renderIncidents },
  clock: { title: "Время и уведомления", subtitle: "Виртуальные часы, начисления, лента событий", render: renderClock },
};

async function render() {
  const page = pages[state.page];
  $("#page-title").textContent = page.title;
  $("#page-subtitle").textContent = page.subtitle;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.page === state.page));
  try {
    await loadCatalog();
    await page.render();
  } catch (error) {
    $("#page").innerHTML = `<div class="card"><h2>Ошибка</h2><p>${esc(error.message)}</p><p class="muted small">Проверьте, что API запущен и доступен по /api/v1.</p></div>`;
  }
}

document.addEventListener("click", async (event) => {
  const nav = event.target.closest(".nav-item");
  if (nav) {
    state.page = nav.dataset.page;
    state.ledgerOrderId = null;
    render();
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const id = Number(button.dataset.id);
  const action = button.dataset.action;
  let result = null;
  if (action === "assemble") result = await guard(api(`/orders/${id}/assemble`, { method: "POST" }), "Заказ собран");
  if (action === "transit") result = await guard(api(`/orders/${id}/transit`, { method: "POST" }), "Заказ в пути");
  if (action === "issue") result = await guard(api(`/orders/${id}/issue`, { method: "POST" }), "Заказ выдан, начисления сформированы");
  if (action === "return") result = await guard(api(`/orders/${id}/return`, { method: "POST" }), "Возврат оформлен");
  if (action === "cancel") result = await guard(api(`/orders/${id}/cancel`, { method: "POST" }), "Заказ отменён");
  if (action === "track") {
    state.buyerCode = ($("#track-code")?.value || "").trim().toUpperCase();
    state.buyerData = null;
    state.buyerError = null;
    try {
      state.buyerData = await api(`/track/${encodeURIComponent(state.buyerCode)}`);
    } catch (error) {
      state.buyerError = error.message;
    }
    render();
    return;
  }
  if (action === "ledger") {
    state.ledgerOrderId = state.ledgerOrderId === id ? null : id;
    render();
    return;
  }
  if (action === "close-ledger") {
    state.ledgerOrderId = null;
    render();
    return;
  }
  if (action === "disable-edge") {
    result = await guard(api(`/edges/${id}/disable`, { method: "POST" }));
    if (result) {
      const incidents = result.incidents || [];
      toast(incidents.length
        ? `Сегмент отключён. Инцидентов: ${incidents.length}, статус: ${incidents[0].incident.status}`
        : "Сегмент отключён. Активных заказов на нём нет.", incidents.length ? "ok" : "");
    }
  }
  if (action === "enable-edge") result = await guard(api(`/edges/${id}/enable`, { method: "POST" }), "Сегмент включён");
  if (action === "reroute") result = await guard(api(`/incidents/${id}/reroute`, { method: "POST", body: { accept_latency: true } }), "Маршрут перевыбран");
  if (action === "relocate") {
    const target = prompt("Введите ID ПВЗ для перемещения остатка (см. список точек):", String(points().find((row) => row.id !== state.pvzId)?.id || 2));
    if (!target) return;
    result = await guard(api(`/items/${id}/relocate`, { method: "POST", body: { to_pvz_id: Number(target) } }));
    if (result) toast(`Остаток перемещён: ${result.relocation.cells} яч.`, "ok");
  }
  if (result) {
    await loadCatalog();
    render();
  }
});

$("#refresh").addEventListener("click", render);

const roleSelect = $("#role-select");
if (roleSelect) {
  roleSelect.value = localStorage.getItem("meshRole") || "admin";
  roleSelect.addEventListener("change", () => {
    localStorage.setItem("meshRole", roleSelect.value);
    location.reload();
  });
}
render();
