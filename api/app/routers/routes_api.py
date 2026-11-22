"""Маршруты и сегменты: сравнение схем, отключение/включение рёбер."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import routing
from ..db import get_db
from ..models import Edge, Pvz, Route
from ..notify import notify
from ..serializers import edge_dict, route_dict

router = APIRouter(tags=["routing"])


@router.get("/routes/compare")
def compare_routes(
    from_pvz: int = Query(alias="from"),
    to_pvz: int = Query(alias="to"),
    accept_latency: bool = True,
    db: Session = Depends(get_db),
):
    try:
        return routing.compare(db, from_pvz, to_pvz, accept_latency)
    except LookupError as exc:
        raise HTTPException(404, "pvz not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/edges")
def list_edges(kind: str | None = None, active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(Edge).order_by(Edge.id)
    if kind:
        q = q.filter(Edge.kind == kind)
    if active_only:
        q = q.filter(Edge.is_active.is_(True))
    pvz = {row.id: row for row in db.query(Pvz).all()}
    return [edge_dict(row, pvz) for row in q.all()]


@router.get("/edges/{edge_id}")
def get_edge(edge_id: int, db: Session = Depends(get_db)):
    row = db.get(Edge, edge_id)
    if row is None:
        raise HTTPException(404, "edge not found")
    pvz = {p.id: p for p in db.query(Pvz).all()}
    return edge_dict(row, pvz)


@router.post("/edges/{edge_id}/disable")
def disable_edge(edge_id: int, db: Session = Depends(get_db)):
    edge = db.get(Edge, edge_id)
    if edge is None:
        raise HTTPException(404, "edge not found")
    if not edge.is_active:
        return {"edge": edge_dict(edge, {p.id: p for p in db.query(Pvz).all()}), "incidents": []}
    edge.is_active = False
    from .incidents import incidents_for_edge

    incidents = incidents_for_edge(db, edge.id, "Сегмент отключён оператором (имитация сбоя).")
    notify(
        db,
        "pvz",
        f"Инцидент: сегмент {edge.from_pvz}→{edge.to_pvz} отключён. Затронуто заказов: {len(incidents)}.",
        payload={"kind": "edge_disabled", "edge_id": edge.id, "affected": len(incidents)},
    )
    db.commit()
    return {
        "edge": edge_dict(edge, {p.id: p for p in db.query(Pvz).all()}),
        "incidents": incidents,
    }


@router.post("/edges/{edge_id}/enable")
def enable_edge(edge_id: int, db: Session = Depends(get_db)):
    edge = db.get(Edge, edge_id)
    if edge is None:
        raise HTTPException(404, "edge not found")
    edge.is_active = True
    notify(
        db,
        "pvz",
        f"Сегмент {edge.from_pvz}→{edge.to_pvz} снова доступен.",
        payload={"kind": "edge_enabled", "edge_id": edge.id},
    )
    db.commit()
    return {"edge": edge_dict(edge, {p.id: p for p in db.query(Pvz).all()})}


@router.get("/routes")
def list_routes(order_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(Route).order_by(Route.id.desc())
    if order_id is not None:
        q = q.filter(Route.order_id == order_id)
    return [route_dict(row) for row in q.all()]


@router.get("/routes/{route_id}")
def get_route(route_id: int, db: Session = Depends(get_db)):
    row = db.get(Route, route_id)
    if row is None:
        raise HTTPException(404, "route not found")
    return route_dict(row)
