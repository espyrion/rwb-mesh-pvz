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
  /* SKU продавца для формы поставки: свои товары + возможность завести новый. */
  const skuList = [...new Map(state.items.filter((i) => i.seller_id === seller.id).map((i) => [i.sku, i])).values()];
  const skuOpts = skuList.map((i) => `<option value="${esc(i.sku)}" data-title="${esc(i.title)}" data-price="${i.price_rub}" data-cell="${i.cell_size}">${esc(i.sku)} · ${esc(i.title)}</option>`).join("");
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
    <div class="card"><h2>Отправить поставку в ПВЗ</h2>
      <form id="supply-form" class="form-row">
        <label>Товар<select name="sku" id="supply-sku">${skuOpts}<option value="__new__">＋ новый SKU</option></select></label>
        <label>Новый SKU<input name="sku_new" id="supply-sku-new" placeholder="TD-2001" disabled /></label>
        <label>Наименование<input name="title" id="supply-title" placeholder="Наушники" /></label>
        <label>Количество<input name="qty" type="number" min="1" value="${BATCH_QTY}" required /></label>
        <label>Ячеек/шт.<input name="cell_size" type="number" min="1" max="10" value="1" required /></label>
        <label>Цена, ₽<input name="price_rub" type="number" min="1" value="${m.params.goods_rub}" required /></label>
        <label>ПВЗ назначения<select name="pvz_id">${pvzOptions(state.pvzId)}</select></label>
        <button class="btn big" type="submit">Отправить поставку в ПВЗ</button>
      </form>
      <div class="hint">Партия уходит на точку и сразу появляется в кабинете ПВЗ: контракт API фиксирован (40 путей), отдельного статуса «поставка в пути» нет — приёмка на ПВЗ фиксируется в момент отправки поставки. Приёмка ${money(m.params.acceptance_cost_rub ?? 8)}/шт списывает ячейки точки.</div>
    </div>
    <div class="grid cols-4">
      <div><h3>Выручка от товаров</h3><div class="metric small">${money(analytics.revenue.goods_rub)}</div><div class="hint">${analytics.orders.length} зак. в периоде</div></div>
      <div><h3>Комиссия маркетплейса</h3><div class="metric small bad">−${money(analytics.costs.commission_rub)}</div><div class="hint">${seller.commission_pct}% от товара</div></div>
      <div><h3>Возвраты</h3><div class="metric small bad">−${money(analytics.costs.refunds_rub)}</div><div class="hint">возврат 7 дней</div></div>
      <div><h3>Прибыль продавца</h3><div class="metric small ${analytics.profit_rub >= 0 ? "good" : "bad"}">${money(analytics.profit_rub)}</div><div class="hint">нетто по выданным заказам</div></div>
    </div>
    <div class="card"><h2>Юнит-экономика эталонного заказа (${money(m.params.goods_rub)} товар)</h2>
      <div class="hint">Комиссия ${money(ref.costs.commission)}, логистика ${money(ref.costs.logistics)}, хранение ${rub(ref.costs.storage)} → прибыль <strong>${rub(ref.profit_rub)}</strong> (${dec(ref.margin_pct)}%). Приёмка на ПВЗ ${money(m.params.acceptance_cost_rub ?? 8)}/шт. Суммы выше — факт по выданным заказам, ниже — модель на один заказ.</div></div>
    <div class="card"><h2>Остатки по ПВЗ</h2>${table([{ title: "Товар" }, { title: "Точка" }, { title: "Кол-во", num: true }, { title: "Хранение" }, { title: "" }], rows, "Нет товаров")}
      <div class="hint">Зелёный &lt; 14 дн · жёлтый 14–21 дн · красный &gt; 21 дн (алерт, вывоз за ${dec(m.params.relocation_cost_rub ?? 60)} ₽).</div></div>
    <div class="card"><h2>Прибыль по заказам</h2>${table([{ title: "Заказ" }, { title: "Статус" }, { title: "Товар", num: true }, { title: "Комиссия", num: true }, { title: "Нетто", num: true }], orderRows, "Заказов ещё нет")}
      <div class="hint">Выручка по заказам: ${money(analytics.revenue.goods_rub)}, прибыль по ledger: ${money(analytics.profit_rub)}.</div></div>`;
  $("#seller-select").addEventListener("change", (e) => { state.sellerId = Number(e.target.value); render(); });
  const supplyForm = $("#supply-form");
  if (supplyForm) {
    const skuSel = $("#supply-sku");
    const skuNew = $("#supply-sku-new");
    const fill = () => {
      const opt = skuSel.selectedOptions[0];
      const isNew = skuSel.value === "__new__";
      skuNew.disabled = !isNew;
      if (isNew) { $("#supply-title").value = ""; return; }
      $("#supply-title").value = opt.dataset.title || "";
      supplyForm.elements.price_rub.value = opt.dataset.price || supplyForm.elements.price_rub.value;
      supplyForm.elements.cell_size.value = opt.dataset.cell || 1;
    };
    skuSel.addEventListener("change", fill);
    fill();
    supplyForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const f = new FormData(supplyForm);
      const sku = String(f.get("sku") === "__new__" ? f.get("sku_new") : f.get("sku") || "").trim();
      if (!sku) return toast("Укажите SKU поставки", "error");
      const body = {
        sku,
        seller_id: seller.id,
        title: String(f.get("title") || "").trim() || sku,
        qty: Number(f.get("qty")),
        cell_size: Number(f.get("cell_size")),
        price_rub: Number(f.get("price_rub")),
        pvz_id: Number(f.get("pvz_id")),
      };
      await withBusy(supplyForm, async () => {
        const r = await guard(api("/items", { method: "POST", body }));
        if (r) {
          const to = state.pvz.find((p) => p.id === body.pvz_id);
          toast(`Поставка ${r.sku}: ${r.qty} шт. принята в ${to ? to.code : "ПВЗ"} (+${r.cells} яч.)`, "ok");
          await refreshAfterAction();
        }
      });
    });
  }
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
  const isHub = !!pvz.is_hub;
  const mine = state.orders;
  /* Хаб — узел графа: заказов «на нём» не лежит, но через него проходят сегменты магистрали.
     Находим межгородские заказы по коду хаба в сегментах маршрута. */
  const hubLeg = (o) => (o.route && o.route.segments ? o.route.segments.find((s) => s.to_code === pvz.code || s.from_code === pvz.code) || null : null);
  const viaHub = isHub ? mine.filter((o) => hubLeg(o)) : [];
  const hubWait = viaHub.filter((o) => o.status === "assembled");
  const hubWay = viaHub.filter((o) => o.status === "in_transit");
  const hubRouted = viaHub.filter((o) => ["routed", "rerouted"].includes(o.status));
  const trunkSum = viaHub.reduce((sum, o) => sum + (hubLeg(o) ? hubLeg(o).cost_rub : 0), 0);
  const toCollect = mine.filter((o) => o.from_pvz === pvz.id && ["routed", "rerouted"].includes(o.status));
  /* Межгородской (hub) заказ нельзя выдать из «Собран»: сначала передача курьеру (transit).
     Локальный mesh-заказ API разрешает выдать сразу после сборки. */
  const toTransit = mine.filter((o) => o.to_pvz === pvz.id && o.status === "assembled" && o.route && o.route.kind !== "mesh");
  const toIssue = mine.filter((o) => o.to_pvz === pvz.id && (o.status === "in_transit" || (o.status === "assembled" && o.route && o.route.kind === "mesh")));
  const task = (o, label, action, cls, what) => `<div class="task"><div><strong>${esc(o.code)}</strong> · ${esc(o.title || o.sku)} × ${o.qty}<br><span class="muted small">${what} · ${esc(o.from_code)} → ${esc(o.to_code)}</span></div>
    <button class="btn big ${cls}" data-action="${action}" data-id="${o.id}">${label}</button></div>`;
  const info = (o, what) => `<div class="task"><div><strong>${esc(o.code)}</strong> · ${esc(o.title || o.sku)} × ${o.qty}<br><span class="muted small">${what} · ${esc(o.from_code)} → ${esc(o.to_code)}</span></div><span class="pill">${esc(o.status === "in_transit" ? "в пути" : "ожидает сборки")}</span></div>`;
  const tasks = isHub
    ? [
        ...hubWait.map((o) => task(o, "Переадресовать в магистраль", "transit", "", `собран в ${esc(o.from_code)}; магистраль ${dec(o.route.total_hours)} ч, ${money(o.route.total_cost_rub)} — после отправки заказ можно выдать`)),
        ...hubWay.map((o) => info(o, "передан в магистраль")),
        ...hubRouted.map((o) => info(o, "сначала сборка в ПВЗ-источнике")),
      ].join("")
    : [
        ...toCollect.map((o) => task(o, "Собрать", "assemble", "", "принять у продавца и уложить в ячейки")),
        ...toTransit.map((o) => task(o, "В путь", "transit", "", `передать курьеру: магистраль ${dec(o.route.total_hours)} ч, ${money(o.route.total_cost_rub)} — выдача после отправки`)),
        ...toIssue.map((o) => task(o, "Выдать", "issue", "good", "выдать покупателю")),
      ].join("");
  const sellers = state.sellers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  const stockHere = state.items.filter((i) => i.stored_pvz_id === pvz.id);
  pageBox().innerHTML = `
    <div class="section-title"><h2>${esc(pvz.code)} · ${esc(pvz.name)}</h2>
      <label>${isHub ? "Узел сети" : "Точка"}<select id="pvz-select">${pvzOptions(pvz.id, true)}</select></label></div>
    <div class="kpi-row">
      ${isHub ? `
      <div class="kpi"><h3>Межгород через хаб</h3><div class="metric small">${num(viaHub.length)}</div><div class="hint">заказов с сегментами магистрали HUB-KZN</div></div>
      <div class="kpi"><h3>Ожидают отправки</h3><div class="metric small ${hubWait.length ? "warn" : ""}">${num(hubWait.length)}</div><div class="hint">собраны в ПВЗ-источнике, ждут переадресации</div></div>
      <div class="kpi"><h3>В пути</h3><div class="metric small">${num(hubWay.length)}</div><div class="hint">переданы в магистраль</div></div>
      <div class="kpi"><h3>Логистика по магистрали</h3><div class="metric small">${money(trunkSum)}</div><div class="hint">хаб — узел графа: ячейки и приёмка не моделируются (заглушка)</div></div>` : `
      <div class="kpi"><h3>Загрузка ячеек</h3><div class="metric small ${over ? "bad" : ""}">${dec(pvz.utilization_pct)}%</div>
        <div class="gauge ${over ? "over" : ""}"><span style="width:${Math.min(100, pvz.utilization_pct)}%"></span><i style="left:${thr}%"></i></div>
        <div class="gauge-label"><b style="left:${thr}%">порог ${dec(thr)}%</b></div>
        <div class="hint">${pvz.used_cells} / ${pvz.capacity_cells} яч., свободно ${pvz.free_cells}</div></div>
      <div class="kpi"><h3>Рейтинг точки</h3><div class="metric small"><span class="rating rating-${esc(rating)}">${esc(rating)}</span></div><div class="hint">шкала A–D по сверкам</div></div>
      <div class="kpi"><h3>Прибыль за период</h3><div class="metric small ${analytics.profit_rub >= 0 ? "good" : "bad"}">${money(analytics.profit_rub)}</div><div class="hint">выдано заказов: ${analytics.orders_issued}</div></div>
      <div class="kpi"><h3>Безубыточность</h3><div class="metric small">${m.breakeven.ops} оп/день</div>
        <div class="hint">${m.breakeven.ops} оп/день при ${Math.round(m.params.mesh_share * 100)}% mesh · ${noMesh.breakeven.ops} оп/день без бонуса · бонус +${money(m.params.mesh_bonus_rub)} · постоянные ${money(m.params.fixed_cost_day_rub)}/день</div></div>`}
    </div>
    <div class="card accent"><h2>${isHub ? "Переадресация межгорода" : "Задания от маркетплейса"}</h2>${tasks || `<div class="empty">${isHub ? "Межгородских заказов нет" : "Активных заданий нет"}</div>`}</div>
    ${isHub ? "" : `<div class="card"><h2>Принятые поставки на ${esc(pvz.code)}</h2>
      ${table([{ title: "Товар" }, { title: "Продавец" }, { title: "Кол-во", num: true }, { title: "Ячеек/шт", num: true }, { title: "Занято ячеек", num: true }, { title: "Принято" }, { title: "Статус" }],
        stockHere.map((i) => `<tr><td><strong>${esc(i.sku)}</strong><br><span class="muted small">${esc(i.title)}</span></td>
          <td>${esc((state.sellers.find((s) => s.id === i.seller_id) || {}).name || "—")}</td>
          <td class="num">${i.qty}</td><td class="num">${i.cell_size}</td><td class="num">${i.cells}</td>
          <td class="muted small">${dt(i.accepted_at)}</td><td>${pill(i.status)}</td></tr>`), "Поставок на точку ещё не было")}
      <div class="hint">Приёмка выполняется автоматически при отправке поставки продавцом (контракт API — 40 путей, отдельного статуса «в пути к ПВЗ» нет). Приёмка занимает ячейки точки и стоит ПВЗ ${money(m.params.acceptance_cost_rub ?? 8)} за единицу — эта сумма уже учтена в «Прибыли за период».</div></div>`}
    ${isHub ? `<div class="card"><h2>Хаб как узел графа</h2><div class="hint">HUB-KZN — помеченная заглушка: ячейки, приёмка и выдача не моделируются. Хаб не хранит товар, он принимает собранный заказ из ПВЗ-источника и передаёт его в магистраль; фактическая выдача идёт в ПВЗ города-получателя. Заказ нельзя выдать до переадресации: API вернёт <span class="mono">409</span> и подскажет вызвать <span class="mono">POST /orders/{id}/transit</span>.</div></div>` : `<div class="card"><h2>Приёмка от продавца</h2>
      <form id="accept-form" class="form-row">
        <label>SKU<input name="sku" required placeholder="TD-2001" /></label>
        <label>Наименование<input name="title" required placeholder="Наушники" /></label>
        <label>Продавец<select name="seller_id">${sellers}</select></label>
        <label>Кол-во<input name="qty" type="number" min="1" value="${BATCH_QTY}" required /></label>
        <label>Ячеек/шт.<input name="cell_size" type="number" min="1" max="10" value="1" required /></label>
        <label>Цена, ₽<input name="price_rub" type="number" min="1" value="${m.params.goods_rub}" required /></label>
        <button class="btn big" type="submit">Принять</button>
      </form></div>`}`;
  $("#pvz-select").addEventListener("change", (e) => { state.pvzId = Number(e.target.value); render(); });
  const acceptForm = $("#accept-form");
  if (acceptForm) acceptForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = new FormData(event.target);
    const body = { sku: f.get("sku"), title: f.get("title"), seller_id: Number(f.get("seller_id")), qty: Number(f.get("qty")), cell_size: Number(f.get("cell_size")), price_rub: Number(f.get("price_rub")), pvz_id: pvz.id };
    await withBusy(event.target, async () => {
      const r = await guard(api("/items", { method: "POST", body }), "Товар принят на хранение");
      if (r) await refreshAfterAction();
    });
  });
}

/* ---------- Маркетплейс: как выбирается маршрут (интерактивно) ---------- */
function cmpSelects(fromId, toId) {
  const opt = (id) => state.pvz.map((p) => `<option value="${p.id}" ${p.id === id ? "selected" : ""}>${esc(p.code)} · ${esc(p.name)}</option>`).join("");
  return { from: opt(fromId), to: opt(toId) };
}

const rub2 = (v) => new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v) + " ₽";
const segWord = (n) => (n % 10 === 1 && n % 100 !== 11 ? `${n} сегмент` : `${n} сегмента`);

function schemeCard(title, scheme, win) {
  if (!scheme || !scheme.available) {
    return `<div class="scheme alt"><div class="scheme-head"><span class="route-badge hub">${esc(title)}</span><b>недоступно</b></div>
      <div class="hint">у этих точек нет такого маршрута в текущей сети</div></div>`;
  }
  const legs = (scheme.segments || []).map((g) => `<li>${esc(g.from_code)} → ${esc(g.to_code)}<span>${rub(g.cost_rub)} · ${dec(g.hours)} ч</span></li>`).join("");
  return `<div class="scheme ${win ? "win" : "alt"}">
      <div class="scheme-head"><span class="route-badge ${scheme.kind === "mesh" ? "mesh" : "hub"}">${esc(scheme.kind === "mesh" ? "MESH" : "ХАБ")}</span>
        <b>${rub(scheme.total_cost_rub)} · ${dec(scheme.total_hours)} ч</b>${win ? '<span class="pill">выбран API</span>' : ""}</div>
      <ul class="legs">${legs || "<li>без сегментов</li>"}</ul>
      <div class="hint">${esc(title)} · ${segWord((scheme.segments || []).length)}, себестоимость логистики складывается из них.${scheme.kind === "hub" ? " Хаб в модели — консолидированная ставка обработки: своего хранения на нём нет, товар идёт со склада маркетплейса через перевалку." : " Товар уже лежит в пункте города получателя, поэтому хаб и магистраль не задействованы."}</div>
    </div>`;
}

function compareBlock(cmp) {
  const load = cmp.load || {};
  const pct = (o) => (o ? dec(o.utilization_pct) + "%" : "—");
  const triggered = (load.triggered_by || []).map((r) => `${esc(r.code)} ${dec(r.utilization_pct)}%`).join(", ");
  return `<div class="grid cols-2">
      ${schemeCard("Из остатка в ПВЗ города — склад не нужен", cmp.mesh, cmp.chosen === "mesh")}
      ${schemeCard("Со склада маркетплейса: хаб + магистраль + последняя миля", cmp.hub, cmp.chosen === "hub")}
    </div>
    <div class="card"><h2>Решение API: ${esc(cmp.reason_code || "—")}</h2>
      <p>${esc(cmp.reason || "—")}</p>
      <div class="list mt">
        <div class="list-item small">Порог перелива <strong>${dec(load.threshold_pct)}%</strong> — выше него маршрут уходит на хаб</div>
        <div class="list-item small">Загрузка отправителя: <strong>${pct(load.from)}</strong> ${load.from ? "(" + esc(load.from.code) + ")" : ""} · получателя: <strong>${pct(load.to)}</strong> ${load.to ? "(" + esc(load.to.code) + ")" : ""}</div>
        <div class="list-item small">Сработал перелив: <strong>${load.triggered ? "да — " + esc(triggered) : "нет"}</strong></div>
      </div>
      <div class="hint">Хотите увидеть перелив — демо-кокпит, шаг «Пик / overflow 85%»: он поднимает загрузку точки до порога, после чего тот же расчёт выбирает хаб.</div>
    </div>`;
}

async function renderRouting() {
  const m = await econ();
  const bp = m.order.compare || {};
  const sc = m.order;
  const pts = points();
  const q0 = pts[0], q1 = pts[1] || pts[0];
  const hubPoint = state.pvz.find((p) => p.is_hub);
  const intercity = state.pvz.find((p) => !p.is_hub && q0 && p.city !== q0.city);
  const sel = cmpSelects(q0.id, q1.id);
  const money = (v) => rub(v);
  const parts = [
    { label: "Маркетплейс (WB)", v: bp.marketplace_delta_rub || 0, tone: "good" },
    { label: "ПВЗ (бонус за заказ без хаба)", v: bp.pvz_delta_rub || 0, tone: "good" },
    { label: "Покупатель (дешевле доставка)", v: -(bp.buyer_delta_rub || 0), tone: "good" },
  ];
  const saving = parts.reduce((sum, p) => sum + p.v, 0);
  const logist = Math.round(((sc.hub.route.cost_rub - sc.mesh.route.cost_rub) * 100)) / 100;
  const bars = parts.map((p) => `<div class="split-row"><span>${esc(p.label)}</span>
      <div class="bar"><span style="width:${saving ? (p.v / saving) * 100 : 0}%"></span></div>
      <b>${p.v > 0 ? "+" : p.v < 0 ? "−" : ""}${rub2(Math.abs(p.v))}</b><span class="muted small">${saving ? dec(((p.v / saving) * 100).toFixed(1)) : 0}%</span></div>`).join("");
  const checks = [
    `Проверка сходимости: ${parts.map((p) => (p.v > 0 ? "+" : p.v < 0 ? "−" : "") + rub2(Math.abs(p.v))).join(" + ")} = ${rub2(saving)} — совпадает с разницей логистики ${money(sc.hub.route.cost_rub)} − ${money(sc.mesh.route.cost_rub)} = ${rub2(logist)}`,
    `Продавец: ${(bp.seller_delta_rub || 0) > 0 ? "+" : (bp.seller_delta_rub || 0) < 0 ? "−" : ""}${rub2(Math.abs(bp.seller_delta_rub || 0))} — прибыль не зависит от схемы (${money(sc.mesh.seller.profit_rub)} в обеих)`,
    `Срок: ${dec(sc.mesh.route.hours)} ч против ${dec(sc.hub.route.hours)} ч — mesh быстрее на ${dec(Math.abs(sc.mesh.route.hours - sc.hub.route.hours))} ч`,
    `Доставка покупателю: ${money(m.params.buyer_delivery_fee_local_rub)} против ${money(m.params.buyer_delivery_fee_hub_rub)}`,
  ];
  pageBox().innerHTML = `
    <div class="card accent"><h2>Склад или ПВЗ-сеть: что выгоднее</h2>
      <div class="split mt">
        <div class="split-row"><span>Из остатка в ПВЗ города (mesh)</span><div class="bar"><span style="width:${Math.round((sc.mesh.route.cost_rub / sc.hub.route.cost_rub) * 100)}%"></span></div><b>${rub(sc.mesh.route.cost_rub)}</b><span class="muted small">${dec(sc.mesh.route.hours)} ч</span></div>
        <div class="split-row"><span>Со склада через хаб (магистраль)</span><div class="bar"><span style="width:${Math.round((sc.hub.route.cost_rub / sc.hub.route.cost_rub) * 100)}%"></span></div><b>${rub(sc.hub.route.cost_rub)}</b><span class="muted small">${dec(sc.hub.route.hours)} ч</span></div>
      </div>
      <p class="hint mt">Вывод: доставлять из остатка, который уже лежит в ПВЗ города, дешевле на <strong>${rub2(logist)}</strong> и быстрее на <strong>${dec(Math.abs(sc.mesh.route.hours - sc.hub.route.hours))} ч</strong>. Склад маркетплейса при этом не расширяется — товар едет из соседнего пункта.</p></div>
    <div class="card"><h2>Как маркетплейс выбирает, как везти заказ</h2>
      <p class="hint">API считает обе схемы по одному набору тарифов и берёт дешёвую, соблюдая ограничения: есть ли локальный сегмент, активны ли узлы, не перегружена ли точка. Ниже — тот же расчёт, что при оформлении заказа: <span class="mono">GET /api/v1/routes/compare</span>.</p>
      <div class="form-row mt">
        <label>Откуда<select id="cmp-from">${sel.from}</select></label>
        <label>Куда<select id="cmp-to">${sel.to}</select></label>
        <button class="btn big" id="cmp-run">Посчитать маршрут</button>
      </div>
      <div class="inline mt">
        <button class="btn ghost small" data-cmp-preset="urban">Пример: городской mesh</button>
        <button class="btn ghost small" data-cmp-preset="intercity">Пример: межгород через хаб</button>
        <button class="btn ghost small" data-cmp-preset="hub">Откуда — не хаб, куда — ${esc(hubPoint ? hubPoint.code : "хаб")}</button>
      </div>
    </div>
    <div id="cmp-out"></div>
    <div class="card"><h2>Как делится выгода: mesh против хаба</h2>
      <div class="grid cols-3">
        <div><h3>Логистика mesh</h3><div class="metric small good">${rub(sc.mesh.route.cost_rub)}</div><div class="hint">${dec(sc.mesh.route.hours)} ч</div></div>
        <div><h3>Логистика через хаб</h3><div class="metric small">${rub(sc.hub.route.cost_rub)}</div><div class="hint">${dec(sc.hub.route.hours)} ч</div></div>
        <div><h3>Экономия на заказ</h3><div class="metric small good">${rub2(logist)}</div><div class="hint">разница себестоимости логистики</div></div>
      </div>
      <div class="split mt">${bars}</div>
      <div class="list mt">${checks.map((c) => `<div class="list-item small">${c}</div>`).join("")}</div>
      <div class="hint">Правило выбора: есть локальный сегмент и точка не перегружена — mesh (<span class="mono">mesh_same_city</span> / <span class="mono">mesh_cheapest</span>); загрузка получателя выше ${dec(m.queue_threshold ? m.queue_threshold.alert_pct : 85)}% — <span class="mono">overflow_to_hub</span>; локального маршрута нет — <span class="mono">hub_required</span>. Все цифры приходят из <span class="mono">/economics/scenario</span> и <span class="mono">/routes/compare</span>; в вёрстке констант нет.</div>
    </div>`;
  const run = async () => {
    const from = Number($("#cmp-from").value), to = Number($("#cmp-to").value);
    const out = $("#cmp-out");
    out.innerHTML = '<div class="card empty">считаю по API…</div>';
    try {
      if (from === to) throw new Error("точка отправления и выдачи совпадают — выберите разные");
      const cmp = await api(`/routes/compare?from=${from}&to=${to}&accept_latency=true`);
      out.innerHTML = compareBlock(cmp);
    } catch (e) {
      out.innerHTML = `<div class="card"><h2>Не получилось посчитать</h2><p>${esc(e.message)}</p></div>`;
    }
  };
  $("#cmp-run").addEventListener("click", run);
  $("#cmp-from").addEventListener("change", run);
  $("#cmp-to").addEventListener("change", run);
  document.querySelectorAll("[data-cmp-preset]").forEach((b) => b.addEventListener("click", () => {
    const set = (id, v) => { const el = $(id); if (el) el.value = String(v); };
    if (b.dataset.cmpPreset === "intercity" && intercity && q0) { set("#cmp-from", q0.id); set("#cmp-to", intercity.id); }
    if (b.dataset.cmpPreset === "hub" && hubPoint && q0) { set("#cmp-from", q0.id); set("#cmp-to", hubPoint.id); }
    if (b.dataset.cmpPreset === "urban") { set("#cmp-from", q0.id); set("#cmp-to", q1.id); }
    run();
  }));
  run();
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
