"""Экономический калькулятор: «что если» по ставкам модели.

Формулы не дублируются — расчёт живёт в economics/scenario.py и economics/model.py,
тех же модулях, что и docs/ECON_REPORT.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from economics.scenario import BOUNDS, build_scenario

from ..config import params
from ..responses import ScenarioOut

router = APIRouter(tags=["economics"])


@router.get("/economics/scenario", response_model=ScenarioOut)
def economics_scenario(
    mesh_bonus: float = Query(default=15.0, ge=BOUNDS["mesh_bonus"][0], le=BOUNDS["mesh_bonus"][1],
                              description="Бонус ПВЗ за заказ без хаба, ₽"),
    storage_rate: float = Query(default=5.0, ge=BOUNDS["storage_rate"][0], le=BOUNDS["storage_rate"][1],
                                description="Тариф хранения, ₽/ячейка·день"),
    fixed_cost: float = Query(default=1200.0, ge=BOUNDS["fixed_cost"][0], le=BOUNDS["fixed_cost"][1],
                              description="Условно-постоянные затраты ПВЗ, ₽/день"),
    commission_pct: float = Query(default=12.0, ge=BOUNDS["commission_pct"][0], le=BOUNDS["commission_pct"][1],
                                  description="Комиссия маркетплейса, %"),
    goods: float = Query(default=2500.0, ge=BOUNDS["goods"][0], le=BOUNDS["goods"][1],
                         description="Цена товара, ₽"),
    mesh_share: float = Query(default=0.3, ge=0.0, le=1.0,
                              description="Доля заказов без хаба для расчёта дня ПВЗ"),
    ops: int = Query(default=60, ge=BOUNDS["ops"][0], le=BOUNDS["ops"][1],
                     description="Операций ПВЗ в день для расчёта прибыли"),
    storage_days: float = Query(default=3.0, ge=BOUNDS["storage_days"][0], le=BOUNDS["storage_days"][1],
                                description="Средний срок хранения, дней"),
):
    """Пересчитывает экономику четырёх сторон при изменённых ставках."""
    return build_scenario(
        params(),
        mesh_bonus=mesh_bonus,
        storage_rate=storage_rate,
        fixed_cost=fixed_cost,
        commission_pct=commission_pct,
        goods=goods,
        mesh_share=mesh_share,
        ops=ops,
        storage_days=storage_days,
    )
