"""Экономический калькулятор: «что если» по ставкам модели.

Формулы не дублируются — расчёт живёт в economics/scenario.py и economics/model.py,
тех же модулях, что и docs/ECON_REPORT.md. Значения по умолчанию берутся из
economics/params.yaml, а не из литералов в коде.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from economics.scenario import BOUNDS, build_report, build_scenario

from ..config import params
from ..responses import ReportOut, ScenarioOut

router = APIRouter(tags=["economics"])

_DEFAULTS = params()


@router.get("/economics/scenario", response_model=ScenarioOut)
def economics_scenario(
    mesh_bonus: float = Query(
        default=float(_DEFAULTS["pvz"]["mesh_bonus_rub"]),
        ge=BOUNDS["mesh_bonus"][0],
        le=BOUNDS["mesh_bonus"][1],
        description="Бонус ПВЗ за заказ без хаба, ₽",
    ),
    storage_rate: float = Query(
        default=float(_DEFAULTS["pvz"]["storage_rate_day_rub"]),
        ge=BOUNDS["storage_rate"][0],
        le=BOUNDS["storage_rate"][1],
        description="Тариф хранения, ₽/ячейка·день",
    ),
    fixed_cost: float = Query(
        default=float(_DEFAULTS["pvz"]["fixed_cost_day_rub"]),
        ge=BOUNDS["fixed_cost"][0],
        le=BOUNDS["fixed_cost"][1],
        description="Условно-постоянные затраты ПВЗ, ₽/день",
    ),
    commission_pct: float = Query(
        default=float(_DEFAULTS["fees"]["commission_pct"]),
        ge=BOUNDS["commission_pct"][0],
        le=BOUNDS["commission_pct"][1],
        description="Комиссия маркетплейса, %",
    ),
    seller_logistics_fee: float = Query(
        default=float(_DEFAULTS["fees"]["seller_logistics_fee_rub"]),
        ge=BOUNDS["seller_logistics_fee"][0],
        le=BOUNDS["seller_logistics_fee"][1],
        description="Логистическое удержание с продавца, ₽/заказ",
    ),
    goods: float = Query(
        default=float(_DEFAULTS["scenario_defaults"]["goods_price_rub"]),
        ge=BOUNDS["goods"][0],
        le=BOUNDS["goods"][1],
        description="Цена товара, ₽",
    ),
    mesh_share: float = Query(
        default=float(_DEFAULTS["scenario_defaults"]["pvz_day_mesh_share_pct"]) / 100.0,
        ge=0.0,
        le=1.0,
        description="Доля заказов без хаба для расчёта дня ПВЗ",
    ),
    ops: int = Query(
        default=int(_DEFAULTS["scenario_defaults"]["pvz_day_ops"]),
        ge=BOUNDS["ops"][0],
        le=BOUNDS["ops"][1],
        description="Операций ПВЗ в день для расчёта прибыли",
    ),
    storage_days: float = Query(
        default=float(_DEFAULTS["scenario_defaults"]["storage_days_typical"]),
        ge=BOUNDS["storage_days"][0],
        le=BOUNDS["storage_days"][1],
        description="Средний срок хранения, дней",
    ),
):
    """Пересчитывает экономику четырёх сторон при изменённых ставках."""
    return build_scenario(
        params(),
        mesh_bonus=mesh_bonus,
        storage_rate=storage_rate,
        fixed_cost=fixed_cost,
        commission_pct=commission_pct,
        seller_logistics_fee=seller_logistics_fee,
        goods=goods,
        mesh_share=mesh_share,
        ops=ops,
        storage_days=storage_days,
    )


@router.get("/economics/report", response_model=ReportOut)
def economics_report():
    """Тот же расчёт по умолчанию, что в docs/ECON_REPORT.md: город, межгород, очередь, чувствительность."""
    return build_report(params())
