"""Заказы: создание с маршрутом, сборка, транзит, выдача."""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import money, routing, storage
from ..paging import page
from ..responses import OrderOut
from ..clock import now
from ..config import p
from ..db import begin_immediate, get_db
from ..models import Item, ORDER_TRANSITIONS, Order, Pvz, Route
from ..notify import notify
from ..schemas import OrderCreate
from ..serializers import order_dict, route_dict

router = APIRouter(tags=["orders"])


def _transition(order: Order, target: str) -> None:
    allowed = ORDER_TRANSITIONS.get(order.status, set())
    if target not in allowed:
        raise HTTPException(409, f"недопустимый переход статуса {order.status} → {target}")


@router.post("/orders", status_code=201)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    # Захватываем запись SQLite до чтения остатка: два параллельных заказа не должны
    # одновременно увидеть одну и ту же последнюю единицу.
    begin_immediate(db)
    if payload.idempotency_key:
        existing = db.query(Order).filter(Order.idempotency_key == payload.idempotency_key).first()
        if existing:
            return {
                "order": order_dict(db, existing),
                "route": route_dict(db.get(Route, existing.route_id)),
                "idempotent_replay": True,
            }

    item = db.get(Item, payload.item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    if item.status in ("issued",):
        raise HTTPException(409, "остаток уже выдан")
    dst = db.get(Pvz, payload.to_pvz_id)
    if dst is None:
        raise HTTPException(404, "to_pvz not found")
    if dst.is_hub:
        raise HTTPException(422, "точка выдачи не может быть хабом")
    if not dst.is_active:
        raise HTTPException(409, f"ПВЗ {dst.code} недоступен")
    src = db.get(Pvz, item.stored_pvz_id)
    if src.id == dst.id:
        raise HTTPException(422, "точка выдачи совпадает с местом хранения")

    from ..serializers import reserved_qty

    available = item.qty - reserved_qty(db, item.id)
    if payload.qty > available:
        raise HTTPException(409, f"недостаточно остатка: доступно {available}, запрошено {payload.qty}")
    try:
        storage.ensure_capacity(dst, item.cell_size * payload.qty)
    except storage.CapacityError as exc:
        raise HTTPException(409, str(exc)) from exc

    comparison = routing.compare(db, src.id, dst.id, payload.accept_latency)
    if not comparison["chosen"]:
        raise HTTPException(409, comparison["reason"])

    # Условный UPDATE: остаток проверяется и резервируется одной операцией.
    if not storage.reserve_stock(db, item.id, payload.qty):
        db.expire(item)
        left = item.qty - reserved_qty(db, item.id)
        raise HTTPException(409, f"недостаточно остатка: доступно {left}, запрошено {payload.qty}")

    route = routing.make_route(db, None, comparison, comparison["chosen"])
    order = Order(
        code="TMP",
        item_id=item.id,
        qty=payload.qty,
        from_pvz=src.id,
        to_pvz=dst.id,
        status="routed",
        route_id=route.id,
        created_at=now(db),
        idempotency_key=payload.idempotency_key,
        public_token=secrets.token_hex(6),
    )
    db.add(order)
    db.flush()
    order.code = f"ORD-{order.id:05d}"
    route.order_id = order.id
    quote = money.quote(db, item, payload.qty, route.kind)
    order.buyer_total_rub = quote["buyer_total_rub"]
    order.seller_net_rub = quote["seller_net_rub"]
    if item.status == "stored":
        item.status = "reserved"
    notify(
        db,
        "pvz",
        f"Новый заказ {order.code}: {item.title} → {dst.code}, схема {route.kind}, "
        f"{route.total_cost_rub:.0f} ₽ / {route.total_hours:.0f} ч.",
        order_id=order.id,
        payload={"kind": "order_created", "route_id": route.id, "kind_route": route.kind},
    )
    db.commit()
    return {"order": order_dict(db, order), "route": route_dict(route), "quote": quote, "comparison": comparison}


@router.get("/orders", response_model=list[OrderOut])
def list_orders(
    response: Response,
    status: str | None = None,
    pvz_id: int | None = Query(default=None, description="Заказы, где ПВЗ — источник или получатель"),
    code: str | None = Query(default=None, description="Поиск по коду (без учёта регистра)"),
    limit: int | None = Query(default=None, ge=1, le=500, description="Сколько вернуть; без него — все"),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(Order).order_by(Order.id.desc())
    if status:
        q = q.filter(Order.status == status)
    if pvz_id is not None:
        q = q.filter((Order.from_pvz == pvz_id) | (Order.to_pvz == pvz_id))
    if code:
        q = q.filter(Order.code.ilike(f"%{code.strip()}%"))
    return [order_dict(db, row) for row in page(q, response, limit, offset)]


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db)):
    row = db.get(Order, order_id)
    if row is None:
        raise HTTPException(404, "order not found")
    return order_dict(db, row)


@router.post("/orders/{order_id}/assemble")
def assemble_order(order_id: int, db: Session = Depends(get_db)):
    begin_immediate(db)
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    if order.status in ("assembled", "in_transit", "issued", "returned"):
        return {"order": order_dict(db, order), "idempotent_replay": True}
    _transition(order, "assembled")

    item = db.get(Item, order.item_id)
    src = db.get(Pvz, order.from_pvz)
    dst = db.get(Pvz, order.to_pvz)
    cells = item.cell_size * order.qty
    try:
        storage.occupy(dst, cells)
    except storage.CapacityError as exc:
        raise HTTPException(409, str(exc)) from exc
    storage.release(src, cells)
    # Заказ может быть частью партии: отделяем проданное количество в отдельную строку,
    # иначе остаток «телепортируется» на ПВЗ выдачи и ячейки расходятся с товаром.
    if order.qty < item.qty:
        remainder = Item(
            sku=item.sku,
            seller_id=item.seller_id,
            title=item.title,
            qty=item.qty - order.qty,
            cell_size=item.cell_size,
            stored_pvz_id=src.id,
            accepted_at=item.accepted_at,
            status="stored",
            price_rub=item.price_rub,
            last_accrued_at=item.last_accrued_at,
            relocate_alert_sent=item.relocate_alert_sent,
        )
        db.add(remainder)
        db.flush()
        item.qty = order.qty
    item.stored_pvz_id = dst.id
    item.status = "assembled"
    order.status = "assembled"
    order.assembled_at = now(db)
    notify(
        db,
        "pvz",
        f"Заказ {order.code} собран на {dst.code}: {order.qty} шт., ячейки перенесены.",
        order_id=order.id,
        payload={"kind": "assembled", "pvz_id": dst.id, "cells": cells},
    )
    db.commit()
    return {"order": order_dict(db, order)}


@router.post("/orders/{order_id}/transit")
def transit_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    if order.status in ("in_transit", "issued", "returned"):
        return {"order": order_dict(db, order), "idempotent_replay": True}
    _transition(order, "in_transit")
    order.status = "in_transit"
    route = db.get(Route, order.route_id)
    notify(
        db,
        "pvz",
        f"Заказ {order.code} в пути по схеме {route.kind if route else '—'}; "
        f"ожидаемое время {route.total_hours if route else 0:.0f} ч.",
        order_id=order.id,
        payload={"kind": "in_transit"},
    )
    db.commit()
    return {"order": order_dict(db, order)}


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, db: Session = Depends(get_db)):
    """Отмена до выдачи: освобождает резерв и возвращает остаток на ПВЗ-источник."""
    begin_immediate(db)
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    if order.status == "cancelled":
        return {"order": order_dict(db, order), "idempotent_replay": True}
    if order.status in ("in_transit", "issued", "returned"):
        raise HTTPException(409, f"нельзя отменить заказ в статусе {order.status}")
    _transition(order, "cancelled")
    was_assembled = order.status == "assembled"

    # Захват статуса одной операцией: параллельная выдача не может «отменить»
    # уже выданный заказ, а повторная отмена не двигает ячейки дважды.
    claimed = db.execute(
        update(Order)
        .where(Order.id == order.id, Order.status.in_(("routed", "rerouted", "assembled")))
        .values(status="cancelled")
    )
    if claimed.rowcount != 1:
        db.expire(order)
        if order.status == "cancelled":
            return {"order": order_dict(db, order), "idempotent_replay": True}
        raise HTTPException(409, f"нельзя отменить заказ в статусе {order.status}")

    item = db.get(Item, order.item_id)
    src = db.get(Pvz, order.from_pvz)
    dst = db.get(Pvz, order.to_pvz)
    cells = item.cell_size * order.qty
    if was_assembled:
        # Заказ уже перевезён на пункт выдачи: возвращаем товар и ячейки обратно.
        try:
            storage.occupy(src, cells)
        except storage.CapacityError as exc:
            raise HTTPException(409, str(exc)) from exc
        storage.release(dst, cells)
        item.stored_pvz_id = src.id

    active = (
        db.query(Order)
        .filter(
            Order.item_id == order.item_id,
            Order.id != order.id,
            Order.status.in_(("routed", "rerouted", "assembled", "in_transit")),
        )
        .count()
    )
    item.status = "reserved" if active else "stored"
    order.status = "cancelled"
    order.cancelled_at = now(db)
    notify(
        db,
        "pvz",
        f"Заказ {order.code} отменён. Остаток {order.qty} шт. остаётся на {src.code}, ячейки освобождены.",
        order_id=order.id,
        payload={"kind": "cancelled", "pvz_id": src.id, "cells": cells},
    )
    notify(
        db,
        "seller",
        f"Заказ {order.code} отменён покупателем. Товар {item.title} возвращён в остатки.",
        order_id=order.id,
        payload={"kind": "cancelled"},
    )
    db.commit()
    return {"order": order_dict(db, order)}


@router.post("/orders/{order_id}/issue")
def issue_order(order_id: int, db: Session = Depends(get_db)):
    begin_immediate(db)
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    if order.status == "issued":
        return {"order": order_dict(db, order), "idempotent_replay": True}
    if order.status == "returned":
        raise HTTPException(409, "заказ уже возвращён")
    route = db.get(Route, order.route_id)
    if order.status == "assembled" and route is not None and route.kind != "mesh":
        raise HTTPException(
            409,
            "межгородской заказ нельзя выдать до отправки: сначала POST /orders/{id}/transit",
        )
    _transition(order, "issued")

    # Выдача фиксируется условным UPDATE: из двух параллельных запросов статус
    # сменит ровно один, второй получит rowcount=0 и не спишет остаток повторно.
    claimed = db.execute(
        update(Order)
        .where(Order.id == order.id, Order.status.in_(("assembled", "in_transit")))
        .values(status="issued")
    )
    if claimed.rowcount != 1:
        db.expire(order)
        if order.status == "issued":
            return {"order": order_dict(db, order), "idempotent_replay": True}
        raise HTTPException(409, f"недопустимый переход статуса {order.status} → issued")

    item = db.get(Item, order.item_id)
    route = db.get(Route, order.route_id)
    dst = db.get(Pvz, order.to_pvz)
    cells = item.cell_size * order.qty
    ts = now(db)

    money.post_issue(db, order, item, route)
    # Последняя единица: списание только при достаточном остатке (qty >= n).
    consumed = db.execute(
        update(Item).where(Item.id == item.id, Item.qty >= order.qty).values(qty=Item.qty - order.qty)
    )
    if consumed.rowcount != 1:
        raise HTTPException(409, "остаток уже выдан: списание невозможно")
    db.refresh(item)
    item.status = "issued" if item.qty == 0 else "stored"
    storage.release(dst, cells)
    order.status = "issued"
    order.issued_at = ts
    order.return_deadline = ts + timedelta(days=int(p("fees", "return_window_days", default=7)))
    notify(
        db,
        "pvz",
        f"Заказ {order.code} выдан на {dst.code}. Начислено ПВЗ: {dst.op_fee:.0f} ₽ за операцию"
        + (", бонус mesh 15 ₽." if route.kind == "mesh" else "."),
        order_id=order.id,
        payload={"kind": "issued", "route_kind": route.kind, "cells_released": cells},
    )
    sell = db.get(Item, order.item_id)
    notify(
        db,
        "seller",
        f"Заказ {order.code} выдан. Начисление продавцу {order.seller_net_rub:.0f} ₽ "
        f"(после комиссии), товар {sell.title}.",
        order_id=order.id,
        payload={"kind": "seller_accrual", "net_rub": order.seller_net_rub},
    )
    db.commit()
    return {"order": order_dict(db, order), "ledger": money.ledger_sum(db, order.id)}
