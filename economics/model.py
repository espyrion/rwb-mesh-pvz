"""Экономическая модель mesh-доставки: расчёт четырёх сторон и сравнение схем.

Все числа берутся из economics/params.yaml. Формулы продублированы в
docs/ECON_MODEL.md; тесты в economics/tests/test_model.py фиксируют ручные числа.

Соглашение о знаках: в отчётах «доход»/«затраты» — положительные величины,
«прибыль» = доход − затраты. Платежи между участниками не создают прибыли
цепочки: результат цепочки = сумма прибылей ПВЗ, маркетплейса и продавца
(= оплата покупателя − внешние затраты − операционные затраты ПВЗ).
"""
from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def load_params(path: str | Path | None = None) -> dict:
    with open(path or HERE / "params.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def route_cost(params: dict, kind: str) -> float:
    """Себестоимость логистики на заказ.

    mesh: локальный сегмент ПВЗ→ПВЗ.
    hub:  обработка на хабе + магистраль (в пересчёте на отправление) + последняя миля.
    """
    lg = params["logistics"]
    if kind == "mesh":
        return float(lg["local_delivery_cost_rub"])
    return (
        float(lg["hub_handling_rub"])
        + float(lg["trunk_cost_per_order_rub"])
        + float(lg["last_mile_cost_rub"])
    )


def route_hours(params: dict, kind: str) -> float:
    lg = params["logistics"]
    if kind == "mesh":
        return float(lg["local_delivery_hours"])
    return float(lg["hub_handling_hours"] + lg["trunk_hours"] + lg["last_mile_hours"])


def buyer_delivery_fee(params: dict, kind: str) -> float:
    fees = params["fees"]
    return float(
        fees["buyer_delivery_fee_local_rub"] if kind == "mesh" else fees["buyer_delivery_fee_hub_rub"]
    )


def storage_cost(params: dict, cells: float, days: float, rate: float | None = None) -> float:
    """Плата за хранение: ячейки × дни × тариф ПВЗ (₽/ячейка/день)."""
    rate = params["pvz"]["storage_rate_day_rub"] if rate is None else rate
    return round(float(cells) * float(days) * float(rate), 2)


def pvz_pnl(
    params: dict,
    *,
    days: float = 0.0,
    ops: int = 1,
    storage_cell_days: float = 0.0,
    mesh_ops: int = 0,
    qty: int = 1,
    extra_costs: float = 0.0,
) -> dict:
    """Доходы/затраты/прибыль ПВЗ.

    days — длительность периода для условно-постоянных затрат;
    ops — число операций выдачи/приёмки; storage_cell_days — ячейко-дни хранения;
    mesh_ops — операции по заказам без хаба (бонус).
    """
    pv = params["pvz"]
    lg = params["logistics"]
    revenue = {
        "pickup_fee": round(float(pv["op_fee_rub"]) * ops, 2),
        "storage": round(float(pv["storage_rate_day_rub"]) * storage_cell_days, 2),
        "mesh_bonus": round(float(pv["mesh_bonus_rub"]) * mesh_ops, 2),
    }
    costs = {
        "acceptance": round(float(lg["acceptance_cost_rub"]) * qty, 2),
        "assemble": round(float(lg["assemble_cost_rub"]) * ops, 2),
        "fixed": round(float(days) * float(pv.get("fixed_cost_day_rub", 0.0)), 2),
        "extra": round(float(extra_costs), 2),
    }
    return {
        "revenue": revenue,
        "revenue_total": round(sum(revenue.values()), 2),
        "costs": costs,
        "costs_total": round(sum(costs.values()), 2),
        "profit_rub": round(sum(revenue.values()) - sum(costs.values()), 2),
    }


def marketplace_pnl(
    params: dict,
    *,
    goods_rub: float,
    kind: str,
    ops: int = 1,
    storage_cell_days: float = 0.0,
    mesh_ops: int | None = None,
    commission_pct: float | None = None,
) -> dict:
    """Доходы/затраты/прибыль маркетплейса по заказу (или пачке операций)."""
    fees = params["fees"]
    pv = params["pvz"]
    pct = float(fees["commission_pct"] if commission_pct is None else commission_pct)
    mesh_ops = ops if (kind == "mesh" and mesh_ops is None) else int(mesh_ops or 0)
    revenue = {
        "commission": round(float(goods_rub) * pct / 100.0, 2),
        "delivery_fee": round(buyer_delivery_fee(params, kind) * ops, 2),
    }
    costs = {
        "logistics": round(route_cost(params, kind) * ops, 2),
        "storage": round(float(pv["storage_rate_day_rub"]) * storage_cell_days, 2),
        "pvz_reward": round(float(pv["op_fee_rub"]) * ops, 2),
        "mesh_bonus": round(float(pv["mesh_bonus_rub"]) * mesh_ops, 2),
    }
    return {
        "revenue": revenue,
        "revenue_total": round(sum(revenue.values()), 2),
        "costs": costs,
        "costs_total": round(sum(costs.values()), 2),
        "profit_rub": round(sum(revenue.values()) - sum(costs.values()), 2),
    }


def seller_pnl(params: dict, *, goods_rub: float, commission_pct: float | None = None) -> dict:
    fees = params["fees"]
    pct = float(fees["commission_pct"] if commission_pct is None else commission_pct)
    commission = round(float(goods_rub) * pct / 100.0, 2)
    return {
        "revenue": {"goods": round(float(goods_rub), 2)},
        "revenue_total": round(float(goods_rub), 2),
        "costs": {"commission": commission},
        "costs_total": commission,
        "profit_rub": round(float(goods_rub) - commission, 2),
    }


def buyer_cost(params: dict, *, goods_rub: float, kind: str, ops: int = 1) -> dict:
    fee = buyer_delivery_fee(params, kind) * ops
    total = round(float(goods_rub) * ops + fee, 2)
    return {
        "goods_rub": round(float(goods_rub) * ops, 2),
        "delivery_fee_rub": round(fee, 2),
        "total_rub": total,
        "hours": route_hours(params, kind),
    }


def order_economics(
    params: dict,
    *,
    kind: str = "mesh",
    goods_rub: float = 2500.0,
    qty: int = 1,
    cells: float = 1.0,
    storage_days: float = 3.0,
    commission_pct: float | None = None,
) -> dict:
    """Полный расчёт одного заказа по схеме mesh или hub для четырёх сторон."""
    cell_days = float(cells) * float(storage_days)
    pvz = pvz_pnl(
        params,
        ops=1,
        storage_cell_days=cell_days,
        mesh_ops=1 if kind == "mesh" else 0,
        qty=qty,
    )
    market = marketplace_pnl(
        params,
        goods_rub=goods_rub,
        kind=kind,
        ops=1,
        storage_cell_days=cell_days,
        commission_pct=commission_pct,
    )
    seller = seller_pnl(params, goods_rub=goods_rub, commission_pct=commission_pct)
    buyer = buyer_cost(params, goods_rub=goods_rub, kind=kind)
    return {
        "kind": kind,
        "route": {
            "cost_rub": round(route_cost(params, kind), 2),
            "hours": round(route_hours(params, kind), 2),
        },
        "storage": {"cell_days": cell_days, "cost_rub": storage_cost(params, cells, storage_days)},
        "buyer": buyer,
        "pvz": pvz,
        "marketplace": market,
        "seller": seller,
        "chain": {
            "profit_rub": round(pvz["profit_rub"] + market["profit_rub"] + seller["profit_rub"], 2),
            "buyer_pays_rub": buyer["total_rub"],
        },
    }


def compare(params: dict, base: dict, mesh: dict) -> dict:
    """Сравнение базовой (хаб) и предложенной (mesh) схем по одному заказу."""
    d_cost = round(mesh["buyer"]["total_rub"] - base["buyer"]["total_rub"], 2)
    d_hours = round(mesh["buyer"]["hours"] - base["buyer"]["hours"], 2)
    d_cost_pct = round(d_cost / base["buyer"]["total_rub"] * 100.0, 2) if base["buyer"]["total_rub"] else 0.0
    acc = params["buyer_acceptance"]
    within_cost = abs(d_cost_pct) <= float(acc["max_cost_increase_pct"]) or d_cost <= 0
    within_time = d_hours <= float(acc["max_time_increase_hours"])
    marketplace_ok = mesh["marketplace"]["profit_rub"] >= 0
    pvz_ok = mesh["pvz"]["profit_rub"] >= 0
    return {
        "buyer_delta_rub": d_cost,
        "buyer_delta_pct": d_cost_pct,
        "buyer_delta_hours": d_hours,
        "buyer_acceptance": {
            "within_cost": within_cost,
            "within_time": within_time,
            "limits": {
                "max_cost_increase_pct": acc["max_cost_increase_pct"],
                "max_time_increase_hours": acc["max_time_increase_hours"],
            },
        },
        "marketplace_profit_mesh": mesh["marketplace"]["profit_rub"],
        "marketplace_profit_base": base["marketplace"]["profit_rub"],
        "pvz_profit_mesh": mesh["pvz"]["profit_rub"],
        "pvz_profit_base": base["pvz"]["profit_rub"],
        "chain_profit_mesh": mesh["chain"]["profit_rub"],
        "chain_profit_base": base["chain"]["profit_rub"],
        "viable": bool(within_cost and within_time and marketplace_ok and pvz_ok),
    }
