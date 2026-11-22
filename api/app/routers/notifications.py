"""Лента уведомлений для UI (PVZ и продавец)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Notification
from ..serializers import notification_dict

router = APIRouter(tags=["notifications"])


@router.get("/notifications")
def list_notifications(
    audience: str | None = Query(default=None, description="pvz | seller"),
    order_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(Notification).order_by(Notification.id.desc())
    if audience:
        q = q.filter(Notification.audience == audience)
    if order_id is not None:
        q = q.filter(Notification.order_id == order_id)
    return [notification_dict(row) for row in q.limit(limit).all()]
