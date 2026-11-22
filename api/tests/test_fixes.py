"""Регрессии по итогам внешнего ревью: партии, seed, хранение, инциденты, статусы.

Каждый тест закрывает конкретный воспроизводимый дефект, найденный до защиты.
"""
from conftest import create_order, issue_order

from app import clock, storage
from app.models import Item


def _items(client):
    return client.get("/api/v1/items").json()


def _stored_qty(client, sku):
    return sum(i["qty"] for i in _items(client) if i["sku"] == sku and i["status"] == "stored")


def test_last_unit_of_batch_can_be_issued(client):
    """Раньше выдача последней единицы падала в 500 на CHECK qty > 0."""
    issue_order(client, qty=6)
    assert _stored_qty(client, "TD-1001") == 0
    issued = [i for i in _items(client) if i["sku"] == "TD-1001" and i["status"] == "issued"]
    assert issued and issued[0]["qty"] == 0


def test_partial_assembly_keeps_remainder_on_source_pvz(client):
    """Заказ на 2 шт. из партии 6 не должен утаскивать всю партию на пункт выдачи."""
    first = create_order(client, qty=2)
    order_id = first["order"]["id"]
    assert client.post(f"/api/v1/orders/{order_id}/assemble").status_code == 200

    rest = [i for i in _items(client) if i["sku"] == "TD-1001" and i["status"] == "stored"]
    assert sum(i["qty"] for i in rest) == 4
    assert rest[0]["stored_pvz_id"] == 1
    assert rest[0]["available_qty"] == 4

    second = create_order(client, item_id=rest[0]["id"], qty=4)
    second_id = second["order"]["id"]
    assert client.post(f"/api/v1/orders/{second_id}/assemble").status_code == 200
    assert client.post(f"/api/v1/orders/{second_id}/issue").status_code == 200
    assert _stored_qty(client, "TD-1001") == 0


def test_seed_used_cells_derived_from_batches(client):
    """Загрузка ПВЗ должна сходиться с партиями, а не быть вписанной руками."""
    expected = {
        "YOL-01": 35, "YOL-02": 45, "YOL-03": 28, "YOL-04": 17,
        "KZN-01": 20, "KZN-02": 10, "KZN-03": 25, "KZN-04": 15,
    }
    for pvz in client.get("/api/v1/pvz").json():
        if pvz["is_hub"]:
            continue
        assert pvz["used_cells"] == expected[pvz["code"]], pvz["code"]


def test_seed_tariffs_come_from_params(client):
    """Ставки ПВЗ и рёбер берутся из params.yaml, а не из литералов в коде."""
    pvz = client.get("/api/v1/pvz/1").json()
    assert pvz["storage_rate_day"] == 5.0 and pvz["op_fee"] == 25.0
    edges = client.get("/api/v1/edges").json()
    local = [e for e in edges if e["kind"] == "local"]
    trunk_out = [e for e in edges if e["kind"] == "trunk" and e["from_code"] == "YOL-01"]
    trunk_in = [e for e in edges if e["kind"] == "trunk" and e["from_code"] == "HUB-KZN"]
    assert {e["cost_rub"] for e in local} == {90.0}
    assert [e["cost_rub"] for e in trunk_out] == [70.0]
    assert {e["cost_rub"] for e in trunk_in} == {65.5}


def test_storage_charges_actual_elapsed_days(client):
    """Начисление идёт по фактически прошедшим дням, а не по параметру запроса."""
    cells = sum(i["qty"] * i["cell_size"] for i in _items(client) if i["status"] == "stored")
    first = client.post("/api/v1/clock/advance", params={"days": 3}).json()
    second = client.post("/api/v1/clock/advance", params={"days": 2}).json()
    assert first["charged_total_rub"] == cells * 5.0 * 3
    assert second["charged_total_rub"] == cells * 5.0 * 2


def test_relocate_restarts_storage_period(client, db):
    """После перемещения остаток не должен сразу снова попадать в длительное хранение."""
    client.post("/api/v1/clock/advance", params={"days": 22})
    db.expire_all()
    row = db.get(Item, 1)
    event = storage.evaluate_long_storage(db, row, clock.now(db))
    assert event and event["days_stored"] >= 21, event

    response = client.post("/api/v1/items/1/relocate", json={"to_pvz_id": 3})
    assert response.status_code == 200, response.text

    db.expire_all()
    row = db.get(Item, 1)
    assert storage.days_stored(row, clock.now(db)) == 0
    assert storage.evaluate_long_storage(db, row, clock.now(db)) is None


def test_hub_order_cannot_be_issued_before_transit(client):
    """Межгородской заказ нельзя выдать сразу после сборки: нужен транзит."""
    order_id = create_order(client, to_pvz=8)["order"]["id"]
    assert client.post(f"/api/v1/orders/{order_id}/assemble").status_code == 200
    response = client.post(f"/api/v1/orders/{order_id}/issue")
    assert response.status_code == 409
    assert "transit" in response.json()["detail"]


def test_disabling_hub_opens_incident_for_transit_order(client):
    """Инцидент должен ловить промежуточный узел маршрута, а не только отправителя и получателя."""
    create_order(client, to_pvz=8)
    result = client.post("/api/v1/pvz/9/disable").json()
    assert result["incidents"], "отключение хаба не задело межгородской заказ"
    assert result["incidents"][0]["incident"]["status"] == "open"
