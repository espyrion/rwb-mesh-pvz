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
  mapNode: null,
  mapOrderId: null,
  mapFailure: null,
  econ: {},
  econBase: null,
  econData: null,
  econReady: false,
  score: null,
  audit: null,
  demo: { steps: {}, ctx: {}, running: null, prep: [] },
};

const $ = (sel) => document.querySelector(sel);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const money = (value) =>
  value === null || value === undefined
    ? "—"
    : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(value) + " ₽";
const num = (value) => new Intl.NumberFormat("ru-RU").format(value ?? 0);
/* Дробные числа в интерфейсе — только через запятую: 0.8 → «0,8». */
const dec = (value) =>
  value === null || value === undefined || Number.isNaN(Number(value)) ? "нет данных" : String(value).replace(".", ",");
const signedMoney = (value) =>
  value === null || value === undefined ? "нет данных" : (value > 0 ? "+" : value < 0 ? "−" : "") + money(Math.abs(value));
const signedDec = (value, unit = "") =>
  value === null || value === undefined
    ? "нет данных"
    : (value > 0 ? "+" : value < 0 ? "−" : "") + dec(Math.abs(value)) + (unit ? " " + unit : "");
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
  if (options.signal) init.signal = options.signal;
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
  if (!state.econData) state.econData = await api("/economics/scenario");
  const alertPct = state.econData.queue_threshold ? state.econData.queue_threshold.alert_pct : null;
  const statuses = summary.orders.by_status || {};
  const cards = [
    { title: "Заказов всего", value: num(summary.orders.total), hint: `mesh ${Math.round((summary.routes.mesh_share || 0) * 100)}% против хаба` },
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
    const cls = alertPct === null ? "" : row.utilization_pct >= alertPct ? "warn" : row.utilization_pct >= alertPct / 2 ? "mid" : "";
    return `<tr>
      <td><strong>${esc(row.code)}</strong><br><span class="muted small">${esc(row.name)}</span></td>
      <td>${esc(row.city)}</td>
      <td class="num">${row.used_cells} / ${row.capacity_cells}</td>
      <td style="min-width:120px"><div class="bar ${cls}"><span style="width:${Math.min(100, row.utilization_pct)}%"></span></div></td>
      <td class="num">${dec(row.utilization_pct)}%</td>
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
      <div class="hint">${alertPct === null ? "Порог предупреждения: нет данных" : "Порог предупреждения — " + dec(alertPct) + "% (queue_threshold.alert_pct)"}. Хаб HUB-KZN — узел графа, не точка выдачи.</div>
    </div>`;
}

function scoreResultHtml(entry) {
  if (!entry) return `<div class="empty">нет данных: скоринг не запускался</div>`;
  if (entry.error) return `<div class="empty">нет данных: ${esc(entry.error)}</div>`;
  const d = entry.data;
  const local = d.recommendation === "mesh_pvz";
  const components = d.components.map(
    (part) => `<tr><td>${esc(part.label)}</td><td class="num">${dec(part.weight)}</td>
      <td class="num">${dec(part.raw)}</td><td class="num">${dec(part.points)} / ${dec(part.max_points)}</td></tr>`
  );
  return `<div class="grid cols-3 mt">
      <div><h3>Score</h3><div class="metric small ${d.score >= d.threshold ? "good" : "bad"}">${dec(d.score)}</div><div class="hint">порог ${dec(d.threshold)}</div></div>
      <div><h3>Рекомендация</h3><div class="metric small">${local ? "оставить в ПВЗ города" : "выгоднее через хаб"}</div>
        <div class="hint">${esc(d.recommendation)} · уверенность ${dec(d.confidence_pct)}%</div></div>
      <div><h3>Партия</h3><div class="metric small">${dec(d.cells_total)} яч.</div>
        <div class="hint">${dec(d.expected_storage_days)} дн · ${money(d.expected_storage_cost_rub)} хранения</div></div>
    </div>
    ${table(
      [{ title: "Компонент" }, { title: "Вес", num: true }, { title: "Факт", num: true }, { title: "Баллы", num: true }],
      components
    )}
    <div class="hint">Спрос ${dec(d.demand_per_day)}/день (источник: ${esc(d.demand_source)}). ${d.capacity_override ? "Партия больше свободных ячеек — это учтено в рекомендации." : "Партия помещается в свободные ячейки."}</div>
    ${d.reasons.length ? `<div class="list mt">${d.reasons.map((reason) => `<div class="list-item small">${esc(reason)}</div>`).join("")}</div>` : ""}`;
}

function auditResultHtml(entry) {
  if (!entry) return `<div class="empty">нет данных: сверка не запускалась</div>`;
  if (entry.error) return `<div class="empty">нет данных: ${esc(entry.error)}</div>`;
  const d = entry.data;
  const rows = d.lines.map(
    (line) => `<tr><td>${esc(line.sku)}</td><td class="num">${line.expected_qty}</td><td class="num">${line.actual_qty}</td>
      <td class="num ${line.delta === 0 ? "muted" : line.delta < 0 ? "bad" : "good"}">${line.delta > 0 ? "+" : ""}${line.delta}</td>
      <td class="num">${line.cells_delta > 0 ? "+" : ""}${line.cells_delta}</td><td>${pill(line.status)}</td>
      <td>${line.incident_id ? "#" + line.incident_id : "—"}</td></tr>`
  );
  return `<div class="grid cols-4 mt">
      <div><h3>Точность сверки</h3><div class="metric small ${d.accuracy_pct >= 100 ? "good" : "bad"}">${dec(d.accuracy_pct)}%</div>
        <div class="hint">до сверки ${dec(d.accuracy_before_pct)}%</div></div>
      <div><h3>SLA-рейтинг</h3><div class="metric small">${esc(d.sla_rating)}</div><div class="hint">сверок всего ${d.audits_total}</div></div>
      <div><h3>Удержания</h3><div class="metric small ${d.penalty_total_rub > 0 ? "bad" : ""}">${money(d.penalty_total_rub)}</div>
        <div class="hint">расхождение ${d.total_delta_units} шт. · ${d.cells_delta} яч.</div></div>
      <div><h3>Совпало</h3><div class="metric small">${d.matched} / ${d.lines.length}</div>
        <div class="hint">недостача ${d.shortage} · излишек ${d.surplus}</div></div>
    </div>
    ${table(
      [{ title: "SKU" }, { title: "Учёт", num: true }, { title: "Факт", num: true }, { title: "Δ шт.", num: true },
       { title: "Δ яч.", num: true }, { title: "Статус" }, { title: "Инцидент" }],
      rows,
      "нет данных"
    )}
    <div class="hint">${d.idempotent ? "Повторная отправка того же факта — без новых удержаний (idempotent)." : `Сверка применена: занято ${d.used_cells} из ${d.used_cells + d.free_cells} ячеек.`}
      audit_accuracy_pct ${dec(d.audit_accuracy_pct)}%.</div>`;
}

async function renderPvz() {
  const pvz = state.pvz.find((row) => row.id === state.pvzId) || points()[0];
  const [analytics, notes] = await Promise.all([
    api(`/analytics/pvz?pvz_id=${pvz.id}`),
    api("/notifications?audience=pvz"),
  ]);
  if (!state.econData) state.econData = await api("/economics/scenario");
  const model = state.econData;
  const alertPct = model.queue_threshold ? model.queue_threshold.alert_pct : null;
  const goodsPrice = model.params.goods_rub;
  const auditScore = state.score && state.score.pvzId === pvz.id ? state.score : null;
  const auditResult = state.audit && state.audit.pvzId === pvz.id ? state.audit : null;
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
        ${pvz.is_hub ? "" : `<button class="btn ghost" data-action="${pvz.is_active ? "node-down" : "node-up"}" data-id="${pvz.id}">${pvz.is_active ? "Вывести из строя" : "Вернуть в строй"}</button>`}
      </div>
      <div class="grid cols-4" style="margin-top:12px">
        <div><h3>Ёмкость</h3><div class="metric small">${pvz.used_cells} / ${pvz.capacity_cells}</div><div class="hint">свободно ${pvz.free_cells} ячеек</div></div>
        <div><h3>Загрузка</h3><div class="metric small">${dec(pvz.utilization_pct)}%</div><div class="bar ${alertPct !== null && pvz.utilization_pct >= alertPct ? "warn" : ""}"><span style="width:${Math.min(100, pvz.utilization_pct)}%"></span></div>
          <div class="hint">${alertPct === null ? "порог предупреждения: нет данных" : "порог предупреждения " + dec(alertPct) + "%"}</div></div>
        <div><h3>Прибыль за период</h3><div class="metric small ${analytics.profit_rub >= 0 ? "good" : "bad"}">${money(analytics.profit_rub)}</div><div class="hint">ledger ${money(analytics.ledger_net_rub)} − операции ${money(analytics.op_costs_modeled_rub)} − постоянные ${money(analytics.fixed_costs_modeled_rub)}</div></div>
        <div><h3>Хранение</h3><div class="metric small">${money(analytics.revenue.storage || 0)}</div><div class="hint">${analytics.storage_cell_days} ячейко-дней</div></div>
      </div>
      <div class="grid cols-3 mt">
        <div><h3>Точность аудита</h3><div class="metric small ${pvz.audit_accuracy_pct === null || pvz.audit_accuracy_pct === undefined ? "" : pvz.audit_accuracy_pct >= 100 ? "good" : "bad"}">${pvz.audit_accuracy_pct === null || pvz.audit_accuracy_pct === undefined ? "нет данных" : dec(pvz.audit_accuracy_pct) + "%"}</div><div class="hint">audit_accuracy_pct · сверок ${pvz.audit_count ?? "нет данных"}</div></div>
        <div><h3>SLA-рейтинг</h3><div class="metric small">${esc(pvz.audit_sla_rating ?? pvz.sla_rating ?? "нет данных")}</div><div class="hint">audit_sla_rating</div></div>
        <div><h3>Последняя сверка</h3><div class="metric small">${pvz.last_audit_at ? dt(pvz.last_audit_at) : "нет данных"}</div><div class="hint">last_audit_at</div></div>
      </div>
      <div class="hint">${esc(pvz.code)} · ${esc(pvz.name)} · ${esc(pvz.city)} · тариф ${money(pvz.storage_rate_day)}/ячейка·день, выдача ${money(pvz.op_fee)}
        ${pvz.is_active ? "" : '<br><strong>Точка выведена из строя:</strong> маршруты через неё не строятся, приёмка и выдача недоступны.'}</div>
    </div>

    <div class="grid cols-2">
      <div class="card"><h2>Принять товар на хранение</h2>
        <form id="accept-form" class="form-row">
          <label>SKU<input name="sku" required placeholder="TD-2001" /></label>
          <label>Наименование<input name="title" required placeholder="Наушники" /></label>
          <label>Продавец<select name="seller_id">${sellers}</select></label>
          <label>Количество<input name="qty" type="number" min="1" value="1" required /></label>
          <label>Ячеек на шт.<input name="cell_size" type="number" min="1" max="10" value="1" required /></label>
          <label>Цена, ₽<input name="price_rub" type="number" min="1" value="${goodsPrice}" required /></label>
          <button class="btn" type="submit">Принять</button>
        </form>
        <div class="hint">Приёмка занимает ячейки и пишет начисление за операции ПВЗ (имитация).</div>
      </div>
      <div class="card"><h2>Доходы и удержания ПВЗ</h2>
        <div class="grid cols-2">
          <div>${table([{ title: "Доход" }, { title: "₽", num: true }], revenueRows, "Пока нет начислений")}</div>
          <div>${table([{ title: "Удержание" }, { title: "₽", num: true }], deductionRows, "Удержаний нет")}</div>
        </div>
        <div class="hint">Порог длительного хранения и стоимость перемещения — параметры модели (<span class="mono">economics/params.yaml</span> → API); при срабатывании приходит предложение.
        ${alert ? `<br><strong>Активно предложение:</strong> ${esc(alert.payload.offer.suggested_code)} — ${alert.payload.offer.cells} яч., ${money(alert.payload.offer.cost_rub)}.` : ""}</div>
      </div>
    </div>

    <div class="card"><h2>Остатки на ${esc(pvz.code)}</h2>
      ${table(
        [{ title: "Товар" }, { title: "Всего", num: true }, { title: "Резерв", num: true }, { title: "Доступно", num: true },
         { title: "Ячеек/шт", num: true }, { title: "Статус" }, { title: "Цена", num: true }, { title: "" }],
        stockRows, "На этой точке пока нет товаров")}
    </div>

    <div class="grid cols-2">
      <div class="card"><h2>Скоринг размещения</h2>
        <form id="score-form" class="form-row">
          <label>SKU<input name="sku" required placeholder="TD-2001" /></label>
          <label>Количество<input name="qty" type="number" min="1" value="1" required /></label>
          <label>Ячеек на шт.<input name="cell_size" type="number" min="1" max="10" value="1" required /></label>
          <label>Спрос, заказов/день<input name="demand_per_day" type="number" min="0" step="0.1" placeholder="оценит API" /></label>
          <label>Оборачиваемость, дней<input name="turnover_days" type="number" min="0" step="0.5" placeholder="оценит API" /></label>
          <button class="btn" type="submit">Оценить</button>
        </form>
        <div class="hint"><span class="mono">POST /catalog/placement-score</span>: баллы за локальный спрос, оборачиваемость и габарит партии.
          Пустое поле спроса/оборота — API подставляет наблюдения. Рекомендация сравнивается с порогом из ответа.</div>
        <div id="score-out">${scoreResultHtml(auditScore)}</div>
      </div>
      <div class="card"><h2>Сверка остатков</h2>
        ${stock.length ? `<form id="audit-form">
          ${table(
            [{ title: "Партия" }, { title: "Учёт", num: true }, { title: "Ячеек/шт", num: true }, { title: "Факт, шт." }],
            stock.map((item) => `<tr><td><strong>${esc(item.sku)}</strong></td><td class="num">${item.qty}</td>
              <td class="num">${item.cell_size}</td>
              <td><input data-audit-item="${item.id}" type="number" min="0" value="${item.qty}" style="width:96px" /></td></tr>`)
          )}
          <button class="btn mt" type="submit">Отправить сверку</button>
          <button class="btn ghost mt" type="button" id="audit-fill">Подставить учёт</button>
        </form>` : '<div class="empty">нет данных: на точке нет партий</div>'}
        <div class="hint"><span class="mono">POST /pvz/{id}/audit</span>: расхождения уменьшают занятые ячейки, недостача даёт удержание и инцидент.
          Повторная отправка того же факта идемпотентна.</div>
        <div id="audit-out">${auditResultHtml(auditResult)}</div>
      </div>
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

  $("#score-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const body = {
      sku: String(form.get("sku") || "").trim(),
      qty: Number(form.get("qty")),
      cell_size: Number(form.get("cell_size")),
      pvz_id: pvz.id,
      city: pvz.city,
    };
    const demand = String(form.get("demand_per_day") || "").trim();
    const turnover = String(form.get("turnover_days") || "").trim();
    if (demand !== "") body.demand_per_day = Number(demand);
    if (turnover !== "") body.turnover_days = Number(turnover);
    const out = $("#score-out");
    if (out) out.innerHTML = '<div class="empty">считаю по API…</div>';
    try {
      const data = await api("/catalog/placement-score", { method: "POST", body });
      state.score = { pvzId: pvz.id, data };
    } catch (error) {
      state.score = { pvzId: pvz.id, error: error.message };
    }
    if (out) out.innerHTML = scoreResultHtml(state.score);
  });

  const auditForm = $("#audit-form");
  if (auditForm) {
    $("#audit-fill").addEventListener("click", () => {
      stock.forEach((item) => {
        const input = document.querySelector(`[data-audit-item="${item.id}"]`);
        if (input) input.value = item.qty;
      });
    });
    auditForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const actual = {};
      stock.forEach((item) => {
        const input = document.querySelector(`[data-audit-item="${item.id}"]`);
        if (input && input.value !== "") actual[item.id] = Number(input.value);
      });
      if (!Object.keys(actual).length) {
        toast("Нет партий для сверки", "error");
        return;
      }
      try {
        const data = await api(`/pvz/${pvz.id}/audit`, { method: "POST", body: { actual } });
        state.audit = { pvzId: pvz.id, data };
        await loadCatalog();
        render();
      } catch (error) {
        state.audit = { pvzId: pvz.id, error: error.message };
        const out = $("#audit-out");
        if (out) out.innerHTML = auditResultHtml(state.audit);
      }
    });
  }
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
    if (order.public_token) actions.push(`<button class="btn ghost small" data-action="track-link" data-token="${order.public_token}">Ссылка покупателю</button>`);
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
  if (!state.econData) state.econData = await api("/economics/scenario");
  const fees = state.econData.params;
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
          <div class="list-item">Хранение: ${money(fees.storage_rate_day_rub)} за ячейку в день — начисляется маркетплейсом в пользу ПВЗ</div>
          <div class="list-item">Длительное хранение: порог и стоимость перемещения — параметры модели (<span class="mono">economics/params.yaml</span>); при срабатывании приходит уведомление с предложением</div>
          <div class="list-item">Выдача: ставка точки за операцию + ${money(fees.mesh_bonus_rub)} бонус за заказ без хаба</div>
          <div class="list-item">Возврат: удержание за обратную логистику и срок возврата — параметры модели</div>
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
        <label style="min-width:260px">Код из ссылки покупателя<input id="track-code" placeholder="a1b2c3d4e5f6" value="${esc(state.buyerCode)}"></label>
        <button class="btn" data-action="track">Показать</button>
      </div>
      <div class="hint">Страница публичная: статус, сроки и стоимость доставки. Внутренний номер заказа здесь не работает —
      он последовательный, и по нему перебирались бы чужие посылки. Комиссии и расчёты продавца не показываются.</div>
    </div>${card}`;
}

/* ---------- экономика: зелёная зона 4 сторон и «что если» ---------- */
/* Ползунки передают только те query-параметры, которые поддерживает
   GET /api/v1/economics/scenario (см. docs/openapi.json). Стартовые значения,
   пороги и проценты приходят из ответа API (economics/params.yaml → API),
   в JS остаются только подписи и границы самих ползунков. */
const ECON_SLIDERS = [
  { param: "mesh_bonus", label: "Бонус ПВЗ за заказ без хаба", unit: "₽", step: 1, scale: 1, read: (d) => d.params.mesh_bonus_rub },
  { param: "mesh_share", label: "Доля заказов без хаба", unit: "%", step: 1, scale: 0.01, read: (d) => d.params.mesh_share * 100 },
  { param: "storage_days", label: "Срок хранения", unit: "дн", step: 0.5, scale: 1, read: (d) => d.params.storage_days },
  { param: "ops", label: "Операций ПВЗ в день", unit: "шт", step: 1, scale: 1, read: (d) => d.pvz_day.ops },
];
let econTimer = null;
let econSeq = 0;
let econAbort = null;

function econQuery() {
  const params = new URLSearchParams();
  ECON_SLIDERS.forEach((slider) => params.set(slider.param, String(state.econ[slider.param] * slider.scale)));
  return params;
}

function sliderMax(slider, value) {
  if (slider.unit === "%") return 100;
  const span = Math.max(value * 2, slider.step * 10);
  return Math.round(span / slider.step) * slider.step;
}

function factCell(text, ok) {
  return `<td class="num ${ok ? "good" : "bad"}">${text}</td>`;
}

function greenZoneHtml(data) {
  const cmp = data && data.order && data.order.compare;
  if (!cmp) {
    return `<div class="card" style="grid-column:1/-1"><h2>Зелёная зона 4 сторон</h2>
      <div class="empty">нет данных: сценарий не рассчитан</div></div>`;
  }
  const checks = [
    { label: "Δ прибыли ПВЗ", rule: "> 0 ₽", fact: signedMoney(cmp.pvz_delta_rub), ok: cmp.pvz_delta_rub > 0 },
    { label: "Δ прибыли маркетплейса", rule: "≥ 0 ₽", fact: signedMoney(cmp.marketplace_delta_rub), ok: cmp.marketplace_delta_rub >= 0 },
    { label: "Δ прибыли продавца", rule: "≥ 0 ₽", fact: signedMoney(cmp.seller_delta_rub), ok: cmp.seller_delta_rub >= 0 },
    {
      label: "Δ покупателя: цена и срок",
      rule: "< 0 ₽ и < 0 ч",
      fact: `${signedMoney(cmp.buyer_delta_rub)} · ${signedDec(cmp.buyer_delta_hours, "ч")}`,
      ok: cmp.buyer_delta_rub < 0 && cmp.buyer_delta_hours < 0,
    },
  ];
  const green = checks.every((row) => row.ok);
  const rows = checks.map(
    (row) => `<tr>
      <td>${esc(row.label)}</td>
      <td class="num">${row.fact}</td>
      <td class="muted">${esc(row.rule)}</td>
      <td><span class="badge ${row.ok ? "badge-good" : "badge-bad"}">${row.ok ? "выполнено" : "не выполнено"}</span></td>
    </tr>`
  );
  const accept = data.buyer_acceptance || {};
  const within = cmp.buyer_acceptance || {};
  const limitCost = accept.max_cost_increase_pct;
  const limitTime = accept.max_time_increase_hours;
  const verdict = (flag) => (flag === undefined ? "нет данных" : flag ? "в допуске" : "вне допуска");
  return `<div class="card" style="grid-column:1/-1">
    <div class="section-title">
      <h2>Зелёная зона 4 сторон</h2>
      <span class="badge ${green ? "badge-good" : "badge-bad"}">зелёная зона: ${green ? "ДА" : "НЕТ"}</span>
    </div>
    <div class="hint">Условия считаются из ответа API по паре «через хаб → mesh». Ни одно значение не задано в JS.</div>
    ${table(
      [{ title: "Сторона" }, { title: "Факт (mesh − хаб)" }, { title: "Порог" }, { title: "Статус" }],
      rows,
      "нет данных"
    )}
    <div class="grid cols-2 mt">
      <div class="list-item">
        <div class="row"><strong>Граница покупателя по цене</strong><span class="badge">${verdict(within.within_cost)}</span></div>
        <div class="muted small">лимит роста: ${limitCost === undefined ? "нет данных" : "+" + dec(limitCost) + "%"} · факт: ${signedDec(cmp.buyer_delta_pct, "%")}</div>
      </div>
      <div class="list-item">
        <div class="row"><strong>Граница покупателя по сроку</strong><span class="badge">${verdict(within.within_time)}</span></div>
        <div class="muted small">лимит роста: ${limitTime === undefined ? "нет данных" : "+" + dec(limitTime) + " ч"} · факт: ${signedDec(cmp.buyer_delta_hours, "ч")}</div>
      </div>
    </div>
    <div class="hint">Границы приемлемости покупателя — параметры модели (<span class="mono">buyer_acceptance</span>), в этом интерфейсе только для чтения.
      Проверка сервера: viable=${cmp.viable === undefined ? "нет данных" : cmp.viable}, цепочка выгоднее=${cmp.chain_more_profitable === undefined ? "нет данных" : cmp.chain_more_profitable}.</div>
  </div>`;
}

function fourSidesHtml(data) {
  const { mesh, hub, compare: cmp } = data.order;
  const pct = (value) => `<td class="num ${value > 0 ? "good" : value < 0 ? "bad" : "muted"}">${signedDec(value, "%")}</td>`;
  const row = (label, base, trial, delta, deltaPct, invert = false, fmt = money) => `<tr>
      <td>${esc(label)}</td>
      <td class="num">${fmt(base)}</td>
      <td class="num">${fmt(trial)}</td>
      <td class="num ${delta === 0 ? "muted" : (invert ? delta < 0 : delta > 0) ? "good" : "bad"}">${delta > 0 ? "+" : ""}${fmt(delta)}</td>
      ${pct(deltaPct)}
    </tr>`;
  const rows = [
    row("ПВЗ", hub.pvz.profit_rub, mesh.pvz.profit_rub, cmp.pvz_delta_rub, cmp.pvz_delta_pct),
    row("Маркетплейс", hub.marketplace.profit_rub, mesh.marketplace.profit_rub, cmp.marketplace_delta_rub, cmp.marketplace_delta_pct),
    row("Продавец", hub.seller.profit_rub, mesh.seller.profit_rub, cmp.seller_delta_rub, cmp.seller_delta_pct),
    row("Цепочка", hub.chain.profit_rub, mesh.chain.profit_rub, cmp.chain_delta_rub, cmp.chain_delta_pct),
    row("Покупатель платит", hub.buyer.total_rub, mesh.buyer.total_rub, cmp.buyer_delta_rub, cmp.buyer_delta_pct, true),
    row("Срок, ч", hub.buyer.hours, mesh.buyer.hours, cmp.buyer_delta_hours, cmp.buyer_delta_hours_pct, true, (value) => dec(value, "ч")),
  ];
  return `<div class="card" style="grid-column:1/-1"><h2>Четыре стороны: через хаб против mesh</h2>
    ${table(
      [{ title: "Сторона" }, { title: "через хаб", num: true }, { title: "mesh", num: true }, { title: "Δ", num: true }, { title: "Δ, %", num: true }],
      rows
    )}
    <div class="hint">Δ — абсолютное изменение при переходе с хаба на mesh; Δ% — то же в процентах, оба значения из <span class="mono">order.compare</span>.
      Логистика: хаб ${money(hub.route.cost_rub)} / ${dec(hub.route.hours)} ч, mesh ${money(mesh.route.cost_rub)} / ${dec(mesh.route.hours)} ч.</div>
  </div>`;
}

function pvzDayHtml(data) {
  const day = data.pvz_day;
  const be = data.breakeven;
  const qt = data.queue_threshold || {};
  const share = be.share_of_capacity === null || be.share_of_capacity === undefined ? null : Math.round(be.share_of_capacity * 100);
  const load = Math.round((day.ops / day.capacity_ops) * 100);
  const ok = day.profit_rub >= 0;
  return `<div class="card"><h2>День ПВЗ при ${num(day.ops)} операциях</h2>
      <div class="metric ${ok ? "good" : "bad"}">${money(day.profit_rub)}</div>
      <div class="hint">${money(day.contribution_per_op_rub)} с операции · ${money(day.costs.fixed)} постоянных затрат</div>
      <div class="bar ${ok ? "" : "warn"} mt"><span style="width:${Math.min(100, load)}%"></span></div>
      <div class="hint">Загрузка точки ${load}% от ${num(day.capacity_ops)} операций</div>
      <p class="mt">Точка выходит в ноль при <strong>${be.ops === null ? "нет данных" : num(be.ops)}</strong> операциях в день${share === null ? "" : ` — ${share}% мощности`}.</p>
      <div class="hint">Порог хранения ${dec(data.storage_breakeven_days)} дн: дольше — маркетплейс на заказе теряет.</div>
    </div>
    <div class="card"><h2>Очередь и границы модели</h2>
      <div class="list">
        <div class="list-item small">Целевая загрузка <strong>${dec(qt.target_pct)}%</strong> — ожидание ${dec(qt.wait_at_target_hours)} ч, операций ${dec(qt.target_ops)}</div>
        <div class="list-item small">Порог предупреждения <strong>${dec(qt.alert_pct)}%</strong> — ожидание ${dec(qt.wait_at_alert_hours)} ч, операций ${dec(qt.alert_ops)}</div>
        <div class="list-item small">Потолок очереди <strong>${dec(qt.queue_cap_pct)}%</strong>; рост ожидания alert к target ${dec(qt.wait_growth_alert_vs_target_pct)}%</div>
        <div class="list-item small">Доля заказов без хаба в дне ПВЗ: <strong>${dec((data.params.mesh_share || 0) * 100)}%</strong> · ячеек на заказ ${dec(data.params.cells)}</div>
      </div>
      <div class="hint">Все пороги приходят из <span class="mono">queue_threshold</span> и <span class="mono">params</span> ответа API.</div>
    </div>`;
}

function econParamsTable(data) {
  const p = data.params || {};
  const rows = [
    ["Тариф хранения", `${money(p.storage_rate_day_rub)} / ячейка·день`],
    ["Постоянные затраты точки", `${money(p.fixed_cost_day_rub)} / день`],
    ["Комиссия маркетплейса", `${dec(p.commission_pct)}%`],
    ["Логистическое удержание с продавца", money(p.seller_logistics_fee_rub)],
    ["Доставка покупателю: локально / хаб", `${money(p.buyer_delivery_fee_local_rub)} / ${money(p.buyer_delivery_fee_hub_rub)}`],
    ["Цена товара в сценарии", money(p.goods_rub)],
    ["Доля локальных пар в сети", `${dec(p.local_share_pct)}%`],
  ].map(([label, value]) => `<tr><td>${esc(label)}</td><td class="num">${value}</td></tr>`);
  return `<div class="card"><h2>Параметры модели в расчёте</h2>
    ${table([{ title: "Параметр" }, { title: "Значение из API", num: true }], rows)}
    <div class="hint">Это значения <span class="mono">params</span> текущего ответа. Четыре ползунка управляют бонусом mesh, долей mesh, сроком хранения и числом операций — эти query-параметры API принимает и пересчитывает. Ставки ниже (тариф хранения, постоянные затраты, комиссия, удержание с продавца, цена товара) API тоже принимает как query, но в интерфейсе они оставлены только для чтения: это подписи к текущему расчёту.</div>
  </div>`;
}

function renderEconOut(data) {
  const out = $("#econ-out");
  if (!out) return;
  out.innerHTML = greenZoneHtml(data) + fourSidesHtml(data) + pvzDayHtml(data) + econParamsTable(data);
}

function setEconStatus(text, kind = "") {
  const status = $("#econ-status");
  if (!status) return;
  status.textContent = text;
  status.className = "econ-status " + kind;
}

async function refreshEcon() {
  const seq = ++econSeq;
  if (econAbort) econAbort.abort();
  econAbort = new AbortController();
  const out = $("#econ-out");
  if (out) out.setAttribute("aria-busy", "true");
  setEconStatus("пересчитываю сценарий…", "busy");
  try {
    const data = await api("/economics/scenario?" + econQuery().toString(), { signal: econAbort.signal });
    if (seq !== econSeq) return;
    state.econData = data;
    renderEconOut(data);
    setEconStatus("рассчитано по API · " + new Date().toLocaleTimeString("ru-RU"), "ok");
  } catch (error) {
    if (error.name === "AbortError" || seq !== econSeq) return;
    setEconStatus("нет данных: " + error.message, "bad");
    if (out) out.innerHTML = `<div class="card" style="grid-column:1/-1"><h2>Зелёная зона 4 сторон</h2>
      <div class="empty">нет данных: ${esc(error.message)}</div></div>`;
  } finally {
    if (seq === econSeq && out) out.removeAttribute("aria-busy");
  }
}

async function renderEconomics() {
  if (!state.econReady) {
    const base = await api("/economics/scenario");
    state.econBase = base;
    state.econData = base;
    state.econ = {};
    ECON_SLIDERS.forEach((slider) => {
      state.econ[slider.param] = slider.read(base);
    });
    state.econReady = true;
  }
  const rows = ECON_SLIDERS.map((slider) => {
    const value = state.econ[slider.param];
    const max = sliderMax(slider, value);
    return `
    <div class="econ-row">
      <label for="econ-${slider.param}">${esc(slider.label)}</label>
      <output id="econ-${slider.param}-out">${dec(value)} ${esc(slider.unit)}</output>
      <input id="econ-${slider.param}" data-econ="${slider.param}" data-unit="${esc(slider.unit)}" type="range"
             min="0" max="${max}" step="${slider.step}" value="${value}" />
    </div>`;
  }).join("");
  $("#page").innerHTML = `
    <div class="card">
      <div class="section-title"><h2>Что если</h2><span id="econ-status" class="econ-status" role="status" aria-live="polite">—</span></div>
      <form id="econ-form" class="grid cols-2" onsubmit="return false">${rows}</form>
      <div class="hint">Четыре ползунка меняют реальные query-параметры <span class="mono">GET /economics/scenario</span>; пересчёт идёт через API с задержкой 350 мс, устаревшие ответы отменяются.
      Ставки — параметры модели (<span class="mono">economics/params.yaml</span>), а не тарифы RWB.</div>
    </div>
    <div id="econ-out" class="grid cols-2"></div>`;
  await refreshEcon();
}

document.addEventListener("input", (event) => {
  const input = event.target.closest("[data-econ]");
  if (!input) return;
  const key = input.dataset.econ;
  state.econ[key] = Number(input.value);
  const out = $(`#econ-${key}-out`);
  if (out) out.textContent = `${dec(input.value)} ${input.dataset.unit}`;
  clearTimeout(econTimer);
  econTimer = setTimeout(() => {
    guard(refreshEcon());
  }, 350);
});

/* ---------- демо-кокпит ---------- */
/* Каждый шаг вызывает реальные эндпоинты и печатает только то, что вернул API.
   Кокпит меняет состояние стенда: принимает партию, выводит/возвращает точку,
   создаёт межгородской заказ. */
const DEMO_STEPS = [
  { id: "calm", title: "1. Спокойный день", hint: "Внутригородская пара без перегрузки: маршрут идёт по mesh." },
  { id: "peak", title: "2. Пиковая нагрузка", hint: "Приёмка партии поднимает загрузку точки до порога overflow — маршрут уходит на хаб." },
  { id: "down", title: "3. Отказ узла", hint: "Точка выводится из строя: эвакуация остатков, переадресация заказов, инцидент." },
  { id: "up", title: "4. Восстановление", hint: "Точка возвращается в строй; маршрут рассчитывается снова." },
  { id: "hub", title: "5. Переключение на хаб", hint: "Межгород: локального маршрута нет, работает хаб и магистраль." },
];

function demoPair(threshold = null) {
  const active = points().filter((row) => row.is_active);
  const byCity = new Map();
  active.forEach((row) => {
    if (!byCity.has(row.city)) byCity.set(row.city, []);
    byCity.get(row.city).push(row);
  });
  const candidates = [];
  for (const rows of byCity.values()) {
    if (rows.length < 2) continue;
    const sorted = rows.slice().sort((a, b) => a.utilization_pct - b.utilization_pct);
    for (let i = 0; i < sorted.length - 1; i += 1) {
      for (let j = i + 1; j < sorted.length; j += 1) candidates.push([sorted[i], sorted[j]]);
    }
  }
  if (!candidates.length) return null;
  const under = candidates.filter(
    ([src, dst]) => dst.free_cells > 0 && (threshold === null || (src.utilization_pct < threshold && dst.utilization_pct < threshold))
  );
  const pool = (under.length ? under : candidates)
    .slice()
    .sort((a, b) => b[1].free_cells - a[1].free_cells || a[0].utilization_pct - b[0].utilization_pct);
  const [fallbackSrc, dst] = pool[0];
  const city = dst.city;
  const srcPool = active.filter(
    (row) => row.city === city && row.id !== dst.id && (threshold === null || row.utilization_pct < threshold)
  );
  const src = srcPool.sort((a, b) => b.utilization_pct - a.utilization_pct)[0] || fallbackSrc;
  return { src, dst };
}

async function demoGoodsPrice() {
  if (!state.demo.goods) {
    const data = await api("/economics/scenario");
    state.demo.goods = data.params.goods_rub;
  }
  return state.demo.goods;
}

async function demoMakeRoom(rows, threshold, exclude = []) {
  const notes = [];
  const pool = points()
    .filter((point) => !exclude.includes(point.id))
    .sort((a, b) => b.free_cells - a.free_cells);
  for (const row of rows) {
    const fresh = await api(`/pvz/${row.id}`);
    const keep = Math.max(0, Math.floor((fresh.capacity_cells * threshold) / 100) - 1);
    let need = fresh.used_cells - keep;
    if (need <= 0) continue;
    const before = fresh.utilization_pct;
    let moved = 0;
    let parts = 0;
    const stock = state.items.filter((item) => item.stored_pvz_id === fresh.id && item.available_qty > 0);
    for (const item of stock) {
      if (moved >= need) break;
      const dest = pool.find(
        (point) => point.id !== fresh.id && point.city === fresh.city && point.free_cells >= item.cells
      );
      if (!dest) continue;
      await api(`/items/${item.id}/relocate`, { method: "POST", body: { to_pvz_id: dest.id } });
      moved += item.cells;
      parts += 1;
      dest.free_cells -= item.cells;
      need -= item.cells;
    }
    const after = await api(`/pvz/${fresh.id}`);
    notes.push(
      `стенд был перегружен: ${fresh.code} ${dec(before)}% → ${dec(after.utilization_pct)}%, перемещено партий: ${parts} (${moved} ячеек)`
    );
  }
  if (notes.length) {
    await loadCatalog();
    rows.forEach((row) => {
      const fresh = state.pvz.find((point) => point.id === row.id);
      if (fresh) Object.assign(row, fresh);
    });
  }
  return notes;
}

async function demoCleanup() {
  const notes = [];
  let parts = 0;
  let units = 0;
  let freed = 0;
  for (const item of state.items) {
    if (item.qty <= 0 || item.available_qty <= 0) continue;
    const sku = item.sku || "";
    const title = item.title || "";
    if (!(sku.startsWith("PEAK-") || sku.startsWith("HUB-") || /демо-кокпит|Демо-партия/.test(title))) continue;
    const origin = state.pvz.find((row) => row.id === item.stored_pvz_id);
    if (!origin) continue;
    let remaining = item.available_qty;
    let attempts = 0;
    while (remaining > 0 && attempts < 12) {
      attempts += 1;
      const dest = state.pvz.find((row) => row.city !== origin.city && row.free_cells >= 1);
      if (!dest) break;
      const chunk = Math.min(remaining, dest.free_cells);
      try {
        const order = (await api("/orders", {
          method: "POST",
          body: { item_id: item.id, qty: chunk, to_pvz_id: dest.id },
        })).order;
        await api(`/orders/${order.id}/assemble`, { method: "POST" });
        await api(`/orders/${order.id}/transit`, { method: "POST" });
        await api(`/orders/${order.id}/issue`, { method: "POST" });
      } catch {
        dest.free_cells = 0;
        continue;
      }
      remaining = (await api(`/items/${item.id}`)).available_qty;
    }
    const issued = item.available_qty - remaining;
    if (issued <= 0) continue;
    parts += 1;
    units += issued;
    freed += issued * item.cell_size;
  }
  if (parts) {
    notes.push(`выданы демо-партии прошлых прогонов: ${parts} шт., ${units} единиц, освобождено ${freed} ячеек`);
    await loadCatalog();
  }
  return notes;
}

const DEMO_RUNNERS = {
  async calm() {
    let pair = demoPair();
    if (!pair) throw new Error("нет двух активных ПВЗ в одном городе");
    let cmp = await api(`/routes/compare?from=${pair.src.id}&to=${pair.dst.id}`);
    const notes = [];
    const threshold = cmp.load.threshold_pct;
    const calmPair = demoPair(threshold);
    if (calmPair) {
      pair = calmPair;
      cmp = await api(`/routes/compare?from=${pair.src.id}&to=${pair.dst.id}`);
    } else {
      const crowded = [pair.src, pair.dst].filter((row) => row.utilization_pct >= threshold);
      if (crowded.length) {
        notes.push(...(await demoMakeRoom(crowded, threshold, [pair.src.id, pair.dst.id])));
        pair = demoPair(threshold) || demoPair();
        if (!pair) throw new Error("после перемещений не осталось пары ПВЗ");
        cmp = await api(`/routes/compare?from=${pair.src.id}&to=${pair.dst.id}`);
      }
    }
    const ok = cmp.chosen === "mesh" && cmp.mesh.available === true && cmp.overflow === false;
    return {
      status: ok ? "pass" : "fail",
      ctx: { pair },
      lines: [
        ...notes,
        `${cmp.from.code} → ${cmp.to.code}: выбрано «${cmp.chosen}», причина ${cmp.reason_code}, overflow=${cmp.overflow}`,
        `mesh ${money(cmp.mesh.total_cost_rub)} / ${dec(cmp.mesh.total_hours)} ч; хаб ${money(cmp.hub.total_cost_rub)} / ${dec(cmp.hub.total_hours)} ч`,
        `загрузка: ${cmp.load.from.code} ${dec(cmp.load.from.utilization_pct)}%, ${cmp.load.to.code} ${dec(cmp.load.to.utilization_pct)}% (порог ${dec(cmp.load.threshold_pct)}%)`,
      ],
    };
  },

  async peak() {
    const pair = state.demo.ctx.pair || demoPair();
    if (!pair) throw new Error("нет двух активных ПВЗ в одном городе");
    const src = pair.src;
    const dst = pair.dst;
    let cmp = await api(`/routes/compare?from=${src.id}&to=${dst.id}`);
    const threshold = cmp.load.threshold_pct;
    const before = cmp.load.to.utilization_pct;
    const notes = [];
    if (before < threshold) {
      let fresh = await api(`/pvz/${dst.id}`);
      if (!fresh.is_active) throw new Error(`${fresh.code} выведена из строя — шаг 2 невозможен`);
      const need = Math.ceil((fresh.capacity_cells * threshold) / 100) + 1 - fresh.used_cells;
      // Сначала переносим остатки внутри города: запас ячеек города не растёт.
      let movedCells = 0;
      let movedParts = 0;
      const pool = state.items
        .filter((item) => item.available_qty > 0 && item.stored_pvz_id && item.stored_pvz_id !== dst.id)
        .filter((item) => (state.pvz.find((point) => point.id === item.stored_pvz_id) || {}).city === fresh.city)
        .sort((a, b) => a.cells - b.cells);
      for (const item of pool) {
        if (movedCells >= need) break;
        fresh = await api(`/pvz/${dst.id}`);
        if (item.cells > fresh.free_cells) continue;
        await api(`/items/${item.id}/relocate`, { method: "POST", body: { to_pvz_id: dst.id } });
        movedCells += item.cells;
        movedParts += 1;
      }
      if (movedCells) {
        notes.push(`остатки внутри города перенесены на ${fresh.code}: партий ${movedParts}, ячеек +${movedCells} (запас города не вырос)`);
      }
      if (movedCells < need) {
        fresh = await api(`/pvz/${dst.id}`);
        const qty = Math.min(Math.max(1, need - movedCells), fresh.free_cells);
        if (qty >= 1) {
          const price = await demoGoodsPrice();
          const accepted = await api("/items", {
            method: "POST",
            body: {
              sku: `PEAK-${fresh.code}-${Date.now().toString().slice(-5)}`,
              seller_id: state.sellers[0].id,
              title: "Пиковая партия (демо-кокпит)",
              qty,
              cell_size: 1,
              pvz_id: fresh.id,
              price_rub: price,
            },
          });
          notes.push(`принята партия ${accepted.sku}: ${accepted.qty} шт. × ${accepted.cell_size} яч. (+${accepted.qty * accepted.cell_size} ячеек)`);
        }
      }
      await loadCatalog();
      cmp = await api(`/routes/compare?from=${src.id}&to=${dst.id}`);
    } else {
      notes.push("загрузка уже не ниже порога — новая нагрузка не понадобилась");
    }
    const after = cmp.load.to.utilization_pct;
    const triggers = (cmp.load.triggered_by || []).map((row) => `${row.side}:${row.code} ${dec(row.utilization_pct)}%`).join(", ") || "нет";
    const ok = cmp.load.triggered === true && cmp.overflow === true && cmp.reason_code === "overflow_to_hub";
    return {
      status: ok ? "pass" : "fail",
      lines: [
        ...notes,
        `загрузка ${cmp.load.to.code}: ${dec(before)}% → ${dec(after)}% (порог ${dec(threshold)}%)`,
        `триггеры overflow: ${triggers}`,
        `маршрут: ${cmp.chosen} (${cmp.reason_code}); mesh ${cmp.mesh.available ? money(cmp.mesh.total_cost_rub) + " / " + dec(cmp.mesh.total_hours) + " ч" : "недоступен"} → хаб ${money(cmp.hub.total_cost_rub)} / ${dec(cmp.hub.total_hours)} ч`,
      ],
    };
  },

  async down() {
    const pair = state.demo.ctx.pair || demoPair();
    if (!pair) throw new Error("нет пары ПВЗ для отказа узла");
    const res = await api(`/pvz/${pair.dst.id}/disable`, { method: "POST" });
    const fresh = await api(`/pvz/${pair.dst.id}`);
    const evacuated = res.evacuated || [];
    const orders = res.orders || [];
    const unresolved = res.unresolved || [];
    const cells = evacuated.reduce((sum, row) => sum + (row.cells || 0), 0);
    const ok = fresh.is_active === false && res.pvz.is_active === false;
    return {
      status: ok ? "pass" : "fail",
      ctx: { downId: pair.dst.id },
      lines: [
        `${res.pvz.code}: ${fresh.is_active ? "осталась в строю" : "выведена из строя"}${res.already_down ? " (уже была выведена)" : ""}`,
        `эвакуировано партий: ${evacuated.length}, ячеек: ${cells}, стоимость перемещений ${money(res.relocation_total_rub)}`,
        `переадресовано заказов: ${orders.length}, не решено: ${unresolved.length}, инцидент ${res.incident ? "#" + res.incident.id : "нет"}`,
        `после отказа: занято ${fresh.used_cells} из ${fresh.capacity_cells} ячеек, свободно ${fresh.free_cells}`,
      ],
    };
  },

  async up() {
    const id = state.demo.ctx.downId || (state.demo.ctx.pair ? state.demo.ctx.pair.dst.id : null);
    if (!id) throw new Error("сначала выполните шаг 3 — отказ узла");
    const res = await api(`/pvz/${id}/enable`, { method: "POST" });
    const fresh = await api(`/pvz/${id}`);
    const pair = state.demo.ctx.pair || demoPair();
    let route = "маршрут не рассчитан: нет пары шага 1";
    let moneyLine = "";
    if (pair) {
      try {
        const cmp = await api(`/routes/compare?from=${pair.src.id}&to=${id}`);
        route = `маршрут ${cmp.chosen} (${cmp.reason_code}), overflow=${cmp.overflow}`;
        moneyLine = `стоимость: mesh ${cmp.mesh.available ? money(cmp.mesh.total_cost_rub) + " / " + dec(cmp.mesh.total_hours) + " ч" : "недоступен"}, хаб ${money(cmp.hub.total_cost_rub)} / ${dec(cmp.hub.total_hours)} ч`;
      } catch (error) {
        route = "маршрут: " + error.message;
      }
    }
    const ok = fresh.is_active === true && res.pvz.is_active === true;
    return {
      status: ok ? "pass" : "fail",
      lines: [
        `${res.pvz.code}: ${fresh.is_active ? "вернулась в строй" : "не активировалась"}`,
        `занято ${fresh.used_cells} из ${fresh.capacity_cells} ячеек, свободно ${fresh.free_cells}`,
        route,
        moneyLine,
      ].filter(Boolean),
    };
  },

  async hub() {
    const price = await demoGoodsPrice();
    let item = state.items.find(
      (row) => row.available_qty > 0 && state.pvz.some((pvz) => pvz.id === row.stored_pvz_id && pvz.is_active)
    );
    if (!item) {
      const home = points().find((row) => row.is_active && !row.is_hub);
      if (!home) throw new Error("нет активной точки, чтобы принять демо-партию");
      item = await api("/items", {
        method: "POST",
        body: {
          sku: `HUB-${home.code}`,
          seller_id: state.sellers[0].id,
          title: "Демо-партия для межгорода",
          qty: 1,
          cell_size: 1,
          pvz_id: home.id,
          price_rub: price,
        },
      });
      await loadCatalog();
    }
    const source = state.pvz.find((row) => row.id === item.stored_pvz_id);
    const sourceCity = source ? source.city : "";
    const dest = points().find((row) => row.is_active && row.city !== sourceCity);
    if (!dest) throw new Error("нет активной точки в другом городе");
    const res = await api("/orders", { method: "POST", body: { item_id: item.id, qty: 1, to_pvz_id: dest.id } });
    const order = res.order;
    const ok = order.route.kind === "hub" && (order.status === "routed" || order.status === "rerouted");
    const segments = (order.route.segments || [])
      .map((seg) => `${seg.from_code}→${seg.to_code} ${seg.kind}`)
      .join(", ");
    return {
      status: ok ? "pass" : "fail",
      lines: [
        `заказ ${order.code}: ${source ? source.code : "?"} → ${dest.code} (${dest.city}), статус ${order.status}`,
        `маршрут ${order.route.kind}, причина ${order.route.reason_code}, ${money(order.route.total_cost_rub)} / ${dec(order.route.total_hours)} ч`,
        `плечи: ${segments || "нет данных"}`,
        `покупатель платит ${money(order.buyer_total_rub)}`,
      ],
    };
  },
};

function demoCount() {
  return DEMO_STEPS.filter((step) => state.demo.steps[step.id] && state.demo.steps[step.id].status === "pass").length;
}

function renderDemo() {
  const done = Object.keys(state.demo.steps).length;
  const passed = demoCount();
  const prepLines = (state.demo.prep || []).length
    ? `<div class="list mt">${state.demo.prep.map((line) => `<div class="list-item small">${esc(line)}</div>`).join("")}</div>`
    : "";
  const cards = DEMO_STEPS.map((step) => {
    const result = state.demo.steps[step.id];
    const running = state.demo.running === step.id;
    const status = running ? "running" : result ? result.status : "idle";
    const badge = running
      ? '<span class="badge badge-warn">выполняется…</span>'
      : result
      ? `<span class="badge ${result.status === "pass" ? "badge-good" : "badge-bad"}">${result.status === "pass" ? "PASS" : "FAIL"}</span>`
      : '<span class="badge badge-quiet">не запускался</span>';
    const lines = result && result.lines && result.lines.length
      ? `<div class="list mt">${result.lines.map((line) => `<div class="list-item small">${esc(line)}</div>`).join("")}</div>`
      : "";
    return `<div class="demo-step">
      <div class="section-title"><h3>${esc(step.title)}</h3>${badge}</div>
      <div class="hint">${esc(step.hint)}</div>
      <button class="btn small mt" data-demo-step="${step.id}" ${state.demo.running ? "disabled" : ""}>Выполнить шаг</button>
      ${lines}
    </div>`;
  }).join("");
  const logRows = DEMO_STEPS.filter((step) => state.demo.steps[step.id]).flatMap((step) => {
    const result = state.demo.steps[step.id];
    return result.lines.map(
      (line) => `<tr><td>${esc(step.title)}</td><td><span class="badge ${result.status === "pass" ? "badge-good" : "badge-bad"}">${result.status === "pass" ? "PASS" : "FAIL"}</span></td><td>${esc(line)}</td></tr>`
    );
  });
  $("#page").innerHTML = `
    <div class="card">
      <div class="section-title">
        <h2>Демо-кокпит: пять сценариев на живом API</h2>
        <span class="badge ${done && passed === DEMO_STEPS.length ? "badge-good" : "badge-quiet"}">итог: ${passed}/${DEMO_STEPS.length}</span>
      </div>
      <div class="hint">Кокпит меняет состояние стенда: принимает демо-партию, выводит и возвращает точку, создаёт межгородской заказ.
        Все цифры берутся из ответов API, PASS/FAIL считается по фактическим полям ответа.</div>
      ${prepLines}
      <div class="inline mt">
        <button class="btn" data-demo-all ${state.demo.running ? "disabled" : ""}>Прогнать все ${DEMO_STEPS.length} шагов</button>
        <button class="btn ghost" data-demo-reset ${state.demo.running ? "disabled" : ""}>Сбросить кокпит</button>
        <span class="muted small">выполнено шагов: ${done} из ${DEMO_STEPS.length}</span>
      </div>
    </div>
    <div class="demo-grid">${cards}</div>
    <div class="card"><h2>Лог различий</h2>
      ${table([{ title: "Шаг" }, { title: "Статус" }, { title: "Что изменилось" }], logRows, "нет данных: запустите шаги")}
    </div>`;
}

async function runDemoStep(id) {
  if (state.demo.running) return;
  if (!DEMO_RUNNERS[id]) return;
  state.demo.running = id;
  renderDemo();
  try {
    const result = await DEMO_RUNNERS[id]();
    state.demo.steps[id] = { ...result, at: new Date() };
    if (result.ctx) Object.assign(state.demo.ctx, result.ctx);
    if (result.status === "fail") toast(`Шаг «${DEMO_STEPS.find((step) => step.id === id).title}»: FAIL`, "error");
  } catch (error) {
    state.demo.steps[id] = { status: "fail", lines: ["ошибка: " + error.message] };
    toast(error.message, "error");
  } finally {
    state.demo.running = null;
    try {
      await loadCatalog();
    } catch {
      /* каталог обновится при следующем рендере */
    }
    render();
  }
}

async function runAllDemo() {
  if (state.demo.running) return;
  state.demo.running = "all";
  renderDemo();
  try {
    const prep = await demoCleanup();
    if (prep.length) state.demo.prep = prep;
  } catch {
    state.demo.prep = [];
  } finally {
    state.demo.running = null;
  }
  for (const step of DEMO_STEPS) {
    if (state.demo.running) break;
    await runDemoStep(step.id);
  }
}

/* ---------- роутинг ---------- */
const pages = {
  dashboard: { title: "Дашборд", subtitle: "Сводка по сети ПВЗ и денежным потокам", render: renderDashboard },
  map: { title: "Карта сети", subtitle: "Схема ПВЗ, сегментов и маршрутов: отказ узла прямо на карте", render: () => renderMap() },
  economics: { title: "Экономика", subtitle: "Что если: ставки, прибыль четырёх сторон, порог безубыточности", render: renderEconomics },
  pvz: { title: "Кабинет ПВЗ", subtitle: "Приёмка, остатки, хранение и доходы точки", render: renderPvz },
  seller: { title: "Кабинет продавца", subtitle: "Остатки, заказы и расчёты продавца", render: renderSeller },
  orders: { title: "Заказы", subtitle: "Маршрут, сборка, выдача, возврат", render: renderOrders },
  buyer: { title: "Покупатель", subtitle: "Отслеживание заказа по ссылке без входа в систему", render: renderBuyer },
  routes: { title: "Маршруты и сеть", subtitle: "mesh против хаба и управление сегментами", render: renderRoutes },
  incidents: { title: "Инциденты", subtitle: "Нештатные ситуации и перевыбор маршрута", render: renderIncidents },
  clock: { title: "Время и уведомления", subtitle: "Виртуальные часы, начисления, лента событий", render: renderClock },
  demo: { title: "Демо-кокпит", subtitle: "Пять сценариев на живом API: PASS/FAIL и лог различий", render: async () => renderDemo() },
};

async function render() {
  const page = pages[state.page];
  $("#page-title").textContent = page.title;
  $("#page-subtitle").textContent = page.subtitle;
  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.page === state.page;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const container = $("#page");
  container.classList.add("is-loading");
  try {
    await loadCatalog();
    await page.render();
  } catch (error) {
    container.innerHTML = `<div class="card"><h2>Ошибка</h2><p>${esc(error.message)}</p><p class="muted small">Проверьте, что API запущен и доступен по /api/v1.</p></div>`;
  } finally {
    container.classList.remove("is-loading");
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
  const demoStep = event.target.closest("[data-demo-step]");
  if (demoStep) {
    await runDemoStep(demoStep.dataset.demoStep);
    return;
  }
  if (event.target.closest("[data-demo-all]")) {
    await runAllDemo();
    return;
  }
  if (event.target.closest("[data-demo-reset]")) {
    state.demo = { steps: {}, ctx: {}, running: null, goods: null, prep: [] };
    renderDemo();
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
  if (action === "track-link") {
    const url = `${location.origin}/?track=${button.dataset.token}`;
    try {
      await navigator.clipboard.writeText(url);
      toast("Ссылка покупателю скопирована", "ok");
    } catch {
      toast(url, "ok");
    }
    return;
  }
  if (action === "track") {
    state.buyerCode = ($("#track-code")?.value || "").trim().toLowerCase();
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
  if (action === "node-down") {
    if (!confirm("Вывести ПВЗ из строя? Свободные остатки переедут к соседям (стоимость перемещения — параметр модели), заказы будут переадресованы.")) return;
    result = await guard(api(`/pvz/${id}/disable`, { method: "POST" }));
    if (result) {
      const moved = (result.evacuated || []).length;
      const reassigned = (result.orders || []).length;
      const left = (result.unresolved || []).length;
      toast(`ПВЗ ${result.pvz.code} выведен из строя: перемещено партий ${moved}, обработано заказов ${reassigned}, не решено ${left}, затраты ${money(result.relocation_total_rub)}.`, left ? "" : "ok");
    }
  }
  if (action === "node-up") result = await guard(api(`/pvz/${id}/enable`, { method: "POST" }), "ПВЗ вернулся в строй");
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

const deepLink = new URLSearchParams(location.search).get("track");
if (deepLink) {
  state.page = "buyer";
  state.buyerCode = deepLink.trim().toLowerCase();
  api(`/track/${encodeURIComponent(state.buyerCode)}`)
    .then((data) => { state.buyerData = data; })
    .catch((error) => { state.buyerError = error.message; })
    .finally(render);
}
render();
