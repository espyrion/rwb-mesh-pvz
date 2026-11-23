"""Экономическая модель mesh-доставки: расчёт четырёх сторон и сравнение схем.

Все числа берутся из economics/params.yaml. Формулы продублированы в
docs/ECON_MODEL.md; тесты в economics/tests/ фиксируют ручные числа.

Соглашение о знаках: в отчётах «доход»/«затраты» — положительные величины,
«прибыль» = доход − затраты. Платежи между участниками не создают прибыли
цепочки: результат цепочки = сумма прибылей ПВЗ, маркетплейса и продавца
(= оплата покупателя − внешние затраты логистики − операционные затраты ПВЗ).

Четыре стороны:
* ПВЗ — операции, хранение, mesh-бонус; затраты на приёмку/сборку и постоянные.
* Маркетплейс — комиссия, плата за доставку покупателя, логистическое удержание
  и хранение продавца (переводы); затраты — внешняя логистика, хранение в пользу
  ПВЗ, вознаграждение ПВЗ и mesh-бонус.
* Продавец — выручка от товара; комиссия, логистическое удержание, хранение,
  удержание за возврат.
* Покупатель — товар + доставка и срок.

GMV (цена товара) и маржа — разные величины: маржа маркетплейса считается
в процентах от GMV, маржа продавца — от выручки, маржа ПВЗ — от его дохода.
Штраф SLA сопоставляется с маржой маркетплейса на заказ, а не с GMV.
"""
from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent

MESH_KINDS = {"mesh", "intercity_mesh"}


def load_params(path: str | Path | None = None) -> dict:
    with open(path or HERE / "params.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def is_mesh(kind: str) -> bool:
    return kind in MESH_KINDS


def route_cost(params: dict, kind: str) -> float:
    """Себестоимость логистики на заказ.

    mesh: локальный сегмент ПВЗ→ПВЗ.
    intercity_mesh: остаток заранее в городе получателя — локальная выдача плюс
    консолидированная магистраль в пересчёте на отправление.
    hub: обработка на хабе + магистраль + последняя миля.
    """
    lg = params["logistics"]
    if kind == "mesh":
        return float(lg["local_delivery_cost_rub"])
    if kind == "intercity_mesh":
        return float(lg["local_delivery_cost_rub"]) + float(lg["trunk_cost_per_order_rub"])
    return (
        float(lg["hub_handling_rub"])
        + float(lg["trunk_cost_per_order_rub"])
        + float(lg["last_mile_cost_rub"])
    )


def route_hours(params: dict, kind: str) -> float:
    """Срок доставки, ч.

    Для intercity_mesh магистраль уже оплачена и выполнена заранее: в срок
    выдачи входит только локальный сегмент, иначе сравнение с hub было бы
    нечестным (там магистраль в сроке есть).
    """
    lg = params["logistics"]
    if kind == "mesh":
        return float(lg["local_delivery_hours"])
    if kind == "intercity_mesh":
        return float(lg["local_delivery_hours"])
    return float(lg["hub_handling_hours"] + lg["trunk_hours"] + lg["last_mile_hours"])


def buyer_delivery_fee(params: dict, kind: str) -> float:
    fees = params["fees"]
    return float(
        fees["buyer_delivery_fee_local_rub"] if is_mesh(kind) else fees["buyer_delivery_fee_hub_rub"]
    )


def storage_cost(params: dict, cells: float, days: float, rate: float | None = None) -> float:
    """Плата за хранение: ячейки × дни × тариф ПВЗ (₽/ячейка/день)."""
    rate = params["pvz"]["storage_rate_day_rub"] if rate is None else rate
    return round(float(cells) * float(days) * float(rate), 2)


def pct_change(new: float, base: float) -> float:
    """Относительное изменение, %: (new − base) / base. Знак сохраняется."""
    return round((float(new) - float(base)) / float(base) * 100.0, 2) if base else 0.0


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
    revenue_total = round(sum(revenue.values()), 2)
    costs_total = round(sum(costs.values()), 2)
    profit = round(revenue_total - costs_total, 2)
    return {
        "revenue": revenue,
        "revenue_total": revenue_total,
        "costs": costs,
        "costs_total": costs_total,
        "profit_rub": profit,
        "margin_pct": round(profit / revenue_total * 100.0, 2) if revenue_total else 0.0,
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
    returned: bool = False,
) -> dict:
    """Доходы/затраты/прибыль маркетплейса по заказу (или пачке операций).

    Хранение продавца и его логистическое удержание — переводы: маркетплейс
    получает их от продавца и передаёт хранение ПВЗ, поэтому в прибыли цепочки
    они не задваиваются. Маржа считается от GMV, чтобы штраф SLA (50 ₽)
    сопоставлялся с маржой, а не с ценой товара.
    """
    fees = params["fees"]
    pv = params["pvz"]
    pct = float(fees["commission_pct"] if commission_pct is None else commission_pct)
    mesh_ops = ops if (is_mesh(kind) and mesh_ops is None) else int(mesh_ops or 0)
    seller_storage = (
        round(float(pv["storage_rate_day_rub"]) * float(storage_cell_days), 2)
        if storage_cell_days
        else 0.0
    )
    seller_return = float(fees["return_reverse_fee_rub"]) if returned else 0.0
    revenue = {
        "commission": round(float(goods_rub) * ops * pct / 100.0, 2),
        "delivery_fee": round(buyer_delivery_fee(params, kind) * ops, 2),
        "seller_logistics": round(float(fees["seller_logistics_fee_rub"]) * ops, 2),
        "seller_storage": seller_storage,
        "seller_return": round(seller_return, 2),
        "pvz_return": round(seller_return, 2),
    }
    costs = {
        "logistics": round(route_cost(params, kind) * ops, 2),
        "storage": seller_storage,
        "pvz_reward": round(float(pv["op_fee_rub"]) * ops, 2),
        "mesh_bonus": round(float(pv["mesh_bonus_rub"]) * mesh_ops, 2),
        "reverse_logistics": round(route_cost(params, kind), 2) if returned else 0.0,
    }
    revenue_total = round(sum(revenue.values()), 2)
    costs_total = round(sum(costs.values()), 2)
    profit = round(revenue_total - costs_total, 2)
    gmv = float(goods_rub) * ops
    return {
        "revenue": revenue,
        "revenue_total": revenue_total,
        "costs": costs,
        "costs_total": costs_total,
        "profit_rub": profit,
        "margin_pct": round(profit / gmv * 100.0, 2) if gmv else 0.0,
    }


def seller_pnl(
    params: dict,
    *,
    goods_rub: float,
    cells: float = 0.0,
    storage_days: float = 0.0,
    commission_pct: float | None = None,
    qty: int = 1,
    returned: bool = False,
) -> dict:
    """Доходы/затраты/прибыль продавца по заказу.

    Продавец платит закупку (goods_cost_pct от цены), комиссию, логистическое
    удержание и хранение по фактическим дням; при возврате — удержание за
    обратную логистику. Удержания — переводы маркетплейсу, а не новые затраты
    цепочки; закупка — внешняя затрата, она уменьшает прибыль цепочки.
    """
    fees = params["fees"]
    pct = float(fees["commission_pct"] if commission_pct is None else commission_pct)
    commission = round(float(goods_rub) * qty * pct / 100.0, 2)
    goods_cost = round(
        float(goods_rub) * qty * float(params["seller"]["goods_cost_pct"]) / 100.0, 2
    )
    storage = storage_cost(params, cells, storage_days)
    logistics = round(float(fees["seller_logistics_fee_rub"]) * qty, 2)
    return_withholding = round(float(fees["return_reverse_fee_rub"]), 2) if returned else 0.0
    costs = {
        "goods_cost": goods_cost,
        "commission": commission,
        "storage": storage,
        "logistics": logistics,
        "return": return_withholding,
    }
    revenue_total = round(float(goods_rub) * qty, 2)
    costs_total = round(sum(costs.values()), 2)
    profit = round(revenue_total - costs_total, 2)
    return {
        "revenue": {"goods": revenue_total},
        "revenue_total": revenue_total,
        "costs": costs,
        "costs_total": costs_total,
        "profit_rub": profit,
        "margin_pct": round(profit / revenue_total * 100.0, 2) if revenue_total else 0.0,
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


def queue_wait_hours(params: dict, utilization: float) -> float:
    """Ожидание обычного покупателя в очереди ПВЗ, ч.

    До queue_cap_pct — M/M/1: Wq = ρ/(1−ρ) × среднее время обслуживания
    (24 ч / ops_per_day_capacity). Выше — кусочно-линейно: очередь растёт на
    overload_delay_hours_per_100pct за каждые +100% загрузки. Сингулярность
    M/M/1 при ρ→1 заменена линейным участком осознанно: бесконечность в отчёте
    бесполезна, а порог 80–85% виден уже на гиперболе.
    """
    cap = float(params["network"]["queue_cap_pct"]) / 100.0
    rho = max(0.0, float(utilization))
    if rho <= 0:
        return 0.0
    service_hours = 24.0 / float(params["pvz"]["ops_per_day_capacity"])
    if rho < cap:
        return round(rho / (1.0 - rho) * service_hours, 4)
    base_wait = cap / (1.0 - cap) * service_hours
    return round(
        base_wait + (rho - cap) * float(params["network"]["overload_delay_hours_per_100pct"]), 4
    )


def order_economics(
    params: dict,
    *,
    kind: str = "mesh",
    goods_rub: float = 2500.0,
    qty: int = 1,
    cells: float = 1.0,
    storage_days: float = 3.0,
    commission_pct: float | None = None,
    returned: bool = False,
) -> dict:
    """Полный расчёт одного заказа по схеме mesh/hub/intercity_mesh для четырёх сторон.

    Внутренние переводы (комиссия, хранение, логистическое удержание, бонус)
    сокращаются в прибыли цепочки: chain = ПВЗ + маркетплейс + продавец.
    """
    cell_days = float(cells) * float(storage_days)
    pvz = pvz_pnl(
        params,
        ops=1,
        storage_cell_days=cell_days,
        mesh_ops=1 if is_mesh(kind) else 0,
        qty=qty,
        extra_costs=float(params["fees"]["return_reverse_fee_rub"]) if returned else 0.0,
    )
    market = marketplace_pnl(
        params,
        goods_rub=goods_rub,
        kind=kind,
        ops=1,
        storage_cell_days=cell_days,
        commission_pct=commission_pct,
        returned=returned,
    )
    seller = seller_pnl(
        params,
        goods_rub=goods_rub,
        cells=cells,
        storage_days=storage_days,
        commission_pct=commission_pct,
        qty=qty,
        returned=returned,
    )
    buyer = buyer_cost(params, goods_rub=goods_rub, kind=kind, ops=qty)
    chain_profit = round(pvz["profit_rub"] + market["profit_rub"] + seller["profit_rub"], 2)
    gmv = round(float(goods_rub) * qty, 2)
    return {
        "kind": kind,
        "gmv_rub": gmv,
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
            "profit_rub": chain_profit,
            "margin_pct": round(chain_profit / gmv * 100.0, 2) if gmv else 0.0,
            "buyer_pays_rub": buyer["total_rub"],
        },
    }


def intercity_economics(
    params: dict,
    *,
    goods_rub: float | None = None,
    cells: float | None = None,
    storage_days: float | None = None,
) -> dict:
    """Межгород: база «каждый заказ через хаб» против предразмещённого остатка."""
    defaults = params["scenario_defaults"]
    goods = float(defaults["goods_price_rub"] if goods_rub is None else goods_rub)
    cell_count = float(params["pvz"]["avg_cells_per_order"] if cells is None else cells)
    days = float(defaults["storage_days_typical"] if storage_days is None else storage_days)
    base = order_economics(params, kind="hub", goods_rub=goods, cells=cell_count, storage_days=days)
    mesh = order_economics(
        params, kind="intercity_mesh", goods_rub=goods, cells=cell_count, storage_days=days
    )
    return {"base": base, "mesh": mesh, "compare": compare(params, base, mesh)}


def compare(params: dict, base: dict, mesh: dict) -> dict:
    """Сравнение базовой (хаб) и предложенной (mesh) схем по одному заказу.

    Все дельты — «mesh минус база»: отрицательная цена/срок означают выгоду
    покупателю, положительная прибыль — выгоду стороне. Прибыль цепочки
    сравнивается словами «прибыльнее», а не «дороже»: знак проверяется по
    chain_more_profitable, а не по модулю.
    """
    d_cost = round(mesh["buyer"]["total_rub"] - base["buyer"]["total_rub"], 2)
    d_hours = round(mesh["buyer"]["hours"] - base["buyer"]["hours"], 2)
    d_chain = round(mesh["chain"]["profit_rub"] - base["chain"]["profit_rub"], 2)
    acc = params["buyer_acceptance"]
    within_cost = d_cost <= 0 or pct_change(mesh["buyer"]["total_rub"], base["buyer"]["total_rub"]) <= float(
        acc["max_cost_increase_pct"]
    )
    within_time = d_hours <= float(acc["max_time_increase_hours"])
    marketplace_ok = mesh["marketplace"]["profit_rub"] >= 0
    pvz_ok = mesh["pvz"]["profit_rub"] >= 0
    seller_ok = mesh["seller"]["profit_rub"] >= 0
    return {
        "buyer_delta_rub": d_cost,
        "buyer_delta_pct": pct_change(mesh["buyer"]["total_rub"], base["buyer"]["total_rub"]),
        "buyer_delta_hours": d_hours,
        "buyer_delta_hours_pct": pct_change(mesh["buyer"]["hours"], base["buyer"]["hours"]),
        "buyer_acceptance": {
            "within_cost": within_cost,
            "within_time": within_time,
            "limits": {
                "max_cost_increase_pct": acc["max_cost_increase_pct"],
                "max_time_increase_hours": acc["max_time_increase_hours"],
            },
        },
        "pvz_profit_mesh": mesh["pvz"]["profit_rub"],
        "pvz_profit_base": base["pvz"]["profit_rub"],
        "pvz_delta_rub": round(mesh["pvz"]["profit_rub"] - base["pvz"]["profit_rub"], 2),
        "pvz_delta_pct": pct_change(mesh["pvz"]["profit_rub"], base["pvz"]["profit_rub"]),
        "marketplace_profit_mesh": mesh["marketplace"]["profit_rub"],
        "marketplace_profit_base": base["marketplace"]["profit_rub"],
        "marketplace_delta_rub": round(
            mesh["marketplace"]["profit_rub"] - base["marketplace"]["profit_rub"], 2
        ),
        "marketplace_delta_pct": pct_change(
            mesh["marketplace"]["profit_rub"], base["marketplace"]["profit_rub"]
        ),
        "seller_profit_mesh": mesh["seller"]["profit_rub"],
        "seller_profit_base": base["seller"]["profit_rub"],
        "seller_delta_rub": round(mesh["seller"]["profit_rub"] - base["seller"]["profit_rub"], 2),
        "seller_delta_pct": pct_change(mesh["seller"]["profit_rub"], base["seller"]["profit_rub"]),
        "chain_profit_mesh": mesh["chain"]["profit_rub"],
        "chain_profit_base": base["chain"]["profit_rub"],
        "chain_delta_rub": d_chain,
        "chain_delta_pct": pct_change(mesh["chain"]["profit_rub"], base["chain"]["profit_rub"]),
        "chain_more_profitable": d_chain > 0,
        "viable": bool(within_cost and within_time and marketplace_ok and pvz_ok and seller_ok),
    }
