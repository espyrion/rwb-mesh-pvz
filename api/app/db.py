"""Сессии БД, инициализация схемы и тестовые данные."""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mesh.db")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # pragma: no cover - infra
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(seed_if_empty: bool = True) -> None:
    Base.metadata.create_all(engine)
    if not seed_if_empty:
        return
    from .models import Pvz
    from .seed import seed

    with SessionLocal() as session:
        if session.query(Pvz).count() == 0:
            seed(session)
