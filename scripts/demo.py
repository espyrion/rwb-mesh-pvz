#!/usr/bin/env python3
"""Прогон пяти демо-сценариев против запущенного API (backup-видео / проверка на стенде).

Запуск:
    python3 scripts/demo.py                    # http://localhost:8000
    API_URL=http://host:8000 python3 scripts/demo.py

Скрипт использует только стандартную библиотеку. Повторный запуск создаёт новые заказы.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/") + "/api/v1"
ROOT = API[: -len("/api/v1")]


def call(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        detail = error.read().decode()
        raise RuntimeError(f"{method} {path} → {error.code}: {detail}") from error


def title(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


def money(value) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def pvz_map() -> dict[str, dict]:
    return {row["code"]: row for row in call("/pvz?include_hub=true")}


def ensure_item(code: str, price: float = 18900.0, qty: int = 10) -> dict:
    """Берёт остаток нужной цены на ПВЗ; если его нет — принимает демо-партию (повторяемый прогон)."""
    for row in call("/items"):
        if row["stored_pvz_code"] == code and row["available_qty"] > 0 and abs(row["price_rub"] - price) < 0.01:
            return row
    zone = pvz_map()[code]
    return call("/items", "POST", {
        "sku": f"DEMO-{int(price)}",
        "seller_id": 1,
        "title": f"Демо-товар {int(price)} ₽",
        "qty": qty,
        "cell_size": 2,
        "pvz_id": zone["id"],
        "price_rub": price,
    })


def print_ledger(order_id: int) -> None:
    rows = call(f"/ledger?order_id={order_id}")
    print(f"  {'сторона':<12}{'операция':<14}{'сумма':>14}")
    for row in rows:
        print(f"  {row['party']:<12}{row['kind']:<14}{row['amount_rub']:>14,.2f}".replace(",", " "))
    total = sum(row["amount_rub"] for row in rows)
    print(f"  {'ИТОГО':<12}{'':<14}{total:>14,.2f}   (ровно −внешняя логистика)".replace(",", " "))


def main() -> int:
    try:
        with urllib.request.urlopen(ROOT + "/health", timeout=10) as response:
            health = json.loads(response.read().decode())
    except Exception as error:  # noqa: BLE001
        print(f"API недоступен: {error}\nЗапустите: docker compose up --build", file=sys.stderr)
        return 1
    print(f"API: {API} | health: {health}")

    zones = pvz_map()
    yolog, yol03, kzn = zones["YOL-01"], zones["YOL-03"], zones["KZN-01"]

    # 1. Городской заказ без хаба
    title("СЦЕНАРИЙ 1. Городской заказ без хаба")
    item = ensure_item("YOL-01")
    created = call("/orders", "POST", {"item_id": item["id"], "qty": 1, "to_pvz_id": yol03["id"]})
    order = created["order"]
    print(f"  Заказ {order['code']}: {order['from_code']} → {order['to_code']}")
    print(f"  Маршрут: {order['route']['kind']}, {money(order['route']['total_cost_rub'])}, {order['route']['total_hours']} ч")
    print(f"  Покупатель платит: {money(order['buyer_total_rub'])}")
    call(f"/orders/{order['id']}/assemble", "POST")
    issued = call(f"/orders/{order['id']}/issue", "POST")["order"]
    print(f"  Статус: {issued['status']}")
    print_ledger(order["id"])

    # 2. Межгород через хаб
    title("СЦЕНАРИЙ 2. Межгородской заказ через хаб")
    item = ensure_item("YOL-01")
    order = call("/orders", "POST", {"item_id": item["id"], "qty": 1, "to_pvz_id": kzn["id"]})["order"]
    segments = " · ".join(f"{seg['from_code']}→{seg['to_code']}" for seg in order["route"]["segments"])
    print(f"  Заказ {order['code']}: {segments}")
    print(f"  Маршрут: {order['route']['kind']}, {money(order['route']['total_cost_rub'])}, {order['route']['total_hours']} ч")
    for step in ("assemble", "transit", "issue"):
        order = call(f"/orders/{order['id']}/{step}", "POST")["order"]
        print(f"  {step}: {order['status']}")
    print(f"  Покупатель платит: {money(order['buyer_total_rub'])}")

    # 3. Возврат
    title("СЦЕНАРИЙ 3. Возврат после выдачи")
    item = ensure_item("YOL-01")
    order = call("/orders", "POST", {"item_id": item["id"], "qty": 1, "to_pvz_id": yol03["id"]})["order"]
    call(f"/orders/{order['id']}/assemble", "POST")
    call(f"/orders/{order['id']}/issue", "POST")
    returned = call(f"/orders/{order['id']}/return", "POST")
    print(f"  Заказ {returned['order']['code']}: {returned['order']['status']}")
    print(f"  Остаток вернулся: {returned['item']['qty']} шт. на ПВЗ id={returned['item']['pvz_id']}")
    print_ledger(order["id"])

    # 4. Логистическая ошибка
    title("СЦЕНАРИЙ 4. Логистическая ошибка и смена маршрута")
    item = ensure_item("YOL-01")
    order = call("/orders", "POST", {"item_id": item["id"], "qty": 1, "to_pvz_id": yol03["id"]})["order"]
    edge = next(row for row in call("/edges") if row["from_pvz"] == yolog["id"] and row["to_pvz"] == yol03["id"] and row["kind"] == "local")
    result = call(f"/edges/{edge['id']}/disable", "POST")
    incidents = result.get("incidents", [])
    updated = call(f"/orders/{order['id']}")
    print(f"  Отключён сегмент {edge['from_code']}→{edge['to_code']} (edge #{edge['id']})")
    print(f"  Инцидентов: {len(incidents)}")
    for row in incidents:
        incident = row["incident"]
        print(f"    #{incident['id']} {incident['type']} → {incident['status']}")
    print(f"  Заказ: {updated['status']}, новый маршрут {updated['route']['kind']}, {money(updated['route']['total_cost_rub'])}, {updated['route']['total_hours']} ч")
    call(f"/edges/{edge['id']}/enable", "POST")

    # 5. Длительное хранение
    title("СЦЕНАРИЙ 5. Длительное хранение и перемещение остатка")
    advanced = call("/clock/advance?days=25", "POST")
    print(f"  Виртуальное время: {advanced['virtual_now']}")
    print(f"  Начислено за хранение: {money(advanced['charged_total_rub'])}, уведомлений: {advanced['alerts']}")
    notes = call("/notifications?audience=pvz")
    alert = None
    for note in notes:
        payload = note.get("payload") or {}
        if payload.get("kind") != "long_storage" or not payload.get("offer"):
            continue
        stored = call(f"/items/{payload['item_id']}")
        if stored["status"] == "stored":
            alert = note
            break
    if alert:
        offer = alert["payload"]["offer"]
        print(f"  Предложение: остаток #{alert['payload']['item_id']} → {offer['suggested_code']} ({offer['cells']} яч., {money(offer['cost_rub'])})")
        moved = call(f"/items/{alert['payload']['item_id']}/relocate", "POST", {"to_pvz_id": offer["suggested_pvz_id"]})
        print(f"  Перемещено: {moved['relocation']['cells']} яч., затраты {money(moved['relocation']['cost_rub'])} на маркетплейс")
    else:
        print("  Предложений нет (все остатки уже в менее загруженных ПВЗ)")

    title("ГОТОВО")
    summary = call("/analytics/summary")
    print(f"  Заказов: {summary['orders']['total']} | статусы: {summary['orders']['by_status']}")
    print(f"  Логистика (внешние затраты): {money(summary['logistics_cost_rub'])}")
    print(f"  Сумма ledger по сторонам: {money(sum(summary['ledger'].values()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
