"""Скоринг размещения SKU: mesh_pvz vs hub, границы критериев и порога, отказы."""
import pytest
from fastapi.testclient import TestClient

from app import placement
from app.auth import key_for
from app.main import app
from conftest import create_order

URL = "/api/v1/catalog/placement-score"


def _score(**kwargs):
    base = {"sku": "TD-1001", "qty": 2, "cell_size": 1, "demand_per_day": 6.0}
    base.update(kwargs)
    return placement.score_placement(**base)


def test_fast_local_sku_is_recommended_for_mesh(client):
    data = client.post(URL, json={"sku": "TD-1003", "qty": 2, "cell_size": 1, "demand_per_day": 6}).json()
    assert data["recommendation"] == "mesh_pvz"
    assert data["score"] > data["threshold"]
    assert data["confidence_pct"] > 0
    assert data["expected_storage_days"] < 1
    assert [c["id"] for c in data["components"]] == ["demand", "turnover", "footprint"]
    reasons = " ".join(data["reasons"])
    for key in (
        "pvz.placement_demand_target_per_day",
        "pvz.placement_fast_turnover_days",
        "pvz.placement_footprint_max_cells",
        "pvz.placement_score_threshold",
        "pvz.storage_rate_day_rub",
    ):
        assert key in reasons, key


def test_slow_bulky_sku_is_recommended_for_hub(client):
    data = client.post(URL, json={"sku": "SM-2001", "qty": 40, "cell_size": 3, "demand_per_day": 0.1}).json()
    assert data["recommendation"] == "hub"
    assert data["score"] < data["threshold"]
    # Спрос 40 ÷ 0,1 = 400 дней, потолок — pvz.placement_max_expected_days.
    assert data["expected_storage_days"] == 60.0
    assert any("placement_max_expected_days" in reason for reason in data["reasons"])


def test_zero_demand_hits_storage_ceiling():
    data = _score(qty=10, demand_per_day=0.0)
    assert data["expected_storage_days"] == 60.0
    assert data["demand_per_day"] == 0.0
    assert data["recommendation"] == "hub"


def test_demand_criterion_boundaries():
    target = float(placement.p("pvz", "placement_demand_target_per_day"))
    at_target = _score(demand_per_day=target)
    assert at_target["components"][0]["raw"] == 1.0
    above = _score(demand_per_day=target * 3)
    assert above["components"][0]["raw"] == 1.0  # обрезано сверху
    assert above["score"] <= 100.0
    below = _score(demand_per_day=target / 2)
    assert below["components"][0]["raw"] == pytest.approx(0.5)


def test_turnover_criterion_boundaries():
    fast = float(placement.p("pvz", "placement_fast_turnover_days"))
    slow = float(placement.p("pvz", "placement_slow_turnover_days"))
    at_fast = _score(turnover_days=fast)
    assert at_fast["components"][1]["raw"] == 1.0
    assert at_fast["expected_storage_days"] == fast
    at_slow = _score(turnover_days=slow)
    assert at_slow["components"][1]["raw"] == 0.0
    midpoint = _score(turnover_days=(fast + slow) / 2)
    assert midpoint["components"][1]["raw"] == pytest.approx(0.5)


def test_footprint_criterion_boundaries():
    max_cells = int(placement.p("pvz", "placement_footprint_max_cells"))
    single = _score(cell_size=1)
    assert single["components"][2]["raw"] == 1.0
    at_max = _score(cell_size=max_cells)
    assert at_max["components"][2]["raw"] == 0.0


def test_threshold_is_read_from_params(monkeypatch):
    """Порог — параметр, а не константа: сдвиг порога переворачивает рекомендацию."""
    base = _score(qty=6, cell_size=2, demand_per_day=2.0)
    assert base["recommendation"] == "mesh_pvz"
    real = placement.p

    def fake(key_section, key, default=None):
        if (key_section, key) == ("pvz", "placement_score_threshold"):
            return 100.0
        return real(key_section, key, default=default)

    monkeypatch.setattr(placement, "p", fake)
    flipped = _score(qty=6, cell_size=2, demand_per_day=2.0)
    assert flipped["score"] < flipped["threshold"]
    assert flipped["recommendation"] == "hub"


def test_capacity_beats_score(client):
    pvz = client.get("/api/v1/pvz/4").json()
    data = client.post(
        URL,
        json={"sku": "SM-2001", "qty": 20, "cell_size": 3, "demand_per_day": 6, "pvz_id": 4},
    ).json()
    assert data["cells_total"] > pvz["free_cells"]
    assert data["capacity_override"] is True
    assert data["recommendation"] == "hub"
    assert data["confidence_pct"] == 100.0
    assert any("Ёмкость важнее score" in reason for reason in data["reasons"])


def test_demand_observed_from_orders_and_assumed_without_them(client):
    create_order(client, item_id=1)
    observed = client.post(URL, json={"sku": "TD-1001", "qty": 2, "cell_size": 2, "pvz_id": 1}).json()
    assert observed["demand_source"] == "observed"
    assert observed["demand_window_days"] == int(placement.p("pvz", "placement_demand_window_days"))
    assert observed["demand_per_day"] > 0

    assumed = client.post(URL, json={"sku": "NO-SUCH-SKU", "qty": 2, "cell_size": 1, "city": "Казань"}).json()
    assert assumed["demand_source"] == "assumed"
    assert assumed["demand_per_day"] == float(placement.p("pvz", "placement_default_demand_per_day"))


def test_placement_score_validation(client):
    assert client.post(URL, json={"qty": 1, "cell_size": 1}).status_code == 422
    assert client.post(URL, json={"sku": "  ", "qty": 1, "cell_size": 1}).status_code == 422
    assert client.post(URL, json={"sku": "X", "qty": 0, "cell_size": 1}).status_code == 422
    assert client.post(URL, json={"sku": "X", "qty": 1, "cell_size": 11}).status_code == 422
    assert client.post(URL, json={"sku": "X", "qty": 1, "cell_size": 1, "demand_per_day": -1}).status_code == 422
    assert client.post(URL, json={"sku": "X", "qty": 1, "cell_size": 1, "turnover_days": 0}).status_code == 422
    assert client.post(URL, json={"sku": "X", "qty": 1, "cell_size": 1, "pvz_id": 999}).status_code == 404
    hub = client.post(URL, json={"sku": "X", "qty": 1, "cell_size": 1, "pvz_id": 9})
    assert hub.status_code == 422 and "хаб" in hub.json()["detail"]


def test_placement_score_roles(client):
    body = {"sku": "TD-1001", "qty": 1, "cell_size": 1, "demand_per_day": 1}
    seller = TestClient(app)
    seller.headers.update({"X-API-Key": key_for("seller")})
    assert seller.post(URL, json=body).status_code == 200

    pvz = TestClient(app)
    pvz.headers.update({"X-API-Key": key_for("pvz")})
    assert pvz.post(URL, json=body).status_code == 403


def test_acceptance_returns_recommendation_only_when_flag_on(client, monkeypatch):
    body = {"sku": "SM-2003", "seller_id": 2, "title": "Термокружка 450 мл", "qty": 4, "cell_size": 1, "pvz_id": 2}
    off = client.post("/api/v1/items", json=body)
    assert off.status_code == 201
    assert "placement" not in off.json()

    assert placement.auto_enabled() is False  # флаг в params.yaml выключен по умолчанию
    monkeypatch.setattr(placement, "auto_enabled", lambda: True)
    body["sku"] = "SM-2003-B"
    on = client.post("/api/v1/items", json=body)
    assert on.status_code == 201
    block = on.json()["placement"]
    assert block["recommendation"] in {"mesh_pvz", "hub"}
    assert block["reasons"] and block["score"] is not None
    assert block["demand_source"] in {"observed", "assumed"}