"""Инциденты: отключение сегмента, перевыбор маршрута, отсутствие пути."""
from conftest import create_order


def _edge(client, from_pvz, to_pvz, kind=None):
    rows = client.get("/api/v1/edges").json()
    for row in rows:
        if row["from_pvz"] == from_pvz and row["to_pvz"] == to_pvz and (kind is None or row["kind"] == kind):
            return row
    raise AssertionError("edge not found")


def test_disable_edge_reroutes_order(client):
    order_id = create_order(client, item_id=1, to_pvz=3)["order"]["id"]
    edge = _edge(client, 1, 3, "local")
    response = client.post(f"/api/v1/edges/{edge['id']}/disable")
    assert response.status_code == 200
    incidents = response.json()["incidents"]
    assert len(incidents) == 1
    incident = incidents[0]["incident"]
    assert incident["status"] == "resolved" and incident["resolved_route_id"]
    order = client.get(f"/api/v1/orders/{order_id}").json()
    assert order["status"] == "rerouted"
    assert order["route"]["total_cost_rub"] == 180.0
    assert order["route"]["kind"] == "mesh"
    assert len(order["route"]["segments"]) == 2


def test_no_route_keeps_incident_open(client):
    create_order(client, item_id=1, to_pvz=5)
    leg = _edge(client, 9, 5, "trunk")
    client.post(f"/api/v1/edges/{leg['id']}/disable")
    open_rows = client.get("/api/v1/incidents", params={"status": "open"}).json()
    assert len(open_rows) == 1
    response = client.post(f"/api/v1/incidents/{open_rows[0]['id']}/reroute", json={"accept_latency": True})
    assert response.status_code == 409
    still_open = client.get("/api/v1/incidents", params={"status": "open"}).json()
    assert len(still_open) == 1


def test_reroute_after_recovery(client):
    create_order(client, item_id=1, to_pvz=5)
    leg = _edge(client, 9, 5, "trunk")
    client.post(f"/api/v1/edges/{leg['id']}/disable")
    incident = client.get("/api/v1/incidents", params={"status": "open"}).json()[0]
    client.post(f"/api/v1/edges/{leg['id']}/enable")
    response = client.post(f"/api/v1/incidents/{incident['id']}/reroute", json={"accept_latency": True})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["incident"]["status"] == "resolved"
    assert data["route"]["kind"] == "hub"
    assert data["order"]["status"] == "rerouted"
