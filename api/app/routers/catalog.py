"""ПВЗ, товары, продавцы, приёмка и перемещение остатков."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import storage
from ..clock import now
from ..db import get_db
from ..models import Item, Pvz, Seller
from ..notify import notify
from ..schemas import ItemCreate, RelocateRequest
from ..serializers import item_dict, pvz_dict, seller_dict

router = APIRouter(tags=["catalog"])


@router.get("/pvz")
def list_pvz(
    city: str | None = None,
    include_hub: bool = True,
    db: Session = Depends(get_db),
):
    q = db.query(Pvz).order_by(Pvz.id)
    if city:
        q = q.filter(Pvz.city == city)
    if not include_hub:
        q = q.filter(Pvz.is_hub.is_(False))
    return [pvz_dict(row) for row in q.all()]


@router.get("/pvz/{pvz_id}")
def get_pvz(pvz_id: int, db: Session = Depends(get_db)):
    row = db.get(Pvz, pvz_id)
    if row is None:
        raise HTTPException(404, "pvz not found")
    return pvz_dict(row)


@router.post("/pvz/{pvz_id}/disable")
def disable_pvz(pvz_id: int, db: Session = Depends(get_db)):
    row = db.get(Pvz, pvz_id)
    if row is None:
        raise HTTPException(404, "pvz not found")
    row.is_active = False
    from .incidents import incidents_for_node

    incidents = incidents_for_node(db, pvz_id, reason=f"ПВЗ {row.code} помечен недоступным оператором")
    return {"pvz": pvz_dict(row), "incidents": incidents}


@router.post("/pvz/{pvz_id}/enable")
def enable_pvz(pvz_id: int, db: Session = Depends(get_db)):
    row = db.get(Pvz, pvz_id)
    if row is None:
        raise HTTPException(404, "pvz not found")
    row.is_active = True
    return {"pvz": pvz_dict(row)}


@router.get("/sellers")
def list_sellers(db: Session = Depends(get_db)):
    return [seller_dict(s) for s in db.query(Seller).order_by(Seller.id).all()]


@router.get("/sellers/{seller_id}")
def get_seller(seller_id: int, db: Session = Depends(get_db)):
    row = db.get(Seller, seller_id)
    if row is None:
        raise HTTPException(404, "seller not found")
    return seller_dict(row)


@router.post("/items", status_code=201)
def create_item(payload: ItemCreate, db: Session = Depends(get_db)):
    seller = db.get(Seller, payload.seller_id)
    if seller is None:
        raise HTTPException(404, "seller not found")
    pvz = db.get(Pvz, payload.pvz_id)
    if pvz is None:
        raise HTTPException(404, "pvz not found")
    if pvz.is_hub:
        raise HTTPException(422, "приёмка на хаб не выполняется: хаб — заглушка магистрали")
    if not pvz.is_active:
        raise HTTPException(409, f"ПВЗ {pvz.code} недоступен")
    cells = payload.cell_size * payload.qty
    try:
        storage.ensure_capacity(pvz, cells)
    except storage.CapacityError as exc:
        raise HTTPException(409, str(exc)) from exc

    ts = now(db)
    item = Item(
        sku=payload.sku,
        seller_id=payload.seller_id,
        title=payload.title,
        qty=payload.qty,
        cell_size=payload.cell_size,
        stored_pvz_id=pvz.id,
        accepted_at=ts,
        last_accrued_at=ts,
        status="stored",
        price_rub=payload.price_rub,
    )
    db.add(item)
    db.flush()
    storage.occupy(pvz, cells)
    notify(
        db,
        "pvz",
        f"Принята партия {item.sku} «{item.title}»: {item.qty} шт., {cells} яч. на {pvz.code}.",
        payload={"kind": "acceptance", "item_id": item.id, "pvz_id": pvz.id, "cells": cells},
    )
    db.commit()
    return item_dict(db, item)


@router.get("/items")
def list_items(
    pvz_id: int | None = None,
    status: str | None = None,
    seller_id: int | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Item).order_by(Item.id)
    if pvz_id is not None:
        q = q.filter(Item.stored_pvz_id == pvz_id)
    if status:
        q = q.filter(Item.status == status)
    if seller_id is not None:
        q = q.filter(Item.seller_id == seller_id)
    return [item_dict(db, row) for row in q.all()]


@router.get("/items/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)):
    row = db.get(Item, item_id)
    if row is None:
        raise HTTPException(404, "item not found")
    return item_dict(db, row)


@router.post("/items/{item_id}/relocate")
def relocate_item(item_id: int, payload: RelocateRequest, db: Session = Depends(get_db)):
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    try:
        result = storage.relocate(db, item, payload.to_pvz_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except storage.CapacityError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return {"relocation": result, "item": item_dict(db, item)}


@router.get("/ledger")
def list_ledger(
    order_id: int | None = None,
    viewer: str | None = Query(default=None, description="pvz | seller | marketplace | buyer"),
    party_id: int | None = None,
    db: Session = Depends(get_db),
):
    from ..models import Ledger
    from ..serializers import ledger_dict

    q = db.query(Ledger).order_by(Ledger.id)
    if order_id is not None:
        q = q.filter(Ledger.order_id == order_id)
    if viewer:
        q = q.filter(Ledger.party == viewer)
    if party_id is not None:
        q = q.filter(Ledger.party_id == party_id)
    return [ledger_dict(row) for row in q.all()]
