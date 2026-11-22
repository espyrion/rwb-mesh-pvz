"""Возвраты: остатки, начисления, срок возврата."""
from conftest import create_order, issue_order


def test_return_restores_stock_and_ledger(client):
    order_id, _ = issue_order(client)
    assert client.get("/api/v1/items/1").json()["qty"] == 5
    response = client.post(f"/api/v1/orders/{order_id}/return")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["order"]["status"] == "returned"
    assert data["reverse_route"]["kind"] == "mesh"
    assert data["item"]["qty"] == 6 and data["item"]["status"] == "returned"
    assert data["item"]["pvz_id"] == 1
    rows = client.get("/api/v1/ledger", params={"order_id": order_id}).json()
    kinds = [row["kind"] for row in rows]
    assert "refund" in kinds and "penalty" in kinds
    assert sum(row["amount_rub"] for row in rows) == -180.0  # 90 вперёд + 90 обратно


def test_return_after_deadline_409(client):
    order_id, _ = issue_order(client)
    client.post("/api/v1/clock/advance", params={"days": 8})
    response = client.post(f"/api/v1/orders/{order_id}/return")
    assert response.status_code == 409
    assert "срок" in response.json()["detail"].lower()
    assert client.get(f"/api/v1/orders/{order_id}").json()["status"] == "issued"


def test_return_requires_issued(client):
    order_id = create_order(client)["order"]["id"]
    response = client.post(f"/api/v1/orders/{order_id}/return")
    assert response.status_code == 409
    assert "выданного" in response.json()["detail"]
