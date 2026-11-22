"""Пять обязательных демонстрационных сценариев ТЗ (печатают отчёт в stdout).

Запуск с отчётом:  pytest -s tests/test_scenarios.py
"""
from conftest import create_order, issue_order


def _edge(client, from_pvz, to_pvz, kind=None):
    for row in client.get("/api/v1/edges").json():
        if row["from_pvz"] == from_pvz and row["to_pvz"] == to_pvz and (kind is None or row["kind"] == kind):
            return row
    raise AssertionError("edge not found")


def _report(title: str, **fields):
    print(f"\n[СЦЕНАРИЙ] {title}")
    for key, value in fields.items():
        print(f"  {key}: {value}")


def test_scenario_1_city_order_without_hub(client):
    data = create_order(client, item_id=1, qty=1, to_pvz=3)
    order_id = data["order"]["id"]
    client.post(f"/api/v1/orders/{order_id}/assemble")
    issued = client.post(f"/api/v1/orders/{order_id}/issue").json()["order"]
    analytics = client.get("/api/v1/analytics/pvz", params={"pvz_id": 3}).json()
    assert issued["route"]["kind"] == "mesh"
    assert issued["status"] == "issued"
    _report("1. Городской заказ без хаба",
            order=issued["code"], route=issued["route"]["kind"],
            cost_rub=issued["route"]["total_cost_rub"], hours=issued["route"]["total_hours"],
            buyer_total_rub=issued["buyer_total_rub"], pvz_profit_rub=analytics["profit_rub"])


def test_scenario_2_intercity_order_with_hub(client):
    data = create_order(client, item_id=1, qty=1, to_pvz=5)
    order_id = data["order"]["id"]
    assert data["order"]["route"]["kind"] == "hub"
    client.post(f"/api/v1/orders/{order_id}/assemble")
    client.post(f"/api/v1/orders/{order_id}/transit")
    issued = client.post(f"/api/v1/orders/{order_id}/issue").json()["order"]
    assert issued["status"] == "issued"
    segments = [seg["from_code"] + "→" + seg["to_code"] for seg in issued["route"]["segments"]]
    _report("2. Межгород через хаб",
            order=issued["code"], segments=segments, hours=issued["route"]["total_hours"],
            cost_rub=issued["route"]["total_cost_rub"], buyer_total_rub=issued["buyer_total_rub"])


def test_scenario_3_return(client):
    order_id, _ = issue_order(client, qty=1)
    returned = client.post(f"/api/v1/orders/{order_id}/return").json()
    assert returned["order"]["status"] == "returned"
    assert returned["item"]["qty"] == 1
    ledger = client.get("/api/v1/ledger", params={"order_id": order_id}).json()
    _report("3. Возврат после выдачи",
            order=returned["order"]["code"], status=returned["order"]["status"],
            item_qty=returned["item"]["qty"], ledger_rows=len(ledger),
            ledger_sum_rub=round(sum(row["amount_rub"] for row in ledger), 2))


def test_scenario_4_logistics_incident(client):
    order_id = create_order(client, item_id=1, to_pvz=3)["order"]["id"]
    edge = _edge(client, 1, 3, "local")
    result = client.post(f"/api/v1/edges/{edge['id']}/disable").json()
    incident = result["incidents"][0]["incident"]
    order = client.get(f"/api/v1/orders/{order_id}").json()
    assert order["status"] == "rerouted"
    assert order["route"]["total_cost_rub"] == 180.0
    assert len(order["route"]["segments"]) == 2
    _report("4. Логистическая ошибка и смена маршрута",
            edge=edge["id"], incident=incident["id"], incident_status=incident["status"],
            new_route_kind=order["route"]["kind"], new_route_cost_rub=order["route"]["total_cost_rub"],
            new_route_segments=len(order["route"]["segments"]), order_status=order["status"])


def test_scenario_5_long_storage(client):
    advance = client.post("/api/v1/clock/advance", params={"days": 25}).json()
    notes = client.get("/api/v1/notifications", params={"audience": "pvz"}).json()
    alert = next(n for n in notes if n["payload"].get("kind") == "long_storage" and n["payload"]["offer"])
    offer = alert["payload"]["offer"]
    moved = client.post(f"/api/v1/items/{alert['payload']['item_id']}/relocate",
                        json={"to_pvz_id": offer["suggested_pvz_id"]})
    assert moved.status_code == 200
    _report("5. Длительное хранение и перемещение остатка",
            virtual_now=advance["virtual_now"], charged_rub=advance["charged_total_rub"],
            alerts=advance["alerts"], item=alert["payload"]["item_id"],
            moved_to=offer["suggested_code"], relocation_cost_rub=offer["cost_rub"])
