"""Виртуальные часы и пересчёт хранения (UC-5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import clock, storage
from ..db import get_db
from ..responses import ClockOut, StorageAccrualOut

router = APIRouter(tags=["clock"])


@router.get("/clock", response_model=ClockOut)
def get_clock(db: Session = Depends(get_db)):
    return {"virtual_now": clock.now(db).isoformat(), "timezone": "Europe/Moscow (virtual)"}


@router.post("/clock/advance", response_model=StorageAccrualOut)
def advance_clock(
    days: int = Query(default=1, ge=0, le=365, description="Сдвиг виртуального времени, дней"),
    db: Session = Depends(get_db),
):
    clock.advance(db, days)
    summary = storage.accrue_storage(db, days) if days else {
        "days": 0,
        "charged_total_rub": 0.0,
        "by_pvz": {},
        "alerts": 0,
        "virtual_now": clock.now(db).isoformat(),
    }
    db.commit()
    return summary


@router.post("/clock/reset", response_model=ClockOut)
def reset_clock(db: Session = Depends(get_db)):
    """Вернуть виртуальные часы на START (день демо) — повторный прогон сценария."""
    clock.set_now(db, clock.START)
    db.commit()
    return {"virtual_now": clock.now(db).isoformat(), "timezone": "Europe/Moscow (virtual)"}
