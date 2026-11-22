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
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
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
    _ensure_schema()
    if not seed_if_empty:
        return
    from .models import Pvz
    from .seed import seed

    with SessionLocal() as session:
        if session.query(Pvz).count() == 0:
            seed(session)


def _ensure_schema() -> None:
    """Лёгкая миграция под уже существующие базы (create_all не добавляет колонки).

    Полноценные миграции — Alembic; здесь только то, без чего старый том не поднимется.
    """
    import secrets

    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql('PRAGMA table_info("order")')}
        if "public_token" not in cols:
            conn.exec_driver_sql('ALTER TABLE "order" ADD COLUMN public_token VARCHAR(24)')
            conn.exec_driver_sql(
                'CREATE UNIQUE INDEX IF NOT EXISTS ix_order_public_token ON "order" (public_token)'
            )
    from .models import Order

    with SessionLocal() as session:
        stale = session.query(Order).filter(Order.public_token.is_(None)).all()
        for row in stale:
            row.public_token = secrets.token_hex(6)
        if stale:
            session.commit()
