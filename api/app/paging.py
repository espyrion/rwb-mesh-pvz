"""Пагинация списков: срез + X-Total-Count, без изменения формы ответа."""
from __future__ import annotations

from fastapi import Response
from sqlalchemy.orm import Query


def page(query: Query, response: Response, limit: int | None, offset: int) -> list:
    total = query.order_by(None).count()
    response.headers["X-Total-Count"] = str(total)
    if limit:
        query = query.limit(limit)
    if offset:
        query = query.offset(offset)
    return query.all()
