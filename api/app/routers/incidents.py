"""Инциденты логистики и автоматический перевыбор маршрута (UC-4)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import routing
from ..clock import now
from ..db import get_db
from ..models import Edge, Incident, Order, Pvz, Route
from ..notify import notify
from ..schemas import RerouteRequest
from ..serializers import incident_dict, order_dict, route_dict

router = APIRouter(tags=["incidents"])

ACTIVE = ("created", "routed", "assembled", "rerouted", "in_transit")


def _route_has(session: Session, route_id: int | None, edge_id: int) -> bool:
    if not route_id:
        return False
    route = session.get(Route, route_id)
    if route is None:
        return False
    return any(seg.get("edge_id") == edge_id for seg in json.loads(route.segments_json))


def _touch_pvz(session: Session, order: Order, pvz_id: int) -> bool:
    return pvz_id in (order.from_pvz, order.to_pvz)


def _open_incident(session: Session, order: Order, kind: str, description: str, edge_id: int | None = None) -> Incident:
    inc = Incident(
        ts=now(session),
        order_id=order.id,
        type=kind,
        description=description,
        status="open",
        edge_id=edge_id,
    )
    session.add(inc)
    session.flush()
    return inc


def reroute_order(session: Session, order: Order, incident: Incident | None = None, accept_latency: bool = True) -> Route | None:
    """Пересчитать маршрут заказа по активным сегментам. Возвращает новый Route или None."""
    try:
        comparison = routing.compare(session, order.from_pvz, order.to_pvz, accept_latency)
    except ValueError as exc:
        comparison = None
        reason = str(exc)
    else:
        reason = comparison["reason"]
    if not comparison or not comparison["chosen"]:
        if incident is not None:
            incident.status = "open"
            incident.description += f" | перевыбор невозможен: {reason}"
        notify(
            session,
            "pvz",
            f"Заказ {order.code}: маршрут недоступен ({reason}). Требуется вмешательство оператора.",
            order_id=order.id,
            payload={"kind": "no_route", "incident_id": incident.id if incident else None},
        )
        return None

    old_route = session.get(Route, order.route_id)
    new_route = routing.make_route(
        session,
        order.id,
        comparison,
        comparison["chosen"],
        reason=f"Перевыбор после инцидента: {comparison['reason']}",
    )
    order.route_id = new_route.id
    if order.status in ("routed", "rerouted"):
        order.status = "rerouted"
    if incident is not None:
        incident.resolved_route_id = new_route.id
        incident.status = "resolved"
    delta_cost = new_route.total_cost_rub - (old_route.total_cost_rub if old_route else 0.0)
    delta_hours = new_route.total_hours - (old_route.total_hours if old_route else 0.0)
    text = (
        f"Заказ {order.code} переназначен на схему {new_route.kind}: "
        f"{new_route.total_cost_rub:.0f} ₽ / {new_route.total_hours:.0f} ч "
        f"(Δ {delta_cost:+.0f} ₽ / {delta_hours:+.0f} ч)."
    )
    notify(session, "pvz", text, order_id=order.id,
           payload={"kind": "rerouted", "route_id": new_route.id, "delta_cost_rub": round(delta_cost, 2)})
    notify(session, "seller", text, order_id=order.id,
           payload={"kind": "rerouted", "route_id": new_route.id, "delta_hours": round(delta_hours, 2)})
    return new_route


def incidents_for_edge(session: Session, edge_id: int, reason: str) -> list[dict]:
    edge = session.get(Edge, edge_id)
    if edge is None:
        return []
    result = []
    orders = session.query(Order).filter(Order.status.in_(ACTIVE)).order_by(Order.id).all()
    for order in orders:
        if not _route_has(session, order.route_id, edge_id):
            continue
        inc = _open_incident(
            session,
            order,
            "node_down",
            f"Сегмент #{edge_id} {edge.from_pvz}→{edge.to_pvz} недоступен. {reason}",
            edge_id=edge_id,
        )
        new_route = reroute_order(session, order, inc)
        result.append({"incident": incident_dict(inc), "order": order_dict(session, order), "new_route": route_dict(new_route)})
    session.flush()
    return result


def incidents_for_node(session: Session, pvz_id: int, reason: str) -> list[dict]:
    result = []
    orders = session.query(Order).filter(Order.status.in_(ACTIVE)).order_by(Order.id).all()
    for order in orders:
        if not _touch_pvz(session, order, pvz_id):
            continue
        inc = _open_incident(session, order, "node_down", f"Узел {pvz_id} недоступен. {reason}")
        new_route = reroute_order(session, order, inc)
        result.append({"incident": incident_dict(inc), "order": order_dict(session, order), "new_route": route_dict(new_route)})
    session.flush()
    return result


@router.get("/incidents")
def list_incidents(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Incident).order_by(Incident.id.desc())
    if status:
        q = q.filter(Incident.status == status)
    return [incident_dict(row) for row in q.all()]


@router.post("/incidents/{incident_id}/reroute")
def reroute_incident(incident_id: int, payload: RerouteRequest | None = None, db: Session = Depends(get_db)):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    order = db.get(Order, incident.order_id) if incident.order_id else None
    if order is None:
        raise HTTPException(409, "инцидент не связан с заказом")
    if order.status in ("issued", "returned"):
        raise HTTPException(409, f"заказ уже {order.status}, перевыбор не требуется")
    accept = payload.accept_latency if payload else True
    route = reroute_order(db, order, incident, accept)
    if route is None:
        db.commit()
        raise HTTPException(409, "маршрут недоступен: включите сегменты или измените точку выдачи")
    db.commit()
    return {"incident": incident_dict(incident), "order": order_dict(db, order), "route": route_dict(route)}
