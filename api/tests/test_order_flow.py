"""Заказ, резерв, сборка, выдача и сходимость начислений."""
from conftest import create_order


def test_create_order_routes_mesh(client):
    data = create_order(client, item_id=1, qty=1, to_pvz=3)
    order = data["order"]
    assert order["status"] == "routed"
    assert order["route"]["kind"] == "mesh"
    assert order["route"]["total_cost_rub"] == 90.0
    assert order["buyer_total_rub"] == 19019.0  # 18900 + 119
    item = client.get("/api/v1/items/1").json()
    assert item["reserved_qty"] == 1
    assert item["available_qty"] == 5


def test_assemble_moves_cells(client):
    order_id = create_order(client)["order"]["id"]
    before_src = client.get("/api/v1/pvz/1").json()["used_cells"]
    before_dst = client.get("/api/v1/pvz/3").json()["used_cells"]
    response = client.post(f"/api/v1/orders/{order_id}/assemble")
    assert response.status_code == 200
    assert response.json()["order"]["status"] == "assembled"
    assert client.get("/api/v1/pvz/1").json()["used_cells"] == before_src - 2
    assert client.get("/api/v1/pvz/3").json()["used_cells"] == before_dst + 2
    item = client.get("/api/v1/items/1").json()
    assert item["stored_pvz_id"] == 3 and item["status"] == "assembled"


def test_assemble_is_idempotent(client):
    order_id = create_order(client)["order"]["id"]
    client.post(f"/api/v1/orders/{order_id}/assemble")
    cells = client.get("/api/v1/pvz/3").json()["used_cells"]
    ledger_after_first = client.get("/api/v1/ledger", params={"order_id": order_id}).json()
    second = client.post(f"/api/v1/orders/{order_id}/assemble")
    assert second.status_code == 200 and second.json()["idempotent_replay"] is True
    assert client.get("/api/v1/pvz/3").json()["used_cells"] == cells
    assert len(ledger_after_first) == len(client.get("/api/v1/ledger", params={"order_id": order_id}).json())


def test_issue_writes_ledger(client):
    order_id = create_order(client)["order"]["id"]
    client.post(f"/api/v1/orders/{order_id}/assemble")
    response = client.post(f"/api/v1/orders/{order_id}/issue")
    assert response.status_code == 200
    order = response.json()["order"]
    assert order["status"] == "issued"
    assert order["issued_at"] is not None and order["return_deadline"] is not None
    rows = client.get("/api/v1/ledger", params={"order_id": order_id}).json()
    kinds = {row["kind"] for row in rows}
    assert {"goods", "delivery", "commission", "pickup_fee"} <= kinds
    assert sum(row["amount_rub"] for row in rows) == -90.0  # только внешняя логистика


def test_insufficient_stock_409(client):
    response = client.post("/api/v1/orders", json={"item_id": 1, "qty": 99, "to_pvz_id": 3})
    assert response.status_code == 409
    assert "остат" in response.json()["detail"].lower()
    assert client.get("/api/v1/items/1").json()["reserved_qty"] == 0


def test_issue_idempotent_and_amounts(client):
    order_id = create_order(client)["order"]["id"]
    client.post(f"/api/v1/orders/{order_id}/assemble")
    client.post(f"/api/v1/orders/{order_id}/issue")
    first = client.get("/api/v1/ledger", params={"order_id": order_id}).json()
    second = client.post(f"/api/v1/orders/{order_id}/issue")
    assert second.status_code == 200 and second.json()["idempotent_replay"] is True
    assert len(client.get("/api/v1/ledger", params={"order_id": order_id}).json()) == len(first)
    by_party = {}
    for row in first:
        by_party[row["party"]] = round(by_party.get(row["party"], 0.0) + row["amount_rub"], 2)
    assert by_party["buyer"] == -19019.0
    assert by_party["seller"] == 16632.0  # 18900 − 12%
    assert by_party["pvz"] == 40.0  # 25 выдача + 15 mesh-бонус
    assert by_party["marketplace"] == 2257.0  # 2268 комиссия + 119 доставка − 90 логистика − 40 ПВЗ
    assert round(sum(by_party.values()), 2) == -90.0
