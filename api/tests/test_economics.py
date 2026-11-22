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
    assert mesh["buyer"]["delivery_fee_rub"] == 119.0
    assert mesh["buyer"]["total_rub"] == 2619.0
    assert mesh["pvz"]["revenue"]["mesh_bonus"] == 15.0
    assert hub["route"]["cost_rub"] == 135.5
    assert hub["buyer"]["total_rub"] == 2659.0
    assert hub["buyer"]["hours"] == 30.0
    assert mesh["marketplace"]["profit_rub"] == 266.5
    assert mesh["chain"]["profit_rub"] == 2509.0


def test_bonus_moves_profit_between_parties(client):
    base = client.get(SCENARIO).json()
    higher = client.get(SCENARIO, params={"mesh_bonus": 25}).json()
    assert higher["order"]["mesh"]["pvz"]["profit_rub"] == pytest.approx(
        base["order"]["mesh"]["pvz"]["profit_rub"] + 10, abs=0.01
    )
    # Бонус ПВЗ — это расход маркетплейса: цепочка не растёт.
    assert higher["order"]["mesh"]["chain"]["profit_rub"] == pytest.approx(
        base["order"]["mesh"]["chain"]["profit_rub"], abs=0.01
    )


def test_fixed_cost_moves_breakeven(client):
    base = client.get(SCENARIO).json()
    cheap = client.get(SCENARIO, params={"fixed_cost": 600}).json()
    assert cheap["breakeven"]["ops"] < base["breakeven"]["ops"]
    assert base["breakeven"]["ops"] == 38


def test_storage_rate_changes_marketplace_margin(client):
    base = client.get(SCENARIO).json()
    pricey = client.get(SCENARIO, params={"storage_rate": 10}).json()
    delta = base["order"]["mesh"]["marketplace"]["profit_rub"] - pricey["order"]["mesh"]["marketplace"]["profit_rub"]
    # 1,5 ячейки × 3 дня × +5 ₽ = 22,50 ₽
    assert delta == pytest.approx(22.5, abs=0.01)
    assert pricey["storage_breakeven_days"] < base["storage_breakeven_days"]


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
