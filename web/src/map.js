/* Карта сети: SVG-схема ПВЗ, сегментов, маршрутов и отказа узла. Только vanilla JS. */

const MAP_POS = {
  1: { x: 120, y: 216 }, 2: { x: 330, y: 186 }, 3: { x: 152, y: 402 }, 4: { x: 344, y: 414 },
  5: { x: 652, y: 202 }, 6: { x: 878, y: 180 }, 7: { x: 660, y: 402 }, 8: { x: 872, y: 416 },
  9: { x: 500, y: 300 },
};
const MAP_W = 1000;
const MAP_H = 540;
const MAP_HUB_ID = 9;
const MAP_CITIES = [
  { label: "Йошкар-Ола", sub: "4 ПВЗ · локальный mesh", x: 36, y: 92, w: 398, h: 418 },
  { label: "Казань", sub: "4 ПВЗ · хаб и магистраль", x: 566, y: 92, w: 398, h: 418 },
];

let MAP_TIPS = {};

const mapPairKey = (a, b) => (a < b ? `${a}-${b}` : `${b}-${a}`);

function mapBend(a, b) {
  if (a === MAP_HUB_ID || b === MAP_HUB_ID) {
    // Пучок магистралей разводим лёгким прогибом, иначе 8 линий сливаются в центре.
    const node = a === MAP_HUB_ID ? b : a;
    return ((node % 3) - 1) * 9;
  }
  return ((a * 13 + b * 7) % 2 ? 1 : -1) * 12;
}

/* Дуга пары: геометрия считается от младшего id, поэтому оба направления идут одной кривой. */
function mapCurve(aId, bId, bendOverride) {
  const lo = Math.min(aId, bId);
  const hi = Math.max(aId, bId);
  const p1 = MAP_POS[lo];
  const p2 = MAP_POS[hi];
  if (!p1 || !p2) return null;
  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;
  const len = Math.hypot(dx, dy) || 1;
  const bend = bendOverride === undefined ? mapBend(lo, hi) : bendOverride;
  const cx = (p1.x + p2.x) / 2 + (-dy / len) * bend;
  const cy = (p1.y + p2.y) / 2 + (dx / len) * bend;
  const forward = aId === lo;
  const s = forward ? p1 : p2;
  const e = forward ? p2 : p1;
  return {
    d: `M ${s.x} ${s.y} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${e.x} ${e.y}`,
    mid: [0.25 * s.x + 0.5 * cx + 0.25 * e.x, 0.25 * s.y + 0.5 * cy + 0.25 * e.y],
  };
}

function mapChip(x, y, text, color) {
  const width = Math.max(28, String(text).length * 5.5 + 12);
  return `<g class="map-chip" transform="translate(${(x - width / 2).toFixed(1)}, ${(y - 9).toFixed(1)})">
    <rect width="${width.toFixed(1)}" height="18" rx="9" fill="#0b101a" fill-opacity="0.96" stroke="${color}" stroke-opacity=".75"/>
    <text x="${(width / 2).toFixed(1)}" y="12.4" text-anchor="middle" fill="${color}" font-size="9.5" font-family="'JetBrains Mono', monospace">${esc(text)}</text>
  </g>`;
}

function mapPairs() {
  const byKey = new Map();
  state.edges.forEach((edge) => {
    if (!MAP_POS[edge.from_pvz] || !MAP_POS[edge.to_pvz]) return;
    const key = mapPairKey(edge.from_pvz, edge.to_pvz);
    if (!byKey.has(key)) {
      byKey.set(key, {
        key,
        a: Math.min(edge.from_pvz, edge.to_pvz),
        b: Math.max(edge.from_pvz, edge.to_pvz),
        rows: [],
        kind: edge.kind,
      });
    }
    const pair = byKey.get(key);
    pair.rows.push(edge);
    if (edge.kind === "trunk") pair.kind = "trunk";
  });
  return [...byKey.values()];
}

function mapEdgeSvg(pair) {
  const path = mapCurve(pair.a, pair.b);
  if (!path) return "";
  const alive = pair.rows.every((row) => row.is_active);
  const trunk = pair.kind === "trunk";
  const cls = ["edge", trunk ? "edge-trunk" : "edge-local", alive ? "" : "edge-dead"].filter(Boolean).join(" ");
  const stroke = alive ? (trunk ? "#4f659e" : "#42537a") : "#7e3437";
  const dash = trunk ? ' stroke-dasharray="9 7"' : "";
  MAP_TIPS[pair.key] = mapTipHtml(pair);
  return `<g class="${cls}" data-tipkey="${pair.key}">
    <path class="edge-hit" d="${path.d}" fill="none" stroke="transparent" stroke-width="18"/>
    <path class="edge-line" d="${path.d}" fill="none" stroke="${stroke}" stroke-width="${trunk ? 2.4 : 1.6}" stroke-linecap="round" stroke-opacity="${trunk ? 0.95 : 0.8}"${dash}/>
  </g>`;
}

function mapTipHtml(pair) {
  const head = `<strong>${esc(pair.rows[0].from_code)} ↔ ${esc(pair.rows[0].to_code)}</strong>`;
  const kind = pair.kind === "trunk" ? "магистраль через хаб" : "локальный сегмент";
  const lines = pair.rows.map((row) => {
    const off = row.is_active ? "" : ' · <span style="color:#ef6461">отключён</span>';
    return `${esc(row.from_code)} → ${esc(row.to_code)}: ${money(row.cost_rub)} · ${dec(row.hours)} ч${off}`;
  });
  return `${head}<br><span class="muted">${kind}</span><br>${lines.join("<br>")}`;
}

function mapNodeSvg(row) {
  const pos = MAP_POS[row.id];
  if (!pos) return "";
  const hub = row.is_hub;
  const r = hub ? 24 : 19;
  const util = Math.min(100, row.utilization_pct || 0);
  const circumference = 2 * Math.PI * r;
  const tone = !row.is_active ? "down" : util >= 85 ? "bad" : util >= 70 ? "warn" : "good";
  const color = { good: "#34c38f", warn: "#f0b429", bad: "#ef6461", down: "#93a0b8" }[tone];
  const selected = state.mapNode === row.id;
  const body = hub
    ? `<path d="M 0 ${-r} L ${r} 0 L 0 ${r} L ${-r} 0 Z" fill="#1b2740" stroke="${color}" stroke-width="2"/>
       <text class="node-hub" x="0" y="3.4" text-anchor="middle">HUB</text>
       ${mapChip(0, r + 18, row.code, "#7ea6ff")}`
    : `<circle r="${r}" fill="none" stroke="#243049" stroke-width="5"/>
       <circle r="${r}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round"
         stroke-dasharray="${((circumference * util) / 100).toFixed(1)} ${circumference.toFixed(1)}" transform="rotate(-90)"/>
       <circle r="${r - 5.5}" fill="#141b29"/>
       <text class="node-pct" x="0" y="3.4" text-anchor="middle">${Math.round(util)}%</text>`;
  const dead = row.is_active
    ? ""
    : `<circle r="${r + 3}" fill="none" stroke="#ef6461" stroke-width="1.4" stroke-dasharray="4 4" stroke-opacity=".85"/>
       <text class="node-down-tag" x="0" y="${r + 31}" text-anchor="middle">выведена</text>`;
  const halo = selected ? `<circle class="halo" r="${r + 12}" fill="none" stroke="#4f8cff" stroke-width="1.6"/>` : "";
  return `<g class="node ${row.is_active ? "" : "is-down"} ${selected ? "is-selected" : ""}"
      transform="translate(${pos.x}, ${pos.y})" data-map-action="node" data-id="${row.id}">
    ${halo}
    ${body}
    ${dead}
    ${hub ? "" : `<text class="node-code" x="0" y="${r + 19}" text-anchor="middle">${esc(row.code)}</text>`}
    <circle r="${r + 18}" fill="transparent"/>
  </g>`;
}

function mapRouteOverlay(route, color, markerId) {
  if (!route || !route.segments || !route.segments.length) return "";
  return route.segments
    .map((seg) => {
      const path = mapCurve(seg.from, seg.to);
      if (!path) return "";
      return `<g class="route-group">
        <path class="route-line" d="${path.d}" fill="none" stroke="${color}" stroke-width="3.2"
          stroke-linecap="round" stroke-dasharray="9 9" marker-end="url(#${markerId})"/>
        ${mapChip(path.mid[0], path.mid[1] - 11, `${Math.round(seg.cost_rub)} ₽`, color)}
      </g>`;
    })
    .join("");
}

function mapFailureOverlay() {
  const failure = state.mapFailure;
  if (!failure || !failure.evacuated || !failure.evacuated.length) return "";
  const origin = failure.pvz.id;
  if (!MAP_POS[origin]) return "";
  const byTarget = new Map();
  failure.evacuated.forEach((row) => {
    const cur = byTarget.get(row.to_pvz_id) || { cells: 0, cost: 0, skus: [] };
    cur.cells += row.cells || 0;
    cur.cost += row.cost_rub || 0;
    cur.skus.push(row.sku);
    byTarget.set(row.to_pvz_id, cur);
  });
  const arcs = [...byTarget.entries()]
    .map(([targetId, info]) => {
      const path = mapCurve(origin, targetId, 46);
      if (!path) return "";
      return `<g class="route-group">
        <path class="failure-line" d="${path.d}" fill="none" stroke="#ef6461" stroke-width="2.2"
          stroke-dasharray="5 7" marker-end="url(#arrow-bad)"/>
        ${mapChip(path.mid[0], path.mid[1], `${info.cells} яч. · ${Math.round(info.cost)} ₽`, "#ef6461")}
      </g>`;
    })
    .join("");
  const rerouted = (failure.orders || []).map((row) => mapRouteOverlay(row.route, "#f0b429", "arrow-warn")).join("");
  return arcs + rerouted;
}

function mapSvgHtml() {
  MAP_TIPS = {};
  const pairs = mapPairs();
  const orders = mapRouteOrders();
  const order = orders.find((row) => row.id === state.mapOrderId) || null;
  const localEdges = pairs.filter((pair) => pair.kind !== "trunk").map(mapEdgeSvg).join("");
  const trunkEdges = pairs.filter((pair) => pair.kind === "trunk").map(mapEdgeSvg).join("");
  const cityPanels = MAP_CITIES.map(
    (city) => `<g class="city">
      <rect x="${city.x}" y="${city.y}" width="${city.w}" height="${city.h}" rx="26"
        fill="#111827" fill-opacity=".6" stroke="#25314a" stroke-width="1"/>
      <text class="city-name" x="${city.x + 20}" y="${city.y + 32}">${esc(city.label)}</text>
      <text class="city-sub" x="${city.x + 20}" y="${city.y + 50}">${esc(city.sub)}</text>
    </g>`
  ).join("");
  const routeOverlay = order ? mapRouteOverlay(order.route, "#4f8cff", "arrow-accent") : "";
  const badge = order
    ? mapChip(128, 40, `${order.code} · ${Math.round(order.route.total_cost_rub)} ₽ · ${dec(order.route.total_hours)} ч`, "#4f8cff")
    : "";
  return `<svg viewBox="0 0 ${MAP_W} ${MAP_H}" role="img" aria-label="Схема сети ПВЗ: узлы, сегменты и маршруты">
    <defs>
      <marker id="arrow-accent" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#4f8cff"/>
      </marker>
      <marker id="arrow-warn" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#f0b429"/>
      </marker>
      <marker id="arrow-bad" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#ef6461"/>
      </marker>
      <pattern id="map-dots" width="26" height="26" patternUnits="userSpaceOnUse">
        <circle cx="1.5" cy="1.5" r="1.2" fill="#22304a" fill-opacity=".6"/>
      </pattern>
    </defs>
    <rect width="${MAP_W}" height="${MAP_H}" fill="url(#map-dots)"/>
    ${cityPanels}
    ${localEdges}
    ${trunkEdges}
    ${routeOverlay}
    ${mapFailureOverlay()}
    ${state.pvz.map(mapNodeSvg).join("")}
    ${badge}
    <text class="map-watermark" x="${MAP_W - 18}" y="${MAP_H - 16}" text-anchor="end">схема сети · не географическая карта</text>
  </svg>`;
}

function mapLegendHtml() {
  return `<div class="map-legend">
    <span class="lg"><i class="dot" style="background:#34c38f"></i>загрузка &lt; 70%</span>
    <span class="lg"><i class="dot" style="background:#f0b429"></i>70–85%</span>
    <span class="lg"><i class="dot" style="background:#ef6461"></i>≥ 85%</span>
    <span class="lg"><i class="dot" style="background:#93a0b8"></i>выведена из строя</span>
    <span class="lg"><i class="line"></i>локальный сегмент</span>
    <span class="lg"><i class="dash"></i>магистраль через хаб</span>
    <span class="lg"><i class="wave"></i>маршрут заказа</span>
    <span class="lg"><i style="background:#ef6461"></i>эвакуация при отказе</span>
  </div>`;
}

function mapRouteOrders() {
  return state.orders.filter(
    (row) => row.route && row.route.segments && row.route.segments.length &&
      ["routed", "rerouted", "assembled", "in_transit"].includes(row.status)
  );
}

function mapPanelHtml() {
  const node = state.mapNode ? state.pvz.find((row) => row.id === state.mapNode) : null;
  const orders = mapRouteOrders();
  const order = orders.find((row) => row.id === state.mapOrderId) || null;
  const failure = state.mapFailure;
  const live = points().filter((row) => row.is_active);
  const avgUtil = live.length ? Math.round(live.reduce((sum, row) => sum + row.utilization_pct, 0) / live.length) : 0;
  const busiest = live.slice().sort((a, b) => b.utilization_pct - a.utilization_pct)[0];
  const openIncidents = state.incidents.filter((row) => row.status === "open").length;

  const nodeBlock = node
    ? `<div class="section-title">
         <h2>${esc(node.code)}${node.is_hub ? " · хаб" : ""}</h2>
         ${node.is_active ? '<span class="badge badge-good">в строю</span>' : '<span class="badge badge-bad">выведена из строя</span>'}
       </div>
       <div class="list">
         <div class="list-item">
           <strong>${esc(node.name)}</strong>
           <div class="muted small">${esc(node.city)}${node.is_hub ? " · перевалочный узел магистрали" : ""}</div>
         </div>
         ${node.is_hub
           ? `<div class="list-item small muted">Ячейки не хранит: только перевалка между городами.</div>`
           : `<div class="list-item">
                <div class="row"><span class="muted small">Заполнено ${node.used_cells} из ${node.capacity_cells} яч.</span><strong>${dec(node.utilization_pct)}%</strong></div>
                <div class="bar ${node.utilization_pct >= 85 ? "warn" : node.utilization_pct >= 70 ? "mid" : ""}" style="margin-top:8px"><span style="width:${Math.min(100, node.utilization_pct)}%"></span></div>
              </div>
              <div class="list-item small">Свободно <strong>${node.free_cells}</strong> яч. · хранение ${money(node.storage_rate_day)}/яч./день · выдача ${money(node.op_fee)}</div>`}
       </div>
       <div class="inline" style="margin-top:12px">
         ${node.is_active
           ? `<button class="btn danger small" data-map-action="down" data-id="${node.id}">Вывести из строя</button>`
           : `<button class="btn good small" data-map-action="up" data-id="${node.id}">Вернуть в строй</button>`}
         ${node.is_hub ? "" : `<button class="btn ghost small" data-map-action="open-pvz" data-id="${node.id}">Кабинет ПВЗ</button>`}
       </div>`
    : `<div class="section-title"><h2>Сводка сети</h2></div>
       <div class="list">
         <div class="list-item small">Точек в строю: <strong>${points().filter((row) => row.is_active).length}</strong> из ${points().length}</div>
         <div class="list-item small">Средняя загрузка: <strong>${dec(avgUtil)}%</strong>${busiest ? ` · максимум ${esc(busiest.code)} — ${dec(busiest.utilization_pct)}%` : ""}</div>
         <div class="list-item small">Открытых инцидентов: <strong>${openIncidents}</strong></div>
         <div class="list-item small muted">Нажмите точку на схеме: покажу загрузку, тарифы и действия «вывести из строя» / «вернуть в строй».</div>
       </div>`;

  const orderBlock = `<div class="section-title mt"><h3>Маршрут заказа</h3></div>
    <label>Заказ<select id="map-order-select">
      <option value="">— не выбран —</option>
      ${orders.map((row) => `<option value="${row.id}" ${row.id === state.mapOrderId ? "selected" : ""}>${esc(row.code)} · ${esc(kindLabel[row.route.kind] || row.route.kind)}</option>`).join("")}
    </select></label>
    ${order
      ? `<div class="list mt">
           <div class="list-item">
             <div class="row"><strong>${esc(order.code)}</strong><span class="pill ${esc(order.status)}">${esc(order.status)}</span></div>
             <div class="muted small">${esc(order.sku || "")} · ${money(order.route.total_cost_rub)} · ${dec(order.route.total_hours)} ч</div>
             <div class="segments">${segmentsText(order.route)}</div>
           </div>
         </div>`
      : `<div class="hint">Выберите заказ — его путь подсветится анимацией поверх схемы.</div>`}`;

  const failureBlock = failure
    ? `<div class="section-title mt"><h3>Отказ узла</h3></div>
       <div class="list">
         <div class="list-item">
           <strong>${esc(failure.pvz.code)}</strong> выведена: перемещений ${(failure.evacuated || []).length},
           заказов обработано ${(failure.orders || []).length}, не решено ${(failure.unresolved || []).length},
           затраты ${money(failure.relocation_total_rub)}
         </div>
         ${(failure.evacuated || []).map((row) => `<div class="list-item small">
             ${esc(row.sku)} → ${esc(row.to_code)} · ${row.cells} яч. · ${money(row.cost_rub)}
             ${row.with_order || row.with_orders ? `<div class="muted small">с заказом ${esc([].concat(row.with_order || row.with_orders).join(", "))}</div>` : ""}
           </div>`).join("")}
         ${(failure.unresolved || []).map((row) => `<div class="list-item small" style="border-color:#5c2929">
             <strong>не решено:</strong> ${esc(row.sku || row.code || "—")} · ${esc(row.reason || "")}
           </div>`).join("")}
       </div>`
    : "";

  return `${nodeBlock}${orderBlock}${failureBlock}`;
}

async function renderMap() {
  const orders = mapRouteOrders();
  if (state.mapOrderId && !orders.some((row) => row.id === state.mapOrderId)) state.mapOrderId = null;
  if (state.mapNode && !state.pvz.some((row) => row.id === state.mapNode)) state.mapNode = null;
  const orderCount = state.orders.filter((row) => row.route).length;
  const pairCount = new Set(state.edges.map((row) => mapPairKey(row.from_pvz, row.to_pvz))).size;
  $("#page").innerHTML = `
    <div class="map-grid">
      <div class="card">
        <div class="section-title">
          <h2>Схема сети</h2>
          <div class="inline">
            <span class="badge">${points().length} ПВЗ · 1 хаб · ${pairCount} сегментов · ${orderCount} маршрутов</span>
            <button class="btn ghost small" data-map-action="reset">Сбросить вид</button>
          </div>
        </div>
        <div class="netmap" id="netmap">${mapSvgHtml()}<div class="map-tip" id="map-tip" hidden></div></div>
        ${mapLegendHtml()}
        <div class="hint">Наведите на сегмент — стоимость и часы по направлениям. Нажмите точку — карточка и действия. «Вывести из строя» запускает эвакуацию остатков и переадресацию заказов, результат рисуется на схеме.</div>
      </div>
      <div class="card">${mapPanelHtml()}</div>
    </div>`;
}

/* ---------- события карты ---------- */
async function mapAction(action, id) {
  if (action === "node") {
    state.mapNode = id;
    render();
    return;
  }
  if (action === "reset") {
    state.mapNode = null;
    state.mapOrderId = null;
    state.mapFailure = null;
    render();
    return;
  }
  if (action === "open-pvz") {
    state.pvzId = id;
    state.page = "pvz";
    render();
    return;
  }
  if (action === "down") {
    if (!confirm("Вывести ПВЗ из строя? Свободные остатки переедут к соседям (перемещение 60 ₽), заказы будут переадресованы.")) return;
    const result = await guard(api(`/pvz/${id}/disable`, { method: "POST" }));
    if (!result) return;
    state.mapNode = id;
    state.mapFailure = result;
    await loadCatalog();
    const left = (result.unresolved || []).length;
    toast(
      `ПВЗ ${result.pvz.code} выведен из строя: перемещено партий ${(result.evacuated || []).length}, ` +
        `обработано заказов ${(result.orders || []).length}, не решено ${left}, затраты ${money(result.relocation_total_rub)}.`,
      left ? "" : "ok"
    );
    render();
    return;
  }
  if (action === "up") {
    const result = await guard(api(`/pvz/${id}/enable`, { method: "POST" }), "ПВЗ вернулся в строй");
    if (!result) return;
    state.mapNode = id;
    state.mapFailure = null;
    await loadCatalog();
    render();
  }
}

document.addEventListener("change", (event) => {
  if (event.target.id !== "map-order-select") return;
  state.mapOrderId = Number(event.target.value) || null;
  render();
});

/* Подсказки сегментов: делегирование, чтобы жить после перерисовок. */
let mapTipKey = null;
document.addEventListener("mousemove", (event) => {
  const holder = event.target.closest("[data-tipkey]");
  const tip = $("#map-tip");
  if (!tip) return;
  if (!holder) {
    if (mapTipKey) tip.hidden = true;
    mapTipKey = null;
    return;
  }
  const key = holder.dataset.tipkey;
  if (key !== mapTipKey) {
    mapTipKey = key;
    tip.innerHTML = MAP_TIPS[key] || "";
    tip.hidden = false;
  }
  const box = $("#netmap").getBoundingClientRect();
  const left = Math.min(event.clientX - box.left + 16, box.width - tip.offsetWidth - 8);
  const top = Math.min(event.clientY - box.top + 16, box.height - tip.offsetHeight - 8);
  tip.style.left = `${Math.max(4, left)}px`;
  tip.style.top = `${Math.max(4, top)}px`;
});

document.addEventListener("click", (event) => {
  const el = event.target.closest("[data-map-action]");
  if (!el) return;
  mapAction(el.dataset.mapAction, el.dataset.id ? Number(el.dataset.id) : null);
});
