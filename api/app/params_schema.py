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
    utilization_alert_pct: float = Field(gt=0, le=100)
    accurate_stock_bonus_rub: float = Field(ge=0)
    fixed_cost_day_rub: float = Field(ge=0)
    ops_per_day_capacity: int = Field(gt=0)
    avg_cells_per_order: float = Field(gt=0, le=10)


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
    return_window_days: int = Field(ge=1, le=60)
    return_reverse_fee_rub: float = Field(ge=0)
    late_penalty_rub: float = Field(ge=0)
    late_threshold_hours: float = Field(gt=0)

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
    return_rate_pct: float = Field(ge=0, le=100)
    incident_rate_pct: float = Field(ge=0, le=100)


class Params(_Section):
    meta: Meta
    pvz: Pvz
    logistics: Logistics
    fees: Fees
    buyer_acceptance: BuyerAcceptance
    scenario_defaults: ScenarioDefaults

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
