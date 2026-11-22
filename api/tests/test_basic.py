"""Базовые проверки сервиса и контракта."""


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_pvz_list_includes_hub(client):
    rows = client.get("/api/v1/pvz").json()
    assert len(rows) == 9
    points = client.get("/api/v1/pvz", params={"include_hub": "false"}).json()
    assert len(points) == 8
    assert {r["city"] for r in points} == {"Йошкар-Ола", "Казань"}
    assert all(r["used_cells"] <= r["capacity_cells"] for r in rows)


def test_openapi_contract(client):
    spec = client.get("/openapi.json").json()
    assert len(spec["paths"]) >= 18
    assert "/api/v1/orders" in spec["paths"]
    assert "/api/v1/routes/compare" in spec["paths"]
