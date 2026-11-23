"""Симуляция операционного дня сети ПВЗ: детерминированная модель нагрузки.

Это не замер и не прогноз RWB: это расчёт на параметрах economics/params.yaml
при явно заданных допущениях (доля локальных пар, число точек, доля отмен при
просрочке, пиковый коэффициент). Все допущения выводятся в отчёт, чтобы числа
нельзя было принять за измеренные.

Деньги разделены на GMV и маржу: GMV — цена товаров, маржа маркетплейса —
его прибыль на заказ, прибыль ПВЗ — отдельно. Штраф SLA (late_penalty_rub)
сопоставляется с маржой маркетплейса, а не с ценой товара.

Срыв срока: просроченный заказ получает штраф; с долей late_cancel_rate он
отменяется — цепочка теряет маржу всех сторон и платит обратную логистику по
коэффициенту peak_surge_multiplier.
"""
from __future__ import annotations

import random
from statistics import mean

from .model import order_economics, pvz_pnl, queue_wait_hours, route_cost

DEFAULT_NODES = 8


def node_names(count: int) -> list[str]:
    return [f"YOL-{index:02d}" for index in range(1, int(count) + 1)]


NODES = node_names(DEFAULT_NODES)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 2)


def _orders(params: dict, orders: int, p_local: float, seed: int, nodes: list[str]) -> list[dict]:
    rnd = random.Random(seed)
    goods = float(params["scenario_defaults"]["goods_price_rub"])
    cells = float(params["pvz"]["avg_cells_per_order"])
    days = float(params["scenario_defaults"]["storage_days_typical"])
    late_threshold = float(params["fees"]["late_threshold_hours"])

    rows = []
    for _ in range(orders):
        kind = "mesh" if rnd.random() < p_local else "hub"
        node = rnd.choice(nodes)
        econ = order_economics(params, kind=kind, goods_rub=goods, cells=cells, storage_days=days)
        rows.append(
            {
                "node": node,
                "kind": kind,
                "route_hours": econ["route"]["hours"],
                "promise_hours": econ["route"]["hours"],
                "base_hours": econ["route"]["hours"],
                "late_threshold": late_threshold,
                "logistics_rub": econ["route"]["cost_rub"],
                "storage_rub": econ["storage"]["cost_rub"],
                "buyer_rub": econ["buyer"]["total_rub"],
                "goods_rub": econ["gmv_rub"],
                "chain_rub": econ["chain"]["profit_rub"],
                "pvz_rub": econ["pvz"]["profit_rub"],
                "marketplace_rub": econ["marketplace"]["profit_rub"],
                "seller_rub": econ["seller"]["profit_rub"],
            }
        )
    return rows


def _node_load(params: dict, rows: list[dict], nodes: list[str]) -> list[dict]:
    capacity = int(params["pvz"]["ops_per_day_capacity"])
    per_node = {node: 0 for node in nodes}
    for row in rows:
        per_node[row["node"]] += 1
    load = []
    for node, ops in per_node.items():
        utilization = ops / capacity
        load.append(
            {
                "node": node,
                "ops": ops,
                "capacity_ops": capacity,
                "utilization": round(utilization, 4),
                "wait_hours": queue_wait_hours(params, utilization),
            }
        )
    load.sort(key=lambda item: item["utilization"], reverse=True)
    return load


def queue_load_table(params: dict, *, base_ops: int | None = None) -> list[dict]:
    """Влияние доп. нагрузки ПВЗ на ожидание обычного покупателя.

    Базовые операции и список приращений — из params.yaml. Показывает, почему
    рабочий порог 80–85%: на гиперболе M/M/1 ожидание растёт кратно быстрее
    загрузки, и после 85% каждая дополнительная операция стоит покупателю
    десятков минут ожидания.
    """
    base = int(params["network"]["queue_base_ops"] if base_ops is None else base_ops)
    capacity = int(params["pvz"]["ops_per_day_capacity"])
    base_wait = queue_wait_hours(params, base / capacity)
    rows = []
    for extra in params["network"]["queue_extra_ops"]:
        ops = base + int(extra)
        utilization = ops / capacity
        wait = queue_wait_hours(params, utilization)
        rows.append(
            {
                "ops": ops,
                "extra_ops": int(extra),
                "utilization": round(utilization, 4),
                "wait_hours": wait,
                "wait_multiplier": round(wait / base_wait, 2) if base_wait else None,
            }
        )
    return rows


def simulate_day(
    params: dict,
    *,
    orders: int | None = None,
    seed: int | None = None,
    p_local: float | None = None,
    nodes: list[str] | None = None,
) -> dict:
    """Один день сети: mesh-режим против «только хаб» на том же потоке."""
    share = float(
        params["scenario_defaults"]["local_share_pct"] / 100.0 if p_local is None else p_local
    )
    count = int(params["network"]["sensitivity_orders"] if orders is None else orders)
    seed_value = int(params["network"]["simulation_seed"] if seed is None else seed)
    names = list(nodes or node_names(int(params["network"]["nodes"])))
    late_cancel_rate = float(params["fees"]["late_cancel_rate"])
    surge = float(params["fees"]["peak_surge_multiplier"])
    penalty = float(params["fees"]["late_penalty_rub"])
    result = {"orders": count, "seed": seed_value, "p_local": share, "modes": {}}
    for mode, mode_share in (("mesh", share), ("hub", 0.0)):
        rows = _orders(params, count, mode_share, seed_value, names)
        load = _node_load(params, rows, names)
        waits = {item["node"]: item["wait_hours"] for item in load}
        hours = []
        hours_by_kind = {"mesh": [], "hub": []}
        late = 0
        cancelled = 0
        penalties = 0.0
        reverse_costs = 0.0
        lost_pvz = lost_market = lost_seller = 0.0
        rnd_cancel = random.Random(seed_value * 1000 + 7)  # соль генератора, не тариф
        for row in rows:
            total = row["base_hours"] + waits[row["node"]]
            hours.append(total)
            hours_by_kind[row["kind"]].append(total)
            if total > row["promise_hours"] + row["late_threshold"]:
                late += 1
                penalties += penalty
                if rnd_cancel.random() < late_cancel_rate:
                    cancelled += 1
                    lost_pvz += row["pvz_rub"]
                    lost_market += row["marketplace_rub"]
                    lost_seller += row["seller_rub"]
                    # Обратная перевозка в пик дороже: коэффициент курьера.
                    reverse_costs += route_cost(params, row["kind"]) * surge

        sum_chain = sum(row["chain_rub"] for row in rows)
        sum_pvz = sum(row["pvz_rub"] for row in rows)
        sum_market = sum(row["marketplace_rub"] for row in rows)
        sum_seller = sum(row["seller_rub"] for row in rows)
        sum_gmv = sum(row["goods_rub"] for row in rows)
        marketplace_profit = round(sum_market - penalties - lost_market - reverse_costs, 2)
        pvz_profit = round(sum_pvz - lost_pvz, 2)
        seller_profit = round(sum_seller - lost_seller, 2)
        chain_profit = round(
            sum_chain - penalties - lost_pvz - lost_market - lost_seller - reverse_costs, 2
        )
        result["modes"][mode] = {
            "mesh_share": round(sum(1 for row in rows if row["kind"] == "mesh") / len(rows), 4)
            if rows
            else 0.0,
            "avg_hours": round(mean(hours), 2) if hours else 0.0,
            "avg_hours_mesh": round(mean(hours_by_kind["mesh"]), 2) if hours_by_kind["mesh"] else None,
            "avg_hours_hub": round(mean(hours_by_kind["hub"]), 2) if hours_by_kind["hub"] else None,
            "p95_hours": percentile(hours, 0.95),
            "late_orders": late,
            "late_share": round(late / len(rows), 4) if rows else 0.0,
            "cancelled_orders": cancelled,
            "cancelled_share_of_late": round(cancelled / late, 4) if late else 0.0,
            "late_penalty_rub": round(penalties, 2),
            "penalties_rub": round(penalties, 2),
            "reverse_logistics_rub": round(reverse_costs, 2),
            "lost_margin_rub": round(lost_pvz + lost_market + lost_seller, 2),
            "logistics_rub": round(sum(row["logistics_rub"] for row in rows), 2),
            "logistics_per_order_rub": round(sum(row["logistics_rub"] for row in rows) / len(rows), 2)
            if rows
            else 0.0,
            "storage_rub": round(sum(row["storage_rub"] for row in rows), 2),
            "gmv_rub": round(sum_gmv, 2),
            "buyer_total_rub": round(sum(row["buyer_rub"] for row in rows), 2),
            "chain_profit_rub": chain_profit,
            "pvz_profit_rub": pvz_profit,
            "marketplace_profit_rub": marketplace_profit,
            "marketplace_margin_pct": round(marketplace_profit / sum_gmv * 100.0, 2) if sum_gmv else 0.0,
            "pvz_profit_per_order_rub": round(pvz_profit / len(rows), 2) if rows else 0.0,
            "seller_profit_rub": seller_profit,
            "seller_margin_pct": round(seller_profit / sum_gmv * 100.0, 2) if sum_gmv else 0.0,
            "nodes": load,
            "bottlenecks": load[:3],
            "max_utilization": load[0]["utilization"] if load else 0.0,
            "overloaded_nodes": sum(1 for item in load if item["utilization"] > 1.0),
        }
    result["delta"] = {
        "buyer_rub": round(
            result["modes"]["mesh"]["buyer_total_rub"] - result["modes"]["hub"]["buyer_total_rub"], 2
        ),
        "logistics_rub": round(
            result["modes"]["mesh"]["logistics_rub"] - result["modes"]["hub"]["logistics_rub"], 2
        ),
        "hours": round(result["modes"]["hub"]["avg_hours"] - result["modes"]["mesh"]["avg_hours"], 2),
        "chain_rub": round(
            result["modes"]["mesh"]["chain_profit_rub"] - result["modes"]["hub"]["chain_profit_rub"], 2
        ),
        "gmv_rub": round(result["modes"]["mesh"]["gmv_rub"] - result["modes"]["hub"]["gmv_rub"], 2),
        "marketplace_margin_pct": round(
            result["modes"]["mesh"]["marketplace_margin_pct"]
            - result["modes"]["hub"]["marketplace_margin_pct"],
            2,
        ),
        "pvz_per_order_rub": round(
            result["modes"]["mesh"]["pvz_profit_per_order_rub"]
            - result["modes"]["hub"]["pvz_profit_per_order_rub"],
            2,
        ),
    }
    return result


def pvz_day_profit(params: dict, *, ops: int, mesh_share: float) -> float:
    """Прибыль одной точки при заданной загрузке — через тот же pvz_pnl."""
    cells = float(params["pvz"]["avg_cells_per_order"])
    days = float(params["scenario_defaults"]["storage_days_typical"])
    return pvz_pnl(
        params,
        days=1,
        ops=ops,
        qty=ops,
        storage_cell_days=ops * cells * days,
        mesh_ops=int(round(ops * mesh_share)),
    )["profit_rub"]
