"""Эвакуация при отказе узла: split-fallback и перелив маршрута на магистраль."""
from __future__ import annotations

from conftest import create_order


def _item(client, sku: str, qty: int, cell_size: int, pvz_id: int) -> dict:
    response = client.post(
        "/api/v1/items",
        json={
            "sku": sku,
            "seller_id": 1,
            "title": sku,
            "qty": qty,
            "cell_size": cell_size,
            "pvz_id": pvz_id,
            "price_rub": 100.0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _rows(client, sku: str) -> list[dict]:
    return [row for row in client.get("/api/v1/items").json() if row["sku"] == sku]


def test_split_fallback_keeps_parent_batch(client):
    """Партия 60 ячеек не влезает целиком ни в одну точку Казани — делится по соседям."""
    item = _item(client, "SPLIT-1", 20, 3, 5)
    data = client.post("/api/v1/pvz/5/disable").json()
    assert data["unresolved"] == []

    rows = _rows(client, "SPLIT-1")
    assert len(rows) >= 2
    assert sum(row["qty"] for row in rows) == 20
    assert {row["stored_pvz_id"] for row in rows} <= {6, 7, 8}
    # Корневая строка сохраняет свой id, отщеплённые части ссылаются на неё.
    assert item["id"] in {row["id"] for row in rows}
    assert all(row["parent_batch_id"] in (None, item["id"]) for row in rows)
    assert any(row["parent_batch_id"] == item["id"] for row in rows)

    parts = [entry for entry in data["evacuated"] if entry["sku"] == "SPLIT-1"]
    assert len(parts) >= 2
    assert all(entry["split"] for entry in parts)
    assert sum(entry["qty"] for entry in parts) == 20


def test_split_fallback_reports_only_deficit(client):
    """В unresolved[] попадает только остаток, который город физически не вмещает."""
    _item(client, "FILL-6", 30, 1, 6)
    _item(client, "FILL-7", 20, 1, 7)
    _item(client, "FILL-8", 40, 1, 8)
    _item(client, "SPLIT-2", 60, 1, 5)

    data = client.post("/api/v1/pvz/5/disable").json()

    rows = _rows(client, "SPLIT-2")
    assert sum(row["qty"] for row in rows) == 60
    stayed = [row for row in rows if row["stored_pvz_id"] == 5]
    assert len(stayed) == 1 and stayed[0]["qty"] == 20
    placed = sum(row["qty"] for row in rows if row["stored_pvz_id"] in (6, 7, 8))
    assert placed == 40

    assert len(data["unresolved"]) == 1
    missing = data["unresolved"][0]
    assert missing["kind"] == "item"
    assert missing["sku"] == "SPLIT-2"
    assert missing["qty"] == 20
    assert data["incident"]["status"] == "open"


def test_overflow_route_goes_to_hub(client):
    """Точка выше порога загрузки — маршрут уходит на магистраль с причиной overflow_to_hub."""
    filler = _item(client, "FILL-1", 34, 1, 1)  # 35 + 34 = 69 из 80 = 86,25% > 85%

    compare = client.get("/api/v1/routes/compare", params={"from": 1, "to": 3}).json()
    assert compare["reason_code"] == "overflow_to_hub"
    assert compare["overflow"] is True
    assert compare["chosen"] == "hub"
    assert compare["load"]["triggered"] is True
    assert [entry["side"] for entry in compare["load"]["triggered_by"]] == ["from"]
    assert compare["load"]["threshold_pct"] == 85.0
    assert compare["load"]["from"]["utilization_pct"] == 86.2

    order = create_order(client, item_id=filler["id"], qty=1, to_pvz=3)["order"]
    assert order["route"]["reason_code"] == "overflow_to_hub"
    assert order["route"]["overflow"] is True
    assert order["route"]["total_cost_rub"] == 135.5

def test_evacuation_with_unplaced_order_keeps_total_qty(client):
    """Партия: заказ 20 шт. не влезает ни в одного соседа + свободный хвост 10 шт. — суммарный qty сохраняется."""
    for pid in (6, 7, 8):  # оставляем в каждом соседе по 12 свободных ячеек
        row = client.get(f"/api/v1/pvz/{pid}").json()
        _item(client, f"FILL-{pid}", row["capacity_cells"] - row["used_cells"] - 12, 1, pid)
    batch = _item(client, "ORD-TAIL", 30, 1, 5)
    create_order(client, item_id=batch["id"], qty=20, to_pvz=1)

    data = client.post("/api/v1/pvz/5/disable").json()

    rows = _rows(client, "ORD-TAIL")
    assert sum(row["qty"] for row in rows) == 30
    stayed = sum(row["qty"] for row in rows if row["stored_pvz_id"] == 5)
    placed = sum(row["qty"] for row in rows if row["stored_pvz_id"] != 5)
    assert stayed >= 20  # единицы под неразмещённым заказом остаются на исходной строке
    assert stayed + placed == 30
    assert data["unresolved"]
