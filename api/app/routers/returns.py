"""Возврат выданного заказа: обратный маршрут, остатки, корректировки ledger (UC-3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import money, routing, storage
from ..clock import now
from ..db import begin_immediate, get_db
from ..models import Item, Order, Pvz
from ..notify import notify
from ..serializers import order_dict, route_dict

router = APIRouter(tags=["returns"])


@router.post("/orders/{order_id}/return")
def return_order(order_id: int, db: Session = Depends(get_db)):
    # Блокировка записи до чтения статуса: параллельные возвраты выстраиваются в очередь,
    # второй видит status=returned и получает идемпотентный ответ (без повторного списания).
    begin_immediate(db)
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    if order.status == "returned":
        return {"order": order_dict(db, order), "idempotent_replay": True}
    if order.status != "issued":
        raise HTTPException(409, f"возврат возможен только для выданного заказа (status={order.status})")
    ts = now(db)
    if order.return_deadline and ts > order.return_deadline:
        raise HTTPException(409, "срок возврата истёк")
    item = db.get(Item, order.item_id)
    if item is None:
        raise HTTPException(409, "товар заказа не найден")

    try:
        comparison = routing.compare(db, order.to_pvz, order.from_pvz, accept_latency=True)
    except ValueError as exc:
        raise HTTPException(409, f"обратный маршрут недоступен: {exc}") from exc
    if not comparison["chosen"]:
        raise HTTPException(409, f"обратный маршрут недоступен: {comparison['reason']}")
    reverse_route = routing.make_route(
        db, order.id, comparison, comparison["chosen"], reason="Обратный маршрут возврата: " + comparison["reason"]
    )

    dst = db.get(Pvz, order.from_pvz)
    cells = item.cell_size * order.qty
    try:
        storage.ensure_capacity(dst, cells)
    except storage.CapacityError as exc:
        raise HTTPException(409, f"на ПВЗ возврата нет места: {exc}") from exc

    money.post_return(db, order, item, reverse_route)
    storage.occupy(dst, cells)
    item.stored_pvz_id = dst.id
    # Партия могла быть разделена при сборке: возвращаем ровно количество заказа.
    # Не перезаписываем qty: инвентаризация могла оприходовать излишек, и присваивание
    # стёрло бы учтённые единицы (аудит 2026-11-25).
    item.qty += order.qty
    item.status = "returned"
    item.last_accrued_at = ts
    item.relocate_alert_sent = False
    order.status = "returned"
    order.return_route_id = reverse_route.id

    notify(
        db,
        "pvz",
        f"Возврат по заказу {order.code} принят на {dst.code}: {order.qty} шт., "
        f"{cells} яч. Удержание за обратную логистику 60 ₽.",
        order_id=order.id,
        payload={"kind": "return_accepted", "pvz_id": dst.id, "cells": cells},
    )
    notify(
        db,
        "seller",
        f"Возврат по заказу {order.code}: товар «{item.title}» вернулся на {dst.code}, "
        f"сторно выручки {order.seller_net_rub:.0f} ₽.",
        order_id=order.id,
        payload={"kind": "return_seller", "net_rub": order.seller_net_rub},
    )
    db.commit()
    return {
        "order": order_dict(db, order),
        "reverse_route": route_dict(reverse_route),
        "ledger": money.ledger_sum(db, order.id),
        "item": {"id": item.id, "qty": item.qty, "status": item.status, "pvz_id": item.stored_pvz_id},
    }
