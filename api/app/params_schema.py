"""Схема economics/params.yaml: типы, диапазоны и смысловые проверки ставок.

Смысл: жюри (и мы сами) может править YAML, не глядя в код. Опечатка в имени ключа
или отрицательный тариф должны падать сразу и с понятным текстом, а не превращаться
в странные цифры в отчёте.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Meta(_Section):
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    disclaimer: str = Field(min_length=10)
    source: str = ""
    prices_include_vat: bool = True

    @model_validator(mode="after")
    def _disclaimer_mentions_model(self) -> "Meta":
        if "RWB" not in self.disclaimer:
            raise ValueError("meta.disclaimer должен явно говорить, что тарифы — параметры модели, а не RWB")
        return self


class Pvz(_Section):
    capacity_cells_default: int = Field(gt=0, le=1000)
    storage_rate_day_rub: float = Field(ge=0)
    op_fee_rub: float = Field(ge=0)
    mesh_bonus_rub: float = Field(ge=0)
    long_storage_threshold_days: int = Field(ge=1)
    long_storage_threshold_rub: float = Field(gt=0)
    relocation_cost_rub: float = Field(ge=0)
    utilization_target_pct: float = Field(gt=0, le=100)
    utilization_alert_pct: float = Field(gt=0, le=100)
    accurate_stock_bonus_rub: float = Field(ge=0)
    fixed_cost_day_rub: float = Field(ge=0)
    ops_per_day_capacity: int = Field(gt=0)
    avg_cells_per_order: float = Field(gt=0, le=10)
    relocation_speed_kmh: float = Field(gt=0, le=120)
    road_detour_factor: float = Field(ge=1.0, le=3.0)
    evacuation_capacity_weight: float = Field(ge=0)
    evacuation_distance_weight: float = Field(ge=0)

    # Скоринг размещения SKU: веса нормированных критериев (сумма = 1) и пороги.
    placement_demand_weight: float = Field(ge=0, le=1)
    placement_turnover_weight: float = Field(ge=0, le=1)
    placement_footprint_weight: float = Field(ge=0, le=1)
    placement_demand_target_per_day: float = Field(gt=0)
    placement_fast_turnover_days: float = Field(gt=0)
    placement_slow_turnover_days: float = Field(gt=0)
    placement_footprint_max_cells: int = Field(gt=1, le=100)
    placement_score_threshold: float = Field(gt=0, le=100)
    placement_confidence_span: float = Field(gt=0, le=100)
    placement_max_expected_days: float = Field(gt=0, le=365)
    placement_default_demand_per_day: float = Field(gt=0)
    placement_demand_window_days: int = Field(ge=1, le=365)
    placement_auto_recommend: bool = False

    # Инвентаризация остатков: сглаживание точности, удержания и границы SLA.
    audit_accuracy_ema_alpha: float = Field(ge=0, le=1)
    audit_shortage_penalty_rub: float = Field(ge=0)
    audit_surplus_penalty_rub: float = Field(ge=0)
    audit_penalty_cap_rub: float = Field(ge=0)
    audit_sla_a_pct: float = Field(ge=0, le=100)
    audit_sla_b_pct: float = Field(ge=0, le=100)
    audit_sla_c_pct: float = Field(ge=0, le=100)
    audit_initial_accuracy_pct: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _target_below_alert(self) -> "Pvz":
        if self.utilization_target_pct >= self.utilization_alert_pct:
            raise ValueError("целевая загрузка ПВЗ должна быть ниже порога предупреждения")
        return self

    @model_validator(mode="after")
    def _placement_weights_sum_to_one(self) -> "Pvz":
        total = self.placement_demand_weight + self.placement_turnover_weight + self.placement_footprint_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"веса скоринга размещения должны давать 1.0 (сейчас {total:.3f}): "
                "pvz.placement_demand_weight + placement_turnover_weight + placement_footprint_weight"
            )
        return self

    @model_validator(mode="after")
    def _placement_turnover_bounds(self) -> "Pvz":
        if self.placement_fast_turnover_days >= self.placement_slow_turnover_days:
            raise ValueError(
                "быстрая оборачиваемость (placement_fast_turnover_days) должна быть меньше мёртвой "
                "(placement_slow_turnover_days), иначе критерий оборачиваемости вывернут"
            )
        if self.placement_max_expected_days < self.placement_slow_turnover_days:
            raise ValueError(
                "placement_max_expected_days не может быть меньше placement_slow_turnover_days: "
                "потолок срока хранения должен покрывать «мёртвую» оборачиваемость"
            )
        return self

    @model_validator(mode="after")
    def _audit_sla_thresholds_ordered(self) -> "Pvz":
        if not (self.audit_sla_a_pct > self.audit_sla_b_pct > self.audit_sla_c_pct):
            raise ValueError("границы SLA должны убывать: audit_sla_a_pct > audit_sla_b_pct > audit_sla_c_pct")
        return self


class Logistics(_Section):
    local_delivery_cost_rub: float = Field(ge=0)
    local_delivery_hours: float = Field(gt=0)
    hub_handling_rub: float = Field(ge=0)
    hub_handling_hours: float = Field(gt=0)
    trunk_cost_per_order_rub: float = Field(ge=0)
    trunk_hours: float = Field(gt=0)
    last_mile_cost_rub: float = Field(ge=0)
    last_mile_hours: float = Field(gt=0)
    assemble_cost_rub: float = Field(ge=0)
    acceptance_cost_rub: float = Field(ge=0)


class Fees(_Section):
    buyer_delivery_fee_local_rub: float = Field(ge=0)
    buyer_delivery_fee_hub_rub: float = Field(ge=0)
    commission_pct: float = Field(ge=0, lt=100)
    seller_logistics_fee_rub: float = Field(ge=0)
    return_window_days: int = Field(ge=1, le=60)
    return_reverse_fee_rub: float = Field(ge=0)
    late_penalty_rub: float = Field(ge=0)
    late_threshold_hours: float = Field(gt=0)
    late_cancel_rate: float = Field(ge=0, le=1)
    peak_surge_multiplier: float = Field(ge=1.0)

    @model_validator(mode="after")
    def _hub_not_cheaper_than_local(self) -> "Fees":
        if self.buyer_delivery_fee_hub_rub < self.buyer_delivery_fee_local_rub:
            raise ValueError("доставка через хаб не может стоить покупателю меньше локальной")
        return self


class BuyerAcceptance(_Section):
    max_cost_increase_pct: float = Field(ge=0, le=100)
    max_time_increase_hours: float = Field(ge=0)


class ScenarioDefaults(_Section):
    goods_price_rub: float = Field(gt=0)
    qty: int = Field(ge=1)
    storage_days_typical: int = Field(ge=0)
    local_share_pct: float = Field(ge=0, le=100)
    pvz_day_ops: int = Field(gt=0)
    pvz_day_mesh_share_pct: float = Field(ge=0, le=100)
    return_rate_pct: float = Field(ge=0, le=100)
    incident_rate_pct: float = Field(ge=0, le=100)


class Network(_Section):
    nodes: int = Field(gt=0, le=1000)
    overflow_utilization_threshold: float = Field(gt=0, lt=1)
    overload_delay_hours_per_100pct: float = Field(gt=0)
    queue_cap_pct: float = Field(gt=0, le=100)
    queue_base_ops: int = Field(ge=0)
    sensitivity_orders: int = Field(gt=0)
    simulation_orders: list[int] = Field(min_length=1)
    simulation_seed: int = Field(ge=0)
    sensitivity_mesh_share_pct: list[float] = Field(min_length=1)
    queue_extra_ops: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def _shares_are_sorted(self) -> "Network":
        shares = self.sensitivity_mesh_share_pct
        if any(share < 0 or share > 100 for share in shares) or shares != sorted(shares):
            raise ValueError("sensitivity_mesh_share_pct: доли в % по возрастанию, в диапазоне 0..100")
        if any(ops < 0 for ops in self.queue_extra_ops):
            raise ValueError("queue_extra_ops: дополнительная нагрузка не может быть отрицательной")
        return self


class Seller(_Section):
    goods_cost_pct: float = Field(ge=0, lt=100)


class Params(_Section):
    meta: Meta
    pvz: Pvz
    logistics: Logistics
    fees: Fees
    seller: Seller
    buyer_acceptance: BuyerAcceptance
    scenario_defaults: ScenarioDefaults
    network: Network

    @model_validator(mode="after")
    def _mesh_must_be_cheaper_than_hub(self) -> "Params":
        local = self.logistics.local_delivery_cost_rub
        hub = self.logistics.hub_handling_rub + self.logistics.trunk_cost_per_order_rub + self.logistics.last_mile_cost_rub
        if local >= hub:
            raise ValueError(
                f"локальная доставка ({local} ₽) должна быть дешевле схемы через хаб ({hub} ₽), "
                "иначе правило «внутри города — mesh» теряет экономический смысл"
            )
        return self


def validate(data: dict) -> Params:
    """Проверяет словарь параметров; бросает pydantic.ValidationError с путём до поля."""
    return Params.model_validate(data)
