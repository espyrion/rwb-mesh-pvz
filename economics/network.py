"""Симуляция операционного дня сети ПВЗ: детерминированная модель нагрузки.

Это не замер и не прогноз RWB: это расчёт на параметрах economics/params.yaml
при явно заданных допущениях (доля локальных пар, число точек). Все допущения
выводятся в отчёт, чтобы числа нельзя было принять за измеренные.
"""
from __future__ import annotations

import random
from statistics import mean

from .model import order_economics, pvz_pnl

NODES = [f"YOL-0{i}" for i in range(1, 9)]
DELAY_PER_OVERLOAD = 24.0  # очередь при перегрузке узла: сутки на каждые +100% загрузки


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
        delay = round(max(0.0, utilization - 1.0) * DELAY_PER_OVERLOAD, 2)
        load.append(
            {
                "node": node,
                "ops": ops,
                "capacity_ops": capacity,
                "utilization": round(utilization, 4),
                "delay_hours": delay,
            }
        )
    load.sort(key=lambda item: item["utilization"], reverse=True)
    return load


def simulate_day(
    params: dict,
    *,
    orders: int = 500,
    seed: int = 42,
    p_local: float = 0.62,
    nodes: list[str] | None = None,
) -> dict:
    """Один день сети: mesh-режим против «только хаб» на том же потоке."""
    names = list(nodes or NODES)
    result = {"orders": int(orders), "seed": int(seed), "p_local": float(p_local), "modes": {}}
    for mode, share in (("mesh", float(p_local)), ("hub", 0.0)):
        rows = _orders(params, int(orders), share, seed, names)
        load = _node_load(params, rows, names)
        delays = {item["node"]: item["delay_hours"] for item in load}
        hours = []
        hours_by_kind = {"mesh": [], "hub": []}
        late = 0
        penalties = 0.0
        for row in rows:
            total = row["base_hours"] + delays[row["node"]]
            hours.append(total)
            hours_by_kind[row["kind"]].append(total)
            if total > row["promise_hours"] + row["late_threshold"]:
                late += 1
                penalties += float(params["fees"]["late_penalty_rub"])

        result["modes"][mode] = {
            "mesh_share": round(sum(1 for row in rows if row["kind"] == "mesh") / len(rows), 4) if rows else 0.0,
            "avg_hours": round(mean(hours), 2) if hours else 0.0,
            "avg_hours_mesh": round(mean(hours_by_kind["mesh"]), 2) if hours_by_kind["mesh"] else None,
            "avg_hours_hub": round(mean(hours_by_kind["hub"]), 2) if hours_by_kind["hub"] else None,
            "p95_hours": percentile(hours, 0.95),
            "late_orders": late,
            "late_share": round(late / len(rows), 4) if rows else 0.0,
            "penalties_rub": round(penalties, 2),
            "logistics_rub": round(sum(row["logistics_rub"] for row in rows), 2),
            "logistics_per_order_rub": round(sum(row["logistics_rub"] for row in rows) / len(rows), 2) if rows else 0.0,
            "storage_rub": round(sum(row["storage_rub"] for row in rows), 2),
            "buyer_total_rub": round(sum(row["buyer_rub"] for row in rows), 2),
            "chain_profit_rub": round(sum(row["chain_rub"] for row in rows) - penalties, 2),
            "pvz_profit_rub": round(sum(row["pvz_rub"] for row in rows) - penalties, 2),
            "marketplace_profit_rub": round(sum(row["marketplace_rub"] for row in rows), 2),
            "seller_profit_rub": round(sum(row["seller_rub"] for row in rows), 2),
            "nodes": load,
            "bottlenecks": load[:3],
            "max_utilization": load[0]["utilization"] if load else 0.0,
            "overloaded_nodes": sum(1 for item in load if item["utilization"] > 1.0),
        }
    result["delta"] = {
        "buyer_rub": round(result["modes"]["mesh"]["buyer_total_rub"] - result["modes"]["hub"]["buyer_total_rub"], 2),
        "logistics_rub": round(result["modes"]["mesh"]["logistics_rub"] - result["modes"]["hub"]["logistics_rub"], 2),
        "hours": round(result["modes"]["hub"]["avg_hours"] - result["modes"]["mesh"]["avg_hours"], 2),
        "chain_rub": round(result["modes"]["mesh"]["chain_profit_rub"] - result["modes"]["hub"]["chain_profit_rub"], 2),
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
