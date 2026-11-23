"""Тесты экономической модели на ручных числах (проверяет жюри).

Числа посчитаны вручную из economics/params.yaml, а не сняты с выхода модели:
так тест ловит дрейф формул, а не повторяет их.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from economics.model import (  # noqa: E402
    buyer_cost,
    compare,
    intercity_economics,
    load_params,
    order_economics,
    pvz_pnl,
    queue_wait_hours,
    route_cost,
    storage_cost,
)
from economics.scenario import (  # noqa: E402
    chain_breakeven_share,
    pvz_breakeven_ops,
    share_sensitivity,
    storage_breakeven_days,
)
from economics.network import queue_load_table  # noqa: E402


def test_route_costs():
    params = load_params()
    assert route_cost(params, "mesh") == 90.0
    assert route_cost(params, "intercity_mesh") == 100.5  # 90 локально + 10,5 магистраль
    assert route_cost(params, "hub") == 135.5  # 70 + 10,5 + 55
    assert storage_cost(params, cells=1, days=3) == 15.0
    assert storage_cost(params, cells=2, days=10, rate=5) == 100.0


def test_city_order_four_parties():
    params = load_params()
    mesh = order_economics(params, kind="mesh", goods_rub=2500, cells=1, storage_days=3)
    assert mesh["route"]["cost_rub"] == 90.0 and mesh["route"]["hours"] == 3.0
    # ПВЗ: 25 операция + 15 хранение + 15 бонус; затраты 8 приёмка + 12 сборка.
    assert mesh["pvz"]["revenue_total"] == 55.0
    assert mesh["pvz"]["profit_rub"] == 35.0
    # МП: 300 комиссия + 129 доставка + 60 удержание продавца + 15 хранение.
    # Затраты: 90 логистика + 15 хранение ПВЗ + 25 вознаграждение + 15 бонус.
    assert mesh["marketplace"]["revenue_total"] == 504.0
    assert mesh["marketplace"]["profit_rub"] == 359.0
    # Продавец: 2500 − 1750 закупка − 300 комиссия − 15 хранение − 60 логистика.
    assert mesh["seller"]["revenue_total"] == 2500.0
    assert mesh["seller"]["costs_total"] == 2125.0
    assert mesh["seller"]["profit_rub"] == 375.0
    assert mesh["seller"]["margin_pct"] == 15.0
    assert mesh["buyer"]["total_rub"] == 2629.0
    assert mesh["chain"]["profit_rub"] == 769.0


def test_hub_order_four_parties():
    params = load_params()
    hub = order_economics(params, kind="hub", goods_rub=2500, cells=1, storage_days=3)
    assert hub["route"]["cost_rub"] == 135.5 and hub["route"]["hours"] == 30.0
    assert hub["pvz"]["profit_rub"] == 20.0  # без mesh-бонуса: 25 + 15 − 20
    # МП: 300 + 159 + 60 + 15 = 534; затраты 135,5 + 15 + 25 = 175,5.
    assert hub["marketplace"]["profit_rub"] == 358.5
    assert hub["seller"]["profit_rub"] == 375.0  # схема не меняет удержания продавца
    assert hub["buyer"]["total_rub"] == 2659.0
    assert hub["chain"]["profit_rub"] == 753.5


def test_transfers_do_not_create_chain_profit():
    params = load_params()
    mesh = order_economics(params, kind="mesh", goods_rub=2500, cells=1, storage_days=3)
    # Внешние затраты: логистика + приёмка + сборка + закупка товара.
    external = 90.0 + 8.0 + 12.0 + 1750.0
    assert mesh["chain"]["profit_rub"] == mesh["buyer"]["total_rub"] - external
    assert mesh["chain"]["profit_rub"] == (
        mesh["pvz"]["profit_rub"] + mesh["marketplace"]["profit_rub"] + mesh["seller"]["profit_rub"]
    )
    # Хранение — перевод: меняет срок, но не прибыль цепочки.
    long_storage = order_economics(params, kind="mesh", goods_rub=2500, cells=1, storage_days=10)
    assert long_storage["chain"]["profit_rub"] == mesh["chain"]["profit_rub"]
    assert long_storage["pvz"]["profit_rub"] > mesh["pvz"]["profit_rub"]
    assert long_storage["seller"]["profit_rub"] < mesh["seller"]["profit_rub"]


def test_compare_within_acceptance():
    params = load_params()
    base = order_economics(params, kind="hub", goods_rub=2500, cells=1, storage_days=3)
    mesh = order_economics(params, kind="mesh", goods_rub=2500, cells=1, storage_days=3)
    result = compare(params, base, mesh)
    assert result["buyer_delta_rub"] == -30.0
    assert result["buyer_delta_pct"] == -1.13
    assert result["buyer_delta_hours"] == -27.0
    assert result["buyer_delta_hours_pct"] == -90.0
    assert result["pvz_delta_rub"] == 15.0
    assert result["marketplace_delta_rub"] == 0.5
    assert result["seller_delta_rub"] == 0.0
    assert result["chain_delta_rub"] == 15.5
    assert result["chain_more_profitable"] is True
    assert result["buyer_acceptance"]["within_cost"] is True
    assert result["buyer_acceptance"]["within_time"] is True
    assert result["viable"] is True


def test_intercity_mesh():
    params = load_params()
    intercity = intercity_economics(params, goods_rub=2500, cells=1, storage_days=3)
    mesh = intercity["mesh"]
    base = intercity["base"]
    assert mesh["route"]["cost_rub"] == 100.5 and mesh["route"]["hours"] == 3.0
    assert mesh["buyer"]["total_rub"] == 2629.0
    assert mesh["marketplace"]["profit_rub"] == 348.5  # 359 − 10,5 магистрали
    assert mesh["pvz"]["profit_rub"] == 35.0
    assert mesh["seller"]["profit_rub"] == 375.0
    assert mesh["chain"]["profit_rub"] == 758.5
    cmp = intercity["compare"]
    assert cmp["buyer_delta_rub"] == -30.0
    assert cmp["buyer_delta_hours"] == -27.0
    assert cmp["marketplace_delta_rub"] == -10.0
    assert cmp["chain_delta_rub"] == 5.0
    assert cmp["chain_more_profitable"] is True
    assert base["chain"]["profit_rub"] == 753.5


def test_pvz_breakeven_38_ops():
    params = load_params()
    low = pvz_pnl(params, days=1, ops=37, qty=37, storage_cell_days=37 * 1.5 * 3, mesh_ops=11)
    ok = pvz_pnl(params, days=1, ops=38, qty=38, storage_cell_days=38 * 1.5 * 3, mesh_ops=11)
    assert low["profit_rub"] < 0
    assert ok["profit_rub"] >= 0
    assert pvz_breakeven_ops(params, mesh_share=0.3) == 38


def test_storage_breakeven_is_seller_margin():
    params = load_params()
    # Продавец при нулевом сроке: 2500 − 1750 − 300 − 60 = 390; 390 / 7,5 = 52 дня.
    assert storage_breakeven_days(params) == 52.0


def test_buyer_cost_formula():
    params = load_params()
    assert buyer_cost(params, goods_rub=1000, kind="mesh")["total_rub"] == 1129.0
    assert buyer_cost(params, goods_rub=1000, kind="hub")["total_rub"] == 1159.0


def test_sensitivity_and_chain_breakeven():
    params = load_params()
    rows = share_sensitivity(params)
    shares = params["network"]["sensitivity_mesh_share_pct"]
    assert [row["share_pct"] for row in rows] == shares
    assert rows[0]["chain_per_order_rub"] == 755.83  # 753,5 + 15% × 15,5
    assert rows[-1]["chain_per_order_rub"] == 764.35
    assert rows[0]["network_chain_rub"] == 377_912.5
    assert rows[0]["pvz_breakeven_ops"] == 41
    assert rows[-1]["pvz_breakeven_ops"] == 32
    breakeven = chain_breakeven_share(params)
    assert breakeven["share_pct"] == 0.0
    assert breakeven["chain_delta_per_order_rub"] == 15.5


def test_queue_grows_faster_than_load():
    params = load_params()
    assert queue_wait_hours(params, 0.5) == 0.2
    assert queue_wait_hours(params, 0.8) == 0.8
    assert queue_wait_hours(params, 0.85) == 1.1333
    assert queue_wait_hours(params, 0.9) == 1.8
    assert queue_wait_hours(params, 0.95) == 3.8
    # Выше 95% — линейная перегрузка: 3,8 + 5% × 24 = 5,0 ч.
    assert queue_wait_hours(params, 1.0) == 5.0
    table = queue_load_table(params)
    assert [row["ops"] for row in table] == [60, 72, 84, 96, 102, 108, 114, 120]
    assert table[4]["wait_multiplier"] == 5.67
