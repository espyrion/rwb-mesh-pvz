"""Экономический калькулятор: сценарии «что если» и безубыточность ПВЗ."""
from __future__ import annotations

import pytest

SCENARIO = "/api/v1/economics/scenario"


def test_defaults_reproduce_report_numbers(client):
    data = client.get(SCENARIO).json()
    mesh = data["order"]["mesh"]
    hub = data["order"]["hub"]
    # Те же числа, что в docs/ECON_REPORT.md и на слайдах.
    assert mesh["route"]["cost_rub"] == 90.0
    assert mesh["route"]["hours"] == 3.0
    assert mesh["buyer"]["delivery_fee_rub"] == 149.0
    assert mesh["buyer"]["total_rub"] == 2649.0
    assert mesh["pvz"]["revenue"]["mesh_bonus"] == 12.0
    assert hub["route"]["cost_rub"] == 135.5
    assert hub["buyer"]["total_rub"] == 2659.0
    assert hub["buyer"]["hours"] == 30.0
    assert mesh["marketplace"]["profit_rub"] == 382.0
    assert mesh["seller"]["profit_rub"] == 367.5
    assert mesh["chain"]["profit_rub"] == 789.0
    assert data["order"]["compare"]["chain_more_profitable"] is True
    assert data["order"]["compare"]["marketplace_delta_rub"] == 23.5
    assert data["buyer_acceptance"] == {
        "max_cost_increase_pct": 10.0,
        "max_time_increase_hours": 6.0,
    }


def test_bonus_moves_profit_between_parties(client):
    base = client.get(SCENARIO).json()
    higher = client.get(SCENARIO, params={"mesh_bonus": 25}).json()
    assert higher["order"]["mesh"]["pvz"]["profit_rub"] == pytest.approx(
        base["order"]["mesh"]["pvz"]["profit_rub"] + 13, abs=0.01
    )
    # Бонус ПВЗ — это расход маркетплейса: цепочка не растёт.
    assert higher["order"]["mesh"]["chain"]["profit_rub"] == pytest.approx(
        base["order"]["mesh"]["chain"]["profit_rub"], abs=0.01
    )


def test_fixed_cost_moves_breakeven(client):
    base = client.get(SCENARIO).json()
    cheap = client.get(SCENARIO, params={"fixed_cost": 600}).json()
    assert cheap["breakeven"]["ops"] < base["breakeven"]["ops"]
    assert base["breakeven"]["ops"] == 39


def test_storage_rate_moves_margin_between_pvz_and_seller(client):
    base = client.get(SCENARIO).json()
    pricey = client.get(SCENARIO, params={"storage_rate": 10}).json()
    mesh_base = base["order"]["mesh"]
    mesh_pricey = pricey["order"]["mesh"]
    # Хранение — перевод: маркетплейс получает с продавца и отдаёт ПВЗ, его маржа не меняется.
    assert mesh_pricey["marketplace"]["profit_rub"] == pytest.approx(
        mesh_base["marketplace"]["profit_rub"], abs=0.01
    )
    # 1,5 ячейки × 3 дня × +5 ₽ = 22,50 ₽ переходит от продавца к ПВЗ.
    assert mesh_pricey["seller"]["profit_rub"] == pytest.approx(
        mesh_base["seller"]["profit_rub"] - 22.5, abs=0.01
    )
    assert mesh_pricey["pvz"]["profit_rub"] == pytest.approx(
        mesh_base["pvz"]["profit_rub"] + 22.5, abs=0.01
    )
    assert mesh_pricey["chain"]["profit_rub"] == pytest.approx(
        mesh_base["chain"]["profit_rub"], abs=0.01
    )
    assert pricey["storage_breakeven_days"] < base["storage_breakeven_days"]


def test_intercity_four_sides(client):
    data = client.get(SCENARIO).json()["intercity"]
    mesh = data["mesh"]
    assert mesh["route"]["cost_rub"] == 100.5
    assert mesh["route"]["hours"] == 3.0
    assert mesh["seller"]["profit_rub"] == 367.5
    assert mesh["chain"]["profit_rub"] == 778.5
    assert data["compare"]["buyer_delta_rub"] == -10.0
    assert data["compare"]["buyer_delta_pct"] == -0.38
    assert data["compare"]["buyer_delta_hours"] == -27.0
    assert data["compare"]["marketplace_delta_rub"] == 13.0
    assert data["compare"]["chain_delta_rub"] == 25.0
    assert data["compare"]["chain_more_profitable"] is True


def test_sensitivity_and_queue_exposed(client):
    data = client.get(SCENARIO).json()
    assert len(data["sensitivity"]) == 12
    assert data["sensitivity"][0]["share_pct"] == 15.0
    assert data["sensitivity"][0]["chain_per_order_rub"] == 758.83
    assert data["sensitivity"][-1]["share_pct"] == 70.0
    assert data["chain_breakeven"]["share_pct"] == 0.0
    assert data["queue_threshold"]["target_pct"] == 80.0
    assert data["queue_threshold"]["alert_pct"] == 85.0
    alerts = [row for row in data["queue"] if row["utilization"] == 0.85]
    assert alerts and alerts[0]["wait_hours"] == 1.1333


def test_report_endpoint_matches_scenario(client):
    scenario = client.get(SCENARIO).json()
    report = client.get("/api/v1/economics/report").json()
    assert report["city"] == scenario["order"]
    assert report["intercity"] == scenario["intercity"]
    assert report["sensitivity"] == scenario["sensitivity"]
    assert report["queue"] == scenario["queue"]
    assert report["params"]["late_cancel_rate"] == 0.35
    assert report["params"]["peak_surge_multiplier"] == 1.25


def test_chain_identity_holds(client):
    data = client.get(SCENARIO, params={"mesh_share": 0.5, "ops": 40}).json()
    for kind in ("mesh", "hub"):
        order = data["order"][kind]
        parts = sum(order[side]["profit_rub"] for side in ("pvz", "marketplace", "seller"))
        assert parts == pytest.approx(order["chain"]["profit_rub"], abs=0.01)
    day = data["pvz_day"]
    assert day["revenue_total"] - day["costs_total"] == pytest.approx(day["profit_rub"], abs=0.01)


@pytest.mark.parametrize(
    "params",
    [
        {"mesh_share": 1.5},
        {"storage_rate": -1},
        {"ops": 0},
        {"commission_pct": 99},
        {"goods": 10},
    ],
)
def test_bad_input_is_rejected(client, params):
    assert client.get(SCENARIO, params=params).status_code == 422
