"""Маршрутизация: mesh дешевле в городе, hub между городами, отключение рёбер."""


def _edge(client, from_pvz, to_pvz, kind=None):
    rows = client.get("/api/v1/edges").json()
    for row in rows:
        if row["from_pvz"] == from_pvz and row["to_pvz"] == to_pvz and (kind is None or row["kind"] == kind):
            return row
    raise AssertionError(f"edge {from_pvz}->{to_pvz} not found")


def test_mesh_cheaper_within_city(client):
    data = client.get("/api/v1/routes/compare", params={"from": 1, "to": 3}).json()
    assert data["chosen"] == "mesh"
    assert data["mesh"]["available"] is True
    assert data["mesh"]["total_cost_rub"] == 90.0
    assert data["hub"]["total_cost_rub"] == 135.5
    assert data["mesh"]["total_hours"] < data["hub"]["total_hours"]
    assert "mesh" in data["reason"].lower()


def test_intercity_requires_hub(client):
    data = client.get("/api/v1/routes/compare", params={"from": 1, "to": 5}).json()
    assert data["mesh"]["available"] is False
    assert data["chosen"] == "hub"
    codes = [seg["to_code"] for seg in data["hub"]["segments"]]
    assert "HUB-KZN" in codes
    assert data["hub"]["total_cost_rub"] == 135.5
    assert data["hub"]["total_hours"] == 30.0


def test_disable_edge_changes_route(client):
    direct = _edge(client, 1, 3, "local")
    before = client.get("/api/v1/routes/compare", params={"from": 1, "to": 3}).json()
    assert before["mesh"]["total_cost_rub"] == 90.0
    disabled = client.post(f"/api/v1/edges/{direct['id']}/disable")
    assert disabled.status_code == 200
    after = client.get("/api/v1/routes/compare", params={"from": 1, "to": 3}).json()
    assert after["mesh"]["total_cost_rub"] == 180.0
    assert len(after["mesh"]["segments"]) == 2
    client.post(f"/api/v1/edges/{direct['id']}/enable")


def test_no_route_when_all_segments_down(client):
    hub_leg = _edge(client, 9, 5, "trunk")
    client.post(f"/api/v1/edges/{hub_leg['id']}/disable")
    data = client.get("/api/v1/routes/compare", params={"from": 1, "to": 5}).json()
    assert data["chosen"] is None
    assert data["hub"]["available"] is False
    client.post(f"/api/v1/edges/{hub_leg['id']}/enable")
    again = client.get("/api/v1/routes/compare", params={"from": 1, "to": 5}).json()
    assert again["chosen"] == "hub"
