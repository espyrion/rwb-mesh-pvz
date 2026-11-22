"""Публичное отслеживание заказа покупателем."""
from conftest import create_order


def test_track_returns_public_card(client):
    created = create_order(client, qty=2)
    code = created["order"]["code"]

    response = client.get(f"/api/v1/track/{code.lower()}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["code"] == code and data["status"] == "routed"
    assert data["status_label"] == "Маршрут построен"
    assert data["qty"] == 2
    assert data["destination"]["code"] == "YOL-03"
    assert data["timeline"][0]["status"] == "created"


def test_track_hides_internal_money(client):
    code = create_order(client)["order"]["code"]
    data = client.get(f"/api/v1/track/{code}").json()
    for forbidden in ("seller_net_rub", "item_id", "route_id", "id", "commission_rub"):
        assert forbidden not in data
    assert set(["code", "status", "buyer_total_rub", "timeline"]).issubset(data)


def test_track_timeline_grows_after_assemble_and_issue(client):
    code = create_order(client)["order"]["code"]
    order_id = client.get(f"/api/v1/track/{code}").json()
    assert len(order_id["timeline"]) == 1

    assert client.post(f"/api/v1/orders/{client.get('/api/v1/orders').json()[0]['id']}/assemble").status_code == 200
    after_assemble = client.get(f"/api/v1/track/{code}").json()
    statuses = [e["status"] for e in after_assemble["timeline"]]
    assert "assembled" in statuses and "eta" in statuses


def test_track_unknown_code_is_404(client):
    response = client.get("/api/v1/track/ORD-99999")
    assert response.status_code == 404
    assert "не найден" in response.json()["detail"]
