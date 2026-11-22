"""Аналитика ПВЗ и продавца на данных ledger."""
from conftest import issue_order


def test_pvz_analytics(client):
    issue_order(client)
    data = client.get("/api/v1/analytics/pvz", params={"pvz_id": 3}).json()
    assert data["pvz"]["code"] == "YOL-03"
    assert data["revenue"]["pickup_fee"] == 25.0
    assert data["revenue"]["bonus"] == 15.0
    assert data["revenue_total_rub"] == 40.0
    # Сборка и приёмка — операции ПВЗ-источника, а не пункта выдачи.
    assert data["op_costs_modeled_rub"] == 0.0
    assert data["fixed_costs_modeled_rub"] == 1200.0
    assert data["profit_rub"] == -1160.0
    src = client.get("/api/v1/analytics/pvz", params={"pvz_id": 1}).json()
    assert src["op_costs_modeled_rub"] == 20.0  # сборка 12 + приёмка 8 за 1 шт.
    assert data["orders_issued"] == 1
    assert data["ledger"]


def test_seller_analytics(client):
    issue_order(client, qty=2)
    data = client.get("/api/v1/analytics/seller", params={"seller_id": 1}).json()
    assert data["revenue"]["goods_rub"] == 37800.0
    assert data["costs"]["commission_rub"] == 4536.0
    assert data["profit_rub"] == 33264.0
    order = data["orders"][0]
    assert order["goods_rub"] == 37800.0 and order["commission_rub"] == 4536.0
    assert order["net_rub"] == 33264.0


def test_summary_balances(client):
    issue_order(client)
    data = client.get("/api/v1/analytics/summary").json()
    assert data["orders"]["total"] == 1
    assert data["logistics_cost_rub"] == 90.0
    assert round(sum(data["ledger"].values()), 2) == -90.0
