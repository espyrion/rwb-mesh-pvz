"""Виртуальное время стенда.

Все события прототипа пишутся с временем из этого модуля, а сценарий
«длительное хранение» двигает время вперёд через POST /clock/advance.
Начало — 23.11.2026 08:00 (день демо).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import Clock

START = datetime(2026, 11, 23, 8, 0, 0)


def now(session: Session) -> datetime:
    row = session.get(Clock, 1)
    if row is None:
        row = Clock(id=1, virtual_now=START)
        session.add(row)
        session.flush()
    return row.virtual_now


def set_now(session: Session, value: datetime) -> datetime:
    row = session.get(Clock, 1)
    if row is None:
        row = Clock(id=1, virtual_now=value)
        session.add(row)
    else:
        row.virtual_now = value
    session.flush()
    return row.virtual_now


def advance(session: Session, days: int) -> datetime:
    if days < 0:
        raise ValueError("days must be >= 0")
    return set_now(session, now(session) + timedelta(days=days))
