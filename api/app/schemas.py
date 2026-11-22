"""Pydantic-схемы запросов API (валидация → 422)."""
from __future__ import annotations

from pydantic import BaseModel, Field


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
