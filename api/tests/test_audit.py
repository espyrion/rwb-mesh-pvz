"""Инвентаризация остатков ПВЗ: сверка, корректировка, инцидент, удержание, идемпотентность."""
from fastapi.testclient import TestClient

from app import audit
from app.auth import key_for
from app.main import app
from app.models import Item
from conftest import create_order

URL = "/api/v1/pvz/{pvz_id}/audit"


def _audit(client, pvz_id=1, actual=None, **extra):
    body = {"actual": actual if actual is not None else {"1": 6, "5": 15}, **extra}
    return client.post(URL.format(pvz_id=pvz_id), json=body)


def _penalty_rows(client):
    return [row for row in client.get("/api/v1/ledger", params={"kind": "penalty"}).json()]


def _mismatch_incidents(client):
    return [row for row in client.get("/api/v1/incidents").json() if row["type"] == "stock_mismatch"]


def test_full_match_creates_no_incident_and_no_penalty(client):
    response = _audit(client)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["matched"] == 2 and data["shortage"] == 0 and data["surplus"] == 0
    assert data["incident_ids"] == []
    assert data["penalty_total_rub"] == 0.0
    assert data["idempotent"] is False
    assert data["audit_accuracy_pct"] == 100.0
    assert data["accuracy_pct"] == 100.0
    assert data["sla_rating"] == "A"
    assert data["audits_total"] == 1
    assert _mismatch_incidents(client) == []
    assert _penalty_rows(client) == []


def test_shortage_corrects_stock_opens_incident_and_charges_pvz(client):
    before = client.get("/api/v1/pvz/1").json()
    response = _audit(client, actual={"1": 4, "5": 15})
    assert response.status_code == 200, response.text
    data = response.json()

    line = next(row for row in data["lines"] if row["item_id"] == 1)
    assert line["status"] == "shortage" and line["delta"] == -2
    assert line["cells_delta"] == -4  # 2 шт. × 2 яч.
    assert line["incident_id"]

    # Остаток скорректирован условным UPDATE, ячейки освобождены.
    assert client.get("/api/v1/items/1").json()["qty"] == 4
    after = client.get("/api/v1/pvz/1").json()
    assert after["used_cells"] == before["used_cells"] - 4
    assert data["used_cells"] == after["used_cells"]

    # Инцидент расхождения с понятным текстом.
    incidents = _mismatch_incidents(client)
    assert len(incidents) == 1
    assert incidents[0]["status"] == "open"
    assert "недостача 2 шт." in incidents[0]["description"]
    assert incidents[0]["id"] == data["incident_ids"][0]

    # Удержание: 2 шт. × pvz.audit_shortage_penalty_rub (30 ₽) = 60 ₽, ПВЗ −, маркетплейс +.
    assert data["penalty_total_rub"] == 60.0
    rows = _penalty_rows(client)
    pvz_row = next(row for row in rows if row["party"] == "pvz")
    market_row = next(row for row in rows if row["party"] == "marketplace")
    assert pvz_row["amount_rub"] == -60.0 and pvz_row["party_id"] == 1
    assert market_row["amount_rub"] == 60.0

    # Точность: свежая проверка 2 из 21 → 90,48%; EMA с исходными 100% → 97,1% → рейтинг B.
    assert data["audit_accuracy_pct"] == 90.48
    assert data["accuracy_before_pct"] == 100.0
    assert data["accuracy_pct"] == 97.1
    assert data["sla_rating"] == "B"
    pvz = client.get("/api/v1/pvz/1").json()
    assert pvz["audit_accuracy_pct"] == 97.1
    assert pvz["audit_sla_rating"] == "B"
    assert pvz["audit_count"] == 1
    assert pvz["last_audit_at"]


def test_surplus_corrects_stock_and_charges_differently(client):
    response = _audit(client, actual={"5": 17})
    assert response.status_code == 200, response.text
    data = response.json()
    line = data["lines"][0]
    assert line["status"] == "surplus" and line["delta"] == 2 and line["cells_delta"] == 2
    assert client.get("/api/v1/items/5").json()["qty"] == 17
    # Излишек дешевле: 2 шт. × pvz.audit_surplus_penalty_rub (10 ₽) = 20 ₽.
    assert data["penalty_total_rub"] == 20.0
    pvz_row = next(row for row in _penalty_rows(client) if row["party"] == "pvz")
    assert pvz_row["amount_rub"] == -20.0


def test_repeat_audit_is_idempotent(client):
    first = _audit(client, actual={"1": 4}).json()
    assert first["idempotent"] is False

    second = _audit(client, actual={"1": 4}).json()
    assert second["idempotent"] is True
    assert second["penalty_total_rub"] == first["penalty_total_rub"]
    assert second["incident_ids"] == first["incident_ids"]
    assert second["accuracy_pct"] == first["accuracy_pct"]
    assert second["audits_total"] == 1  # повтор не считается новой проверкой

    assert len(_mismatch_incidents(client)) == 1
    pvz_rows = [row for row in _penalty_rows(client) if row["party"] == "pvz"]
    assert len(pvz_rows) == 1
    assert client.get("/api/v1/pvz/1").json()["audit_count"] == 1


def test_changed_stock_after_audit_is_rechecked(client, db):
    """Слепок прошлой сверки не применяется, если учёт успел измениться."""
    _audit(client, actual={"1": 4}).json()

    item = db.get(Item, 1)
    item.qty = 5
    db.commit()

    again = _audit(client, actual={"1": 4}).json()
    assert again["idempotent"] is False
    assert again["lines"][0]["expected_qty"] == 5
    assert again["lines"][0]["delta"] == -1


def test_accuracy_smoothing(client):
    first = _audit(client, actual={"1": 4, "5": 15}).json()
    assert first["accuracy_pct"] == 97.1
    assert first["sla_rating"] == "B"

    # Новая партия на ПВЗ + чистая сверка: сглаженная точность = alpha × 100 + (1−alpha) × прежняя.
    created = client.post(
        "/api/v1/items",
        json={"sku": "SM-2002", "seller_id": 2, "title": "Рюкзак", "qty": 1, "cell_size": 1, "pvz_id": 1},
    )
    assert created.status_code == 201
    new_id = created.json()["id"]

    second = _audit(client, actual={"1": 4, "5": 15, str(new_id): 1}).json()
    alpha = float(audit.p("pvz", "audit_accuracy_ema_alpha"))
    expected = round(alpha * 100.0 + (1 - alpha) * first["accuracy_pct"], 1)
    assert second["audit_accuracy_pct"] == 100.0
    assert second["accuracy_before_pct"] == 97.1
    assert second["accuracy_pct"] == expected
    assert second["sla_rating"] == "A"


def test_rating_boundaries():
    a = float(audit.p("pvz", "audit_sla_a_pct"))
    b = float(audit.p("pvz", "audit_sla_b_pct"))
    c = float(audit.p("pvz", "audit_sla_c_pct"))
    assert audit._rating(a) == "A" and audit._rating(a - 0.1) == "B"
    assert audit._rating(b) == "B" and audit._rating(b - 0.1) == "C"
    assert audit._rating(c) == "C" and audit._rating(c - 0.1) == "D"


def test_audit_rejects_invalid_requests(client):
    assert _audit(client, pvz_id=999).status_code == 404
    hub = _audit(client, pvz_id=9)
    assert hub.status_code == 422 and "хаб" in hub.json()["detail"]
    assert client.post(URL.format(pvz_id=1), json={"actual": {}}).status_code == 422
    assert client.post(URL.format(pvz_id=1), json={"actual": {"1": -1}}).status_code == 422
    assert client.post(URL.format(pvz_id=1), json={"actual": {"0": 1}}).status_code == 422
    unknown = _audit(client, actual={"999": 1})
    assert unknown.status_code == 422 and "999" in unknown.json()["detail"]
    foreign = _audit(client, pvz_id=2, actual={"1": 6})
    assert foreign.status_code == 422  # партия #1 хранится на другом ПВЗ


def test_audit_refuses_fact_below_reserved(client):
    create_order(client, item_id=1, qty=5)
    response = _audit(client, actual={"1": 2})
    assert response.status_code == 409
    assert "резерв" in response.json()["detail"]
    assert client.get("/api/v1/items/1").json()["qty"] == 6  # остаток не тронут


def test_audit_overflow_rolls_back_everything(client):
    """Излишек, не влезающий в ёмкость, отклоняется целиком: ни остатка, ни инцидента, ни проводки."""
    response = _audit(client, pvz_id=2, actual={"2": 40, "6": 25})
    assert response.status_code == 409
    assert "ёмкости" in response.json()["detail"]
    assert client.get("/api/v1/items/2").json()["qty"] == 4
    assert _mismatch_incidents(client) == []
    assert _penalty_rows(client) == []
    assert client.get("/api/v1/pvz/2").json()["audit_count"] == 0


def test_audit_roles(client):
    seller = TestClient(app)
    seller.headers.update({"X-API-Key": key_for("seller")})
    assert seller.post(URL.format(pvz_id=1), json={"actual": {"1": 6}}).status_code == 403

    pvz = TestClient(app)
    pvz.headers.update({"X-API-Key": key_for("pvz")})
    assert pvz.post(URL.format(pvz_id=1), json={"actual": {"1": 6}}).status_code == 200


def test_audit_penalty_is_capped(client):
    cap = float(audit.p("pvz", "audit_penalty_cap_rub"))
    response = _audit(client, actual={"1": 0, "5": 0})
    assert response.status_code == 200, response.text
    assert response.json()["penalty_total_rub"] == cap
    assert "потолок" in _penalty_rows(client)[0]["note"]