"""Скоринг размещения SKU: остаток в ПВЗ города (mesh_pvz) или через хаб.

Модель намеренно интерпретируемая: score 0..100 — взвешенная сумма трёх
нормированных критериев, каждый вклад виден в ответе отдельной строкой, а все
веса, ориентиры и порог живут в economics/params.yaml (pvz.placement_*).

    score = 100 × (w_demand × S_demand + w_turnover × S_turnover + w_footprint × S_footprint)

1. S_demand   — локальный спрос: min(спрос/ориентир, 1). Чем чаще SKU заказывают
                в городе, тем быстрее партия уйдёт и тем выше смысл держать её рядом.
2. S_turnover — оборачиваемость: (медленный порог − ожидаемый срок) / (медленный −
                быстрый), обрезано в 0..1. Хранение стоит денег (storage_rate_day_rub),
                поэтому у критерия самый большой вес.
3. S_footprint — габарит: 1 − (ячеек/шт − 1)/(максимум − 1), обрезано в 0..1.
                Крупный товар занимает ёмкость ПВЗ и мешает быстрым SKU.

Ожидаемый срок хранения считается по закону Литтла (партия ÷ спрос в день) либо
берётся из наблюдённой оборачиваемости; сверху он ограничен
pvz.placement_max_expected_days. Решение: score ≥ placement_score_threshold →
mesh_pvz, иначе hub. Жёсткое ограничение ёмкости (если передан ПВЗ и свободных
ячеек меньше, чем нужно партии) перекрывает score: рекомендация hub.

Никаких «магических» коэффициентов в коде: значения по умолчанию в вызовах
p(...) совпадают с economics/params.yaml и нужны только как страховка.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from . import clock
from .config import p
from .models import Item, Order, Pvz


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(high, x))


def observed_demand(session: Session, sku: str, city: str | None = None) -> tuple[float, str, int]:
    """Спрос по фактическим заказам за окно наблюдения: (заказов/день, источник, окно).

    Источник «observed» — были заказы этого SKU в городе за окно; «assumed» —
    наблюдений нет, берём pvz.placement_default_demand_per_day. Это честная
    оценка: в ответе видно, откуда взялась частота спроса.
    """
    window = int(p("pvz", "placement_demand_window_days", default=14))
    if window < 1:
        window = 1
    since = clock.now(session) - timedelta(days=window)
    query = (
        session.query(Order)
        .join(Item, Order.item_id == Item.id)
        .filter(Item.sku == sku, Order.created_at >= since, Order.status != "cancelled")
    )
    if city:
        query = query.join(Pvz, Order.to_pvz == Pvz.id).filter(Pvz.city == city)
    count = query.count()
    if count == 0:
        return float(p("pvz", "placement_default_demand_per_day", default=1.0)), "assumed", window
    return round(count / window, 3), "observed", window


def score_placement(
    *,
    sku: str,
    qty: int,
    cell_size: int,
    demand_per_day: float,
    turnover_days: float | None = None,
    free_cells: int | None = None,
    pvz_code: str | None = None,
    demand_source: str = "operator",
    demand_window_days: int | None = None,
) -> dict:
    """Посчитать score размещения и объяснить решение.

    Возвращает словарь с ожидаемым сроком хранения, рекомендацией mesh_pvz|hub,
    массивом причин (русский, с числами и именем параметра) и уверенностью.
    """
    if qty < 1:
        raise ValueError("qty должна быть ≥ 1")
    if cell_size < 1:
        raise ValueError("cell_size должен быть ≥ 1")
    if demand_per_day < 0:
        raise ValueError("demand_per_day не может быть отрицательным")

    w_demand = float(p("pvz", "placement_demand_weight", default=0.35))
    w_turnover = float(p("pvz", "placement_turnover_weight", default=0.45))
    w_footprint = float(p("pvz", "placement_footprint_weight", default=0.2))
    demand_target = float(p("pvz", "placement_demand_target_per_day", default=4.0))
    fast_days = float(p("pvz", "placement_fast_turnover_days", default=3.0))
    slow_days = float(p("pvz", "placement_slow_turnover_days", default=21.0))
    footprint_max = int(p("pvz", "placement_footprint_max_cells", default=3))
    threshold = float(p("pvz", "placement_score_threshold", default=60.0))
    confidence_span = float(p("pvz", "placement_confidence_span", default=25.0))
    max_days = float(p("pvz", "placement_max_expected_days", default=60.0))
    storage_rate = float(p("pvz", "storage_rate_day_rub", default=5.0))

    reasons: list[str] = []
    cells_total = int(cell_size) * int(qty)

    # Ожидаемый срок хранения: наблюдение или закон Литтла (партия ÷ спрос в день).
    if turnover_days is not None:
        raw_days = float(turnover_days)
        source_note = f"Оборачиваемость взята из наблюдений: {raw_days:.1f} дн."
    elif demand_per_day > 0:
        raw_days = qty / demand_per_day
        source_note = (
            f"Срок хранения по закону Литтла: {qty} шт. ÷ {demand_per_day:.2f} заказов/день "
            f"= {raw_days:.1f} дн."
        )
    else:
        raw_days = max_days
        source_note = "Локального спроса нет (0 заказов/день): срок хранения принят по потолку."
    expected_days = round(_clamp(raw_days, 0.0, max_days), 2)
    reasons.append(source_note)
    if expected_days < raw_days:
        reasons.append(
            f"Срок хранения ограничен потолком {max_days:.1f} дн. "
            f"(pvz.placement_max_expected_days): спрос слишком низкий."
        )

    # 1. Локальный спрос.
    demand_raw = _clamp(demand_per_day / demand_target, 0.0, 1.0) if demand_target else 0.0
    demand_points = round(w_demand * demand_raw * 100.0, 2)
    reasons.append(
        f"Локальный спрос {demand_per_day:.2f} заказов/день при ориентире {demand_target:.1f} "
        f"(pvz.placement_demand_target_per_day, источник: {demand_source}): критерий спроса "
        f"{demand_raw * 100.0:.0f}% → {demand_points:.1f} из {w_demand * 100.0:.1f} балла."
    )

    # 2. Оборачиваемость: быстрая (fast_days) → 1.0, мёртвая (slow_days) → 0.0.
    span = slow_days - fast_days
    turnover_raw = _clamp((slow_days - expected_days) / span, 0.0, 1.0) if span > 0 else 0.0
    turnover_points = round(w_turnover * turnover_raw * 100.0, 2)
    reasons.append(
        f"Ожидаемый срок хранения {expected_days:.1f} дн. (быстро ≤ {fast_days:.1f}, "
        f"мёртво ≥ {slow_days:.1f}: pvz.placement_fast_turnover_days, "
        f"pvz.placement_slow_turnover_days): критерий оборачиваемости "
        f"{turnover_raw * 100.0:.0f}% → {turnover_points:.1f} из {w_turnover * 100.0:.1f} балла."
    )

    # 3. Габарит партии: 1 ячейка/шт → 1.0, footprint_max и больше → 0.0.
    footprint_raw = _clamp(1.0 - (cell_size - 1) / (footprint_max - 1), 0.0, 1.0) if footprint_max > 1 else 0.0
    footprint_points = round(w_footprint * footprint_raw * 100.0, 2)
    reasons.append(
        f"Габарит {cell_size} яч./шт при максимуме {footprint_max} "
        f"(pvz.placement_footprint_max_cells): критерий габарита "
        f"{footprint_raw * 100.0:.0f}% → {footprint_points:.1f} из {w_footprint * 100.0:.1f} балла."
    )

    score = round(demand_points + turnover_points + footprint_points, 2)
    storage_cost = round(storage_rate * cells_total * expected_days, 2)
    reasons.append(
        f"Ожидаемое хранение: {storage_rate:.2f} ₽/яч./день × {cells_total} яч. × "
        f"{expected_days:.1f} дн. = {storage_cost:.2f} ₽ (pvz.storage_rate_day_rub)."
    )

    recommendation = "mesh_pvz" if score >= threshold else "hub"
    if recommendation == "mesh_pvz":
        reasons.append(
            f"Score {score:.1f} ≥ порога {threshold:.1f} (pvz.placement_score_threshold): "
            "партию выгодно держать в ПВЗ города — локальный спрос окупает хранение."
        )
    else:
        reasons.append(
            f"Score {score:.1f} < порога {threshold:.1f} (pvz.placement_score_threshold): "
            "остаток выгоднее провести через хаб и не занимать ячейки ПВЗ."
        )

    confidence = min(100.0, abs(score - threshold) / confidence_span * 100.0) if confidence_span > 0 else 0.0

    # Жёсткое ограничение ёмкости сильнее скоринга: если партия не влезает — только хаб.
    capacity_override = free_cells is not None and cells_total > max(0, int(free_cells))
    if capacity_override:
        recommendation = "hub"
        confidence = 100.0
        where = f"на {pvz_code}" if pvz_code else "в ПВЗ"
        reasons.append(
            f"Ёмкость важнее score: {where} свободно {max(0, int(free_cells))} яч., "
            f"партии нужно {cells_total} яч. — размещение в ПВЗ невозможно, остаток идёт на хаб."
        )

    components = [
        {
            "id": "demand",
            "label": "локальный спрос",
            "weight": w_demand,
            "raw": round(demand_raw, 4),
            "points": demand_points,
            "max_points": round(w_demand * 100.0, 2),
        },
        {
            "id": "turnover",
            "label": "оборачиваемость",
            "weight": w_turnover,
            "raw": round(turnover_raw, 4),
            "points": turnover_points,
            "max_points": round(w_turnover * 100.0, 2),
        },
        {
            "id": "footprint",
            "label": "габарит партии",
            "weight": w_footprint,
            "raw": round(footprint_raw, 4),
            "points": footprint_points,
            "max_points": round(w_footprint * 100.0, 2),
        },
    ]

    return {
        "sku": sku,
        "qty": int(qty),
        "cell_size": int(cell_size),
        "cells_total": cells_total,
        "demand_per_day": round(float(demand_per_day), 3),
        "demand_source": demand_source,
        "demand_window_days": demand_window_days,
        "expected_storage_days": expected_days,
        "expected_storage_cost_rub": storage_cost,
        "components": components,
        "score": score,
        "threshold": threshold,
        "recommendation": recommendation,
        "confidence_pct": round(confidence, 1),
        "capacity_override": capacity_override,
        "reasons": reasons,
        "params_used": {
            "placement_demand_weight": w_demand,
            "placement_turnover_weight": w_turnover,
            "placement_footprint_weight": w_footprint,
            "placement_demand_target_per_day": demand_target,
            "placement_fast_turnover_days": fast_days,
            "placement_slow_turnover_days": slow_days,
            "placement_footprint_max_cells": footprint_max,
            "placement_score_threshold": threshold,
            "placement_confidence_span": confidence_span,
            "placement_max_expected_days": max_days,
            "storage_rate_day_rub": storage_rate,
        },
    }


def auto_enabled() -> bool:
    """Включена ли авторекомендация при приёмке партии (pvz.placement_auto_recommend)."""
    return bool(p("pvz", "placement_auto_recommend", default=False))


def acceptance_recommendation(session: Session, item: Item, pvz: Pvz) -> dict | None:
    """Рекомендация для только что принятой партии; None, если флаг выключен.

    Спрос берётся из фактических заказов SKU в городе ПВЗ за окно наблюдения
    (pvz.placement_demand_window_days); если заказов не было — допущение
    pvz.placement_default_demand_per_day. Свободные ячейки считаются до занятия
    партией: вызывать до storage.occupy().
    """
    if not auto_enabled():
        return None
    demand, source, window = observed_demand(session, item.sku, pvz.city)
    return score_placement(
        sku=item.sku,
        qty=item.qty,
        cell_size=item.cell_size,
        demand_per_day=demand,
        free_cells=max(0, pvz.capacity_cells - pvz.used_cells),
        pvz_code=pvz.code,
        demand_source=source,
        demand_window_days=window,
    )