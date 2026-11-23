#!/usr/bin/env python
"""Симуляция дня сети и генерация docs/SCALE.md.

Пример:
    .venv/bin/python scripts/simulate.py --write-md docs/SCALE.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from economics.model import load_params  # noqa: E402
from economics.network import simulate_day  # noqa: E402

HEADER = """# Масштаб: что происходит при росте потока

> Числа ниже — расчёт детерминированной модели (`economics/network.py`), а не замеры
> и не прогноз RWB. Допущения: {nodes} точек по {capacity} операций в день, {p_local:.0%}
> заказов — пары внутри города, остальные идут через хаб; срок хранения {storage_days} дня,
> товар {goods} ₽, доля отмен при просрочке {cancel:.0%}, пиковый коэффициент курьера ×{surge}.
> Источник тарифов — `economics/params.yaml`.

Запуск: `.venv/bin/python scripts/simulate.py --write-md docs/SCALE.md` (объёмы, seed и допущения
берутся из `network.simulation_orders`, `network.simulation_seed` и `scenario_defaults`).

GMV и маржа разделены: GMV — цена товаров, маржа маркетплейса и прибыль ПВЗ — отдельные колонки.
Штраф SLA сопоставляется с маржой маркетплейса, а не с GMV.

| Заказов в день | Режим | Доля без хаба | Срок mesh-заказов | Срок через хаб | p95 по всем | Логистика на заказ | GMV, ₽ | Маржа МП, % | ПВЗ/заказ, ₽ | Опоздания | Отмены | Самая загруженная точка | Прибыль цепочки, ₽ |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
"""


def fmt_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def fmt_num(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def row(orders: int, mode: str, data: dict, max_node: dict, share: float) -> str:
    mesh_hours = f"{fmt_num(data['avg_hours_mesh'])} ч" if data.get("avg_hours_mesh") is not None else "—"
    hub_hours = f"{fmt_num(data['avg_hours_hub'])} ч" if data.get("avg_hours_hub") is not None else "—"
    return (
        f"| {orders} | {mode} | {fmt_num(share * 100, 0)}% | {mesh_hours} | {hub_hours} | {fmt_num(data['p95_hours'])} ч "
        f"| {fmt_money(data['logistics_per_order_rub'])} ₽ | {fmt_money(data['gmv_rub'])} "
        f"| {fmt_num(data['marketplace_margin_pct'], 2)}% | {fmt_money(data['pvz_profit_per_order_rub'])} "
        f"| {data['late_orders']} | {data['cancelled_orders']} "
        f"| {max_node['node']} · {fmt_num(max_node['utilization'] * 100, 0)}% | {fmt_money(data['chain_profit_rub'])} ₽ |"
    )


def build_markdown(params: dict, runs: list[dict], seed: int) -> str:
    p_local = runs[0]["p_local"]
    network = params["network"]
    defaults = params["scenario_defaults"]
    lines = [
        HEADER.format(
            capacity=params["pvz"]["ops_per_day_capacity"],
            nodes=network["nodes"],
            p_local=p_local,
            storage_days=defaults["storage_days_typical"],
            goods=fmt_money(defaults["goods_price_rub"]),
            cancel=params["fees"]["late_cancel_rate"],
            surge=fmt_money(params["fees"]["peak_surge_multiplier"]),
        ).rstrip("\n")
    ]
    for run in runs:
        for mode, label in (("mesh", "mesh"), ("hub", "только хаб")):
            lines.append(row(run["orders"], label, run["modes"][mode], run["modes"][mode]["bottlenecks"][0], p_local))
    lines.append("")

    # Где именно ломается — считаем по данным, а не пишем от руки.
    first_overload = next((run for run in runs if run["modes"]["mesh"]["overloaded_nodes"] > 0), None)
    ceiling = int(params["pvz"]["ops_per_day_capacity"]) * int(network["nodes"])
    lines.append("## Где ломается")
    lines.append("")
    if first_overload:
        mesh = first_overload["modes"]["mesh"]
        worst = mesh["bottlenecks"][0]
        lines.append(
            f"- При {first_overload['orders']} заказах в день в mesh-режиме перегружено "
            f"{mesh['overloaded_nodes']} из {network['nodes']} точек; худшая — {worst['node']} с "
            f"{fmt_num(worst['utilization'] * 100, 0)}% мощности и очередью {fmt_num(worst['wait_hours'])} ч. "
            f"Опозданий: {mesh['late_orders']} ({fmt_num(mesh['late_share'] * 100)}%), отмен: {mesh['cancelled_orders']}, "
            f"штрафов {fmt_money(mesh['late_penalty_rub'])} ₽, обратная логистика "
            f"{fmt_money(mesh['reverse_logistics_rub'])} ₽."
        )
        lines.append(
            f"- Потолок {network['nodes']} точек по {params['pvz']['ops_per_day_capacity']} операций — "
            f"{ceiling} заказов в день. Выше него сеть деградирует по сроку и отменам, а не по стоимости: "
            f"логистика на заказ почти не растёт, растут опоздания и потеря маржи."
        )
    else:
        lines.append(
            f"- Во всех сценариях до {runs[-1]['orders']} заказов в день ни одна точка не перегружена."
        )
    lines.append(
        "- Что делать при росте: добавлять точки в районы с очередью, вводить слоты приёмки "
        "и буферные ячейки на пиковые часы. Это операционные решения, не изменение модели."
    )
    lines.append("")
    lines.append("## Допущения, которые надо назвать вслух")
    lines.append("")
    lines.append(
        f"- Доля локальных пар {p_local:.0%} — допущение `scenario_defaults.local_share_pct`, а не измерение. "
        "На демо-данных доля заказов без хаба выше, потому что заказы создавались вручную."
    )
    lines.append(
        f"- Доля отмен при просрочке {fmt_num(params['fees']['late_cancel_rate'] * 100, 0)}% "
        "(`fees.late_cancel_rate`): отменённый заказ теряет маржу всех сторон и платит обратную логистику "
        f"по коэффициенту ×{fmt_money(params['fees']['peak_surge_multiplier'])}."
    )
    lines.append(
        f"- Очередь: M/M/1 до {fmt_num(params['network']['queue_cap_pct'], 0)}% загрузки, дальше линейно — "
        f"{fmt_money(params['network']['overload_delay_hours_per_100pct'])} ч на каждые +100%."
    )
    lines.append(
        "- Нагрузка по точкам распределена равномерно; в жизни она неравномерна, и потолок наступает раньше."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Симуляция дня сети ПВЗ")
    parser.add_argument("--orders", default=None, help="список объёмов через запятую (по умолчанию из params)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--p-local", type=float, default=None, help="доля заказов без хаба (по умолчанию из params)")
    parser.add_argument("--params", default=None)
    parser.add_argument("--json", default=None, help="куда сохранить полный JSON")
    parser.add_argument("--write-md", default=None, help="куда записать docs/SCALE.md")
    args = parser.parse_args()

    params = load_params(args.params or ROOT / "economics" / "params.yaml")
    volumes = (
        [int(part) for part in args.orders.split(",") if part.strip()]
        if args.orders
        else [int(value) for value in params["network"]["simulation_orders"]]
    )
    seed = int(params["network"]["simulation_seed"] if args.seed is None else args.seed)
    p_local = (
        float(params["scenario_defaults"]["local_share_pct"]) / 100.0
        if args.p_local is None
        else args.p_local
    )
    runs = [simulate_day(params, orders=volume, seed=seed, p_local=p_local) for volume in volumes]

    if args.json:
        Path(args.json).write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_md:
        Path(args.write_md).write_text(build_markdown(params, runs, seed), encoding="utf-8")

    for run in runs:
        mesh = run["modes"]["mesh"]
        hub = run["modes"]["hub"]
        print(
            f"{run['orders']:>5} заказов: mesh {mesh['avg_hours']:.1f} ч / {mesh['logistics_per_order_rub']:.2f} ₽ "
            f"против хаба {hub['avg_hours']:.1f} ч / {hub['logistics_per_order_rub']:.2f} ₽; "
            f"перегружено точек {mesh['overloaded_nodes']}, опозданий {mesh['late_orders']}, "
            f"отмен {mesh['cancelled_orders']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
