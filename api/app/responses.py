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
    lat: float | None = None
    lng: float | None = None
    # Контроль точности остатков: сглаженная точность, SLA-рейтинг, число сверок.
    audit_accuracy_pct: float | None = None
    audit_sla_rating: str | None = None
    audit_count: int | None = None
    last_audit_at: str | None = None


class PlacementComponentOut(Out):
    id: str
    label: str | None = None
    weight: float | None = None
    raw: float | None = None
    points: float | None = None
    max_points: float | None = None


class PlacementScoreOut(Out):
    sku: str
    qty: int | None = None
    cell_size: int | None = None
    cells_total: int | None = None
    demand_per_day: float | None = None
    demand_source: str | None = None
    demand_window_days: int | None = None
    expected_storage_days: float | None = None
    expected_storage_cost_rub: float | None = None
    components: list[PlacementComponentOut] | None = None
    score: float | None = None
    threshold: float | None = None
    recommendation: str | None = None
    confidence_pct: float | None = None
    capacity_override: bool | None = None
    reasons: list[str] | None = None
    params_used: dict[str, float] | None = None


class AuditLineOut(Out):
    item_id: int
    sku: str | None = None
    expected_qty: int | None = None
    actual_qty: int | None = None
    delta: int | None = None
    cell_size: int | None = None
    cells_delta: int | None = None
    status: str | None = None
    incident_id: int | None = None


class AuditOut(Out):
    pvz_id: int
    code: str | None = None
    audited_at: str | None = None
    note: str | None = None
    lines: list[AuditLineOut] | None = None
    matched: int | None = None
    shortage: int | None = None
    surplus: int | None = None
    total_delta_units: int | None = None
    cells_delta: int | None = None
    incident_ids: list[int] | None = None
    penalty_total_rub: float | None = None
    accuracy_pct: float | None = None
    audit_accuracy_pct: float | None = None
    accuracy_before_pct: float | None = None
    sla_rating: str | None = None
    audits_total: int | None = None
    used_cells: int | None = None
    free_cells: int | None = None
    idempotent: bool | None = None


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
    parent_batch_id: int | None = None


class RouteOut(Out):
    id: int
    order_id: int | None = None
    kind: str | None = None
    segments: list[dict[str, Any]] | None = None
    total_cost_rub: float | None = None
    total_hours: float | None = None
    reason: str | None = None
    reason_code: str | None = None
    overflow: bool | None = None
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
    reason_code: str | None = None


class PvzRefOut(Out):
    id: int
    code: str | None = None
    city: str | None = None


class CompareOut(Out):
    mesh: PlannedRouteOut | None = None
    hub: PlannedRouteOut | None = None
    chosen: str | None = None
    reason: str | None = None
    reason_code: str | None = None
    overflow: bool | None = None
    load: dict[str, Any] | None = None
    # routing.compare() отдаёт вложенные объекты «from»/«to»; имена с подчёркиванием —
    # потому что from — ключевое слово, наружу уходит псевдоним.
    from_: PvzRefOut | None = Field(default=None, alias="from")
    to_: PvzRefOut | None = Field(default=None, alias="to")


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


class TrackRouteOut(Out):
    kind: str | None = None
    hours: float | None = None


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
    route: TrackRouteOut | None = None


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


class MoneySideOut(Out):
    """Доходы/затраты одной стороны: доходы и затраты — положительные величины."""

    revenue: dict[str, float] | None = None
    revenue_total: float | None = None
    costs: dict[str, float] | None = None
    costs_total: float | None = None
    profit_rub: float | None = None
    margin_pct: float | None = None


class BuyerCostOut(Out):
    goods_rub: float | None = None
    delivery_fee_rub: float | None = None
    total_rub: float | None = None
    hours: float | None = None


class OrderEconOut(Out):
    kind: str
    gmv_rub: float | None = None
    route: dict[str, float] | None = None
    storage: dict[str, float] | None = None
    buyer: BuyerCostOut | None = None
    pvz: MoneySideOut | None = None
    marketplace: MoneySideOut | None = None
    seller: MoneySideOut | None = None
    chain: dict[str, float] | None = None


class CompareEconOut(Out):
    buyer_delta_rub: float | None = None
    buyer_delta_pct: float | None = None
    buyer_delta_hours: float | None = None
    buyer_delta_hours_pct: float | None = None
    buyer_acceptance: dict[str, Any] | None = None
    pvz_profit_mesh: float | None = None
    pvz_profit_base: float | None = None
    pvz_delta_rub: float | None = None
    pvz_delta_pct: float | None = None
    marketplace_profit_mesh: float | None = None
    marketplace_profit_base: float | None = None
    marketplace_delta_rub: float | None = None
    marketplace_delta_pct: float | None = None
    seller_profit_mesh: float | None = None
    seller_profit_base: float | None = None
    seller_delta_rub: float | None = None
    seller_delta_pct: float | None = None
    chain_profit_mesh: float | None = None
    chain_profit_base: float | None = None
    chain_delta_rub: float | None = None
    chain_delta_pct: float | None = None
    chain_more_profitable: bool | None = None
    viable: bool | None = None


class OrderPairOut(Out):
    mesh: OrderEconOut
    hub: OrderEconOut
    compare: CompareEconOut


class IntercityPairOut(Out):
    base: OrderEconOut
    mesh: OrderEconOut
    compare: CompareEconOut


class PvzDayOut(MoneySideOut):
    ops: int
    contribution_per_op_rub: float | None = None
    capacity_ops: int | None = None


class BreakevenOut(Out):
    ops: int | None = None
    share_of_capacity: float | None = None
    capacity_ops: int | None = None


class ScenarioOut(Out):
    params: dict[str, float]
    buyer_acceptance: dict[str, float] | None = None
    order: OrderPairOut
    intercity: IntercityPairOut
    pvz_day: PvzDayOut
    breakeven: BreakevenOut
    storage_breakeven_days: float | None = None
    sensitivity: list[dict[str, Any]] = Field(default_factory=list)
    chain_breakeven: dict[str, Any] | None = None
    queue: list[dict[str, Any]] = Field(default_factory=list)
    queue_threshold: dict[str, Any] | None = None
    disclaimer: str | None = None


class ReportOut(Out):
    params: dict[str, Any]
    buyer_acceptance: dict[str, float] | None = None
    city: OrderPairOut
    intercity: IntercityPairOut
    pvz_day: PvzDayOut
    breakeven: BreakevenOut
    storage_breakeven_days: float | None = None
    sensitivity: list[dict[str, Any]] = Field(default_factory=list)
    chain_breakeven: dict[str, Any] | None = None
    queue: list[dict[str, Any]] = Field(default_factory=list)
    queue_threshold: dict[str, Any] | None = None
    disclaimer: str | None = None
