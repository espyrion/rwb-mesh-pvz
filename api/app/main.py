"""FastAPI-приложение mesh-PVZ.

Все эндпоинты, кроме /health и /openapi.json, доступны под /api/v1.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .db import init_db
from .routers import (
    analytics,
    catalog,
    clock_api,
    incidents,
    notifications,
    orders,
    returns,
    routes_api,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db(seed_if_empty=True)
    yield


app = FastAPI(
    title="Mesh-PVZ API",
    version="1.0.0",
    description=(
        "MVP mesh-доставки с хранением в ПВЗ: приёмка, заказы, маршруты mesh/hub, "
        "выдача, возврат, инциденты, виртуальные часы и аналитика. "
        "Хаб, магистраль и платёжный шлюз — помеченные заглушки."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["service"])
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


for module in (catalog, orders, routes_api, incidents, returns, clock_api, analytics, notifications):
    app.include_router(module.router, prefix="/api/v1")

app.add_api_route("/api/v1/health", health, methods=["GET"], tags=["service"])
