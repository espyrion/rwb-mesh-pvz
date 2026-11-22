#!/usr/bin/env python
"""Симуляция дня сети и генерация docs/SCALE.md.

Пример:
    .venv/bin/python scripts/simulate.py --orders 100,500,2000 --write-md docs/SCALE.md
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
> и не прогноз RWB. Допущения: 8 точек по {capacity} операций в день, {p_local:.0%}
> заказов — пары внутри города, остальные идут через хаб; срок хранения 3 дня,
> товар 2 500 ₽. Источник тарифов — `economics/params.yaml`.

Запуск: `.venv/bin/python scripts/simulate.py --orders {orders} --seed {seed} --write-md docs/SCALE.md`

| Заказов в день | Режим | Доля без хаба | Срок mesh | Срок через хаб | p95 | Логистика на заказ | Опоздания | Самая загруженная точка | Прибыль цепочки |
|---:|---|---:|---:|---:|---:|---:|---:|---|---:|
"""


def fmt_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def row(orders: int, mode: str, data: dict, max_node: dict) -> str:
    mesh_hours = f"{data['avg_hours_mesh']:.1f} ч" if data.get("avg_hours_mesh") is not None else "—"
    hub_hours = f"{data['avg_hours_hub']:.1f} ч" if data.get("avg_hours_hub") is not None else "—"
    return (
        f"| {orders} | {mode} | {data['mesh_share']:.0%} | {mesh_hours} | {hub_hours} | {data['p95_hours']:.1f} ч "
        f"| {fmt_money(data['logistics_per_order_rub'])} ₽ | {data['late_orders']} "
        f"| {max_node['node']} · {max_node['utilization']:.0%} | {fmt_money(data['chain_profit_rub'])} ₽ |"
    )


def build_markdown(params: dict, runs: list[dict], seed: int) -> str:
    p_local = runs[0]["p_local"]
    lines = [
        HEADER.format(
            capacity=params["pvz"]["ops_per_day_capacity"],
            p_local=p_local,
            orders=",".join(str(run["orders"]) for run in runs),
            seed=seed,
        ).rstrip("\n")
    ]
    for run in runs:
        for mode, label in (("mesh", "mesh"), ("hub", "только хаб")):
            lines.append(row(run["orders"], label, run["modes"][mode], run["modes"][mode]["bottlenecks"][0]))
    lines.append("")

    # Где именно ломается — считаем по данным, а не пишем от руки.
    first_overload = next((run for run in runs if run["modes"]["mesh"]["overloaded_nodes"] > 0), None)
    ceiling = int(params["pvz"]["ops_per_day_capacity"]) * 8
    lines.append("## Где ломается")
    lines.append("")
    if first_overload:
        mesh = first_overload["modes"]["mesh"]
        worst = mesh["bottlenecks"][0]
        lines.append(
            f"- При {first_overload['orders']} заказах в день в mesh-режиме перегружено "
            f"{mesh['overloaded_nodes']} из 8 точек; худшая — {worst['node']} с {worst['utilization']:.0%} мощности "
            f"и очередью {worst['delay_hours']:.1f} ч. Опозданий: {mesh['late_orders']} "
            f"({mesh['late_share']:.1%}), штрафов {fmt_money(mesh['penalties_rub'])} ₽."
        )
        lines.append(
            f"- Потолок восьми точек по {params['pvz']['ops_per_day_capacity']} операций — "
            f"{ceiling} заказов в день. Выше него сеть деградирует по сроку, а не по стоимости: "
            f"логистика на заказ почти не растёт, растут опоздания."
        )
    else:
        lines.append(f"- Во всех сценариях до {runs[-1]['orders']} заказов в день ни одна точка не перегружена.")
    lines.append(
        "- Что делать при росте: добавлять точки в районы с очередью, вводить слоты приёмки "
        "и буферные ячейки на пиковые часы. Это операционные решения, не изменение модели."
    )
    lines.append("")
    lines.append("## Допущения, которые надо назвать вслух")
    lines.append("")
    lines.append(
        f"- Доля локальных пар {p_local:.0%} — допущение, а не измерение. На демо-данных доля заказов "
        "без хаба выше, потому что заказы создавались вручную."
    )
    lines.append("- Нагрузка по точкам распределена равномерно; в жизни она неравномерна, и потолок наступает раньше.")
    lines.append("- Очередь при перегрузке оценена линейно: сутки задержки на каждые +100% загрузки узла.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Симуляция дня сети ПВЗ")
    parser.add_argument("--orders", default="100,500,2000", help="список объёмов через запятую")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--p-local", type=float, default=0.62, help="доля заказов без хаба")
    parser.add_argument("--params", default=None)
    parser.add_argument("--json", default=None, help="куда сохранить полный JSON")
    parser.add_argument("--write-md", default=None, help="куда записать docs/SCALE.md")
    args = parser.parse_args()

    params = load_params(args.params or ROOT / "economics" / "params.yaml")
    volumes = [int(part) for part in args.orders.split(",") if part.strip()]
    runs = [simulate_day(params, orders=volume, seed=args.seed, p_local=args.p_local) for volume in volumes]

    if args.json:
        Path(args.json).write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_md:
        Path(args.write_md).write_text(build_markdown(params, runs, args.seed), encoding="utf-8")

    for run in runs:
        mesh = run["modes"]["mesh"]
        hub = run["modes"]["hub"]
        print(
            f"{run['orders']:>5} заказов: mesh {mesh['avg_hours']:.1f} ч / {mesh['logistics_per_order_rub']:.2f} ₽ "
            f"против хаба {hub['avg_hours']:.1f} ч / {hub['logistics_per_order_rub']:.2f} ₽; "
            f"перегружено точек {mesh['overloaded_nodes']}, опозданий {mesh['late_orders']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
