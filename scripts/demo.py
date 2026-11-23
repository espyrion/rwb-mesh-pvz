#!/usr/bin/env python3
"""Демо-кокпит: пять сценариев против запущенного API с PASS/FAIL и итогом.

Запуск:
    python3 scripts/demo.py                          # http://localhost:8000
    API_URL=http://localhost:8001 MESH_API_KEY=demo-admin-key python3 scripts/demo.py

Сценарии (те же, что в разделе «Демо-кокпит» интерфейса):
    1. спокойный день        — внутригородской маршрут идёт по mesh без overflow;
    2. пиковая нагрузка      — приёмка партии поднимает загрузку до порога, маршрут уходит на хаб;
    3. отказ узла            — disable: эвакуация остатков и переадресация заказов;
    4. восстановление        — enable: точка снова в строю, маршрут рассчитывается;
    5. переключение на хаб   — межгород: локального маршрута нет, работает хаб.

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


def scenario_calm(pair: tuple[dict, dict]) -> tuple[bool, list[str]]:
    source, target = pair
    row = compare(source, target)
    ok = row["chosen"] == "mesh" and row["mesh"]["available"] is True and row["overflow"] is False
    return ok, [
        f"{row['from']['code']} → {row['to']['code']}: выбрано «{row['chosen']}», причина {row['reason_code']}, overflow={row['overflow']}",
        route_line(row),
        f"загрузка: {row['load']['from']['code']} {pct(row['load']['from']['utilization_pct'])}, "
        f"{row['load']['to']['code']} {pct(row['load']['to']['utilization_pct'])} (порог {pct(row['load']['threshold_pct'])})",
    ]


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


def scenario_down(pair: tuple[dict, dict]) -> tuple[bool, list[str]]:
    _, target = pair
    result = call(f"/pvz/{target['id']}/disable", "POST")
    fresh = call(f"/pvz/{target['id']}")
    evacuated = result.get("evacuated") or []
    orders = result.get("orders") or []
    unresolved = result.get("unresolved") or []
    cells = sum(int(part.get("cells") or 0) for part in evacuated)
    ok = fresh["is_active"] is False and result["pvz"]["is_active"] is False
    return ok, [
        f"{result['pvz']['code']}: {'выведена из строя' if not fresh['is_active'] else 'осталась в строю'}"
        f"{' (уже была выведена)' if result.get('already_down') else ''}",
        f"эвакуировано партий: {len(evacuated)}, ячеек: {cells}, стоимость перемещений {money(result.get('relocation_total_rub') or 0)}",
        f"переадресовано заказов: {len(orders)}, не решено: {len(unresolved)}, "
        f"инцидент {'#' + str(result['incident']['id']) if result.get('incident') else 'нет'}",
        f"после отказа: занято {fresh['used_cells']} из {fresh['capacity_cells']} ячеек, свободно {fresh['free_cells']}",
    ]


def scenario_up(pair: tuple[dict, dict]) -> tuple[bool, list[str]]:
    source, target = pair
    result = call(f"/pvz/{target['id']}/enable", "POST")
    fresh = call(f"/pvz/{target['id']}")
    lines = [
        f"{result['pvz']['code']}: {'вернулась в строй' if fresh['is_active'] else 'не активировалась'}",
        f"занято {fresh['used_cells']} из {fresh['capacity_cells']} ячеек, свободно {fresh['free_cells']}",
    ]
    try:
        row = compare(source, target)
        lines.append(f"маршрут {row['chosen']} ({row['reason_code']}), overflow={row['overflow']}")
        lines.append(f"стоимость: {route_line(row)}")
    except RuntimeError as error:
        lines.append(f"маршрут не рассчитан: {error}")
    return fresh["is_active"] is True and result["pvz"]["is_active"] is True, lines


def scenario_hub(pair: tuple[dict, dict]) -> tuple[bool, list[str]]:
    source, _ = pair
    active = {row["id"] for row in pvz_list() if row["is_active"]}
    item = None
    for row in call("/items"):
        if row["stored_pvz_id"] in active and row["available_qty"] > 0:
            item = row
            break
    note = ""
    if item is None:
        home = next(row for row in pvz_list() if row["id"] == source["id"])
        item = call("/items", "POST", {
            "sku": unique_sku(f"HUB-{home['code']}"),
            "seller_id": 1,
            "title": "Демо-партия для межгорода",
            "qty": 1,
            "cell_size": 1,
            "pvz_id": home["id"],
            "price_rub": goods_price(),
        })
        note = f"принята демо-партия {item['sku']} на {home['code']}"
    origin = call(f"/pvz/{item['stored_pvz_id']}")
    dest = next(
        row for row in pvz_list()
        if row["is_active"] and not row["is_hub"] and row["city"] != origin["city"]
    )
    order = call("/orders", "POST", {"item_id": item["id"], "qty": 1, "to_pvz_id": dest["id"]})["order"]
    route = order["route"]
    segments = ", ".join(f"{seg['from_code']}→{seg['to_code']} {seg['kind']}" for seg in route["segments"])
    ok = route["kind"] == "hub" and order["status"] in {"routed", "rerouted"}
    lines = []
    if note:
        lines.append(note)
    lines += [
        f"заказ {order['code']}: {origin['code']} → {dest['code']} ({dest['city']}), статус {order['status']}",
        f"маршрут {route['kind']}, причина {route['reason_code']}, {money(route['total_cost_rub'])} / {hours(route['total_hours'])}",
        f"плечи: {segments or 'нет данных'}",
        f"покупатель платит {money(order['buyer_total_rub'])}",
    ]
    return ok, lines


SCENARIOS = [
    ("1. Спокойный день", scenario_calm),
    ("2. Пиковая нагрузка", scenario_peak),
    ("3. Отказ узла", scenario_down),
    ("4. Восстановление", scenario_up),
    ("5. Переключение на хаб", scenario_hub),
]


def main() -> int:
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

    passed = 0
    failed: list[str] = []
    for index, (name, runner) in enumerate(SCENARIOS, start=1):
        title(f"СЦЕНАРИЙ {name}")
        try:
            ok, lines = runner(pair)
        except Exception as error:  # noqa: BLE001
            ok, lines = False, [f"исключение: {error}"]
        status = "PASS" if ok else "FAIL"
        print(f"  [{index}/{len(SCENARIOS)}] {name}: {status}")
        for line in lines:
            print(f"      {line}")
        if ok:
            passed += 1
        else:
            failed.append(name)

    title("ИТОГ")
    print(f"  итог: {passed}/{len(SCENARIOS)}")
    if failed:
        print("  провалено: " + ", ".join(failed))
    print(f"  интерфейс: {WEB}/#demo · ссылка покупателя: {WEB}/?track=<token заказа>")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())