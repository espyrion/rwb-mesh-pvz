"""Схемы ответов API: контракт для /docs и клиентов.

Модели намеренно «мягкие»: необязательные поля и extra="ignore". Смысл — зафиксировать
в OpenAPI состав и типы полей, а не ломать сервис на добавленном поле.
Обязательные поля — только те, без которых ответ бессмысленен (id, код, статус).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class PvzOut(Out):
    id: int
    code: str
    name: str
    city: str
    capacity_cells: int
    used_cells: int
    free_cells: int | None = None
    utilization_pct: float | None = None
    is_active: bool | None = None
    is_hub: bool | None = None
    storage_rate_day: float | None = None
    op_fee: float | None = None


class SellerOut(Out):
    id: int
    name: str
    commission_pct: float | None = None


class ItemOut(Out):
    id: int
    sku: str
    title: str | None = None
    seller_id: int | None = None
    qty: int | None = None
    reserved_qty: int | None = None
    available_qty: int | None = None
    cell_size: int | None = None
    cells: int | None = None
    stored_pvz_id: int | None = None
    stored_pvz_code: str | None = None
    accepted_at: str | None = None
    status: str | None = None
    price_rub: float | None = None


class RouteOut(Out):
    id: int
    order_id: int | None = None
    kind: str | None = None
    segments: list[dict[str, Any]] | None = None
    total_cost_rub: float | None = None
    total_hours: float | None = None
    reason: str | None = None
    created_at: str | None = None


class OrderOut(Out):
    id: int
    code: str
    status: str
    item_id: int | None = None
    sku: str | None = None
    title: str | None = None
    qty: int | None = None
    from_pvz: int | None = None
    from_code: str | None = None
    to_pvz: int | None = None
    to_code: str | None = None
    route_id: int | None = None
    route: RouteOut | None = None
    created_at: str | None = None
    assembled_at: str | None = None
    issued_at: str | None = None
    cancelled_at: str | None = None
    return_deadline: str | None = None
    buyer_total_rub: float | None = None
    seller_net_rub: float | None = None
    return_route_id: int | None = None
    public_token: str | None = None


class EdgeOut(Out):
    id: int
    from_pvz: int
    to_pvz: int
    from_code: str | None = None
    to_code: str | None = None
    kind: str | None = None
    cost_rub: float | None = None
    hours: float | None = None
    is_active: bool | None = None


class SegmentOut(Out):
    edge_id: int | None = None
    from_: int | None = Field(default=None, alias="from")
    to: int | None = None
    from_code: str | None = None
    to_code: str | None = None
    from_name: str | None = None
    to_name: str | None = None
    kind: str | None = None
    cost_rub: float | None = None
    hours: float | None = None


class PlannedRouteOut(Out):
    kind: str
    available: bool
    segments: list[SegmentOut] | None = None
    total_cost_rub: float | None = None
    total_hours: float | None = None
    reason: str | None = None


class CompareOut(Out):
    mesh: PlannedRouteOut | None = None
    hub: PlannedRouteOut | None = None
    chosen: str | None = None
    reason: str | None = None
    from_pvz: int | None = None
    to_pvz: int | None = None


class IncidentOut(Out):
    id: int
    ts: str | None = None
    order_id: int | None = None
    type: str | None = None
    description: str | None = None
    status: str | None = None
    resolved_route_id: int | None = None
    edge_id: int | None = None


class NotificationOut(Out):
    id: int
    ts: str | None = None
    audience: str | None = None
    text: str | None = None
    order_id: int | None = None
    payload: dict[str, Any] | None = None


class LedgerRowOut(Out):
    id: int
    ts: str | None = None
    order_id: int | None = None
    item_id: int | None = None
    party: str | None = None
    party_id: int | None = None
    amount_rub: float | None = None
    kind: str | None = None
    note: str | None = None


class ClockOut(Out):
    virtual_now: str
    timezone: str | None = None


class StorageAccrualOut(Out):
    days: int | None = None
    charged_total_rub: float | None = None
    by_pvz: dict[str, float] | None = None
    alerts: int | None = None
    virtual_now: str | None = None


class TrackEventOut(Out):
    ts: str | None = None
    label: str | None = None
    status: str | None = None


class TrackPartyOut(Out):
    code: str | None = None
    city: str | None = None
    name: str | None = None


class TrackOut(Out):
    code: str
    status: str
    status_label: str | None = None
    qty: int | None = None
    created_at: str | None = None
    assembled_at: str | None = None
    issued_at: str | None = None
    cancelled_at: str | None = None
    return_deadline: str | None = None
    buyer_total_rub: float | None = None
    from_: TrackPartyOut | None = Field(default=None, alias="from")
    destination: TrackPartyOut | None = None
    hint: str | None = None
    timeline: list[TrackEventOut] | None = None


class PvzAnalyticsOut(Out):
    pvz: PvzOut
    period: dict[str, Any] | None = None
    revenue: dict[str, float] | None = None
    revenue_total_rub: float | None = None
    deductions: dict[str, float] | None = None
    deductions_total_rub: float | None = None
    ledger_net_rub: float | None = None
    op_costs_modeled_rub: float | None = None
    fixed_costs_modeled_rub: float | None = None
    profit_rub: float | None = None
    storage_cell_days: float | None = None
    orders_issued: int | None = None
    accepted_units: int | None = None
    utilization_pct: float | None = None
    ledger: list[dict[str, Any]] | None = None


class SellerAnalyticsOut(Out):
    seller: SellerOut
    period: dict[str, Any] | None = None
    revenue: dict[str, float] | None = None
    costs: dict[str, float] | None = None
    profit_rub: float | None = None
    orders: list[dict[str, Any]] | None = None
    ledger: list[dict[str, Any]] | None = None


class SummaryOut(Out):
    orders: dict[str, Any]
    ledger: dict[str, float]
    routes: dict[str, Any]
    ledger_by_kind: dict[str, dict[str, float]] | None = None
    logistics_cost_rub: float | None = None
    storage_charged_rub: float | None = None
    incidents_open: int | None = None


class HealthOut(Out):
    status: str


class CheckOut(Out):
    name: str
    ok: bool
    detail: str | None = None


class DeepHealthOut(Out):
    status: str
    checks: list[CheckOut]
    counts: dict[str, int] | None = None
