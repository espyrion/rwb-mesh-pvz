"""Приёмка на ПВЗ: ёмкость, остатки, идемпотентность чтения."""


def _pvz(client, pvz_id=4):
    return client.get(f"/api/v1/pvz/{pvz_id}").json()


def test_acceptance_occupies_cells(client):
    before = _pvz(client)
    payload = {"sku": "TEST-1", "seller_id": 1, "title": "Тестовый товар", "qty": 5,
               "cell_size": 2, "pvz_id": 4, "price_rub": 1000}
    response = client.post("/api/v1/items", json=payload)
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["qty"] == 5 and item["cells"] == 10
    after = _pvz(client)
    assert after["used_cells"] == before["used_cells"] + 10


def test_acceptance_overflow_409(client):
    pvz = _pvz(client)
    free = pvz["capacity_cells"] - pvz["used_cells"]
    payload = {"sku": "TEST-BIG", "seller_id": 1, "title": "Слишком много", "qty": free + 1,
               "cell_size": 1, "pvz_id": 4}
    response = client.post("/api/v1/items", json=payload)
    assert response.status_code == 409
    assert "ячеек" in response.json()["detail"]
    assert _pvz(client)["used_cells"] == pvz["used_cells"]


def test_items_filter_stock(client):
    client.post("/api/v1/items", json={"sku": "TEST-2", "seller_id": 2, "title": "Фильтр",
                                       "qty": 2, "cell_size": 1, "pvz_id": 4})
    rows = client.get("/api/v1/items", params={"pvz_id": 4, "status": "stored"}).json()
    assert any(r["sku"] == "TEST-2" for r in rows)
    assert all(r["stored_pvz_id"] == 4 and r["status"] == "stored" for r in rows)
    assert all(r["available_qty"] == r["qty"] for r in rows)


def test_repeated_read_idempotent(client):
    first = client.get("/api/v1/items", params={"pvz_id": 1}).json()
    pvz_first = _pvz(client, 1)
    second = client.get("/api/v1/items", params={"pvz_id": 1}).json()
    pvz_second = _pvz(client, 1)
    assert first == second
    assert pvz_first["used_cells"] == pvz_second["used_cells"]
