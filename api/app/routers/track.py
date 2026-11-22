"""Публичное отслеживание заказа покупателем по коду (без авторизации).

Покупатель не входит в MVP как отдельное приложение: вместо клиента — страница
отслеживания по коду заказа. Деньги продавца и маркетплейса наружу не отдаются.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Order, Pvz, Route
from ..serializers import iso

router = APIRouter(tags=["track"])

STATUS_LABELS = {
    "created": "Оформлен",
    "routed": "Маршрут построен",
    "rerouted": "Маршрут перестроен",
    "assembled": "Собран в пункте выдачи",
    "in_transit": "В пути",
    "issued": "Выдан",
    "returned": "Возвращён",
    "cancelled": "Отменён",
}

STATUS_HINTS = {
    "routed": "Заказ ждёт сборки на пункте отправления.",
    "rerouted": "Маршрут изменён из-за инцидента, заказ ждёт сборки.",
    "assembled": "Заказ готов к выдаче в пункте назначения.",
    "in_transit": "Заказ в пути между пунктами.",
    "issued": "Заказ получен. Возврат возможен в течение срока, указанного ниже.",
    "returned": "Заказ возвращён продавцу.",
    "cancelled": "Заказ отменён, товар вернулся в остатки.",
}


def _timeline(order: Order, route: Route | None) -> list[dict]:
    events = [{"ts": iso(order.created_at), "status": "created", "label": STATUS_LABELS["created"]}]
    if order.assembled_at:
        events.append({"ts": iso(order.assembled_at), "status": "assembled", "label": STATUS_LABELS["assembled"]})
    if route is not None and order.assembled_at:
        eta = order.assembled_at + timedelta(hours=route.total_hours)
        events.append({"ts": iso(eta), "status": "eta", "label": f"Ожидаемое прибытие, {route.total_hours:.0f} ч"})
    if order.issued_at:
        events.append({"ts": iso(order.issued_at), "status": "issued", "label": STATUS_LABELS["issued"]})
    if order.cancelled_at:
        events.append({"ts": iso(order.cancelled_at), "status": "cancelled", "label": STATUS_LABELS["cancelled"]})
    if order.return_deadline:
        events.append({"ts": iso(order.return_deadline), "status": "deadline", "label": "Крайний срок возврата"})
    return events


@router.get("/track/{code}")
def track_order(code: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.code == code.strip().upper()).first()
    if order is None:
        raise HTTPException(404, "заказ с таким кодом не найден")
    route = db.get(Route, order.route_id) if order.route_id else None
    destination = db.get(Pvz, order.to_pvz)
    source = db.get(Pvz, order.from_pvz)
    return {
        # Публичная карточка: без комиссий, выручки продавца и внутренних id.
        "code": order.code,
        "status": order.status,
        "status_label": STATUS_LABELS.get(order.status, order.status),
        "qty": order.qty,
        "created_at": iso(order.created_at),
        "assembled_at": iso(order.assembled_at),
        "issued_at": iso(order.issued_at),
        "cancelled_at": iso(order.cancelled_at),
        "return_deadline": iso(order.return_deadline),
        "buyer_total_rub": order.buyer_total_rub,
        "from": {
            "code": source.code if source else None,
            "city": source.city if source else None,
        },
        "destination": {
            "code": destination.code if destination else None,
            "name": destination.name if destination else None,
            "city": destination.city if destination else None,
        },
        "hint": STATUS_HINTS.get(order.status, "Статус обновляется по мере обработки заказа."),
        "timeline": _timeline(order, route),
        "route": {"kind": route.kind, "hours": route.total_hours} if route else None,
    }
