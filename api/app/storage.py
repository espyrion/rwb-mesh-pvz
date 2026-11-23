"""Ёмкость ПВЗ, тарифы хранения, пороги и правило перемещения остатка."""
from __future__ import annotations

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from . import clock, money
from .config import p
from .models import Item, Ledger, Order, Pvz
from .notify import notify

# Статусы, при которых заказ держит остаток зарезервированным.
ACTIVE_RESERVE_STATUSES = ("created", "routed", "assembled", "rerouted", "in_transit")


def reserve_stock(session: Session, item_id: int, qty: int) -> bool:
    """Атомарно зарезервировать qty единиц остатка.

    Проверка остатка и запись статуса — один условный UPDATE: параллельный заказ
    либо видит уже списанный резерв (rowcount=0), либо сам забирает остаток.
    Это защищает от двойного бронирования последней единицы.
    """
    reserved = (
        select(func.coalesce(func.sum(Order.qty), 0))
        .where(Order.item_id == item_id, Order.status.in_(ACTIVE_RESERVE_STATUSES))
        .scalar_subquery()
    )
    stmt = (
        update(Item)
        .where(Item.id == item_id, Item.qty >= qty, Item.qty - reserved >= qty)
        .values(status=case((Item.status == "stored", "reserved"), else_=Item.status))
    )
    return session.execute(stmt).rowcount == 1


class CapacityError(Exception):
    """Нет свободных ячеек на ПВЗ."""


def cells_for(item: Item, qty: int | None = None) -> int:
    return int(item.cell_size) * int(qty if qty is not None else item.qty)


def ensure_capacity(pvz: Pvz, needed: int) -> None:
    if needed < 0:
        raise ValueError("needed cells must be >= 0")
    if pvz.used_cells + needed > pvz.capacity_cells:
        raise CapacityError(
            f"на {pvz.code} недостаточно ячеек: свободно {pvz.capacity_cells - pvz.used_cells}, нужно {needed}"
        )


def occupy(pvz: Pvz, cells: int) -> None:
    ensure_capacity(pvz, cells)
    pvz.used_cells += cells


def release(pvz: Pvz, cells: int) -> None:
    pvz.used_cells = max(0, pvz.used_cells - max(0, cells))


def utilization(pvz: Pvz) -> float:
    if not pvz.capacity_cells:
        return 0.0
    return round(pvz.used_cells / pvz.capacity_cells * 100.0, 2)


def accrued_storage(session: Session, item_id: int, since=None) -> float:
    q = session.query(Ledger).filter(
        Ledger.item_id == item_id, Ledger.party == "pvz", Ledger.kind == "storage"
    )
    if since is not None:
        q = q.filter(Ledger.ts > since)
    rows = q.all()
    return round(sum(r.amount_rub for r in rows), 2)


def days_stored(item: Item, now) -> float:
    return round((now - item.accepted_at).total_seconds() / 86400.0, 2)


def relocate_offer(session: Session, item: Item) -> dict | None:
    """Предложить ПВЗ того же города с меньшей загрузкой и свободной ёмкостью."""
    src = session.get(Pvz, item.stored_pvz_id)
    if src is None:
        return None
    needed = cells_for(item)
    candidates = (
        session.query(Pvz)
        .filter(
            Pvz.city == src.city,
            Pvz.id != src.id,
            Pvz.is_active.is_(True),
            Pvz.is_hub.is_(False),
            Pvz.capacity_cells - Pvz.used_cells >= needed,
        )
        .all()
    )
    if not candidates:
        return None
    best = min(candidates, key=lambda z: (z.used_cells / z.capacity_cells if z.capacity_cells else 1.0, z.id))
    return {
        "from_pvz_id": src.id,
        "from_code": src.code,
        "suggested_pvz_id": best.id,
        "suggested_code": best.code,
        "suggested_utilization_pct": utilization(best),
        "source_utilization_pct": utilization(src),
        "cells": needed,
        "cost_rub": float(p("pvz", "relocation_cost_rub", default=60.0)),
    }


def evaluate_long_storage(session: Session, item: Item, now) -> dict | None:
    """Порог длительного хранения: дни или накопленная плата. Возвращает событие."""
    threshold_days = float(p("pvz", "long_storage_threshold_days", default=21))
    threshold_rub = float(p("pvz", "long_storage_threshold_rub", default=600))
    days = days_stored(item, now)
    accrued = accrued_storage(session, item.id, since=item.accepted_at)
    if days >= threshold_days or accrued >= threshold_rub:
        return {
            "item_id": item.id,
            "sku": item.sku,
            "days_stored": days,
            "accrued_storage_rub": accrued,
            "threshold_days": threshold_days,
            "threshold_rub": threshold_rub,
            "offer": relocate_offer(session, item) if item.status == "stored" else None,
        }
    return None


def accrue_storage(session: Session, days: int) -> dict:
    """Пересчитать хранение за days дней по всем остаткам на ПВЗ."""
    if days < 0:
        raise ValueError("days must be >= 0")
    now = clock.now(session)
    total = 0.0
    alerts = 0
    per_pvz: dict[int, float] = {}
    items = (
        session.query(Item)
        .filter(Item.status.in_(("stored", "reserved", "assembled", "returned")))
        .order_by(Item.id)
        .all()
    )
    for item in items:
        base = item.last_accrued_at or item.accepted_at
        elapsed = round((now - base).total_seconds() / 86400.0, 4)
        if elapsed <= 0:
            continue
        amount = money.charge_storage(session, item, elapsed)
        total += amount
        per_pvz[item.stored_pvz_id] = round(per_pvz.get(item.stored_pvz_id, 0.0) + amount, 2)
        item.last_accrued_at = now
        event = evaluate_long_storage(session, item, now)
        if event and not item.relocate_alert_sent:
            item.relocate_alert_sent = True
            alerts += 1
            offer = event["offer"]
            text = (
                f"Длительное хранение {item.sku} ({event['days_stored']:.1f} дн., "
                f"начислено {event['accrued_storage_rub']:.0f} ₽). "
            )
            if offer:
                text += (
                    f"Предложение: переместить в {offer['suggested_code']} "
                    f"(загрузка {offer['suggested_utilization_pct']:.0f}% против "
                    f"{offer['source_utilization_pct']:.0f}%), стоимость {offer['cost_rub']:.0f} ₽."
                )
            else:
                if item.status != "stored":
                    text += "Остаток зарезервирован под заказ — перемещение возможно после решения по заказу."
                else:
                    text += "Свободных ПВЗ для перемещения нет — рекомендуется согласовать уценку/возврат продавцу."
            notify(
                session,
                "pvz",
                text,
                payload={"kind": "long_storage", "item_id": item.id, "offer": offer},
            )
    session.flush()
    return {
        "days": days,
        "charged_total_rub": round(total, 2),
        "by_pvz": {str(k): v for k, v in sorted(per_pvz.items())},
        "alerts": alerts,
        "virtual_now": now.isoformat(),
    }


def relocate(session: Session, item: Item, to_pvz_id: int) -> dict:
    src = session.get(Pvz, item.stored_pvz_id)
    dst = session.get(Pvz, to_pvz_id)
    if src is None or dst is None:
        raise LookupError("pvz not found")
    if dst.is_hub:
        raise ValueError("нельзя переместить остаток на хаб")
    if dst.id == src.id:
        raise ValueError("остаток уже на этом ПВЗ")
    if dst.city != src.city:
        raise ValueError("перемещение остатка допускается только в пределах города")
    if item.status not in ("stored",):
        raise ValueError("перемещать можно только свободный остаток (status=stored)")
    cells = cells_for(item)
    ensure_capacity(dst, cells)
    release(src, cells)
    occupy(dst, cells)
    item.stored_pvz_id = dst.id
    item.last_accrued_at = clock.now(session)
    # Перемещение начинает новый период хранения: иначе алерт о длительном хранении
    # срабатывает сразу после переезда и порог по накопленной плате считается заново.
    item.accepted_at = clock.now(session)
    item.relocate_alert_sent = False
    cost = money.charge_relocation(session, item, src, dst)
    notify(
        session,
        "pvz",
        f"Остаток {item.sku} перемещён {src.code}→{dst.code} ({cells} яч.), затраты {cost:.0f} ₽ отнесены на маркетплейс.",
        payload={"kind": "relocation", "item_id": item.id, "from_pvz_id": src.id, "to_pvz_id": dst.id},
    )
    session.flush()
    return {
        "item_id": item.id,
        "from_pvz_id": src.id,
        "to_pvz_id": dst.id,
        "cells": cells,
        "cost_rub": cost,
        "used_cells": {"from": src.used_cells, "to": dst.used_cells},
    }
