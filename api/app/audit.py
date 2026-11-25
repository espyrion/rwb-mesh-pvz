"""Инвентаризация остатков ПВЗ: сверка факта с учётом, корректировка, удержание.

Правила (все числа — economics/params.yaml, секция pvz.audit_*):

* оператор передаёт фактические остатки по партиям (item_id → qty);
* расхождение корректирует остаток условным UPDATE (rowcount = 1, иначе 409:
  остаток изменился параллельно), ячейки ПВЗ двигаются тем же способом в
  пределах ёмкости;
* на каждую расхождённую партию открывается инцидент типа stock_mismatch;
* удержание с ПВЗ: недостача × audit_shortage_penalty_rub + излишек ×
  audit_surplus_penalty_rub, но не больше audit_penalty_cap_rub. Проводка —
  пара строк ledger (ПВЗ −удержание, маркетплейс +компенсация), как в возврате;
* точность проверки = 100 × (1 − Σ|Δ| / Σ учётных единиц); сглаженная точность
  ПВЗ = alpha × свежая + (1 − alpha) × прежняя (audit_accuracy_ema_alpha);
  SLA-рейтинг — по границам audit_sla_a/b/c_pct;
* повторная инвентаризация с теми же фактическими данными и неизменным учётом
  возвращает сохранённый ответ (idempotent=true) и не создаёт ни инцидента,
  ни проводки.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import update
from sqlalchemy.orm import Session

from . import clock, money
from .config import p
from .models import Incident, Item, Pvz
from .notify import notify
from .serializers import iso, reserved_qty


class AuditError(Exception):
    """Нарушение правил инвентаризации: HTTP-код и текст для клиента."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _fingerprint(pvz_id: int, actual: dict[int, int]) -> str:
    payload = json.dumps(
        {"pvz": pvz_id, "actual": [[int(k), int(v)] for k, v in sorted(actual.items())]},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cached_is_current(session: Session, pvz: Pvz, cached: dict) -> bool:
    """Слепок ещё актуален: учётные количества не изменились после прошлой сверки."""
    for line in cached.get("lines", []):
        item = session.get(Item, line["item_id"])
        if item is None or item.stored_pvz_id != pvz.id or int(item.qty) != int(line["actual_qty"]):
            return False
    return True


def _rating(accuracy_pct: float) -> str:
    if accuracy_pct >= float(p("pvz", "audit_sla_a_pct", default=98.0)):
        return "A"
    if accuracy_pct >= float(p("pvz", "audit_sla_b_pct", default=95.0)):
        return "B"
    if accuracy_pct >= float(p("pvz", "audit_sla_c_pct", default=90.0)):
        return "C"
    return "D"


def run_audit(session: Session, pvz: Pvz, actual: dict[int, int], note: str = "") -> dict:
    """Сверить остатки ПВЗ с фактом. Вызывать внутри транзакции с BEGIN IMMEDIATE."""
    if pvz.is_hub:
        raise AuditError(422, "инвентаризация на хабе не проводится: хаб — заглушка магистрали")
    if not actual:
        raise AuditError(422, "нужен хотя бы один item_id с фактическим количеством")

    fingerprint = _fingerprint(pvz.id, actual)
    if pvz.last_audit_fingerprint == fingerprint and pvz.last_audit_json:
        cached = json.loads(pvz.last_audit_json)
        if _cached_is_current(session, pvz, cached):
            cached["idempotent"] = True
            return cached

    requested = sorted(int(item_id) for item_id in actual)
    items = {
        row.id: row
        for row in session.query(Item).filter(Item.stored_pvz_id == pvz.id, Item.id.in_(requested)).all()
    }
    missing = [item_id for item_id in requested if item_id not in items]
    if missing:
        raise AuditError(422, f"партии не найдены на {pvz.code}: {missing}")

    # Факт ниже резерва активных заказов сломал бы инвариант «резерв ≤ остаток»:
    # сначала нужно закрыть/перевесить заказы, потом списывать.
    for item_id in requested:
        fact = int(actual[item_id])
        reserved = reserved_qty(session, item_id)
        if fact < reserved:
            raise AuditError(
                409,
                f"факт по партии #{item_id} ({fact} шт.) ниже резерва активных заказов "
                f"({reserved} шт.): сначала решите заказы",
            )

    ts = clock.now(session)
    lines: list[dict] = []
    incident_ids: list[int] = []
    shortage_units = 0
    surplus_units = 0
    cells_delta_total = 0
    counted_units = 0
    mismatch_units = 0

    for item_id in requested:
        item = items[item_id]
        expected = int(item.qty)
        fact = int(actual[item_id])
        delta = fact - expected
        counted_units += expected
        mismatch_units += abs(delta)

        incident_id = None
        if delta:
            # Условный UPDATE: корректируем только если учёт всё ещё равен прочитанному.
            stmt = (
                update(Item)
                .where(Item.id == item_id, Item.stored_pvz_id == pvz.id, Item.qty == expected)
                .values(qty=fact)
            )
            if session.execute(stmt).rowcount != 1:
                raise AuditError(
                    409,
                    f"остаток партии #{item_id} изменился во время инвентаризации: повторите сверку",
                )
            cells_delta = delta * int(item.cell_size)
            cells_delta_total += cells_delta
            if delta < 0:
                shortage_units += -delta
            else:
                surplus_units += delta

            kind = "недостача" if delta < 0 else "излишек"
            incident = Incident(
                ts=ts,
                order_id=None,
                type="stock_mismatch",
                description=(
                    f"Инвентаризация {pvz.code}: партия #{item.id} {item.sku} — "
                    f"учёт {expected} шт., факт {fact} шт. ({kind} {abs(delta)} шт.)"
                    + (f". {note}" if note else "")
                ),
                status="open",
            )
            session.add(incident)
            session.flush()
            incident_id = incident.id
            incident_ids.append(incident.id)

        lines.append(
            {
                "item_id": item.id,
                "sku": item.sku,
                "expected_qty": expected,
                "actual_qty": fact,
                "delta": delta,
                "cell_size": int(item.cell_size),
                "cells_delta": delta * int(item.cell_size),
                "status": "matched" if delta == 0 else ("shortage" if delta < 0 else "surplus"),
                "incident_id": incident_id,
            }
        )

    # Ячейки ПВЗ двигаются одним условным UPDATE в границах [0, capacity_cells].
    if cells_delta_total:
        stmt = (
            update(Pvz)
            .where(
                Pvz.id == pvz.id,
                Pvz.used_cells + cells_delta_total >= 0,
                Pvz.used_cells + cells_delta_total <= Pvz.capacity_cells,
            )
            .values(used_cells=Pvz.used_cells + cells_delta_total)
        )
        if session.execute(stmt).rowcount != 1:
            raise AuditError(
                409,
                f"корректировка остатков вывела бы {pvz.code} за границы ёмкости "
                f"({cells_delta_total:+d} яч.): сверьте партии и свободные ячейки",
            )
        session.refresh(pvz)

    shortage_rate = float(p("pvz", "audit_shortage_penalty_rub", default=30.0))
    surplus_rate = float(p("pvz", "audit_surplus_penalty_rub", default=10.0))
    cap = float(p("pvz", "audit_penalty_cap_rub", default=200.0))
    penalty = round(min(cap, shortage_units * shortage_rate + surplus_units * surplus_rate), 2)

    if penalty > 0:
        parts = []
        if shortage_units:
            parts.append(f"недостача {shortage_units} шт. × {shortage_rate:.2f} ₽")
        if surplus_units:
            parts.append(f"излишек {surplus_units} шт. × {surplus_rate:.2f} ₽")
        note_text = f"инвентаризация {pvz.code}: " + ", ".join(parts)
        if penalty >= cap:
            note_text += f" (потолок {cap:.2f} ₽)"
        money.add_ledger(
            session,
            order_id=None,
            item_id=None,
            party="pvz",
            party_id=pvz.id,
            amount_rub=-penalty,
            kind="penalty",
            note=note_text,
        )
        money.add_ledger(
            session,
            order_id=None,
            item_id=None,
            party="marketplace",
            party_id=None,
            amount_rub=penalty,
            kind="penalty",
            note=f"компенсация удержания с {pvz.code} за расхождение остатков",
        )
        notify(
            session,
            "pvz",
            f"Инвентаризация {pvz.code}: расхождения {shortage_units + surplus_units} шт., "
            f"удержание {penalty:.2f} ₽, инцидентов {len(incident_ids)}.",
            payload={"kind": "stock_audit", "pvz_id": pvz.id, "penalty_rub": penalty},
        )

    audit_accuracy = (0.0 if mismatch_units > 0 else 100.0) if counted_units == 0 else 100.0 * (1.0 - mismatch_units / counted_units)
    audit_accuracy = round(max(0.0, min(100.0, audit_accuracy)), 2)
    alpha = float(p("pvz", "audit_accuracy_ema_alpha"))
    # До первой сверки точку считаем точной (audit_initial_accuracy_pct): EMA нужен
    # стартовый уровень, иначе одна плановая проверка сразу роняет рейтинг.
    if (pvz.audit_count or 0) > 0:
        previous = float(pvz.audit_accuracy_pct)
    else:
        previous = float(p("pvz", "audit_initial_accuracy_pct", default=100.0))
    smoothed = alpha * audit_accuracy + (1.0 - alpha) * previous
    pvz.audit_accuracy_pct = round(max(0.0, min(100.0, smoothed)), 1)
    pvz.audit_count = int(pvz.audit_count or 0) + 1
    pvz.audit_sla_rating = _rating(pvz.audit_accuracy_pct)
    pvz.last_audit_at = ts
    pvz.last_audit_fingerprint = fingerprint

    result = {
        "pvz_id": pvz.id,
        "code": pvz.code,
        "audited_at": iso(ts),
        "note": note,
        "lines": lines,
        "matched": sum(1 for line in lines if line["status"] == "matched"),
        "shortage": sum(1 for line in lines if line["status"] == "shortage"),
        "surplus": sum(1 for line in lines if line["status"] == "surplus"),
        "total_delta_units": surplus_units - shortage_units,
        "cells_delta": cells_delta_total,
        "incident_ids": incident_ids,
        "penalty_total_rub": penalty,
        "accuracy_pct": pvz.audit_accuracy_pct,
        "audit_accuracy_pct": audit_accuracy,
        "accuracy_before_pct": round(previous, 1),
        "sla_rating": pvz.audit_sla_rating,
        "audits_total": pvz.audit_count,
        "used_cells": pvz.used_cells,
        "free_cells": max(0, pvz.capacity_cells - pvz.used_cells),
        "idempotent": False,
    }
    pvz.last_audit_json = json.dumps(result, ensure_ascii=False)
    session.flush()
    return result