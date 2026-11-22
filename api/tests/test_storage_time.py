"""Длительное хранение: начисления по часам, пороги, перемещение остатка."""


def _total_cells(client):
    items = client.get("/api/v1/items").json()
    return sum(row["cells"] for row in items)


def test_advance_charges_storage(client):
    cells = _total_cells(client)
    before = client.get("/api/v1/clock").json()["virtual_now"]
    response = client.post("/api/v1/clock/advance", params={"days": 3})
    assert response.status_code == 200
    data = response.json()
    assert data["charged_total_rub"] == cells * 5.0 * 3
    after = client.get("/api/v1/clock").json()["virtual_now"]
    assert after > before
    rows = client.get("/api/v1/ledger", params={"viewer": "pvz"}).json()
    assert any(row["kind"] == "storage" for row in rows)


def test_long_storage_alert_with_offer(client):
    response = client.post("/api/v1/clock/advance", params={"days": 25})
    assert response.json()["alerts"] >= 1
    notes = client.get("/api/v1/notifications", params={"audience": "pvz"}).json()
    alerts = [n for n in notes if n["payload"].get("kind") == "long_storage"]
    assert alerts
    offer = alerts[0]["payload"]["offer"]
    assert offer and offer["suggested_pvz_id"] and offer["cost_rub"] == 60.0
    assert "переместить" in alerts[0]["text"].lower()


def test_relocate_stock_moves_cells(client):
    client.post("/api/v1/clock/advance", params={"days": 25})
    notes = client.get("/api/v1/notifications", params={"audience": "pvz"}).json()
    offer = next(n["payload"]["offer"] for n in notes if n["payload"].get("kind") == "long_storage")
    item_id = next(n["payload"]["item_id"] for n in notes
                   if n["payload"].get("kind") == "long_storage" and n["payload"]["offer"])
    src_before = client.get(f"/api/v1/pvz/{offer['from_pvz_id']}").json()["used_cells"]
    dst_before = client.get(f"/api/v1/pvz/{offer['suggested_pvz_id']}").json()["used_cells"]
    response = client.post(f"/api/v1/items/{item_id}/relocate", json={"to_pvz_id": offer["suggested_pvz_id"]})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["item"]["stored_pvz_id"] == offer["suggested_pvz_id"]
    assert data["relocation"]["cells"] == offer["cells"]
    assert client.get(f"/api/v1/pvz/{offer['from_pvz_id']}").json()["used_cells"] == src_before - offer["cells"]
    assert client.get(f"/api/v1/pvz/{offer['suggested_pvz_id']}").json()["used_cells"] == dst_before + offer["cells"]
    relocation_rows = [r for r in client.get("/api/v1/ledger").json() if r["kind"] == "relocation"]
    assert relocation_rows and relocation_rows[0]["party"] == "marketplace"
