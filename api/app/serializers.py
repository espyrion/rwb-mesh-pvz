"""Преобразование ORM-объектов в JSON-представления API."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from .models import Edge, Incident, Item, Ledger, Notification, Order, Pvz, Route, Seller
from .storage import cells_for

ACTIVE_ORDER_STATUSES = ("created", "routed", "assembled", "rerouted", "in_transit")


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def pvz_dict(pvz: Pvz) -> dict:
    return {
        "id": pvz.id,
        "code": pvz.code,
        "name": pvz.name,
        "city": pvz.city,
        "capacity_cells": pvz.capacity_cells,
        "used_cells": pvz.used_cells,
        "free_cells": max(0, pvz.capacity_cells - pvz.used_cells),
        "utilization_pct": round(pvz.used_cells / pvz.capacity_cells * 100.0, 1) if pvz.capacity_cells else 0.0,
        "is_active": pvz.is_active,
        "is_hub": pvz.is_hub,
        "storage_rate_day": pvz.storage_rate_day,
        "op_fee": pvz.op_fee,
    }


def reserved_qty(session: Session, item_id: int) -> int:
    rows = (
        session.query(Order)
        .filter(Order.item_id == item_id, Order.status.in_(ACTIVE_ORDER_STATUSES))
        .all()
    )
    return sum(o.qty for o in rows)


def item_dict(session: Session, item: Item) -> dict:
    pvz = session.get(Pvz, item.stored_pvz_id)
    reserved = reserved_qty(session, item.id)
    return {
        "id": item.id,
        "sku": item.sku,
        "seller_id": item.seller_id,
        "title": item.title,
        "qty": item.qty,
        "reserved_qty": reserved,
        "available_qty": max(0, item.qty - reserved),
        "cell_size": item.cell_size,
        "cells": cells_for(item) if item.qty else item.cell_size,
        "stored_pvz_id": item.stored_pvz_id,
        "stored_pvz_code": pvz.code if pvz else None,
        "accepted_at": iso(item.accepted_at),
        "status": item.status,
        "price_rub": item.price_rub,
    }


def route_dict(route: Route | None) -> dict | None:
    if route is None:
        return None
    return {
        "id": route.id,
        "order_id": route.order_id,
        "kind": route.kind,
        "segments": json.loads(route.segments_json),
        "total_cost_rub": route.total_cost_rub,
        "total_hours": route.total_hours,
        "reason": route.reason,
        "created_at": iso(route.created_at),
    }


def order_dict(session: Session, order: Order) -> dict:
    item = session.get(Item, order.item_id)
    src = session.get(Pvz, order.from_pvz)
    dst = session.get(Pvz, order.to_pvz)
    route = session.get(Route, order.route_id) if order.route_id else None
    return {
        "id": order.id,
        "code": order.code,
        "item_id": order.item_id,
        "sku": item.sku if item else None,
        "title": item.title if item else None,
        "qty": order.qty,
        "from_pvz": order.from_pvz,
        "from_code": src.code if src else None,
        "to_pvz": order.to_pvz,
        "to_code": dst.code if dst else None,
        "status": order.status,
        "route_id": order.route_id,
        "route": route_dict(route),
        "created_at": iso(order.created_at),
        "assembled_at": iso(order.assembled_at),
        "issued_at": iso(order.issued_at),
        "cancelled_at": iso(order.cancelled_at),
        "return_deadline": iso(order.return_deadline),
        "buyer_total_rub": order.buyer_total_rub,
        "seller_net_rub": order.seller_net_rub,
        "return_route_id": order.return_route_id,
        "public_token": order.public_token,
    }


def edge_dict(edge: Edge, pvz: dict[int, Pvz]) -> dict:
    return {
        "id": edge.id,
        "from_pvz": edge.from_pvz,
        "to_pvz": edge.to_pvz,
        "from_code": pvz[edge.from_pvz].code if edge.from_pvz in pvz else None,
        "to_code": pvz[edge.to_pvz].code if edge.to_pvz in pvz else None,
        "kind": edge.kind,
        "cost_rub": edge.cost_rub,
        "hours": edge.hours,
        "is_active": edge.is_active,
    }


def ledger_dict(row: Ledger) -> dict:
    return {
        "id": row.id,
        "ts": iso(row.ts),
        "order_id": row.order_id,
        "item_id": row.item_id,
        "party": row.party,
        "party_id": row.party_id,
        "amount_rub": row.amount_rub,
        "kind": row.kind,
        "note": row.note,
    }


def incident_dict(row: Incident) -> dict:
    return {
        "id": row.id,
        "ts": iso(row.ts),
        "order_id": row.order_id,
        "type": row.type,
        "description": row.description,
        "status": row.status,
        "resolved_route_id": row.resolved_route_id,
        "edge_id": row.edge_id,
    }


def notification_dict(row: Notification) -> dict:
    return {
        "id": row.id,
        "ts": iso(row.ts),
        "audience": row.audience,
        "text": row.text,
        "order_id": row.order_id,
        "payload": json.loads(row.payload_json or "{}"),
    }


def seller_dict(seller: Seller) -> dict:
    return {"id": seller.id, "name": seller.name, "commission_pct": seller.commission_pct}
