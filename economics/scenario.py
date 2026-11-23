"""Сценарии экономики поверх params.yaml: переопределения и безубыточность ПВЗ.

Единственный источник формул — economics/model.py и economics/network.py. Здесь
только сборка сценария и подстановка «что если»: бонус ПВЗ, тариф хранения,
постоянные расходы, комиссия, логистическое удержание продавца.
"""
from __future__ import annotations

from copy import deepcopy

from .model import (
    compare,
    intercity_economics,
    order_economics,
    pvz_pnl,
    queue_wait_hours,
)
from .network import queue_load_table

# Границы «что если» — те же, что в API, чтобы отчёт и страница не расходились.
BOUNDS = {
    "mesh_bonus": (0.0, 100.0),
    "storage_rate": (0.0, 100.0),
    "fixed_cost": (0.0, 200_000.0),
    "commission_pct": (0.0, 40.0),
    "seller_logistics_fee": (0.0, 500.0),
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
    seller_logistics_fee: float | None = None,
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
    if seller_logistics_fee is not None:
        data["fees"]["seller_logistics_fee_rub"] = float(seller_logistics_fee)
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
    """Срок хранения, при котором прибыль продавца по заказу обнуляется.

    Хранение — перевод маркетплейсу/ПВЗ, поэтому маржа маркетплейса от срока не
    зависит; риск длительного хранения несёт продавец. Считаем, за сколько дней
    плата за хранение съедает его прибыль при нулевом сроке.
    """
    cells = float(params["pvz"]["avg_cells_per_order"])
    rate = float(params["pvz"]["storage_rate_day_rub"])
    if rate <= 0:
        return 0.0
    base = order_economics(params, kind=kind, goods_rub=goods, cells=cells, storage_days=0.0)
    margin = base["seller"]["profit_rub"]
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


def share_sensitivity(params: dict, *, orders: int | None = None) -> list[dict]:
    """Экономика сети при доле локальных пар из params.yaml (шаг задан там же).

    Считается по заказу, а не по случайной выборке: средний чек и прибыль
    линейны по доле, поэтому это точная интерполяция двух схем, а не оценка.
    День ПВЗ считается тем же pvz_pnl при фиксированной загрузке.
    """
    shares = [float(share) / 100.0 for share in params["network"]["sensitivity_mesh_share_pct"]]
    volume = int(params["network"]["sensitivity_orders"] if orders is None else orders)
    cells = float(params["pvz"]["avg_cells_per_order"])
    days = float(params["scenario_defaults"]["storage_days_typical"])
    goods = float(params["scenario_defaults"]["goods_price_rub"])
    hub = order_economics(params, kind="hub", goods_rub=goods, cells=cells, storage_days=days)
    mesh = order_economics(params, kind="mesh", goods_rub=goods, cells=cells, storage_days=days)
    ops = int(params["scenario_defaults"]["pvz_day_ops"])
    rows = []
    for share in shares:
        chain = hub["chain"]["profit_rub"] + share * (
            mesh["chain"]["profit_rub"] - hub["chain"]["profit_rub"]
        )
        buyer = hub["buyer"]["total_rub"] + share * (
            mesh["buyer"]["total_rub"] - hub["buyer"]["total_rub"]
        )
        rows.append(
            {
                "share_pct": round(share * 100.0, 2),
                "buyer_avg_rub": round(buyer, 2),
                "chain_per_order_rub": round(chain, 2),
                "network_chain_rub": round(chain * volume, 2),
                "pvz_day_profit_rub": pvz_day(params, ops=ops, mesh_share=share)["profit_rub"],
                "pvz_breakeven_ops": pvz_breakeven_ops(params, mesh_share=share),
            }
        )
    return rows


def chain_breakeven_share(params: dict) -> dict:
    """Точная доля локальных пар, при которой прибыль цепочки равна схеме «только хаб».

    Прибыль цепочки на заказ линейна по доле: hub + s × (mesh − hub). Если
    разница положительна, точка безубыточности — 0% (любой mesh-заказ выгоден);
    если отрицательна — 100% (mesh невыгоден вовсе).
    """
    cells = float(params["pvz"]["avg_cells_per_order"])
    days = float(params["scenario_defaults"]["storage_days_typical"])
    goods = float(params["scenario_defaults"]["goods_price_rub"])
    hub = order_economics(params, kind="hub", goods_rub=goods, cells=cells, storage_days=days)
    mesh = order_economics(params, kind="mesh", goods_rub=goods, cells=cells, storage_days=days)
    delta = round(mesh["chain"]["profit_rub"] - hub["chain"]["profit_rub"], 2)
    share = 0.0 if delta > 0 else (1.0 if delta < 0 else 0.0)
    return {
        "share_pct": round(share * 100.0, 2),
        "chain_delta_per_order_rub": delta,
        "hub_chain_per_order_rub": hub["chain"]["profit_rub"],
        "mesh_chain_per_order_rub": mesh["chain"]["profit_rub"],
        "method": "линейная интерполяция прибыли цепочки на заказ",
    }


def queue_threshold(params: dict) -> dict:
    """Порог загрузки и ожидание покупателя на пороге — из params и M/M/1."""
    capacity = float(params["pvz"]["ops_per_day_capacity"])
    target = float(params["pvz"]["utilization_target_pct"])
    alert = float(params["pvz"]["utilization_alert_pct"])
    wait_target = queue_wait_hours(params, target / 100.0)
    wait_alert = queue_wait_hours(params, alert / 100.0)
    return {
        "target_pct": target,
        "alert_pct": alert,
        "queue_cap_pct": float(params["network"]["queue_cap_pct"]),
        "wait_at_target_hours": wait_target,
        "wait_at_alert_hours": wait_alert,
        "wait_growth_alert_vs_target_pct": round(
            (wait_alert - wait_target) / wait_target * 100.0, 2
        )
        if wait_target
        else 0.0,
        "target_ops": round(capacity * target / 100.0, 1),
        "alert_ops": round(capacity * alert / 100.0, 1),
    }


def build_scenario(
    params: dict,
    *,
    mesh_bonus: float | None = None,
    storage_rate: float | None = None,
    fixed_cost: float | None = None,
    commission_pct: float | None = None,
    seller_logistics_fee: float | None = None,
    goods: float | None = None,
    mesh_share: float | None = None,
    ops: int | None = None,
    storage_days: float | None = None,
) -> dict:
    """Полный сценарий: город и межгород, четыре стороны, день ПВЗ, очередь, чувствительность."""
    data = with_overrides(
        params,
        mesh_bonus=mesh_bonus,
        storage_rate=storage_rate,
        fixed_cost=fixed_cost,
        commission_pct=commission_pct,
        seller_logistics_fee=seller_logistics_fee,
    )
    goods_rub = float(goods if goods is not None else data["scenario_defaults"]["goods_price_rub"])
    cells = float(data["pvz"]["avg_cells_per_order"])
    days = float(storage_days if storage_days is not None else data["scenario_defaults"]["storage_days_typical"])
    share = float(
        data["scenario_defaults"]["pvz_day_mesh_share_pct"] / 100.0
        if mesh_share is None
        else mesh_share
    )
    day_ops = int(data["scenario_defaults"]["pvz_day_ops"] if ops is None else ops)

    mesh = order_economics(
        data, kind="mesh", goods_rub=goods_rub, cells=cells, storage_days=days,
        commission_pct=commission_pct,
    )
    hub = order_economics(
        data, kind="hub", goods_rub=goods_rub, cells=cells, storage_days=days,
        commission_pct=commission_pct,
    )
    cmp = compare(data, hub, mesh)
    intercity = intercity_economics(data, goods_rub=goods_rub, cells=cells, storage_days=days)
    day = pvz_day(data, ops=day_ops, mesh_share=share)
    breakeven = pvz_breakeven_ops(data, mesh_share=share)
    queue = queue_load_table(data)

    return {
        "params": {
            "mesh_bonus_rub": round(float(data["pvz"]["mesh_bonus_rub"]), 2),
            "storage_rate_day_rub": round(float(data["pvz"]["storage_rate_day_rub"]), 2),
            "fixed_cost_day_rub": round(float(data["pvz"]["fixed_cost_day_rub"]), 2),
            "commission_pct": round(float(data["fees"]["commission_pct"]), 2),
            "seller_logistics_fee_rub": round(float(data["fees"]["seller_logistics_fee_rub"]), 2),
            "buyer_delivery_fee_local_rub": round(
                float(data["fees"]["buyer_delivery_fee_local_rub"]), 2
            ),
            "buyer_delivery_fee_hub_rub": round(float(data["fees"]["buyer_delivery_fee_hub_rub"]), 2),
            "late_penalty_rub": round(float(data["fees"]["late_penalty_rub"]), 2),
            "late_cancel_rate": round(float(data["fees"]["late_cancel_rate"]), 4),
            "peak_surge_multiplier": round(float(data["fees"]["peak_surge_multiplier"]), 2),
            "goods_rub": round(goods_rub, 2),
            "mesh_share": round(share, 4),
            "local_share_pct": round(float(data["scenario_defaults"]["local_share_pct"]), 2),
            "storage_days": round(days, 2),
            "cells": cells,
        },
        "buyer_acceptance": {
            "max_cost_increase_pct": round(
                float(data["buyer_acceptance"]["max_cost_increase_pct"]), 2
            ),
            "max_time_increase_hours": round(
                float(data["buyer_acceptance"]["max_time_increase_hours"]), 2
            ),
        },
        "order": {"mesh": mesh, "hub": hub, "compare": cmp},
        "intercity": intercity,
        "pvz_day": day,
        "breakeven": {
            "ops": breakeven,
            "share_of_capacity": round(breakeven / float(data["pvz"]["ops_per_day_capacity"]), 4)
            if breakeven
            else None,
            "capacity_ops": int(data["pvz"]["ops_per_day_capacity"]),
        },
        "storage_breakeven_days": storage_breakeven_days(data, goods=goods_rub),
        "sensitivity": share_sensitivity(data),
        "chain_breakeven": chain_breakeven_share(data),
        "queue": queue,
        "queue_threshold": queue_threshold(data),
        "disclaimer": data["meta"]["disclaimer"],
    }


def build_report(params: dict) -> dict:
    """Данные для /economics/report: тот же сценарий по умолчанию без «что если»."""
    scenario = build_scenario(params)
    return {
        "params": scenario["params"],
        "buyer_acceptance": scenario["buyer_acceptance"],
        "city": scenario["order"],
        "intercity": scenario["intercity"],
        "pvz_day": scenario["pvz_day"],
        "breakeven": scenario["breakeven"],
        "storage_breakeven_days": scenario["storage_breakeven_days"],
        "sensitivity": scenario["sensitivity"],
        "chain_breakeven": scenario["chain_breakeven"],
        "queue": scenario["queue"],
        "queue_threshold": scenario["queue_threshold"],
        "disclaimer": scenario["disclaimer"],
    }
