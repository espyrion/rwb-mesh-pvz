"""Контракт чтения: страницы списков и OpenAPI-документация."""
from conftest import create_order


def test_orders_pagination_and_total_header(client):
    for _ in range(3):
        create_order(client)

    first = client.get("/api/v1/orders", params={"limit": 2})
    assert first.status_code == 200
    assert len(first.json()) == 2
    assert first.headers["x-total-count"] == "3"

    second = client.get("/api/v1/orders", params={"limit": 2, "offset": 2})
    assert len(second.json()) == 1
    ids = {row["id"] for row in first.json()} | {row["id"] for row in second.json()}
    assert len(ids) == 3


def test_orders_filter_by_pvz_and_code(client):
    order = create_order(client)["order"]
    by_pvz = client.get("/api/v1/orders", params={"pvz_id": order["to_pvz"]}).json()
    assert [row["code"] for row in by_pvz] == [order["code"]]

    by_code = client.get("/api/v1/orders", params={"code": order["code"].lower()}).json()
    assert [row["id"] for row in by_code] == [order["id"]]

    assert client.get("/api/v1/orders", params={"code": "ORD-00000"}).json() == []


def test_ledger_pagination_and_kind_filter(client):
    order = create_order(client)["order"]
    # Проводки появляются при выдаче: до неё ledger пуст по замыслу модели.
    client.post(f"/api/v1/orders/{order['id']}/assemble")
    client.post(f"/api/v1/orders/{order['id']}/issue")
    page = client.get("/api/v1/ledger", params={"limit": 2})
    assert page.status_code == 200 and len(page.json()) <= 2
    assert int(page.headers["x-total-count"]) >= len(page.json())

    kinds = {row["kind"] for row in client.get("/api/v1/ledger").json()}
    assert kinds, "в ledger должны быть проводки после заказа"
    one_kind = sorted(kinds)[0]
    filtered = client.get("/api/v1/ledger", params={"kind": one_kind}).json()
    assert filtered and {row["kind"] for row in filtered} == {one_kind}


def test_items_pagination_header(client):
    response = client.get("/api/v1/items", params={"limit": 1})
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert int(response.headers["x-total-count"]) >= 1


def test_deep_health_reports_invariants(client):
    data = client.get("/api/v1/health/deep").json()
    assert data["status"] == "ok", data
    names = [check["name"] for check in data["checks"]]
    assert "резерв не больше остатка" in names
    assert all(check["ok"] for check in data["checks"])
    assert data["counts"]["pvz"] == 9  # 8 ПВЗ + хаб


def test_openapi_declares_response_schemas_and_api_key(client):
    schema = client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["ApiKey"]["name"] == "X-API-Key" and schemes["ApiKey"]["in"] == "header"

    orders = schema["paths"]["/api/v1/orders"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert orders.get("items", {}).get("$ref", "").endswith("/OrderOut")

    assert "security" not in schema["paths"]["/api/v1/orders"]["get"]
    assert schema["paths"]["/api/v1/orders"]["post"]["security"] == [{"ApiKey": []}]
