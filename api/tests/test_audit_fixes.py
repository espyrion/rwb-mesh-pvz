"""Регрессии аудита 2026-11-25: гонки возврата/перемещения и бесконечная цена."""
from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from app.auth import key_for
from app.main import app
from conftest import issue_order


def _parallel(n, fn):
    barrier, out = threading.Barrier(n), []

    def run(i):
        with TestClient(app) as c:
            c.headers.update({"X-API-Key": key_for("admin")})
            barrier.wait()
            out.append(fn(c, i))

    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def test_parallel_return_refunds_once(client):
    order_id, _ = issue_order(client)
    res = _parallel(6, lambda c, i: c.post(f"/api/v1/orders/{order_id}/return"))
    assert all(r.status_code == 200 for r in res)
    assert sum(1 for r in res if not r.json().get("idempotent_replay")) == 1
    rows = client.get("/api/v1/ledger", params={"order_id": order_id}).json()
    other, _ = issue_order(client)
    assert client.post(f"/api/v1/orders/{other}/return").status_code == 200
    base = client.get("/api/v1/ledger", params={"order_id": other}).json()
    assert len(rows) == len(base)  # столько же проводок, сколько при одном возврате
    assert sum(r["amount_rub"] for r in rows) == -180.0


def test_parallel_relocate_posts_once(client):
    src = client.get("/api/v1/items/1").json()["stored_pvz_id"]
    dst = 2 if src != 2 else 3
    before = len(client.get("/api/v1/ledger", params={"kind": "relocation"}).json())
    res = _parallel(4, lambda c, i: c.post("/api/v1/items/1/relocate", json={"to_pvz_id": dst}))
    assert sorted(r.status_code for r in res) == [200, 409, 409, 409]
    rows = client.get("/api/v1/ledger", params={"kind": "relocation"}).json()
    assert len(rows) - before == 1  # одна проводка, а не по числу запросов


def test_item_price_inf_rejected(client):
    body = {"sku": "X-1", "seller_id": 1, "title": "t", "qty": 1, "cell_size": 1, "pvz_id": 1}
    r = client.post("/api/v1/items", content='{"sku":"X-1","seller_id":1,"title":"t","qty":1,"cell_size":1,"pvz_id":1,"price_rub":1e999}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert client.post("/api/v1/items", json={**body, "price_rub": 1e12}).status_code == 422
