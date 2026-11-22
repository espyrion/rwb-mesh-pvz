"""Сценарии экономики поверх params.yaml: переопределения и безубыточность ПВЗ.

Единственный источник формул — economics/model.py. Здесь только сборка сценария
и подстановка «что если»: бонус ПВЗ, тариф хранения, постоянные расходы, комиссия.
"""
from __future__ import annotations

from copy import deepcopy

from .model import compare, order_economics, pvz_pnl

# Границы «что если» — те же, что в API, чтобы отчёт и страница не расходились.
BOUNDS = {
    "mesh_bonus": (0.0, 100.0),
    "storage_rate": (0.0, 100.0),
    "fixed_cost": (0.0, 200_000.0),
    "commission_pct": (0.0, 40.0),
    "mesh_share": (0.0, 1.0),
    "goods": (100.0, 500_000.0),
    "ops": (1, 500),
    "storage_days": (0.0, 90.0),
}


def with_overrides(
    params: dict,
    *,
    mesh_bonus: float | None = None,
    storage_rate: float | None = None,
    fixed_cost: float | None = None,
    commission_pct: float | None = None,
) -> dict:
    """Копия параметров с подменёнными ставками. Исходный dict не трогаем."""
    data = deepcopy(params)
    if mesh_bonus is not None:
        data["pvz"]["mesh_bonus_rub"] = float(mesh_bonus)
    if storage_rate is not None:
        data["pvz"]["storage_rate_day_rub"] = float(storage_rate)
    if fixed_cost is not None:
        data["pvz"]["fixed_cost_day_rub"] = float(fixed_cost)
    if commission_pct is not None:
        data["fees"]["commission_pct"] = float(commission_pct)
    return data


def pvz_breakeven_ops(params: dict, *, mesh_share: float = 0.3, max_ops: int = 2000) -> int | None:
    """Сколько операций в день нужно ПВЗ, чтобы выйти в ноль.

    Считаем тем же pvz_pnl, что и отчёт: постоянные расходы за день, ячейко-дни
    хранения по среднему сроку, доля заказов без хаба — mesh_share.
    """
    cells = float(params["pvz"]["avg_cells_per_order"])
    days = float(params["scenario_defaults"]["storage_days_typical"])
    for ops in range(1, max_ops + 1):
        result = pvz_pnl(
            params,
            days=1,
            ops=ops,
            qty=ops,
            storage_cell_days=ops * cells * days,
            mesh_ops=int(round(ops * float(mesh_share))),
        )
        if result["profit_rub"] >= 0:
            return ops
    return None


def storage_breakeven_days(params: dict, *, kind: str = "mesh", goods: float = 2500.0) -> float:
    """Срок хранения, при котором прибыль маркетплейса по заказу обнуляется."""
    cells = float(params["pvz"]["avg_cells_per_order"])
    rate = float(params["pvz"]["storage_rate_day_rub"])
    if rate <= 0:
        return 0.0
    base = order_economics(params, kind=kind, goods_rub=goods, cells=cells, storage_days=0.0)
    margin = base["marketplace"]["profit_rub"]
    return round(margin / (rate * cells), 2)


def pvz_day(params: dict, *, ops: int, mesh_share: float) -> dict:
    """P&L точки за один день при заданной загрузке."""
    cells = float(params["pvz"]["avg_cells_per_order"])
    days = float(params["scenario_defaults"]["storage_days_typical"])
    result = pvz_pnl(
        params,
        days=1,
        ops=int(ops),
        qty=int(ops),
        storage_cell_days=int(ops) * cells * days,
        mesh_ops=int(round(int(ops) * float(mesh_share))),
    )
    fixed = float(params["pvz"]["fixed_cost_day_rub"])
    per_op = round((result["profit_rub"] + fixed) / ops, 2) if ops else 0.0
    return {
        **result,
        "ops": int(ops),
        "contribution_per_op_rub": per_op,
        "capacity_ops": int(params["pvz"]["ops_per_day_capacity"]),
    }


def build_scenario(
    params: dict,
    *,
    mesh_bonus: float | None = None,
    storage_rate: float | None = None,
    fixed_cost: float | None = None,
    commission_pct: float | None = None,
    goods: float | None = None,
    mesh_share: float = 0.3,
    ops: int = 60,
    storage_days: float | None = None,
) -> dict:
    """Полный сценарий: заказ mesh, заказ через хаб, сравнение, день ПВЗ, пороги."""
    data = with_overrides(
        params,
        mesh_bonus=mesh_bonus,
        storage_rate=storage_rate,
        fixed_cost=fixed_cost,
        commission_pct=commission_pct,
    )
    goods_rub = float(goods if goods is not None else data["scenario_defaults"]["goods_price_rub"])
    cells = float(data["pvz"]["avg_cells_per_order"])
    days = float(storage_days if storage_days is not None else data["scenario_defaults"]["storage_days_typical"])

    mesh = order_economics(
        data, kind="mesh", goods_rub=goods_rub, cells=cells, storage_days=days,
        commission_pct=commission_pct,
    )
    hub = order_economics(
        data, kind="hub", goods_rub=goods_rub, cells=cells, storage_days=days,
        commission_pct=commission_pct,
    )
    cmp = compare(data, hub, mesh)
    day = pvz_day(data, ops=int(ops), mesh_share=float(mesh_share))
    breakeven = pvz_breakeven_ops(data, mesh_share=float(mesh_share))

    return {
        "params": {
            "mesh_bonus_rub": round(float(data["pvz"]["mesh_bonus_rub"]), 2),
            "storage_rate_day_rub": round(float(data["pvz"]["storage_rate_day_rub"]), 2),
            "fixed_cost_day_rub": round(float(data["pvz"]["fixed_cost_day_rub"]), 2),
            "commission_pct": round(float(data["fees"]["commission_pct"]), 2),
            "goods_rub": round(goods_rub, 2),
            "mesh_share": round(float(mesh_share), 4),
            "storage_days": round(days, 2),
            "cells": cells,
        },
        "order": {"mesh": mesh, "hub": hub, "compare": cmp},
        "pvz_day": day,
        "breakeven": {
            "ops": breakeven,
            "share_of_capacity": round(breakeven / float(data["pvz"]["ops_per_day_capacity"]), 4)
            if breakeven
            else None,
            "capacity_ops": int(data["pvz"]["ops_per_day_capacity"]),
        },
        "storage_breakeven_days": storage_breakeven_days(data, goods=goods_rub),
        "disclaimer": data["meta"]["disclaimer"],
    }
