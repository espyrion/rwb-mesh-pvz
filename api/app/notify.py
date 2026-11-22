"""Уведомления для UI-ленты (PVZ и продавец)."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from . import clock
from .models import Notification


def notify(
    session: Session,
    audience: str,
    text: str,
    order_id: int | None = None,
    payload: dict | None = None,
) -> Notification:
    row = Notification(
        ts=clock.now(session),
        audience=audience,
        text=text,
        order_id=order_id,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )
    session.add(row)
    session.flush()
    return row
