"""Публичное отслеживание заказа покупателем по токену из ссылки."""
from conftest import create_order


def track_url(order: dict) -> str:
    return f"/api/v1/track/{order['public_token']}"


def test_track_returns_public_card(client):
    created = create_order(client, qty=2)
    order = created["order"]
    assert order["public_token"] and order["public_token"] != order["code"]

    response = client.get(track_url(order))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["code"] == order["code"] and data["status"] == "routed"
    assert data["status_label"] == "Маршрут построен"
    assert data["qty"] == 2
    assert data["destination"]["code"] == "YOL-03"
    assert data["timeline"][0]["status"] == "created"


def test_track_token_is_case_insensitive(client):
    token = create_order(client)["order"]["public_token"]
    assert client.get(f"/api/v1/track/{token.upper()}").status_code == 200


def test_public_card_has_route_kind_for_buyer(client):
    order = create_order(client)["order"]
    card = client.get(track_url(order)).json()
    assert card["route"]["kind"] == "mesh"
    assert card["route"]["hours"] > 0
    assert card["destination"]["code"] == order["to_code"]


def test_track_hides_internal_money(client):
    data = client.get(track_url(create_order(client)["order"])).json()
    for forbidden in ("seller_net_rub", "item_id", "route_id", "id", "commission_rub"):
        assert forbidden not in data
    assert set(["code", "status", "buyer_total_rub", "timeline"]).issubset(data)


def test_track_timeline_grows_after_assemble_and_issue(client):
    order = create_order(client)["order"]
    assert len(client.get(track_url(order)).json()["timeline"]) == 1

    assert client.post(f"/api/v1/orders/{order['id']}/assemble").status_code == 200
    after_assemble = client.get(track_url(order)).json()
    statuses = [e["status"] for e in after_assemble["timeline"]]
    assert "assembled" in statuses and "eta" in statuses


def test_track_by_sequential_code_is_closed(client):
    """Код ORD-00001 перебирается, поэтому публичный поиск по нему запрещён."""
    order = create_order(client)["order"]
    for guess in (order["code"], order["code"].lower(), "ORD-00001"):
        response = client.get(f"/api/v1/track/{guess}")
        assert response.status_code == 404, guess
        assert "ссылк" in response.json()["detail"]


def test_track_unknown_token_is_404(client):
    response = client.get("/api/v1/track/000000000000")
    assert response.status_code == 404
    assert "не найден" in response.json()["detail"]
