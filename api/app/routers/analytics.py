"""Аналитика ПВЗ и продавца по данным ledger + сводка для жюри."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import clock
from ..config import p
from ..db import get_db
from ..responses import PvzAnalyticsOut, SellerAnalyticsOut, SummaryOut
from ..models import Incident, Item, Ledger, Order, Pvz, Route, Seller
from ..serializers import pvz_dict, seller_dict
from ..storage import utilization

router = APIRouter(tags=["analytics"])


def _period(q, column, date_from: str | None, date_to: str | None):
    if date_from:
        q = q.filter(column >= datetime.fromisoformat(date_from))
    if date_to:
        dt = datetime.fromisoformat(date_to)
        if len(date_to.strip()) <= 10:
            dt = dt + timedelta(days=1)
        q = q.filter(column < dt)
    return q


@router.get("/analytics/pvz", response_model=PvzAnalyticsOut)
def analytics_pvz(
    pvz_id: int,
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
):
    pvz = db.get(Pvz, pvz_id)
    if pvz is None:
        raise HTTPException(404, "pvz not found")
    q = db.query(Ledger).filter(Ledger.party == "pvz", Ledger.party_id == pvz_id)
    rows = _period(q, Ledger.ts, date_from, date_to).order_by(Ledger.id).all()

    revenue: dict[str, float] = defaultdict(float)
    deductions: dict[str, float] = defaultdict(float)
    for row in rows:
        if row.amount_rub >= 0:
            revenue[row.kind] += row.amount_rub
        else:
            deductions[row.kind] += -row.amount_rub

    net = round(sum(row.amount_rub for row in rows), 2)
    storage_rub = round(revenue.get("storage", 0.0), 2)
    rate = float(pvz.storage_rate_day) or 1.0
    cell_days = round(storage_rub / rate, 1)

    oq = db.query(Order).filter(Order.to_pvz == pvz_id, Order.issued_at.is_not(None))
    issued_orders = _period(oq, Order.issued_at, date_from, date_to).all()
    # Сборка — операция ПВЗ-источника: заказ собирают там, где лежит остаток.
    aq = db.query(Order).filter(Order.from_pvz == pvz_id, Order.assembled_at.is_not(None))
    assembled_here = _period(aq, Order.assembled_at, date_from, date_to).all()
    accepted_units = sum(o.qty for o in assembled_here)

    assemble_cost = float(p("logistics", "assemble_cost_rub", default=12))
    acceptance_cost = float(p("logistics", "acceptance_cost_rub", default=8))
    op_costs = round(
        len(assembled_here) * assemble_cost + accepted_units * acceptance_cost,
        2,
    )

    # Постоянные затраты ПВЗ (аренда/ФОТ) не видны в ledger, но без них прибыль фиктивна.
    now_ts = clock.now(db)
    start_ts = datetime.fromisoformat(date_from) if date_from else clock.START
    end_ts = (datetime.fromisoformat(date_to) + timedelta(days=1)) if date_to else now_ts
    period_days = max(1, (end_ts - start_ts).days)
    fixed_cost = round(float(p("pvz", "fixed_cost_day_rub", default=1200)) * period_days, 2)
    return {
        "pvz": pvz_dict(pvz),
        "period": {"from": date_from, "to": date_to, "days": period_days},
        "revenue": {k: round(v, 2) for k, v in sorted(revenue.items())},
        "revenue_total_rub": round(sum(revenue.values()), 2),
        "deductions": {k: round(v, 2) for k, v in sorted(deductions.items())},
        "deductions_total_rub": round(sum(deductions.values()), 2),
        "ledger_net_rub": net,
        "op_costs_modeled_rub": op_costs,
        "fixed_costs_modeled_rub": fixed_cost,
        "profit_rub": round(net - op_costs - fixed_cost, 2),
        "storage_cell_days": cell_days,
        "orders_issued": len(issued_orders),
        "accepted_units": accepted_units,
        "utilization_pct": utilization(pvz),
        "ledger": [
            {"id": r.id, "ts": r.ts.isoformat(), "order_id": r.order_id, "kind": r.kind,
             "amount_rub": r.amount_rub, "note": r.note}
            for r in rows
        ],
    }


@router.get("/analytics/seller", response_model=SellerAnalyticsOut)
def analytics_seller(
    seller_id: int,
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
):
    seller = db.get(Seller, seller_id)
    if seller is None:
        raise HTTPException(404, "seller not found")
    q = db.query(Ledger).filter(Ledger.party == "seller", Ledger.party_id == seller_id)
    rows = _period(q, Ledger.ts, date_from, date_to).order_by(Ledger.id).all()

    goods = round(sum(r.amount_rub for r in rows if r.kind == "goods" and r.amount_rub > 0), 2)
    commission = round(-sum(r.amount_rub for r in rows if r.kind == "commission"), 2)
    refunds = round(sum(r.amount_rub for r in rows if r.kind == "refund"), 2)
    net = round(sum(r.amount_rub for r in rows), 2)

    per_order: dict[int, dict] = {}
    order_ids = sorted({r.order_id for r in rows if r.order_id})
    for oid in order_ids:
        order = db.get(Order, oid)
        item = db.get(Item, order.item_id) if order else None
        o_rows = [r for r in rows if r.order_id == oid]
        o_goods = round(sum(r.amount_rub for r in o_rows if r.kind == "goods" and r.amount_rub > 0), 2)
        o_comm = round(-sum(r.amount_rub for r in o_rows if r.kind == "commission"), 2)
        per_order[oid] = {
            "order_id": oid,
            "code": order.code if order else None,
            "title": item.title if item else None,
            "status": order.status if order else None,
            "goods_rub": o_goods,
            "commission_rub": o_comm,
            "refund_rub": round(sum(r.amount_rub for r in o_rows if r.kind == "refund"), 2),
            "net_rub": round(sum(r.amount_rub for r in o_rows), 2),
            "delivery_fee_rub": None,
        }
    return {
        "seller": seller_dict(seller),
        "period": {"from": date_from, "to": date_to},
        "revenue": {"goods_rub": goods},
        "costs": {"commission_rub": commission, "refunds_rub": refunds},
        "profit_rub": net,
        "orders": list(per_order.values()),
        "ledger": [
            {"id": r.id, "ts": r.ts.isoformat(), "order_id": r.order_id, "kind": r.kind,
             "amount_rub": r.amount_rub, "note": r.note}
            for r in rows
        ],
    }


@router.get("/analytics/summary", response_model=SummaryOut)
def analytics_summary(db: Session = Depends(get_db)):
    rows = db.query(Ledger).all()
    by_party: dict[str, float] = defaultdict(float)
    by_party_kind: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        by_party[row.party] += row.amount_rub
        by_party_kind[row.party][row.kind] += row.amount_rub
    orders = db.query(Order).all()
    by_status: dict[str, int] = defaultdict(int)
    for o in orders:
        by_status[o.status] += 1
    mesh_orders = sum(1 for o in orders if o.status in ("issued", "returned") and o.route_id)
    routes = db.query(Route).all()
    route_kinds: dict[str, int] = defaultdict(int)
    for r in routes:
        route_kinds[r.kind or "unknown"] += 1
    mesh_share = round(route_kinds.get("mesh", 0) / len(routes), 3) if routes else 0.0
    logistics_cost = round(
        -sum(r.amount_rub for r in rows if r.party == "marketplace" and r.kind == "delivery" and r.amount_rub < 0),
        2,
    )
    return {
        "orders": {
            "total": len(orders),
            "by_status": dict(sorted(by_status.items())),
            "mesh_or_hub": mesh_orders,
        },
        "ledger": {party: round(value, 2) for party, value in sorted(by_party.items())},
        "routes": {"by_kind": dict(sorted(route_kinds.items())), "mesh_share": mesh_share},
        "ledger_by_kind": {party: {k: round(v, 2) for k, v in sorted(kinds.items())}
                            for party, kinds in sorted(by_party_kind.items())},
        "logistics_cost_rub": logistics_cost,
        "storage_charged_rub": round(by_party_kind.get("pvz", {}).get("storage", 0.0), 2),
        "incidents_open": db.query(Incident).filter(Incident.status == "open").count(),
    }
