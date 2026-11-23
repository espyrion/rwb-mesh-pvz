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

## Две схемы потока: наивный mesh и адаптивный mesh с overflow

| Заказов в день | Режим | Доля без хаба | Срок mesh-заказов | Срок через хаб | p95 по всем | Логистика на заказ | GMV, ₽ | Маржа МП, % | ПВЗ/заказ, ₽ | Опоздания | Отмены | Самая загруженная точка | Прибыль цепочки, ₽ |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
"""

ADAPTIVE_HEADER = """## Наивный mesh против адаптивного mesh с overflow

Наивный mesh отправляет локальную пару местной схемой всегда. Адаптивный mesh
переливает заказ на магистраль, если приём поднял бы загрузку точки выше
`network.overflow_utilization_threshold`. Поток заказов в обоих режимах один и тот же
(seed {seed}), поэтому разница — только в решениях маршрутизатора.

| Заказов в день | Режим | Опоздания | Макс. загрузка ПВЗ | Потери от отмен, ₽ | Стоимость доставки/заказ, ₽ | Маржа МП, % | Ожидание клиента, ч |
|---:|---|---:|---:|---:|---:|---:|---:|
"""

ASSUMPTIONS = """## Допущения адаптивного роутинга и эвакуации

- Порог перелива `network.overflow_utilization_threshold` = {threshold_pct}% совпадает с
  `pvz.utilization_alert_pct` = {alert_pct}%, то есть с уровнем, на котором точка уже
  считается проблемной. Очередь ПВЗ — M/M/1: при рабочем уровне {target_pct}% ожидание
  {base_wait} ч, при {alert_pct}% — {alert_wait} ч, у потолка {queue_cap_pct}% — {cap_wait} ч.
  Порог взят по существующему сигналу тревоги, а не «примерно посередине»: до него точка
  работает в рабочем режиме, после — каждый новый локальный заказ добавляет очередь.
- Что значит «уходит на хаб» в модели: внутригородская операция снимается с точки, партию
  везёт магистраль по расписанию. Хаб смоделирован как заглушка магистрали без очереди,
  поэтому перелив не создаёт нового ожидания, но добавляет {hub_extra} ₽ логистики и
  {hub_hours} ч срока к заказу (тарифы `logistics.hub_handling_rub` +
  `trunk_cost_per_order_rub` + `last_mile_cost_rub` против `local_delivery_cost_rub`).
- Перелив применяется только к локальным парам: заказ, который и так идёт через хаб,
  переливать некуда. Поэтому выше потолка сети загрузка точек всё равно превышает 100% —
  overflow снижает пик по сроку, но не заменяет расширение сети.
- Перелив — не бесплатная операция: в таблице видно, что адаптивный режим снижает
  опоздания и потери отмен, но повышает стоимость доставки на заказ. Это и есть
  компромисс backpressure, а не «улучшение всего сразу».
- Координаты точек (seed-данные `api/app/seed.py`) — приближённые центры районов, не
  геодезия: погрешность порядка 0,5 км, что для выбора соседа внутри города приемлемо.
  Хаб `HUB-KZN` — точка привязки магистрали, ёмкости и тарифов приёмки не имеет.
- Расстояние между точками — по дуге большого круга × `pvz.road_detour_factor` = {detour}
  (типичное удлинение улиц относительно прямой в городской застройке). Время перегона —
  расстояние ÷ `pvz.relocation_speed_kmh` = {speed} км/ч (курьер с остановками и разгрузкой).
- Тариф перемещения остатка — единый `pvz.relocation_cost_rub` = {relocation} ₽ за рейс:
  это стоимость внешнего перевозчика, а не такси по счётчику. Расстояние влияет на выбор
  соседа и на оценку времени, но не на сумму проводки — иначе пришлось бы вводить
  несуществующий километровый тариф.
- Скор выбора соседа при эвакуации: `свободные_ячейки × pvz.evacuation_capacity_weight`
  (= {w_cap}) минус `расстояние_км × pvz.evacuation_distance_weight` (= {w_dist}). Свободные
  ячейки берутся абсолютным числом, а не процентом: точка с 40 свободными ячейками полезнее
  для эвакуации, чем маленькая с 5 свободными при 30% загрузки. Вес расстояния 2 означает,
  что лишний километр перегона стоит как две ячейки ёмкости — при типичных размерах партии
  (2–8 ячеек) это заставляет предпочитать ближнюю точку при сопоставимой ёмкости.
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


def adaptive_row(orders: int, label: str, data: dict) -> str:
    return (
        f"| {orders} | {label} | {data['late_orders']} "
        f"| {fmt_num(data['max_utilization'] * 100, 0)}% | {fmt_money(data['cancellation_losses_rub'])} "
        f"| {fmt_money(data['logistics_per_order_rub'])} | {fmt_num(data['marketplace_margin_pct'], 2)}% "
        f"| {fmt_num(data['avg_wait_hours'], 2)} |"
    )


def build_markdown(params: dict, runs: list[dict], seed: int) -> str:
    p_local = runs[0]["p_local"]
    network = params["network"]
    defaults = params["scenario_defaults"]
    pvz = params["pvz"]
    lines = [
        HEADER.format(
            capacity=pvz["ops_per_day_capacity"],
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
    lines.append(ADAPTIVE_HEADER.format(seed=seed).rstrip("\n"))
    for run in runs:
        lines.append(adaptive_row(run["orders"], "Наивный mesh", run["modes"]["mesh"]))
        lines.append(adaptive_row(run["orders"], "Адаптивный с overflow", run["adaptive"]))
    lines.append("")

    # Где именно ломается — считаем по данным, а не пишем от руки.
    first_overload = next((run for run in runs if run["modes"]["mesh"]["overloaded_nodes"] > 0), None)
    ceiling = int(pvz["ops_per_day_capacity"]) * int(network["nodes"])
    lines.append("## Где ломается")
    lines.append("")
    if first_overload:
        mesh = first_overload["modes"]["mesh"]
        adaptive = first_overload["adaptive"]
        worst = mesh["bottlenecks"][0]
        lines.append(
            f"- При {first_overload['orders']} заказах в день в наивном mesh перегружено "
            f"{mesh['overloaded_nodes']} из {network['nodes']} точек; худшая — {worst['node']} с "
            f"{fmt_num(worst['utilization'] * 100, 0)}% мощности и очередью {fmt_num(worst['wait_hours'])} ч. "
            f"Опозданий: {mesh['late_orders']} ({fmt_num(mesh['late_share'] * 100)}%), отмен: {mesh['cancelled_orders']}, "
            f"штрафов {fmt_money(mesh['late_penalty_rub'])} ₽, обратная логистика "
            f"{fmt_money(mesh['reverse_logistics_rub'])} ₽."
        )
        lines.append(
            f"- Адаптивный режим на том же потоке переливает {adaptive['overflow_orders']} заказов "
            f"({fmt_num(adaptive['overflow_share'] * 100)}%) и держит максимум загрузки на "
            f"{fmt_num(adaptive['max_utilization'] * 100, 0)}%: опозданий {adaptive['late_orders']}, "
            f"отмен {adaptive['cancelled_orders']}, потери от отмен {fmt_money(adaptive['cancellation_losses_rub'])} ₽ "
            f"против {fmt_money(mesh['cancellation_losses_rub'])} ₽. Плата за это — "
            f"{fmt_money(adaptive['logistics_per_order_rub'])} ₽ за заказ против "
            f"{fmt_money(mesh['logistics_per_order_rub'])} ₽."
        )
        lines.append(
            f"- Потолок {network['nodes']} точек по {pvz['ops_per_day_capacity']} операций — "
            f"{ceiling} заказов в день. Overflow снимает пик по сроку, но не расширяет сеть: "
            f"выше потолка локальные заказы уходят на магистраль, а заказы, которые и так идут "
            f"через хаб, продолжают нагружать точки — выигрыш по опозданиям оплачивается логистикой."
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
        f"- Очередь: M/M/1 до {fmt_num(network['queue_cap_pct'], 0)}% загрузки, дальше линейно — "
        f"{fmt_money(network['overload_delay_hours_per_100pct'])} ч на каждые +100%."
    )
    lines.append(
        "- Нагрузка по точкам распределена равномерно; в жизни она неравномерна, и потолок наступает раньше."
    )
    lines.append("")

    # Числа для обоснования порога берём из той же модели очереди, что и таблица.
    from economics.model import queue_wait_hours, route_hours  # локальный импорт: нужен только для отчёта

    capacity = int(pvz["ops_per_day_capacity"])
    target_wait = queue_wait_hours(params, float(pvz["utilization_target_pct"]) / 100.0)
    alert_wait = queue_wait_hours(params, float(pvz["utilization_alert_pct"]) / 100.0)
    cap_wait = queue_wait_hours(params, float(network["queue_cap_pct"]) / 100.0)
    hub_extra = (
        float(params["logistics"]["hub_handling_rub"])
        + float(params["logistics"]["trunk_cost_per_order_rub"])
        + float(params["logistics"]["last_mile_cost_rub"])
        - float(params["logistics"]["local_delivery_cost_rub"])
    )
    hub_hours = route_hours(params, "hub") - route_hours(params, "mesh")
    lines.append(
        ASSUMPTIONS.format(
            threshold_pct=fmt_num(float(network["overflow_utilization_threshold"]) * 100, 0),
            queue_cap_pct=fmt_num(network["queue_cap_pct"], 0),
            alert_pct=fmt_num(pvz["utilization_alert_pct"], 0),
            target_pct=fmt_num(pvz["utilization_target_pct"], 0),
            alert_wait=fmt_num(alert_wait, 2),
            base_wait=fmt_num(target_wait, 2),
            cap_wait=fmt_num(cap_wait, 2),
            hub_extra=fmt_money(hub_extra),
            hub_hours=fmt_num(hub_hours, 1),
            detour=fmt_money(pvz["road_detour_factor"]),
            speed=fmt_num(pvz["relocation_speed_kmh"], 0),
            relocation=fmt_money(pvz["relocation_cost_rub"]),
            w_cap=fmt_money(pvz["evacuation_capacity_weight"]),
            w_dist=fmt_money(pvz["evacuation_distance_weight"]),
        ).rstrip("\n")
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
        adaptive = run["adaptive"]
        print(
            f"{run['orders']:>5} заказов: mesh {mesh['avg_hours']:.1f} ч / {mesh['logistics_per_order_rub']:.2f} ₽ "
            f"против хаба {hub['avg_hours']:.1f} ч / {hub['logistics_per_order_rub']:.2f} ₽; "
            f"перегружено точек {mesh['overloaded_nodes']}, опозданий {mesh['late_orders']}, "
            f"отмен {mesh['cancelled_orders']}"
        )
        print(
            f"      адаптивный: перелив {adaptive['overflow_orders']}, опозданий {adaptive['late_orders']}, "
            f"макс. загрузка {adaptive['max_utilization'] * 100:.0f}%, "
            f"потери отмен {adaptive['cancellation_losses_rub']:.2f} ₽, "
            f"доставка {adaptive['logistics_per_order_rub']:.2f} ₽, "
            f"маржа МП {adaptive['marketplace_margin_pct']:.2f}%, "
            f"ожидание {adaptive['avg_wait_hours']:.2f} ч"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())