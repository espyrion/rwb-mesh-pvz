"""SQLAlchemy-модели mesh-PVZ (контракт раздела 3.5 ТЗ).

Денежные величины — в рублях (float, точность демо-стенда).
Время — виртуальное (см. app/clock.py), хранится как naive datetime.
Все тарифы приходят из economics/params.yaml (параметры модели).
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

ITEM_STATUSES = ("stored", "reserved", "assembled", "issued", "returned")

ORDER_STATUSES = (
    "created",
    "routed",
    "assembled",
    "in_transit",
    "issued",
    "returned",
    "rerouted",
)

# Разрешённый граф переходов заказа. Проверяется тестами и API.
ORDER_TRANSITIONS = {
    "created": {"routed"},
    "routed": {"assembled", "rerouted", "cancelled"},
    "rerouted": {"assembled", "rerouted", "cancelled"},
    "assembled": {"in_transit", "issued", "cancelled"},
    "in_transit": {"issued"},
    "issued": {"returned"},
    "returned": set(),
    "cancelled": set(),
}

ROUTE_KINDS = ("mesh", "hub")
LEDGER_PARTIES = ("pvz", "marketplace", "seller", "buyer")
LEDGER_KINDS = (
    "goods",
    "storage",
    "pickup_fee",
    "delivery",
    "commission",
    "refund",
    "penalty",
    "bonus",
    "ops",
    "relocation",
)


class Pvz(Base):
    """Пункт выдачи заказов — узел сети и место хранения."""

    __tablename__ = "pvz"
    __table_args__ = (
        CheckConstraint("used_cells >= 0", name="ck_pvz_used_nonneg"),
        CheckConstraint("used_cells <= capacity_cells", name="ck_pvz_capacity"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String(16), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    city = Column(String(64), nullable=False, index=True)
    capacity_cells = Column(Integer, nullable=False)
    used_cells = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    is_hub = Column(Boolean, nullable=False, default=False)  # хаб — узел графа, не точка выдачи
    storage_rate_day = Column(Float, nullable=False)
    op_fee = Column(Float, nullable=False)


class Edge(Base):
    """Ребро графа: локальный сегмент ПВЗ↔ПВЗ или магистраль через хаб."""

    __tablename__ = "edge"

    id = Column(Integer, primary_key=True)
    from_pvz = Column(Integer, ForeignKey("pvz.id"), nullable=False, index=True)
    to_pvz = Column(Integer, ForeignKey("pvz.id"), nullable=False, index=True)
    kind = Column(String(8), nullable=False)  # local | trunk
    cost_rub = Column(Float, nullable=False)
    hours = Column(Float, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)


class Seller(Base):
    __tablename__ = "seller"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    commission_pct = Column(Float, nullable=False)


class Item(Base):
    """Товарная партия на ПВЗ. cell_size — число ячеек на единицу товара."""

    __tablename__ = "item"
    __table_args__ = (
        CheckConstraint("qty >= 0", name="ck_item_qty_non_negative"),
        CheckConstraint("cell_size > 0", name="ck_item_cell_positive"),
    )

    id = Column(Integer, primary_key=True)
    sku = Column(String(32), nullable=False, index=True)
    seller_id = Column(Integer, ForeignKey("seller.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    qty = Column(Integer, nullable=False)
    cell_size = Column(Integer, nullable=False)
    stored_pvz_id = Column(Integer, ForeignKey("pvz.id"), nullable=False, index=True)
    accepted_at = Column(DateTime, nullable=False)
    status = Column(String(16), nullable=False, default="stored", index=True)
    price_rub = Column(Float, nullable=False, default=0.0)
    last_accrued_at = Column(DateTime, nullable=True)
    relocate_alert_sent = Column(Boolean, nullable=False, default=False)


class Route(Base):
    __tablename__ = "route"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("order.id"), nullable=True, index=True)
    kind = Column(String(8), nullable=False)  # mesh | hub
    segments_json = Column(Text, nullable=False)  # JSON list of segments
    total_cost_rub = Column(Float, nullable=False)
    total_hours = Column(Float, nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)


class Order(Base):
    __tablename__ = "order"

    id = Column(Integer, primary_key=True)
    code = Column(String(24), unique=True, nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("item.id"), nullable=False, index=True)
    qty = Column(Integer, nullable=False)
    from_pvz = Column(Integer, ForeignKey("pvz.id"), nullable=False, index=True)
    to_pvz = Column(Integer, ForeignKey("pvz.id"), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="created", index=True)
    route_id = Column(Integer, ForeignKey("route.id"), nullable=True)
    created_at = Column(DateTime, nullable=False)
    assembled_at = Column(DateTime, nullable=True)
    issued_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    return_deadline = Column(DateTime, nullable=True)
    buyer_total_rub = Column(Float, nullable=False, default=0.0)
    seller_net_rub = Column(Float, nullable=False, default=0.0)
    return_route_id = Column(Integer, ForeignKey("route.id"), nullable=True)
    idempotency_key = Column(String(64), unique=True, nullable=True, index=True)
    # Публичный токен для ссылки покупателя: код заказа последовательный и легко перебирается.
    public_token = Column(String(24), unique=True, nullable=True, index=True)


class Ledger(Base):
    __tablename__ = "ledger"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("order.id"), nullable=True, index=True)
    item_id = Column(Integer, ForeignKey("item.id"), nullable=True, index=True)
    party = Column(String(12), nullable=False, index=True)  # pvz|marketplace|seller|buyer
    party_id = Column(Integer, nullable=True, index=True)  # id ПВЗ или продавца
    amount_rub = Column(Float, nullable=False)  # + доход стороны, − расход
    kind = Column(String(16), nullable=False)
    note = Column(String(240), nullable=False, default="")


class Incident(Base):
    __tablename__ = "incident"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("order.id"), nullable=True, index=True)
    type = Column(String(24), nullable=False)  # node_down | late | no_route
    description = Column(Text, nullable=False)
    resolved_route_id = Column(Integer, ForeignKey("route.id"), nullable=True)
    status = Column(String(12), nullable=False, default="open", index=True)
    edge_id = Column(Integer, ForeignKey("edge.id"), nullable=True)


class Notification(Base):
    __tablename__ = "notification"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, nullable=False, index=True)
    audience = Column(String(12), nullable=False, index=True)  # pvz | seller
    text = Column(Text, nullable=False)
    order_id = Column(Integer, ForeignKey("order.id"), nullable=True, index=True)
    payload_json = Column(Text, nullable=False, default="{}")


class Clock(Base):
    """Singleton-строка виртуального времени (сценарий 5)."""

    __tablename__ = "clock"

    id = Column(Integer, primary_key=True, default=1)
    virtual_now = Column(DateTime, nullable=False)
