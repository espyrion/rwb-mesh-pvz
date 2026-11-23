"""Pydantic-схемы запросов API (валидация → 422)."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ItemCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=32)
    seller_id: int
    title: str = Field(min_length=1, max_length=200)
    qty: int = Field(ge=1, le=1000)
    cell_size: int = Field(ge=1, le=10)
    pvz_id: int
    price_rub: float = Field(default=2500.0, ge=0)


class OrderCreate(BaseModel):
    item_id: int
    qty: int = Field(ge=1, le=1000)
    to_pvz_id: int
    accept_latency: bool = True
    idempotency_key: str | None = Field(default=None, max_length=64)


class RelocateRequest(BaseModel):
    to_pvz_id: int


class RerouteRequest(BaseModel):
    accept_latency: bool = True


class PlacementScoreRequest(BaseModel):
    """Характеристики партии для скоринга размещения (POST /catalog/placement-score).

    demand_per_day и turnover_days необязательны: без них спрос наблюдается по
    заказам SKU за окно pvz.placement_demand_window_days, а срок хранения
    считается по закону Литтла.
    """

    sku: str = Field(min_length=1, max_length=32)
    qty: int = Field(ge=1, le=1000)
    cell_size: int = Field(ge=1, le=10, description="ячеек на единицу товара")
    demand_per_day: float | None = Field(default=None, ge=0, le=1000)
    turnover_days: float | None = Field(default=None, gt=0, le=365)
    pvz_id: int | None = Field(default=None, description="ПВЗ для проверки свободной ёмкости")
    city: str | None = Field(default=None, max_length=64, description="город для наблюдения спроса")

    @field_validator("sku")
    @classmethod
    def _sku_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("sku не может быть пустым")
        return value


class AuditRequest(BaseModel):
    """Фактические остатки по партиям ПВЗ: item_id → qty (POST /pvz/{id}/audit)."""

    actual: dict[int, int]
    note: str = Field(default="", max_length=200)

    @field_validator("actual")
    @classmethod
    def _actual_is_sane(cls, value: dict[int, int]) -> dict[int, int]:
        if not value:
            raise ValueError("actual не может быть пустым: нужен словарь item_id → фактическое количество")
        for item_id, qty in value.items():
            if item_id < 1:
                raise ValueError(f"item_id должен быть ≥ 1 (получено {item_id})")
            if qty < 0:
                raise ValueError(f"фактическое количество по партии #{item_id} не может быть отрицательным")
        return value