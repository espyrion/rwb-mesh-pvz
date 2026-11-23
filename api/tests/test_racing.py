"""Гонки в SQLite: повторный disable, параллельный резерв последней единицы, отмена выдачи."""
from __future__ import annotations

import threading

from conftest import create_order

from app import storage
from app.clock import now
from app.db import SessionLocal, begin_immediate
from app.models import Order


def _relocation_ledger(client):
    return client.get("/api/v1/ledger", params={"kind": "relocation"}).json()


def test_double_disable_is_idempotent(client):
    """Повторный disable не эвакуирует остатки второй раз и не пишет новых проводок."""
    first = client.post("/api/v1/pvz/2/disable")
    assert first.status_code == 200
    payload = first.json()
    assert payload["already_down"] is False
    assert payload["relocation_total_rub"] == 120.0
    ledger_before = _relocation_ledger(client)
    used_before = client.get("/api/v1/pvz/3").json()["used_cells"]

    second = client.post("/api/v1/pvz/2/disable")
    assert second.status_code == 200
    again = second.json()
    assert again["already_down"] is True
    assert again["evacuated"] == []
    assert again["orders"] == []
    assert again["relocation_total_rub"] == 0.0
    assert len(_relocation_ledger(client)) == len(ledger_before)
    assert client.get("/api/v1/pvz/3").json()["used_cells"] == used_before


def _reserve_worker(item_id: int, results: list[str], barrier: threading.Barrier) -> None:
    """Одна попытка зарезервировать последнюю единицу в своей транзакции."""
    session = SessionLocal()
    try:
        barrier.wait(timeout=10)
        begin_immediate(session)
        if not storage.reserve_stock(session, item_id, 1):
            session.rollback()
            results.append("no-stock")
            return
        session.add(
            Order(
                code=f"RACE-{threading.get_ident()}",
                item_id=item_id,
                qty=1,
                from_pvz=4,
                to_pvz=1,
                status="routed",
                created_at=now(session),
            )
        )
        session.commit()
        results.append("reserved")
    finally:
        session.close()


def test_parallel_reserve_last_unit(client):
    """Две параллельные транзакции не могут обе зарезервировать одну последнюю единицу."""
    created = client.post(
        "/api/v1/items",
        json={
            "sku": "RACE-1",
            "seller_id": 1,
            "title": "Последняя единица",
            "qty": 1,
            "cell_size": 1,
            "pvz_id": 4,
            "price_rub": 500.0,
        },
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    barrier = threading.Barrier(2)
    results: list[str] = []
    threads = [
        threading.Thread(target=_reserve_worker, args=(item_id, results, barrier))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == ["no-stock", "reserved"]
    rows = [row for row in client.get("/api/v1/items").json() if row["id"] == item_id]
    assert rows[0]["qty"] == 1  # остаток не ушёл дважды
    orders = [row for row in client.get("/api/v1/orders").json() if row["item_id"] == item_id]
    assert len(orders) == 1


def test_cancel_issued_order_is_rejected(client):
    """Отмена уже выданного заказа — 409, статус и остаток не меняются."""
    order_id = create_order(client, item_id=1, qty=1, to_pvz=3)["order"]["id"]
    assert client.post(f"/api/v1/orders/{order_id}/assemble").status_code == 200
    assert client.post(f"/api/v1/orders/{order_id}/issue").status_code == 200
    item_before = [row for row in client.get("/api/v1/items").json() if row["id"] == 1][0]

    response = client.post(f"/api/v1/orders/{order_id}/cancel")
    assert response.status_code == 409
    assert client.get(f"/api/v1/orders/{order_id}").json()["status"] == "issued"
    item_after = [row for row in client.get("/api/v1/items").json() if row["id"] == 1][0]
    assert item_after["qty"] == item_before["qty"]