"""Отказ узла: эвакуация остатков, переадресация заказов, возврат в строй."""
from conftest import create_order


def _pvz(client, pvz_id):
    return client.get(f"/api/v1/pvz/{pvz_id}").json()


def _items_at(client, pvz_id):
    rows = client.get("/api/v1/items").json()
    return [r for r in rows if r["stored_pvz_id"] == pvz_id]


def test_node_down_evacuates_free_stock(client):
    """Свободные партии уезжают к соседям с наименьшей загрузкой, 60 ₽ за перемещение."""
    before = client.get("/api/v1/health/deep").json()
    assert before["status"] == "ok"

    response = client.post("/api/v1/pvz/1/disable")
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["pvz"]["is_active"] is False
    assert data["already_down"] is False
    assert data["unresolved"] == []
    # TD-1001 (12 яч.) и SM-2002 (15 яч.) — оба эвакуированы.
    assert len(data["evacuated"]) == 2
    assert data["relocation_total_rub"] == 120.0
    assert data["incident"]["status"] == "resolved"
    assert data["incident"]["type"] == "node_down"

    # YOL-04 был наименее загружен (17/50), туда ушёл первый остаток;
    # затем YOL-03 (28/70) — меньше загрузки остальных.
    targets = {row["sku"]: row["to_pvz_id"] for row in data["evacuated"]}
    assert targets == {"TD-1001": 4, "SM-2002": 3}

    assert _pvz(client, 1)["used_cells"] == 8  # остались только чужие партии
    assert _pvz(client, 3)["used_cells"] == 43
    assert _pvz(client, 4)["used_cells"] == 29

    ledger = client.get("/api/v1/ledger", params={"kind": "relocation"}).json()
    assert len(ledger) == 2
    assert sum(row["amount_rub"] for row in ledger) == -120.0

    after = client.get("/api/v1/health/deep").json()
    assert after["status"] == "ok"


def test_node_down_reassigns_pickup_point(client):
    """Заказ с выдачей в отказавшем узле переадресуется, покупатель уведомлён."""
    order_id = create_order(client, item_id=1, to_pvz=3)["order"]["id"]

    data = client.post("/api/v1/pvz/3/disable").json()
    assert data["unresolved"] == []

    order = client.get(f"/api/v1/orders/{order_id}").json()
    assert order["to_pvz"] == 4  # наименее загруженный сосед, не источник
    assert order["status"] == "rerouted"
    assert order["route"]["total_cost_rub"] == 90.0

    notes = client.get("/api/v1/notifications").json()
    assert any(n["payload"].get("kind") == "pickup_reassigned" for n in notes)


def test_node_down_moves_source_batch(client):
    """Партия-источник заказа переезжает целиком, заказ стартует с нового места."""
    order_id = create_order(client, item_id=2, to_pvz=1)["order"]["id"]

    data = client.post("/api/v1/pvz/2/disable").json()
    assert data["unresolved"] == []

    order = client.get(f"/api/v1/orders/{order_id}").json()
    assert order["from_pvz"] == 3  # YOL-03 — наименее загруженный сосед, не цель
    assert order["to_pvz"] == 1
    assert order["status"] == "rerouted"
    assert order["route"]["total_cost_rub"] == 90.0

    rows = [r for r in client.get("/api/v1/items").json() if r["sku"] == "TD-1002"]
    assert {r["stored_pvz_id"] for r in rows} == {3}
    assert sorted(r["qty"] for r in rows) == [4]  # партия переехала целиком, без дробления
    # Два перемещения: сама партия заказа и термокружки.
    assert len(data["evacuated"]) == 2
    assert data["relocation_total_rub"] == 120.0


def test_node_up_restores_routing(client):
    client.post("/api/v1/pvz/3/disable")
    assert _pvz(client, 3)["is_active"] is False

    response = client.post("/api/v1/pvz/3/enable")
    assert response.status_code == 200
    assert response.json()["pvz"]["is_active"] is True

    created = create_order(client, item_id=1, to_pvz=3)
    assert created["order"]["to_pvz"] == 3


def test_no_capacity_leaves_incident_open(client):
    """Если у соседей нет ёмкости — остаток остаётся, сводный инцидент открыт."""
    for pvz_id, free in ((2, 15), (3, 42), (4, 33)):
        response = client.post(
            "/api/v1/items",
            json={
                "sku": f"FILL-{pvz_id}",
                "seller_id": 1,
                "title": f"Заполнитель {pvz_id}",
                "qty": free,
                "cell_size": 1,
                "pvz_id": pvz_id,
                "price_rub": 100.0,
            },
        )
        assert response.status_code == 201, response.text

    data = client.post("/api/v1/pvz/1/disable").json()
    assert len(data["unresolved"]) == 2
    assert all(row["kind"] == "item" for row in data["unresolved"])
    assert data["incident"]["status"] == "open"
    assert data["relocation_total_rub"] == 0.0

    skus = {row["sku"] for row in _items_at(client, 1)}
    assert skus == {"TD-1001", "SM-2002"}  # остатки не брошены, но ждут оператора


def test_assembled_order_follows_reassigned_pickup(client):
    """Заказ в сборке переезжает вместе с изменённой точкой выдачи."""
    order_id = create_order(client, item_id=3, qty=1, to_pvz=4)["order"]["id"]
    assert client.post(f"/api/v1/orders/{order_id}/assemble").status_code == 200

    data = client.post("/api/v1/pvz/4/disable").json()
    assert data["unresolved"] == []

    order = client.get(f"/api/v1/orders/{order_id}").json()
    assert order["to_pvz"] == 1
    assert order["status"] == "assembled"
    rows = [r for r in client.get("/api/v1/items").json() if r["sku"] == "TD-1003"]
    picked = [r for r in rows if r["stored_pvz_id"] == 1]
    assert len(picked) == 1 and picked[0]["qty"] == 1  # собранная единица переехала с точкой выдачи

    assert client.post(f"/api/v1/orders/{order_id}/issue").status_code == 200
    assert client.get(f"/api/v1/orders/{order_id}").json()["status"] == "issued"
