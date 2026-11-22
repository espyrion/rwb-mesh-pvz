"""Тесты экономической модели на ручных числах (проверяет жюри)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from economics.model import (  # noqa: E402
    buyer_cost,
    compare,
    load_params,
    order_economics,
    pvz_pnl,
    route_cost,
    storage_cost,
)


def test_route_costs():
    params = load_params()
    assert route_cost(params, "mesh") == 90.0
    assert route_cost(params, "hub") == 135.5
    assert storage_cost(params, cells=1, days=3) == 15.0
    assert storage_cost(params, cells=2, days=10, rate=5) == 100.0


def test_city_order_four_parties():
    params = load_params()
    mesh = order_economics(params, kind="mesh", goods_rub=2500, cells=1, storage_days=3)
    assert mesh["route"]["cost_rub"] == 90.0 and mesh["route"]["hours"] == 3.0
    assert mesh["pvz"]["revenue_total"] == 55.0  # 25 + 15 + 15
    assert mesh["pvz"]["profit_rub"] == 35.0
    assert mesh["marketplace"]["revenue_total"] == 419.0  # 300 + 119
    assert mesh["marketplace"]["profit_rub"] == 274.0
    assert mesh["seller"]["profit_rub"] == 2200.0
    assert mesh["buyer"]["total_rub"] == 2619.0


def test_hub_order_four_parties():
    params = load_params()
    hub = order_economics(params, kind="hub", goods_rub=2500, cells=1, storage_days=3)
    assert hub["route"]["cost_rub"] == 135.5 and hub["route"]["hours"] == 30.0
    assert hub["pvz"]["profit_rub"] == 20.0  # без mesh-бонуса
    assert hub["marketplace"]["profit_rub"] == 283.5
    assert hub["buyer"]["total_rub"] == 2659.0


def test_transfers_do_not_create_chain_profit():
    params = load_params()
    mesh = order_economics(params, kind="mesh", goods_rub=2500, cells=1, storage_days=3)
    external = mesh["route"]["cost_rub"] + 20.0  # логистика + операционные затраты ПВЗ
    assert mesh["chain"]["profit_rub"] == mesh["buyer"]["total_rub"] - external
    assert mesh["chain"]["profit_rub"] == (
        mesh["pvz"]["profit_rub"] + mesh["marketplace"]["profit_rub"] + mesh["seller"]["profit_rub"]
    )


def test_compare_within_acceptance():
    params = load_params()
    base = order_economics(params, kind="hub", goods_rub=2500, cells=1, storage_days=3)
    mesh = order_economics(params, kind="mesh", goods_rub=2500, cells=1, storage_days=3)
    result = compare(params, base, mesh)
    assert result["buyer_delta_rub"] == -40.0
    assert round(result["buyer_delta_pct"], 2) == -1.5
    assert result["buyer_delta_hours"] == -27.0
    assert result["buyer_acceptance"]["within_cost"] is True
    assert result["viable"] is True
    assert result["chain_profit_mesh"] == 2509.0


def test_pvz_breakeven_38_ops():
    params = load_params()
    low = pvz_pnl(params, days=1, ops=37, qty=37, storage_cell_days=37 * 1.5 * 3, mesh_ops=11)
    ok = pvz_pnl(params, days=1, ops=38, qty=38, storage_cell_days=38 * 1.5 * 3, mesh_ops=11)
    assert low["profit_rub"] < 0
    assert ok["profit_rub"] >= 0


def test_buyer_cost_formula():
    params = load_params()
    assert buyer_cost(params, goods_rub=1000, kind="mesh")["total_rub"] == 1119.0
    assert buyer_cost(params, goods_rub=1000, kind="hub")["total_rub"] == 1159.0
