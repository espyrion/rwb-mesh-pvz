"""Регрессии по списку аудита 2026-11-25.

Каждый тест воспроизводит конкретный дефект (отчёт печатает хардкод, схема
допускает 100% queue_cap, ползунок не доходит до чувствительности и т.д.) и
фиксирует исправленное поведение. Числа считаются из модели, а не копируются
из docs/ECON_REPORT.md.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from economics.model import (  # noqa: E402
    load_params,
    order_economics,
    queue_wait_hours,
    route_cost,
    route_hours,
)
from economics.network import percentile  # noqa: E402
from economics.report import build  # noqa: E402
from economics.scenario import (  # noqa: E402
    build_scenario,
    pvz_breakeven_ops,
    storage_breakeven_days,
)


def _params() -> dict:
    return load_params()


# 1) queue_cap_pct = 100: схема отклоняет, модель не делит на ноль.
def test_schema_rejects_queue_cap_100():
    from pydantic import ValidationError

    from api.app import params_schema

    raw = deepcopy(_params())
    raw["network"]["queue_cap_pct"] = 100.0
    with pytest.raises(ValidationError):
        params_schema.validate(raw)


def test_queue_wait_survives_cap_100():
    params = deepcopy(_params())
    params["network"]["queue_cap_pct"] = 100.0
    wait = queue_wait_hours(params, 1.2)  # раньше: ZeroDivisionError
    assert wait == pytest.approx(wait)  # не NaN
    assert 0.0 < wait < 1000.0
    # Защита не должна менять поведение при штатном cap = 95%.
    assert queue_wait_hours(_params(), 0.95) == 3.8


# 2) Строка инцидента считается из модели, а не хардкодом.
def test_report_incident_row_computed_from_model():
    params = _params()
    mesh = order_economics(params, kind="mesh", goods_rub=2500, cells=1.5, storage_days=3)
    hub = order_economics(params, kind="hub", goods_rub=2500, cells=1.5, storage_days=3)
    margin = round(hub["marketplace"]["profit_rub"] - (route_cost(params, "hub") - route_cost(params, "mesh")), 2)
    delta_hours = round(route_hours(params, "hub") - route_hours(params, "mesh"), 1)
    threshold = float(params["fees"]["late_threshold_hours"])
    assert margin == 313.0 and delta_hours == 27.0 and threshold == 12.0

    text = build()
    assert "313,00" in text
    assert "45,50" in text  # перепробег = route_cost(hub) − route_cost(mesh)
    assert "27,0 ч" in text
    assert "SLA не сохраняется" in text
    assert "сохраняет SLA" not in text


# 3) Формулировки «прибыльнее / не ограничивает долю / приемлема» — по факту.
def test_report_viable_word_follows_compare():
    params = deepcopy(_params())
    params["pvz"]["op_fee_rub"] = 400.0  # маржа МП на заказ уходит в минус
    text = build(params)
    assert "viable = False" in text
    assert "не приемлема" in text


def test_report_chain_words_follow_sign():
    params = deepcopy(_params())
    params["logistics"]["local_delivery_cost_rub"] = 130.0  # mesh дороже хаба по цепочке
    text = build(params)
    assert "менее прибыльна" in text
    assert "не ограничивает долю" not in text


# 4) Недостижимый порог безубыточности не превращается в 0.
def test_report_unreachable_breakeven_is_not_zero():
    params = deepcopy(_params())
    params["pvz"]["fixed_cost_day_rub"] = 200_000.0
    text = build(params)
    assert "недостижим при 120 операциях/день" in text
    assert "ноль при **0 операциях" not in text


# 5) Штраф больше маржи МП: доля маржи не считается.
def test_report_penalty_over_margin():
    params = deepcopy(_params())
    params["pvz"]["op_fee_rub"] = 400.0  # маржа МП < штрафа
    text = build(params)
    assert "штраф превышает маржу" in text


# 6) Одна точка в sensitivity_mesh_share_pct не роняет отчёт.
def test_report_single_sensitivity_point():
    params = deepcopy(_params())
    params["network"]["sensitivity_mesh_share_pct"] = [30]
    text = build(params)
    assert "Чувствительность к доле локальных пар" in text
    assert "30,00%" in text


def test_schema_requires_two_sensitivity_points():
    from pydantic import ValidationError

    from api.app import params_schema

    raw = deepcopy(_params())
    raw["network"]["sensitivity_mesh_share_pct"] = [30]
    with pytest.raises(ValidationError):
        params_schema.validate(raw)


# 7) Ползунки goods / storage_days доходят до чувствительности и безубыточности.
def test_scenario_sliders_reach_sensitivity_and_breakeven():
    params = _params()
    scenario = build_scenario(params, goods=5000.0, storage_days=10.0)
    cells = float(params["pvz"]["avg_cells_per_order"])
    hub = order_economics(params, kind="hub", goods_rub=5000.0, cells=cells, storage_days=10.0)
    mesh = order_economics(params, kind="mesh", goods_rub=5000.0, cells=cells, storage_days=10.0)

    first = scenario["sensitivity"][0]
    share = first["share_pct"] / 100.0
    expected_chain = hub["chain"]["profit_rub"] + share * (
        mesh["chain"]["profit_rub"] - hub["chain"]["profit_rub"]
    )
    assert first["chain_per_order_rub"] == pytest.approx(expected_chain, abs=0.02)
    assert scenario["chain_breakeven"]["mesh_chain_per_order_rub"] == mesh["chain"]["profit_rub"]

    # День ПВЗ и порог безубыточности — тоже на заданном сроке хранения.
    ops = int(params["scenario_defaults"]["pvz_day_ops"])
    assert scenario["pvz_day"]["revenue"]["storage"] == pytest.approx(
        ops * cells * 10.0 * float(params["pvz"]["storage_rate_day_rub"]), abs=0.01
    )
    patched = deepcopy(params)
    patched["scenario_defaults"]["storage_days_typical"] = 10
    assert scenario["breakeven"]["ops"] == pvz_breakeven_ops(patched, mesh_share=0.3)
    assert scenario["params"]["goods_rub"] == 5000.0
    assert scenario["params"]["storage_days"] == 10.0

    # Глобальные params не мутированы.
    assert params["scenario_defaults"]["goods_price_rub"] == 2500.0
    assert params["scenario_defaults"]["storage_days_typical"] == 3


# 8) Убыточный продавец / нулевой тариф → порог хранения None, а не 0.
def test_storage_breakeven_none_when_seller_loses():
    params = deepcopy(_params())
    params["seller"]["goods_cost_pct"] = 95.0
    assert storage_breakeven_days(params) is None

    zero_rate = deepcopy(_params())
    zero_rate["pvz"]["storage_rate_day_rub"] = 0.0
    assert storage_breakeven_days(zero_rate) is None


def test_report_survives_unreachable_storage_breakeven():
    params = deepcopy(_params())
    params["pvz"]["storage_rate_day_rub"] = 0.0
    text = build(params)
    assert "порог хранения недостижим" in text


# 9) Неизвестный kind — ValueError, а не тихий расчёт «как хаб».
def test_unknown_route_kind_raises():
    params = _params()
    for func in (route_cost, route_hours):
        with pytest.raises(ValueError, match="kind"):
            func(params, "drone")
    with pytest.raises(ValueError, match="kind"):
        order_economics(params, kind="drone")


# 10) percentile: правка на линейную интерполяцию ОТЛОЖЕНА — она двигает p95
# в docs/SCALE.md (заказ 500: 30,2 → 30,3 ч), а файл генерирует
# scripts/simulate.py и сверяется в CI. Фиксируем текущее поведение и границу:
# p95 по одному значению стабилен, пустой список — 0.
def test_percentile_current_behaviour_is_documented():
    assert percentile([], 0.95) == 0.0
    assert percentile([7.0], 0.95) == 7.0
    # Индекс округляется: это не истинный p95, см. докстринг percentile().
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0
    assert percentile([1.0, 2.0], 0.5) == 1.0
