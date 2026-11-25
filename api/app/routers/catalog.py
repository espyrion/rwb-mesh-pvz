"""ПВЗ, товары, продавцы, приёмка и перемещение остатков."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from .. import audit, placement, storage
from ..clock import now
from ..db import begin_immediate, get_db
from ..models import Item, Pvz, Seller
from ..notify import notify
from ..paging import page
from ..responses import (
    AuditOut,
    ItemOut,
    LedgerRowOut,
    PlacementScoreOut,
    PvzOut,
    SellerOut,
)
from ..schemas import AuditRequest, ItemCreate, PlacementScoreRequest, RelocateRequest
from ..serializers import item_dict, pvz_dict, seller_dict

router = APIRouter(tags=["catalog"])


@router.get("/pvz", response_model=list[PvzOut])
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


@router.get("/pvz/{pvz_id}", response_model=PvzOut)
def get_pvz(pvz_id: int, db: Session = Depends(get_db)):
    row = db.get(Pvz, pvz_id)
    if row is None:
        raise HTTPException(404, "pvz not found")
    return pvz_dict(row)


@router.post("/pvz/{pvz_id}/disable")
def disable_pvz(pvz_id: int, db: Session = Depends(get_db)):
    """Вывести точку из строя: блокировка, эвакуация остатков, переадресация заказов."""
    begin_immediate(db)
    row = db.get(Pvz, pvz_id)
    if row is None:
        raise HTTPException(404, "pvz not found")
    from ..node_failure import node_down

    try:
        result = node_down(db, row, reason=f"ПВЗ {row.code} помечен недоступным оператором")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return result


@router.post("/pvz/{pvz_id}/enable")
def enable_pvz(pvz_id: int, db: Session = Depends(get_db)):
    row = db.get(Pvz, pvz_id)
    if row is None:
        raise HTTPException(404, "pvz not found")
    from ..node_failure import node_up

    result = node_up(db, row)
    db.commit()
    return result


@router.post("/pvz/{pvz_id}/audit", response_model=AuditOut)
def audit_pvz(pvz_id: int, payload: AuditRequest, db: Session = Depends(get_db)):
    """Инвентаризация остатков ПВЗ: сверка факта, корректировка, инцидент, удержание.

    Роль pvz/admin. Повтор с теми же фактическими данными идемпотентен:
    возвращает сохранённый результат (idempotent=true) без новых инцидентов и проводок.
    """
    begin_immediate(db)
    pvz = db.get(Pvz, pvz_id)
    if pvz is None:
        raise HTTPException(404, "pvz not found")
    try:
        result = audit.run_audit(db, pvz, payload.actual, payload.note)
    except audit.AuditError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.detail) from exc
    db.commit()
    return result


@router.post("/catalog/placement-score", response_model=PlacementScoreOut)
def placement_score(payload: PlacementScoreRequest, db: Session = Depends(get_db)):
    """Скоринг размещения SKU: держать партию в ПВЗ города (mesh_pvz) или на хабе.

    Роль seller/admin. Ответ объясним: score, вклад каждого критерия и причины
    на русском с числами и именами параметров economics/params.yaml.
    """
    pvz = None
    if payload.pvz_id is not None:
        pvz = db.get(Pvz, payload.pvz_id)
        if pvz is None:
            raise HTTPException(404, "pvz not found")
        if pvz.is_hub:
            raise HTTPException(422, "хаб — заглушка магистрали: скоринг считается для ПВЗ")

    if payload.demand_per_day is not None:
        demand = float(payload.demand_per_day)
        source, window = "operator", None
    else:
        demand, source, window = placement.observed_demand(
            db, payload.sku, payload.city or (pvz.city if pvz else None)
        )

    try:
        return placement.score_placement(
            sku=payload.sku,
            qty=payload.qty,
            cell_size=payload.cell_size,
            demand_per_day=demand,
            turnover_days=payload.turnover_days,
            free_cells=(pvz.capacity_cells - pvz.used_cells) if pvz is not None else None,
            pvz_code=pvz.code if pvz is not None else None,
            demand_source=source,
            demand_window_days=window,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/sellers", response_model=list[SellerOut])
def list_sellers(db: Session = Depends(get_db)):
    return [seller_dict(s) for s in db.query(Seller).order_by(Seller.id).all()]


@router.get("/sellers/{seller_id}", response_model=SellerOut)
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
    # Авторекомендация размещения — только при включённом флаге pvz.placement_auto_recommend.
    # Считается до занятия ячеек: свободные ячейки в причине должны включать эту партию.
    recommendation = placement.acceptance_recommendation(db, item, pvz)
    storage.occupy(pvz, cells)
    notify(
        db,
        "pvz",
        f"Принята партия {item.sku} «{item.title}»: {item.qty} шт., {cells} яч. на {pvz.code}.",
        payload={"kind": "acceptance", "item_id": item.id, "pvz_id": pvz.id, "cells": cells},
    )
    db.commit()
    data = item_dict(db, item)
    if recommendation is not None:
        # Аддитивный блок: при выключенном флаге ответ приёмки не меняется.
        data["placement"] = recommendation
    return data


@router.get("/items", response_model=list[ItemOut])
def list_items(
    response: Response,
    pvz_id: int | None = None,
    status: str | None = None,
    seller_id: int | None = None,
    limit: int | None = Query(default=None, ge=1, le=500, description="Сколько вернуть; без него — все"),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(Item).order_by(Item.id)
    if pvz_id is not None:
        q = q.filter(Item.stored_pvz_id == pvz_id)
    if status:
        q = q.filter(Item.status == status)
    if seller_id is not None:
        q = q.filter(Item.seller_id == seller_id)
    return [item_dict(db, row) for row in page(q, response, limit, offset)]


@router.get("/items/{item_id}", response_model=ItemOut)
def get_item(item_id: int, db: Session = Depends(get_db)):
    row = db.get(Item, item_id)
    if row is None:
        raise HTTPException(404, "item not found")
    return item_dict(db, row)


@router.post("/items/{item_id}/relocate")
def relocate_item(item_id: int, payload: RelocateRequest, db: Session = Depends(get_db)):
    # Блокировка записи: иначе параллельные relocate дублируют проводки и двоят занятые ячейки.
    begin_immediate(db)
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


@router.get("/ledger", response_model=list[LedgerRowOut])
def list_ledger(
    response: Response,
    order_id: int | None = None,
    viewer: str | None = Query(default=None, description="pvz | seller | marketplace | buyer"),
    party_id: int | None = None,
    kind: str | None = Query(default=None, description="Вид начисления: delivery, storage, commission…"),
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
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
    if kind:
        q = q.filter(Ledger.kind == kind)
    return [ledger_dict(row) for row in page(q, response, limit, offset)]
