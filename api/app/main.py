"""FastAPI-приложение mesh-PVZ.

Все эндпоинты, кроме /health и /openapi.json, доступны под /api/v1.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .db import get_db, init_db
from .auth import ApiKeyMiddleware
from .responses import DeepHealthOut, HealthOut
from .routers import (
    analytics,
    catalog,
    clock_api,
    incidents,
    notifications,
    orders,
    returns,
    routes_api,
    track,
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
app.add_middleware(ApiKeyMiddleware)


@app.middleware("http")
async def request_id(request, call_next):
    """Каждый ответ несёт X-Request-ID: с ним проще найти запрос в логах жюри/поддержки."""
    import uuid

    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@app.get("/health", tags=["service"], response_model=HealthOut)
def health():
    return {"status": "ok"}


@app.get("/api/v1/health/deep", tags=["service"], response_model=DeepHealthOut)
def health_deep(db: Session = Depends(get_db)):
    """Проверка инвариантов данных: то, что обычный /health не увидит.

    Дешёвые запросы, пригодные для мониторинга: переполнение и отрицательные значения
    ячеек, резерв больше остатка, заказ без маршрута, пустой ledger при выданных заказах.
    """
    from .models import Item, Ledger, Order, Pvz
    from .serializers import reserved_qty

    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail or ("ok" if ok else "нарушено")})

    bad_cells = (
        db.query(Pvz).filter((Pvz.used_cells < 0) | (Pvz.used_cells > Pvz.capacity_cells)).count()
    )
    add("ячейки в пределах ёмкости", bad_cells == 0, f"ПВЗ с нарушением: {bad_cells}")

    # Резервы не хранятся в Item: они выводятся из активных заказов, поэтому сверяем через сериализатор.
    items = db.query(Item).all()
    over_reserved = [item.id for item in items if reserved_qty(db, item.id) > item.qty]
    add("резерв не больше остатка", not over_reserved,
        f"партий с нарушением: {len(over_reserved)}" if over_reserved else "ok")

    no_route = (
        db.query(Order)
        .filter(Order.route_id.is_(None), Order.status.notin_(["cancelled", "returned"]))
        .count()
    )
    add("у активных заказов есть маршрут", no_route == 0, f"заказов без маршрута: {no_route}")

    issued = db.query(Order).filter(Order.status.in_(["issued", "returned", "rerouted"])).count()
    ledger_rows = db.query(Ledger).count()
    add("проводки есть при выданных заказах", issued == 0 or ledger_rows > 0,
        f"заказов: {issued}, проводок: {ledger_rows}")

    counts = {
        "pvz": db.query(Pvz).count(),
        "items": db.query(Item).count(),
        "orders": db.query(Order).count(),
        "ledger_rows": ledger_rows,
    }
    status = "ok" if all(check["ok"] for check in checks) else "degraded"
    return {"status": status, "checks": checks, "counts": counts}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


for module in (catalog, orders, routes_api, incidents, returns, clock_api, analytics, notifications, track):
    app.include_router(module.router, prefix="/api/v1")

app.add_api_route("/api/v1/health", health, methods=["GET"], tags=["service"], response_model=HealthOut)


def _openapi() -> dict:
    """OpenAPI с описанием X-API-Key: /docs получает рабочую кнопку Authorize.

    Проверка ключа живёт в middleware (default-deny по таблице RULES); здесь —
    только контракт. Запись помечена security, чтение и /track остаются публичными.
    """
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})["ApiKey"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": (
            "Роль определяется ключом: pvz / seller / admin. "
            "Демо-ключи: demo-pvz-key, demo-seller-key, demo-admin-key (переопределяются MESH_*_KEY)."
        ),
    }
    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            if method in {"get", "head", "options"}:
                operation.pop("security", None)
            else:
                operation["security"] = [{"ApiKey": []}]
    app.openapi_schema = schema
    return schema


app.openapi = _openapi
