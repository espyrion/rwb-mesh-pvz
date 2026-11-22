"""Денежные инварианты: формула хранения, запрет перескока статуса, ёмкость."""
from conftest import create_order


def test_storage_formula_one_day(client):
    cells = sum(row["cells"] for row in client.get("/api/v1/items").json())
    data = client.post("/api/v1/clock/advance", params={"days": 1}).json()
    assert data["charged_total_rub"] == cells * 5.0
    assert sum(data["by_pvz"].values()) == data["charged_total_rub"]


def test_issue_requires_assembly(client):
    order_id = create_order(client)["order"]["id"]
    response = client.post(f"/api/v1/orders/{order_id}/issue")
    assert response.status_code == 409
    assert "переход" in response.json()["detail"]
    assert client.get(f"/api/v1/orders/{order_id}").json()["status"] == "routed"


def test_capacity_never_exceeded(client):
    create_order(client)  # 1→3, 2 ячейки
    client.post("/api/v1/orders/1/assemble")
    pvz = client.get("/api/v1/pvz/3").json()
    assert pvz["used_cells"] <= pvz["capacity_cells"]
    assert pvz["free_cells"] == pvz["capacity_cells"] - pvz["used_cells"]
