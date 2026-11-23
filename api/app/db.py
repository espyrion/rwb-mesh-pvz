"""Сессии БД, инициализация схемы и тестовые данные."""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event, text
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


def begin_immediate(db) -> None:
    """Захватить блокировку записи SQLite в начале операции.

    В SQLite нет `SELECT … FOR UPDATE`: два параллельных запроса могут прочитать
    один и тот же остаток и оба пройти проверку. `BEGIN IMMEDIATE` берёт
    RESERVED-блокировку сразу, поэтому writer'ы выстраиваются в очередь ещё до
    чтения, а не после (в WAL читатели при этом не блокируются).
    """
    if db.get_bind().dialect.name != "sqlite":
        return
    if db.in_transaction():
        return
    db.execute(text("BEGIN IMMEDIATE"))


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

        pvz_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(pvz)")}
        for column, ddl in (
            ("lat", "ALTER TABLE pvz ADD COLUMN lat FLOAT"),
            ("lng", "ALTER TABLE pvz ADD COLUMN lng FLOAT"),
            ("is_hub", "ALTER TABLE pvz ADD COLUMN is_hub BOOLEAN NOT NULL DEFAULT 0"),
            # Инвентаризация остатков: точность/SLA и слепок последней проверки.
            ("audit_accuracy_pct", "ALTER TABLE pvz ADD COLUMN audit_accuracy_pct FLOAT NOT NULL DEFAULT 100.0"),
            ("audit_sla_rating", "ALTER TABLE pvz ADD COLUMN audit_sla_rating VARCHAR(2) NOT NULL DEFAULT 'A'"),
            ("audit_count", "ALTER TABLE pvz ADD COLUMN audit_count INTEGER NOT NULL DEFAULT 0"),
            ("last_audit_at", "ALTER TABLE pvz ADD COLUMN last_audit_at DATETIME"),
            ("last_audit_fingerprint", "ALTER TABLE pvz ADD COLUMN last_audit_fingerprint VARCHAR(64)"),
            ("last_audit_json", "ALTER TABLE pvz ADD COLUMN last_audit_json TEXT"),
        ):
            if column not in pvz_cols:
                conn.exec_driver_sql(ddl)

        item_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(item)")}
        if "parent_batch_id" not in item_cols:
            conn.exec_driver_sql("ALTER TABLE item ADD COLUMN parent_batch_id INTEGER REFERENCES item(id)")
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_item_parent_batch_id ON item (parent_batch_id)"
            )

        route_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(route)")}
        if "reason_code" not in route_cols:
            conn.exec_driver_sql("ALTER TABLE route ADD COLUMN reason_code VARCHAR(32) NOT NULL DEFAULT ''")
        if "overflow" not in route_cols:
            conn.exec_driver_sql("ALTER TABLE route ADD COLUMN overflow BOOLEAN NOT NULL DEFAULT 0")
    from .models import Order

    with SessionLocal() as session:
        stale = session.query(Order).filter(Order.public_token.is_(None)).all()
        for row in stale:
            row.public_token = secrets.token_hex(6)
        if stale:
            session.commit()
