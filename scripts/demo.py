#!/usr/bin/env python3
"""Демо-кокпит: 7 сценариев (5 обязательных по Приложению 1 п. 4 + 2 расширенных) с PASS/FAIL.

Запуск:
    python3 scripts/demo.py                          # http://localhost:8000
    API_URL=http://localhost:8001 MESH_API_KEY=demo-admin-key python3 scripts/demo.py
    python3 scripts/demo.py --write-report docs/SCENARIOS_REPORT.md

Обязательные:
    1. городской заказ       — mesh без хаба: приёмка → заказ → сборка → выдача;
    2. хаб и магистраль      — межгород; хаб и магистраль — заглушки модели, помечены;
    3. возврат               — создать → собрать → выдать → вернуть: обратный маршрут, остаток, refund;
    4. отказ узла            — disable: эвакуация, переадресация заказов, инцидент;
    5. длительное хранение   — приёмка → виртуальные часы > 21 дн → уведомление → перемещение остатка.
Расширенные:
    6. пик / overflow 85%    — загрузка у порога, маршрут уходит на хаб;
    7. восстановление узла   — enable: точка снова в строю.
Порядок выполнения безопасный: 1, 2, 3, 5, 6 (пик), 4 (отказ), 7 (восстановление).

Только стандартная библиотека. Скрипт идемпотентен и работает на «грязной» базе:
демо-партии прошлых прогонов выдаются обычным заказом и освобождают ячейки, пиковая
нагрузка создаётся переносом остатков внутри города (запас не растёт), пара ПВЗ
выбирается по фактической загрузке, SKU подбирается уникальный, отключённая точка
возвращается в строй.
Все числа печатаются из ответов API — своих значений скрипт не придумывает.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/") + "/api/v1"
API_KEY = os.environ.get("MESH_API_KEY", "demo-admin-key")
ROOT = API[: -len("/api/v1")]
WEB = os.environ.get("WEB_URL", "http://localhost:5173").rstrip("/")


def call(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("X-API-Key", API_KEY)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        detail = error.read().decode()
        raise RuntimeError(f"{method} {path} → {error.code}: {detail}") from error


def money(value) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def hours(value) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ч"


def pct(value) -> str:
    return f"{value:,.1f}".replace(",", " ").replace(".", ",") + "%"


def title(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


def pvz_list() -> list[dict]:
    return call("/pvz?include_hub=true")


def pick_pair(threshold: float | None = None) -> tuple[dict, dict] | None:
    """Две активные точки одного города: источник — загруженнее, приёмник — свободнее.

    Если задан порог загрузки, сначала ищем пару, где обе точки ниже порога —
    иначе шаг «спокойный день» физически не может пройти.
    """
    points = [row for row in pvz_list() if not row["is_hub"] and row["is_active"]]
    by_city: dict[str, list[dict]] = {}
    for row in points:
        by_city.setdefault(row["city"], []).append(row)
    candidates: list[tuple[dict, dict]] = []
    for rows in by_city.values():
        if len(rows) < 2:
            continue
        rows = sorted(rows, key=lambda row: row["utilization_pct"])
        for index, source in enumerate(rows[:-1]):
            for target in rows[index + 1:]:
                candidates.append((source, target))
    if not candidates:
        return None
    calm = [
        (source, target) for source, target in candidates
        if threshold is not None
        and source["utilization_pct"] < threshold
        and target["utilization_pct"] < threshold
        and target["free_cells"] > 0
    ]
    pool = calm or candidates
    # приёмник — самая свободная точка пары, источник — самая загруженная в том же городе
    best = max(pool, key=lambda pair: (pair[1]["free_cells"], -pair[0]["utilization_pct"]))
    source, target = best
    peers = [row for row in by_city[source["city"]] if row["id"] != target["id"]]
    source = max(peers or [source], key=lambda row: row["utilization_pct"])
    return source, target


def ensure_pair(threshold: float | None = None) -> tuple[dict, dict]:
    pair = pick_pair(threshold)
    if pair:
        return pair
    for row in pvz_list():
        if not row["is_hub"] and not row["is_active"]:
            call(f"/pvz/{row['id']}/enable", "POST")
    pair = pick_pair(threshold)
    if pair:
        return pair
    raise RuntimeError("нет двух активных ПВЗ в одном городе — стенд не пригоден для демо")


def utilization(point: dict) -> float:
    capacity = point["capacity_cells"] or 1
    return point["used_cells"] * 100.0 / capacity


def make_room(points: list[dict], threshold: float, exclude: set[int] | None = None) -> list[str]:
    """Если стенд уже перегружен, освобождает место реальными перемещениями остатков.

    Перемещение допускается только в пределах города и не в точки из текущей пары,
    иначе мы просто переложим перегрузку из одной точки в другую.
    """
    exclude = exclude or set()
    notes: list[str] = []
    pool = sorted(
        (
            row for row in pvz_list()
            if row["is_active"] and not row["is_hub"] and row["id"] not in exclude
        ),
        key=lambda row: -row["free_cells"],
    )
    for target in points:
        keep = max(0, int(target["capacity_cells"] * threshold // 100) - 1)
        cells_to_move = target["used_cells"] - keep
        if cells_to_move <= 0:
            continue
        before = utilization(target)
        moved, parts = 0, 0
        for item in call("/items"):
            if moved >= cells_to_move:
                break
            if item.get("status") != "stored" or item["stored_pvz_id"] != target["id"] or item["available_qty"] <= 0:
                continue
            dest = next(
                (
                    row for row in pool
                    if row["id"] != target["id"]
                    and row["city"] == target["city"]
                    and row["free_cells"] >= item["cells"]
                ),
                None,
            )
            if dest is None:
                continue
            call(f"/items/{item['id']}/relocate", "POST", {"to_pvz_id": dest["id"]})
            moved += item["cells"]
            parts += 1
            dest["free_cells"] -= item["cells"]
        fresh = call(f"/pvz/{target['id']}")
        notes.append(
            f"стенд был перегружен: {target['code']} {pct(before)} → {pct(utilization(fresh))}, "
            f"перемещено партий: {parts} ({moved} ячеек)"
        )
    return notes


def prepare_pair() -> tuple[tuple[dict, dict], list[str]]:
    base = ensure_pair()
    threshold = compare(base[0], base[1])["load"]["threshold_pct"]
    pair = pick_pair(threshold)
    notes: list[str] = []
    if pair is None:
        crowded = [row for row in base if utilization(row) >= threshold]
        notes = make_room(crowded, threshold, {base[0]["id"], base[1]["id"]})
        pair = pick_pair(threshold) or base
        pair = (call(f"/pvz/{pair[0]['id']}"), call(f"/pvz/{pair[1]['id']}"))
    return pair, notes


def compare(source: dict, target: dict) -> dict:
    return call(f"/routes/compare?from={source['id']}&to={target['id']}")


def route_line(row: dict) -> str:
    mesh = row["mesh"]
    return (
        f"mesh {'недоступен' if not mesh.get('available') else money(mesh['total_cost_rub']) + ' / ' + hours(mesh['total_hours'])}"
        f" → хаб {money(row['hub']['total_cost_rub'])} / {hours(row['hub']['total_hours'])}"
    )


def unique_sku(prefix: str) -> str:
    existing = {row["sku"] for row in call("/items")}
    sku, n = prefix, 1
    while sku in existing:
        n += 1
        sku = f"{prefix}-{n}"
    return sku


def goods_price() -> float:
    return call("/economics/scenario")["params"]["goods_rub"]


def cleanup_demo_stock() -> list[str]:
    """Выдаёт демо-партии прошлых прогонов, чтобы вернуть стенду запас ячеек.

    Без этого каждый прогон оставляет в городе лишние занятые ячейки и через
    несколько запусков «спокойный день» физически перестаёт проходить.
    """
    notes: list[str] = []
    points = {row["id"]: row for row in pvz_list() if row["is_active"] and not row["is_hub"]}
    issued_parts = 0
    issued_units = 0
    freed_cells = 0
    for item in call("/items"):
        if item["qty"] <= 0 or item["available_qty"] <= 0:
            continue
        sku = item["sku"] or ""
        title = item.get("title") or ""
        if not (sku.startswith(("PEAK-", "HUB-")) or "демо-кокпит" in title or "Демо-партия" in title):
            continue
        origin = points.get(item["stored_pvz_id"])
        if origin is None:
            continue
        remaining = int(item["available_qty"])
        attempts = 0
        while remaining > 0 and attempts < 12:
            attempts += 1
            dest = next(
                (
                    row for row in points.values()
                    if row["city"] != origin["city"] and row["free_cells"] >= 1
                ),
                None,
            )
            if dest is None:
                break
            chunk = min(remaining, int(dest["free_cells"]))
            try:
                order = call("/orders", "POST", {
                    "item_id": item["id"],
                    "qty": chunk,
                    "to_pvz_id": dest["id"],
                })["order"]
                call(f"/orders/{order['id']}/assemble", "POST")
                call(f"/orders/{order['id']}/transit", "POST")
                call(f"/orders/{order['id']}/issue", "POST")
            except RuntimeError:
                points.pop(dest["id"], None)
                continue
            fresh = call(f"/items/{item['id']}")
            remaining = int(fresh["available_qty"])
        issued_now = int(item["available_qty"]) - remaining
        if issued_now <= 0:
            continue
        issued_parts += 1
        issued_units += issued_now
        freed_cells += issued_now * int(item["cell_size"])
    if issued_parts:
        notes.append(
            f"выданы демо-партии прошлых прогонов: {issued_parts} шт., {issued_units} единиц, "
            f"освобождено {freed_cells} ячеек"
        )
    return notes


def scenario_peak(pair: tuple[dict, dict]) -> tuple[bool, list[str]]:
    source, target = pair
    row = compare(source, target)
    threshold = row["load"]["threshold_pct"]
    before = row["load"]["to"]["utilization_pct"]
    notes: list[str] = []
    if before < threshold:
        fresh = call(f"/pvz/{target['id']}")
        if not fresh["is_active"]:
            raise RuntimeError(f"{fresh['code']} выведена из строя — шаг «Пик / overflow 85%» невозможен")
        need = -(-int(fresh["capacity_cells"] * threshold) // 100) + 1 - fresh["used_cells"]
        # Сначала пробуем довести точку до порога переносом остатков внутри города:
        # так общий запас ячеек в городе не растёт и прогон остаётся повторяемым.
        moved_cells = 0
        moved_parts = 0
        cities = {point["id"]: point["city"] for point in pvz_list()}
        pool = [
            item for item in call("/items")
            if item.get("status") == "stored" and item.get("available_qty", 0) > 0
            and item["stored_pvz_id"] not in (None, fresh["id"])
            and cities.get(item["stored_pvz_id"]) == fresh["city"]
        ]
        pool.sort(key=lambda item: item["cells"])
        for item in pool:
            if moved_cells >= need:
                break
            room = call(f"/pvz/{fresh['id']}")["free_cells"]
            if item["cells"] > room:
                continue
            call(f"/items/{item['id']}/relocate", "POST", {"to_pvz_id": fresh["id"]})
            moved_cells += int(item["cells"])
            moved_parts += 1
        if moved_cells:
            notes.append(
                f"остатки внутри города перенесены на {fresh['code']}: партий {moved_parts}, "
                f"ячеек +{moved_cells} (запас города не вырос)"
            )
        if moved_cells < need:
            fresh = call(f"/pvz/{fresh['id']}")
            qty = min(max(1, need - moved_cells), fresh["free_cells"])
            if qty >= 1:
                item = call("/items", "POST", {
                    "sku": unique_sku(f"PEAK-{fresh['code']}"),
                    "seller_id": 1,
                    "title": "Пиковая партия (демо-кокпит)",
                    "qty": qty,
                    "cell_size": 1,
                    "pvz_id": fresh["id"],
                    "price_rub": goods_price(),
                })
                notes.append(
                    f"принята партия {item['sku']}: {item['qty']} шт. × {item['cell_size']} яч. "
                    f"(+{item['qty'] * item['cell_size']} ячеек)"
                )
        row = compare(source, target)
    else:
        notes.append("загрузка уже не ниже порога — новая нагрузка не понадобилась")
    after = row["load"]["to"]["utilization_pct"]
    triggers = ", ".join(
        f"{part['side']}:{part['code']} {pct(part['utilization_pct'])}" for part in row["load"]["triggered_by"]
    ) or "нет"
    ok = row["load"]["triggered"] is True and row["overflow"] is True and row["reason_code"] == "overflow_to_hub"
    return ok, notes + [
        f"загрузка {row['load']['to']['code']}: {pct(before)} → {pct(after)} (порог {pct(threshold)})",
        f"триггеры overflow: {triggers}",
        f"маршрут: {row['chosen']} ({row['reason_code']}); {route_line(row)}",
    ]



# ---------------------------------------------------------------- обязательные сценарии

PARTIES = ("buyer", "pvz", "seller", "marketplace")


def ledger_totals() -> dict[str, float]:
    totals = {party: 0.0 for party in PARTIES}
    for row in call("/ledger"):
        totals[row["party"]] = round(totals.get(row["party"], 0.0) + row["amount_rub"], 2)
    return totals


def ledger_text(before: dict, after: dict) -> str:
    return "; ".join(
        f"{party}: {money(before[party])} → {money(after[party])}" for party in PARTIES
    )


def accept(point: dict, prefix: str, title_text: str, qty: int = 1) -> dict:
    return call("/items", "POST", {
        "sku": unique_sku(f"{prefix}-{point['code']}"),
        "seller_id": 1,
        "title": f"Демо-партия ТЗ: {title_text}",
        "qty": qty,
        "cell_size": 1,
        "pvz_id": point["id"],
        "price_rub": goods_price(),
    })


def stock(item_id: int) -> str:
    row = call(f"/items/{item_id}")
    return f"qty {row['qty']}, резерв {row['reserved_qty']}, статус {row['status']}, {row['stored_pvz_code']}"


def cells(point_id: int) -> str:
    row = call(f"/pvz/{point_id}")
    return f"{row['code']} {row['used_cells']}/{row['capacity_cells']}"


def route_facts(order: dict) -> str:
    route = order["route"]
    segments = " → ".join(
        [route["segments"][0]["from_code"]] + [seg["to_code"] for seg in route["segments"]]
    ) if route["segments"] else "нет данных"
    return f"{route['kind']} ({route['reason_code']}): {segments}; {money(route['total_cost_rub'])} / {hours(route['total_hours'])}"


def scenario_city(pair):
    source, target = pair
    item = accept(source, "CITY", "городской заказ")
    facts = {"Товар": f"id {item['id']}, {item['sku']}"}
    facts["Остаток до"] = f"{stock(item['id'])}; ячейки {cells(source['id'])}, {cells(target['id'])}"
    before = ledger_totals()
    order = call("/orders", "POST", {"item_id": item["id"], "qty": 1, "to_pvz_id": target["id"]})["order"]
    statuses = [order["status"]]
    for step in ("assemble", "issue"):
        order = call(f"/orders/{order['id']}/{step}", "POST")["order"]
        statuses.append(order["status"])
    after = ledger_totals()
    facts["Заказ"] = f"id {order['id']}, {order['code']}"
    facts["Статусы"] = " → ".join(statuses)
    facts["Маршрут и стоимость"] = route_facts(order) + f"; покупатель платит {money(order['buyer_total_rub'])}"
    facts["Остаток после"] = f"{stock(item['id'])}; ячейки {cells(source['id'])}, {cells(target['id'])}"
    facts["Начисления до → после"] = ledger_text(before, after)
    ok = order["route"]["kind"] == "mesh" and order["status"] == "issued" and not any(
        seg["kind"] == "hub" for seg in order["route"]["segments"])
    lines = [f"{k}: {v}" for k, v in facts.items()]
    return ok, lines, facts


def scenario_intercity(pair):
    source, _ = pair
    item = accept(source, "HUB", "межгород")
    dest = next(
        row for row in pvz_list()
        if row["is_active"] and not row["is_hub"] and row["city"] != source["city"]
    )
    facts = {"Товар": f"id {item['id']}, {item['sku']}"}
    facts["Остаток до"] = f"{stock(item['id'])}; ячейки {cells(source['id'])}, {cells(dest['id'])}"
    before = ledger_totals()
    order = call("/orders", "POST", {"item_id": item["id"], "qty": 1, "to_pvz_id": dest["id"]})["order"]
    statuses = [order["status"]]
    for step in ("assemble", "transit", "issue"):
        order = call(f"/orders/{order['id']}/{step}", "POST")["order"]
        statuses.append(order["status"])
    after = ledger_totals()
    facts["Заказ"] = f"id {order['id']}, {order['code']} ({source['code']} → {dest['code']}, {dest['city']})"
    facts["Статусы"] = " → ".join(statuses)
    facts["Маршрут и стоимость"] = route_facts(order) + f"; покупатель платит {money(order['buyer_total_rub'])}"
    facts["Заглушки"] = "хаб и магистраль — параметры модели (economics/params.yaml), не реальный перевозчик"
    facts["Остаток после"] = f"{stock(item['id'])}; ячейки {cells(source['id'])}, {cells(dest['id'])}"
    facts["Начисления до → после"] = ledger_text(before, after)
    ok = order["route"]["kind"] == "hub" and order["status"] == "issued"
    return ok, [f"{k}: {v}" for k, v in facts.items()], facts


def scenario_return(pair):
    source, target = pair
    item = accept(source, "RET", "возврат")
    facts = {"Товар": f"id {item['id']}, {item['sku']}"}
    facts["Остаток до"] = f"{stock(item['id'])}; ячейки {cells(source['id'])}, {cells(target['id'])}"
    before = ledger_totals()
    order = call("/orders", "POST", {"item_id": item["id"], "qty": 1, "to_pvz_id": target["id"]})["order"]
    statuses = [order["status"]]
    for step in ("assemble", "issue"):
        order = call(f"/orders/{order['id']}/{step}", "POST")["order"]
        statuses.append(order["status"])
    mid = ledger_totals()
    result = call(f"/orders/{order['id']}/return", "POST")
    statuses.append(result["order"]["status"])
    after = ledger_totals()
    rows = [r for r in call("/ledger") if r["order_id"] == order["id"]]
    refunds = [r for r in rows if r["kind"] == "refund"]
    facts["Заказ"] = f"id {order['id']}, {order['code']}"
    facts["Статусы"] = " → ".join(statuses)
    facts["Маршрут прямой"] = route_facts(order)
    facts["Обратный маршрут"] = f"id {result['order'].get('return_route_id')} ({target['code']} → {source['code']})"
    facts["Остаток после"] = f"{stock(item['id'])}; ячейки {cells(source['id'])}, {cells(target['id'])}"
    facts["Проводки refund"] = f"{len(refunds)} шт., сумма по сторонам: " + ", ".join(
        f"{r['party']} {money(r['amount_rub'])}" for r in refunds)
    facts["Ledger заказа"] = f"{len(rows)} проводок, сумма {money(round(sum(r['amount_rub'] for r in rows), 2))}"
    facts["Начисления до → после"] = ledger_text(before, after)
    ok = (
        result["order"]["status"] == "returned" and result["item"]["qty"] == 1
        and result["item"]["pvz_id"] == source["id"] and len(refunds) >= 3
    )
    return ok, [f"{k}: {v}" for k, v in facts.items()], facts


def scenario_storage(pair):
    source, _ = pair
    item = accept(source, "STORE", "длительное хранение")
    threshold = 21
    facts = {"Товар": f"id {item['id']}, {item['sku']}"}
    facts["Остаток до"] = f"{stock(item['id'])}; ячейки {cells(source['id'])}"
    before = ledger_totals()
    clock_before = call("/clock")["virtual_now"]
    advance = call("/clock/advance?days=22", "POST")
    notes = call("/notifications?audience=pvz")
    alert = next(
        (n for n in notes
         if (n.get("payload") or {}).get("kind") == "long_storage" and n["payload"].get("item_id") == item["id"]),
        None,
    )
    facts["Виртуальные часы"] = f"{clock_before} → {advance['virtual_now']} (+22 дн., порог {threshold} дн.)"
    facts["Начислено хранение"] = f"{money(advance['charged_total_rub'])} по стенду, алертов: {advance['alerts']}"
    ok = alert is not None and alert["payload"].get("offer") is not None
    if alert is None:
        facts["Уведомление"] = "не найдено"
        return False, [f"{k}: {v}" for k, v in facts.items()], facts
    offer = alert["payload"]["offer"]
    facts["Уведомление"] = alert.get("text") or alert.get("message") or "long_storage"
    moved = call(f"/items/{item['id']}/relocate", "POST", {"to_pvz_id": offer["suggested_pvz_id"]})
    after = ledger_totals()
    rel = moved["relocation"]
    facts["Перемещение"] = f"{offer['from_code']} → {offer['suggested_code']}, {money(rel['cost_rub'])}, {rel['cells']} яч."
    facts["Остаток после"] = f"{stock(item['id'])}; ячейки {cells(source['id'])}, {cells(offer['suggested_pvz_id'])}"
    facts["Начисления до → после"] = ledger_text(before, after)
    facts["Заказ"] = "не требуется (сценарий про остаток)"
    return ok, [f"{k}: {v}" for k, v in facts.items()], facts


STATE: dict = {}


def scenario_down(pair):
    source, target = pair
    item = accept(target, "FAIL", "отказ узла", qty=2)
    facts = {"Товар": f"id {item['id']}, {item['sku']}"}
    order = call("/orders", "POST", {"item_id": item["id"], "qty": 1, "to_pvz_id": source["id"]})["order"]
    STATE["down_order"] = order["id"]
    facts["Заказ"] = f"id {order['id']}, {order['code']}, статус {order['status']}, {route_facts(order)}"
    facts["Остаток до"] = f"{stock(item['id'])}; ячейки {cells(target['id'])}"
    before = ledger_totals()
    result = call(f"/pvz/{target['id']}/disable", "POST")
    after = ledger_totals()
    fresh = call(f"/pvz/{target['id']}")
    moved = call(f"/orders/{order['id']}")
    evacuated = result.get("evacuated") or []
    orders = result.get("orders") or []
    unresolved = result.get("unresolved") or []
    parts = [p for p in call("/items") if p["id"] == item["id"] or p.get("parent_batch_id") == item["id"]]
    facts["Узел"] = f"{result['pvz']['code']}: {'выведена из строя' if not fresh['is_active'] else 'в строю'}"
    facts["Эвакуация"] = (
        f"партий {len(evacuated)}, ячеек {sum(int(x.get('cells') or 0) for x in evacuated)}, "
        f"{money(result.get('relocation_total_rub') or 0)}"
    )
    facts["Переадресация"] = f"заказов {len(orders)}, не решено {len(unresolved)}; статус заказа теперь {moved['status']}"
    facts["Инцидент"] = f"#{result['incident']['id']}" if result.get("incident") else "нет"
    facts["Остаток после"] = "; ".join(f"id {p['id']}: qty {p['qty']}, {p['status']}, {p['stored_pvz_code']}" for p in parts) + f"; ячейки {cells(target['id'])}"
    facts["Начисления до → после"] = ledger_text(before, after)
    ok = fresh["is_active"] is False and not unresolved and bool(evacuated)
    return ok, [f"{k}: {v}" for k, v in facts.items()], facts


def scenario_up(pair):
    source, target = pair
    result = call(f"/pvz/{target['id']}/enable", "POST")
    fresh = call(f"/pvz/{target['id']}")
    lines = [
        f"{result['pvz']['code']}: {'вернулась в строй' if fresh['is_active'] else 'не активировалась'}",
        f"занято {fresh['used_cells']} из {fresh['capacity_cells']} ячеек, свободно {fresh['free_cells']}",
    ]
    order_id = STATE.pop("down_order", None)
    if order_id:
        try:
            call(f"/orders/{order_id}/cancel", "POST")
            lines.append(f"демо-заказ {order_id} отменён, резерв снят")
        except RuntimeError as error:
            lines.append(f"демо-заказ {order_id} не отменён: {error}")
    try:
        row = compare(source, target)
        lines.append(f"маршрут {row['chosen']} ({row['reason_code']}), overflow={row['overflow']}")
        lines.append(f"стоимость: {route_facts_row(row)}")
    except RuntimeError as error:
        lines.append(f"маршрут не рассчитан: {error}")
    return fresh["is_active"] is True and result["pvz"]["is_active"] is True, lines, {}


def route_facts_row(row: dict) -> str:
    return route_line(row)


def scenario_peak_ext(pair):
    ok, lines = scenario_peak(pair)
    return ok, lines, {}


# (номер, название, обязательный, функция); порядок выполнения — EXEC_ORDER
SCENARIOS = [
    (1, "Городской заказ (mesh без хаба)", True, scenario_city),
    (2, "Хаб и магистраль (межгород)", True, scenario_intercity),
    (3, "Возврат", True, scenario_return),
    (4, "Ошибка в логистике / отказ узла", True, scenario_down),
    (5, "Длительное хранение", True, scenario_storage),
    (6, "Пик / overflow 85%", False, scenario_peak_ext),
    (7, "Восстановление узла", False, scenario_up),
]
EXEC_ORDER = [1, 2, 3, 5, 6, 4, 7]


def write_report(path: str, results: dict, health: dict, passed: int) -> None:
    out = [
        "# Отчёт о прогоне сценариев ТЗ",
        "",
        "Сформирован `scripts/demo.py --write-report` реальным прогоном против API "
        f"(`{ROOT}`, чистая БД). Все значения взяты из ответов API.",
        "",
        f"**Итог: {passed}/{len(SCENARIOS)}** · `GET /api/v1/health/deep` → **{health.get('status')}**",
        "",
        "| № | Сценарий | Тип | Результат |",
        "|---|---|---|---|",
    ]
    for num, name, mandatory, _ in SCENARIOS:
        ok = results[num]["ok"]
        out.append(f"| {num} | {name} | {'обязательный' if mandatory else 'расширенный'} | {'PASS' if ok else 'FAIL'} |")
    out.append("")
    for num, name, mandatory, _ in SCENARIOS:
        res = results[num]
        out += [f"## Сценарий {num}. {name} — {'PASS' if res['ok'] else 'FAIL'}", ""]
        if res["facts"]:
            out += ["| Параметр | Значение |", "|---|---|"]
            out += [f"| {k} | {str(v).replace('|', '/')} |" for k, v in res["facts"].items()]
        else:
            out += [f"- {line}" for line in res["lines"]]
        out.append("")
    out += ["## Проверка инвариантов", ""]
    out += [f"- {c['name']}: {'ok' if c['ok'] else 'НАРУШЕНО'} ({c['detail']})" for c in health.get("checks", [])]
    out.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


def main() -> int:
    report_path = None
    if "--write-report" in sys.argv:
        report_path = sys.argv[sys.argv.index("--write-report") + 1]
    try:
        with urllib.request.urlopen(ROOT + "/health", timeout=10) as response:
            health = json.loads(response.read().decode())
    except Exception as error:  # noqa: BLE001
        print(f"API недоступен: {error}\nЗапустите стенд или проверьте API_URL", file=sys.stderr)
        return 1
    print(f"API: {API} | health: {health}")

    cleanup_notes = cleanup_demo_stock()
    pair, stand_notes = prepare_pair()
    stand_notes = cleanup_notes + stand_notes
    print(f"Пара для сценариев: {pair[0]['code']} → {pair[1]['code']} ({pair[1]['city']})")
    for note in stand_notes:
        print(f"  подготовка стенда: {note}")

    by_num = {num: (name, mandatory, runner) for num, name, mandatory, runner in SCENARIOS}
    results: dict = {}
    for num in EXEC_ORDER:
        name, mandatory, runner = by_num[num]
        title(f"СЦЕНАРИЙ {num}. {name}" + ("" if mandatory else " (расширенный)"))
        try:
            ok, lines, facts = runner(pair)
        except Exception as error:  # noqa: BLE001
            ok, lines, facts = False, [f"исключение: {error}"], {}
        results[num] = {"ok": ok, "lines": lines, "facts": facts}
        print(f"  [{num}/{len(SCENARIOS)}] {name}: {'PASS' if ok else 'FAIL'}")
        for line in lines:
            print(f"      {line}")

    passed = sum(1 for r in results.values() if r["ok"])
    failed = [by_num[n][0] for n in sorted(results) if not results[n]["ok"]]
    try:
        deep = call("/health/deep")
    except RuntimeError as error:
        deep = {"status": f"ошибка: {error}", "checks": []}
    title("ИТОГ")
    print(f"  итог: {passed}/{len(SCENARIOS)}")
    print(f"  health/deep: {deep.get('status')}")
    if failed:
        print("  провалено: " + ", ".join(failed))
    print(f"  интерфейс: {WEB}/#demo · ссылка покупателя: {WEB}/?track=<token заказа>")
    if report_path:
        write_report(report_path, results, deep, passed)
        print(f"  отчёт: {report_path}")
    return 0 if not failed and deep.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
