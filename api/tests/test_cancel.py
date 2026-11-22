"""Отмена заказа до выдачи: резерв, ячейки, статусы."""
from conftest import create_order, issue_order


def _items(client, sku="TD-1001"):
    return [i for i in client.get("/api/v1/items").json() if i["sku"] == sku]


def test_cancel_routed_order_releases_reservation(client):
    order_id = create_order(client, qty=2)["order"]["id"]
    response = client.post(f"/api/v1/orders/{order_id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["order"]["status"] == "cancelled"

    item = _items(client)[0]
    assert item["qty"] == 6 and item["status"] == "stored" and item["available_qty"] == 6
    assert item["stored_pvz_id"] == 1


def test_cancel_after_assemble_moves_stock_back(client):
    order_id = create_order(client, qty=2)["order"]["id"]
    assert client.post(f"/api/v1/orders/{order_id}/assemble").status_code == 200

    pvz_after_assemble = {p["code"]: p["used_cells"] for p in client.get("/api/v1/pvz").json() if not p["is_hub"]}
    assert pvz_after_assemble["YOL-01"] == 31 and pvz_after_assemble["YOL-03"] == 32

    response = client.post(f"/api/v1/orders/{order_id}/cancel")
    assert response.status_code == 200, response.text

    pvz_after_cancel = {p["code"]: p["used_cells"] for p in client.get("/api/v1/pvz").json() if not p["is_hub"]}
    assert pvz_after_cancel["YOL-01"] == 35 and pvz_after_cancel["YOL-03"] == 28
    item = _items(client)[0]
    assert item["stored_pvz_id"] == 1 and item["status"] == "stored"


def test_cancel_is_idempotent(client):
    order_id = create_order(client)["order"]["id"]
    assert client.post(f"/api/v1/orders/{order_id}/cancel").status_code == 200
    second = client.post(f"/api/v1/orders/{order_id}/cancel")
    assert second.status_code == 200 and second.json()["idempotent_replay"] is True


def test_cancel_forbidden_after_transit_and_issue(client):
    order_id, _ = issue_order(client)
    response = client.post(f"/api/v1/orders/{order_id}/cancel")
    assert response.status_code == 409
    assert "issued" in response.json()["detail"]
