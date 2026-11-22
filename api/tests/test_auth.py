"""Роли и API-ключи: продавец, ПВЗ, маркетплейс."""
from fastapi.testclient import TestClient

from app.auth import key_for
from app.main import app
from conftest import create_order


def _anonymous() -> TestClient:
    return TestClient(app)


def _as(role: str) -> TestClient:
    client = TestClient(app)
    client.headers.update({"X-API-Key": key_for(role)})
    return client


def test_write_without_key_is_401(client):
    response = _anonymous().post("/api/v1/orders", json={"item_id": 1, "qty": 1, "to_pvz_id": 3})
    assert response.status_code == 401
    assert "X-API-Key" in response.json()["detail"]
    assert response.headers["www-authenticate"] == "ApiKey"


def test_reads_and_tracking_are_public(client):
    code = create_order(client)["order"]["code"]
    anonymous = _anonymous()
    assert anonymous.get("/api/v1/orders").status_code == 200
    assert anonymous.get(f"/api/v1/track/{code}").status_code == 200


def test_pvz_can_assemble_but_cannot_open_order(client):
    order_id = create_order(client)["order"]["id"]
    pvz = _as("pvz")
    assert pvz.post(f"/api/v1/orders/{order_id}/assemble").status_code == 200
    forbidden = pvz.post("/api/v1/orders", json={"item_id": 1, "qty": 1, "to_pvz_id": 3})
    assert forbidden.status_code == 403
    assert "seller" in forbidden.json()["detail"]


def test_seller_cannot_assemble(client):
    order_id = create_order(client)["order"]["id"]
    response = _as("seller").post(f"/api/v1/orders/{order_id}/assemble")
    assert response.status_code == 403


def test_only_admin_toggles_edges_and_clock(client):
    assert _as("pvz").post("/api/v1/clock/advance?days=1").status_code == 403
    assert _as("seller").post("/api/v1/edges/1/disable").status_code == 403
    assert _as("admin").post("/api/v1/clock/advance?days=1").status_code == 200


def test_unknown_key_is_401(client):
    anonymous = TestClient(app)
    anonymous.headers.update({"X-API-Key": "not-a-key"})
    assert anonymous.post("/api/v1/clock/advance?days=1").status_code == 401
