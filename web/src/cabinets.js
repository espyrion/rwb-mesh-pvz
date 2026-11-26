/* Кабинеты: покупатель, продавец, ПВЗ + маршрутизация маркетплейса. Только существующие API. */
const rub = (v) => (v === null || v === undefined ? "—" : Number.isInteger(v)
  ? new Intl.NumberFormat("ru-RU").format(v) + " ₽"
  : new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v) + " ₽");
const BATCH_QTY = 20;

async function econ() {
  if (!state.econData) state.econData = await api("/economics/scenario");
  return state.econData;
}

/* ---------- Покупатель ---------- */
const STEPS = ["Создан", "Собран", "В пути", "Готов к выдаче", "Выдан"];
const STEP_IDX = { created: 0, routed: 0, rerouted: 0, assembled: 1, in_transit: 2, issued: 4 };

function stepper(status) {
  if (status === "cancelled" || status === "returned") return `<div>${pill(status)}</div>`;
  const now = STEP_IDX[status] ?? 0;
  return `<ol class="stepper">${STEPS.map((s, i) => `<li class="${i < now || (i === now && now === 4) ? "done" : ""} ${i === now ? "now" : ""}">${s}</li>`).join("")}</ol>`;
}

function deliveryNote(item, toId, m) {
  const dest = state.pvz.find((p) => p.id === Number(toId));
  const src = item && state.pvz.find((p) => p.id === item.stored_pvz_id);
  const fast = dest && src && dest.city === src.city && dest.id !== src.id && dest.is_active;
  const loc = m.order.mesh, hub = m.order.hub;
  return fast
    ? { fast: true, text: `Быстрая доставка: ${dec(loc.buyer.hours)} ч · ${money(loc.buyer.delivery_fee_rub)} (${signedMoney(loc.buyer.delivery_fee_rub - hub.buyer.delivery_fee_rub).replace(" ₽", "")} ₽)` }
    : { fast: false, text: `Доставка: ${dec(hub.buyer.hours)} ч · ${money(hub.buyer.delivery_fee_rub)}` };
}

async function renderBuyerCab() {
  const m = await econ();
  const avail = state.items.filter((i) => i.available_qty > 0);
  const opts = avail.map((i) => `<option value="${i.id}">${esc(i.title)} · ${esc(i.sku)} (${i.available_qty} шт.)</option>`).join("");
  const dflt = points().find((p) => avail[0] && p.id !== avail[0].stored_pvz_id) || points()[0];
  const orders = state.orders.slice().sort((a, b) => b.id - a.id);
  const cards = orders.map((o) => `<div class="card order-card">
      <div class="section-title"><h2>${esc(o.title || o.sku)} × ${o.qty}</h2>${pill(o.status)}</div>
      <div class="muted small">Заказ ${esc(o.code)} · выдача в ${esc(o.to_code)} · к оплате <strong>${money(o.buyer_total_rub)}</strong>${o.route ? ` · срок ${dec(o.route.total_hours)} ч` : ""}</div>
      ${stepper(o.status)}
      ${o.status === "issued" ? `<div><button class="btn ghost small" data-action="return" data-id="${o.id}">Оформить возврат (7 дней)</button></div>` : ""}
    </div>`).join("");
  let track = "";
  if (state.buyerData) track = `<div class="card"><h2>Заказ ${esc(state.buyerData.code)}</h2><p>${esc(state.buyerData.hint)}</p>${stepper(state.buyerData.status)}</div>`;
  pageBox().innerHTML = `${track}
    <div class="card accent"><h2>Оформить заказ</h2>
      <form id="order-form" class="form-row">
        <label>Товар<select name="item_id" required>${opts || '<option value="">нет товаров в наличии</option>'}</select></label>
        <label>Количество<input name="qty" type="number" min="1" value="1" required /></label>
        <label>Пункт выдачи<select name="to_pvz_id">${pvzOptions(dflt.id)}</select></label>
        <button class="btn big" type="submit">Заказать</button>
      </form>
      <div id="delivery-note" class="delivery-note"></div>
    </div>
    <div><h2 style="margin:4px 0 12px">Мои заказы</h2><div class="grid cols-2">${cards || '<div class="card empty">Заказов пока нет</div>'}</div></div>`;
  const form = $("#order-form");
  const upd = () => {
    const f = new FormData(form);
    const note = deliveryNote(state.items.find((i) => i.id === Number(f.get("item_id"))), f.get("to_pvz_id"), m);
    const box = $("#delivery-note");
    box.textContent = note.text;
    box.classList.toggle("slow", !note.fast);
  };
  form.addEventListener("change", upd);
  upd();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = new FormData(form);
    if (!f.get("item_id")) return toast("Нет товаров в наличии", "error");
    const body = { item_id: Number(f.get("item_id")), qty: Number(f.get("qty")), to_pvz_id: Number(f.get("to_pvz_id")), accept_latency: true };
    await withBusy(form, async () => {
      const r = await guard(api("/orders", { method: "POST", body }));
      if (r) { toast(`Заказ ${r.order.code} оформлен · ${money(r.order.buyer_total_rub)}`, "ok"); await refreshAfterAction(); }
    });
  });
}

/* ---------- Продавец ---------- */
async function sellerPlan(seller, m) {
  const stock = state.items.filter((i) => i.seller_id === seller.id);
  const skus = [...new Map(stock.map((i) => [i.sku, i])).values()];
  const threshold = 60;
  const pts = points().filter((p) => p.is_active && p.utilization_pct < (m.queue_threshold ? m.queue_threshold.alert_pct : 85));
  const scored = [];
  for (const it of skus) {
    const target = pts.filter((p) => p.free_cells >= BATCH_QTY * it.cell_size).sort((a, b) => b.free_cells - a.free_cells)[0] || null;
    try {
      const d = await api("/catalog/placement-score", { method: "POST", body: { sku: it.sku, qty: BATCH_QTY, cell_size: it.cell_size, pvz_id: target ? target.id : undefined, city: target ? target.city : undefined } });
      scored.push({ it, score: d.score, thr: d.threshold || threshold, target });
    } catch (e) { scored.push({ it, score: null, thr: threshold, target }); }
  }
  const good = scored.filter((s) => s.score !== null && s.score >= s.thr && s.target).sort((a, b) => b.score - a.score);
  return { scored, good, rest: scored.filter((s) => !good.includes(s)) };
}

function ageCell(item) {
  const days = Math.max(0, Math.floor((new Date(state.now) - new Date(item.accepted_at)) / 86400000));
  const cls = days > 21 ? "age-bad" : days >= 14 ? "age-mid" : "age-ok";
  return { days, html: `<span class="pill ${cls}">${days} дн${days > 21 ? " · алерт" : ""}</span>` };
}

async function renderSellerCab() {
  const m = await econ();
  const seller = state.sellers.find((s) => s.id === state.sellerId) || state.sellers[0];
  const [analytics, plan] = await Promise.all([api(`/analytics/seller?seller_id=${seller.id}`), sellerPlan(seller, m)]);
  const best = plan.good[0];
  const ref = m.order.mesh.seller;
  const rows = state.items.filter((i) => i.seller_id === seller.id).map((i) => {
    const a = ageCell(i);
    return `<tr><td><strong>${esc(i.sku)}</strong><br><span class="muted small">${esc(i.title)}</span></td><td>${esc(i.stored_pvz_code)}</td>
      <td class="num">${i.qty}</td><td>${a.html}</td>
      <td><button class="btn ${a.days > 21 ? "warn" : "ghost"} small" data-action="relocate" data-id="${i.id}">Вывезти в другой ПВЗ · ${dec(m.params.relocation_cost_rub ?? 60)} ₽</button></td></tr>`;
  });
  const orderRows = analytics.orders.map((o) => `<tr><td>${esc(o.code)}<br><span class="muted small">${esc(o.title || "")}</span></td><td>${pill(o.status)}</td>
      <td class="num">${money(o.goods_rub)}</td><td class="num">−${money(o.commission_rub)}</td><td class="num">${money(o.net_rub)}</td></tr>`);
  pageBox().innerHTML = `
    <div class="card accent"><div class="section-title"><h2>Новая поставка · Рекомендация WB</h2>
      <label>Продавец<select id="seller-select">${state.sellers.map((s) => `<option value="${s.id}" ${s.id === seller.id ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></label></div>
      ${best ? `<p>Отгрузить партию <strong>${BATCH_QTY} единиц</strong> «${esc(best.it.title)}» (${esc(best.it.sku)}, скоринг ${dec(best.score)} ≥ 60) в ближайший подходящий ПВЗ — <strong>${esc(best.target.code)} · ${esc(best.target.name)}</strong>, свободно ${best.target.free_cells} яч., загрузка ${dec(best.target.utilization_pct)}%.</p>
        <button class="btn big" data-ship="1" data-sku="${esc(best.it.sku)}" data-pvz="${best.target.id}">Отгрузить партию (${BATCH_QTY} шт.) в ПВЗ</button>`
        : `<p>Сейчас нет SKU со скорингом ≥ 60 и свободными ячейками — партию лучше отправить на стандартный хаб / FBW.</p>`}
      ${plan.rest.length ? `<div class="hint">Неходовые SKU (${plan.rest.map((s) => `${esc(s.it.sku)}: ${s.score === null ? "—" : dec(s.score)}`).join(", ")}) идут на стандартный хаб / FBW.</div>` : ""}
      <div class="hint">Приёмка ${money(m.params.acceptance_cost_rub ?? 8)}/шт.</div></div>
    <div class="grid cols-4">
      <div><h3>Товар (эталон)</h3><div class="metric small">${money(ref.revenue.goods)}</div></div>
      <div><h3>Комиссия</h3><div class="metric small bad">−${money(ref.costs.commission)}</div><div class="hint">${seller.commission_pct}% от товара</div></div>
      <div><h3>Логистика + хранение</h3><div class="metric small bad">−${money(ref.costs.logistics + ref.costs.storage)}</div><div class="hint">логистика ${money(ref.costs.logistics)}, хранение ${rub(ref.costs.storage)}</div></div>
      <div><h3>Прибыль продавца</h3><div class="metric small good">${rub(ref.profit_rub)}</div><div class="hint">с заказа: mesh и хаб одинаково</div></div>
    </div>
    <div class="card"><h2>Остатки по ПВЗ</h2>${table([{ title: "Товар" }, { title: "Точка" }, { title: "Кол-во", num: true }, { title: "Хранение" }, { title: "" }], rows, "Нет товаров")}
      <div class="hint">Зелёный &lt; 14 дн · жёлтый 14–21 дн · красный &gt; 21 дн (алерт, вывоз за ${dec(m.params.relocation_cost_rub ?? 60)} ₽).</div></div>
    <div class="card"><h2>Прибыль по заказам</h2>${table([{ title: "Заказ" }, { title: "Статус" }, { title: "Товар", num: true }, { title: "Комиссия", num: true }, { title: "Нетто", num: true }], orderRows, "Заказов ещё нет")}
      <div class="hint">Выручка по заказам: ${money(analytics.revenue.goods_rub)}, прибыль по ledger: ${money(analytics.profit_rub)}.</div></div>`;
  $("#seller-select").addEventListener("change", (e) => { state.sellerId = Number(e.target.value); render(); });
}

document.addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-ship]");
  if (!btn) return;
  const it = state.items.find((i) => i.sku === btn.dataset.sku && i.seller_id === state.sellerId);
  if (!it) return;
  await withBusy(btn, async () => {
    const r = await guard(api("/items", { method: "POST", body: { sku: it.sku, seller_id: it.seller_id, title: it.title, qty: BATCH_QTY, cell_size: it.cell_size, pvz_id: Number(btn.dataset.pvz), price_rub: it.price_rub } }), `Партия ${BATCH_QTY} шт. отгружена в ПВЗ`);
    if (r) await refreshAfterAction();
  });
});

/* ---------- ПВЗ ---------- */
async function renderPvzCab() {
  const pvz = state.pvz.find((p) => p.id === state.pvzId) || points()[0];
  const [analytics, m, noMesh] = await Promise.all([api(`/analytics/pvz?pvz_id=${pvz.id}`), econ(), api("/economics/scenario?mesh_share=0")]);
  const thr = m.queue_threshold ? m.queue_threshold.alert_pct : 85;
  const rating = pvz.audit_sla_rating ?? pvz.sla_rating ?? "—";
  const over = pvz.utilization_pct >= thr;
  const mine = state.orders;
  const toCollect = mine.filter((o) => o.from_pvz === pvz.id && ["routed", "rerouted"].includes(o.status));
  const toIssue = mine.filter((o) => o.to_pvz === pvz.id && ["assembled", "in_transit"].includes(o.status));
  const task = (o, label, action, cls, what) => `<div class="task"><div><strong>${esc(o.code)}</strong> · ${esc(o.title || o.sku)} × ${o.qty}<br><span class="muted small">${what} · ${esc(o.from_code)} → ${esc(o.to_code)}</span></div>
    <button class="btn big ${cls}" data-action="${action}" data-id="${o.id}">${label}</button></div>`;
  const tasks = [
    ...toCollect.map((o) => task(o, "Собрать", "assemble", "", "передать курьеру в SLA")),
    ...toIssue.map((o) => task(o, "Выдать", "issue", "good", "выдать покупателю")),
  ].join("");
  const sellers = state.sellers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  pageBox().innerHTML = `
    <div class="section-title"><h2>${esc(pvz.code)} · ${esc(pvz.name)}</h2>
      <label>Точка<select id="pvz-select">${pvzOptions(pvz.id)}</select></label></div>
    <div class="kpi-row">
      <div class="kpi"><h3>Загрузка ячеек</h3><div class="metric small ${over ? "bad" : ""}">${dec(pvz.utilization_pct)}%</div>
        <div class="gauge ${over ? "over" : ""}"><span style="width:${Math.min(100, pvz.utilization_pct)}%"></span><i style="left:${thr}%"></i></div>
        <div class="gauge-label"><b style="left:${thr}%">порог ${dec(thr)}%</b></div>
        <div class="hint">${pvz.used_cells} / ${pvz.capacity_cells} яч., свободно ${pvz.free_cells}</div></div>
      <div class="kpi"><h3>Рейтинг точки</h3><div class="metric small"><span class="rating rating-${esc(rating)}">${esc(rating)}</span></div><div class="hint">шкала A–D по сверкам</div></div>
      <div class="kpi"><h3>Прибыль за период</h3><div class="metric small ${analytics.profit_rub >= 0 ? "good" : "bad"}">${money(analytics.profit_rub)}</div><div class="hint">выдано заказов: ${analytics.orders_issued}</div></div>
      <div class="kpi"><h3>Безубыточность</h3><div class="metric small">${m.breakeven.ops} оп/день</div>
        <div class="hint">${m.breakeven.ops} оп/день при ${Math.round(m.params.mesh_share * 100)}% mesh · ${noMesh.breakeven.ops} оп/день без бонуса · бонус +${money(m.params.mesh_bonus_rub)} · постоянные ${money(m.params.fixed_cost_day_rub)}/день</div></div>
    </div>
    <div class="card accent"><h2>Задания от маркетплейса</h2>${tasks || '<div class="empty">Активных заданий нет</div>'}</div>
    <div class="card"><h2>Приёмка от продавца</h2>
      <form id="accept-form" class="form-row">
        <label>SKU<input name="sku" required placeholder="TD-2001" /></label>
        <label>Наименование<input name="title" required placeholder="Наушники" /></label>
        <label>Продавец<select name="seller_id">${sellers}</select></label>
        <label>Кол-во<input name="qty" type="number" min="1" value="${BATCH_QTY}" required /></label>
        <label>Ячеек/шт.<input name="cell_size" type="number" min="1" max="10" value="1" required /></label>
        <label>Цена, ₽<input name="price_rub" type="number" min="1" value="${m.params.goods_rub}" required /></label>
        <button class="btn big" type="submit">Принять</button>
      </form></div>`;
  $("#pvz-select").addEventListener("change", (e) => { state.pvzId = Number(e.target.value); render(); });
  $("#accept-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = new FormData(event.target);
    const body = { sku: f.get("sku"), title: f.get("title"), seller_id: Number(f.get("seller_id")), qty: Number(f.get("qty")), cell_size: Number(f.get("cell_size")), price_rub: Number(f.get("price_rub")), pvz_id: pvz.id };
    await withBusy(event.target, async () => {
      const r = await guard(api("/items", { method: "POST", body }), "Товар принят на хранение");
      if (r) await refreshAfterAction();
    });
  });
}

/* ---------- Маркетплейс: решение о маршруте ---------- */
function routeReason(o) {
  const r = o.route || {};
  const thr = state.econData && state.econData.queue_threshold ? state.econData.queue_threshold.alert_pct : 85;
  if (r.kind === "mesh") return `сток в ${o.from_code}, загрузка < ${dec(thr)}%`;
  if (r.reason_code === "overflow_to_hub") return `перелив > ${dec(thr)}%`;
  if (r.reason_code === "hub_required") return "нет стока в городе";
  return r.reason || "—";
}

function routingTableHtml() {
  const rows = state.orders.slice().sort((a, b) => b.id - a.id).map((o) => {
    const r = o.route;
    const badge = !r ? "—" : r.kind === "mesh"
      ? `<span class="route-badge mesh">MESH · ${rub(r.total_cost_rub)} / ${dec(r.total_hours)} ч</span>`
      : `<span class="route-badge hub">ХАБ · ${rub(r.total_cost_rub)} / ${dec(r.total_hours)} ч</span>`;
    return `<tr><td><strong>${esc(o.code)}</strong><br><span class="muted small">${esc(o.title || "")} × ${o.qty}</span></td>
      <td>${esc(o.from_code)} → ${esc(o.to_code)}</td><td>${badge}<span class="reason">${esc(routeReason(o))}</span></td>
      <td>${pill(o.status)}</td><td class="num">${money(o.buyer_total_rub)}</td></tr>`;
  });
  return `<div class="card accent"><h2>Заказы: как маркетплейс везёт</h2>
    ${table([{ title: "Заказ" }, { title: "Откуда → куда" }, { title: "Маршрут и причина выбора" }, { title: "Статус" }, { title: "Покупатель", num: true }], rows, "Заказов нет")}</div>`;
}

async function renderAdminDashboard() {
  await renderDashboard();
  await econ();
  $("#page").insertAdjacentHTML("beforeend", routingTableHtml());
}

async function renderLedger() {
  const rows = await api("/ledger?limit=200");
  const body = rows.map((r) => `<tr><td>${r.order_id ? "#" + r.order_id : "—"}</td><td>${esc(r.party)}</td><td>${esc(r.kind)}</td>
    <td class="num" style="color:${r.amount_rub >= 0 ? "var(--good)" : "var(--bad)"}">${rub(r.amount_rub)}</td><td class="muted small">${esc(r.note || "")}</td></tr>`);
  pageBox().innerHTML = `<div class="card"><h2>Журнал проводок (ledger)</h2>
    ${table([{ title: "Заказ" }, { title: "Сторона" }, { title: "Операция" }, { title: "Сумма", num: true }, { title: "Комментарий" }], body, "Проводок нет")}</div>`;
}
