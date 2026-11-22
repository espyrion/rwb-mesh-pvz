"""API-ключи и роли: ПВЗ, продавец, маркетплейс.

Одна точка правды: таблица правил «метод + путь → кто может». Проверка идёт в middleware,
поэтому новые эндпоинты без правила автоматически закрыты для всех, кроме администратора.
Ключи задаются окружением (docker-compose), по умолчанию — демонстрационные.
"""
from __future__ import annotations

import os
import re

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

DEFAULT_KEYS = {
    "pvz": "demo-pvz-key",
    "seller": "demo-seller-key",
    "admin": "demo-admin-key",
}

ADMIN = "admin"

# (метод, регулярка пути без /api/v1, роли). Порядок важен: первое совпадение выигрывает.
RULES: list[tuple[str, str, set[str]]] = [
    ("POST", r"^/orders$", {"seller", ADMIN}),
    ("POST", r"^/orders/\d+/(assemble|transit|issue|return)$", {"pvz", ADMIN}),
    ("POST", r"^/orders/\d+/cancel$", {"seller", ADMIN}),
    ("POST", r"^/items$", {"seller", ADMIN}),
    ("POST", r"^/items/\d+/relocate$", {"pvz", ADMIN}),
    ("POST", r"^/edge", {ADMIN}),          # /edges/{id}/disable|enable
    ("POST", r"^/clock/advance$", {ADMIN}),
    ("POST", r"^/incidents/\d+/resolve$", {"pvz", ADMIN}),
]

PUBLIC = [
    (re.compile(r"^/track/[^/]+$"), {"GET"}),
]

_COMPILED = [(method, re.compile(pattern), roles) for method, pattern, roles in RULES]


def key_for(role: str) -> str:
    return os.environ.get(f"MESH_{role.upper()}_KEY", DEFAULT_KEYS[role])


def keys() -> dict[str, str]:
    return {role: key_for(role) for role in DEFAULT_KEYS}


def role_of(api_key: str | None) -> str | None:
    if not api_key:
        return None
    for role, value in keys().items():
        if api_key == value:
            return role
    return None


def _required_roles(method: str, path: str) -> set[str] | None:
    """None — путь открыт; иначе набор ролей, которым разрешена операция."""
    if path.startswith("/api/v1"):
        path = path[len("/api/v1"):]
    for pattern, methods in PUBLIC:
        if method in methods and pattern.match(path):
            return None
    if method in ("GET", "HEAD", "OPTIONS"):
        return None
    for rule_method, pattern, roles in _COMPILED:
        if method == rule_method and pattern.match(path):
            return roles
    # Любая другая запись — только администратор: забытый эндпоинт не остаётся открытым.
    return {ADMIN}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if os.environ.get("MESH_AUTH_DISABLED") == "1":
            return await call_next(request)
        required = _required_roles(request.method, request.url.path)
        if required is None:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        role = role_of(api_key)
        if role is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "нужен заголовок X-API-Key с ключом роли (ПВЗ, продавец или маркетплейс)"},
                headers={"WWW-Authenticate": "ApiKey"},
            )
        if role not in required:
            return JSONResponse(
                status_code=403,
                content={"detail": f"роль «{role}» не может выполнять эту операцию; нужна одна из: {', '.join(sorted(required))}"},
            )
        request.state.role = role
        return await call_next(request)
