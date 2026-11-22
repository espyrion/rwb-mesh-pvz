"""Симуляция дня сети: детерминизм, сходимость денег, потолок мощности."""
from __future__ import annotations

import pytest

from economics.model import load_params
from economics.network import simulate_day

PATH = "/home/stingcorpse/mesh-pvz/economics/params.yaml"


@pytest.fixture(scope="module")
def params():
    return load_params(PATH)


def test_same_seed_same_numbers(params):
    first = simulate_day(params, orders=200, seed=7)
    second = simulate_day(params, orders=200, seed=7)
    assert first == second


def test_seed_changes_assignment(params):
    first = simulate_day(params, orders=200, seed=7)
    second = simulate_day(params, orders=200, seed=8)
    assert [n["ops"] for n in first["modes"]["mesh"]["nodes"]] != [n["ops"] for n in second["modes"]["mesh"]["nodes"]]


def test_hub_mode_has_no_mesh_orders(params):
    data = simulate_day(params, orders=100, seed=1)
    assert data["modes"]["hub"]["mesh_share"] == 0.0
    assert data["modes"]["hub"]["avg_hours_mesh"] is None


def test_mesh_share_follows_local_share(params):
    data = simulate_day(params, orders=1000, seed=3, p_local=0.62)
    assert data["modes"]["mesh"]["mesh_share"] == pytest.approx(0.62, abs=0.04)


def test_money_adds_up(params):
    data = simulate_day(params, orders=300, seed=5)
    for mode in ("mesh", "hub"):
        block = data["modes"][mode]
        parts = block["pvz_profit_rub"] + block["marketplace_profit_rub"] + block["seller_profit_rub"]
        assert parts == pytest.approx(block["chain_profit_rub"], abs=0.02)
        assert block["logistics_rub"] / 300 == pytest.approx(
            block["logistics_per_order_rub"], abs=0.01
        )


def test_eight_nodes_hold_500_but_not_2000(params):
    small = simulate_day(params, orders=500, seed=11)
    big = simulate_day(params, orders=2000, seed=11)
    assert small["modes"]["mesh"]["overloaded_nodes"] == 0
    assert small["modes"]["mesh"]["late_orders"] == 0
    assert big["modes"]["mesh"]["overloaded_nodes"] == 8
    assert big["modes"]["mesh"]["late_orders"] == 2000
    # Перегрузка бьёт по сроку, а не по стоимости: логистика на заказ почти та же.
    assert big["modes"]["mesh"]["logistics_per_order_rub"] == pytest.approx(
        small["modes"]["mesh"]["logistics_per_order_rub"], abs=1.5
    )


def test_mesh_is_cheaper_for_buyer_and_chain(params):
    data = simulate_day(params, orders=500, seed=2)
    assert data["delta"]["buyer_rub"] < 0
    assert data["delta"]["logistics_rub"] < 0
    assert data["delta"]["chain_rub"] > 0
