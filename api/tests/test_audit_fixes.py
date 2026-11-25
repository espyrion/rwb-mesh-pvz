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


def test_blank_idempotency_key_does_not_collide(client):
    body = {"item_id": 1, "qty": 1, "to_pvz_id": 3, "idempotency_key": ""}
    assert client.post("/api/v1/orders", json=body).status_code == 201
    body2 = {"item_id": 5, "qty": 1, "to_pvz_id": 4, "idempotency_key": "   "}
    assert client.post("/api/v1/orders", json=body2).status_code == 201


def test_order_from_disabled_pvz_is_409(client, db):
    items = client.get("/api/v1/items").json()
    item = next(i for i in items if i["available_qty"] > 0 and i["stored_pvz_id"] not in (None,))
    from app.models import Pvz

    src = db.get(Pvz, item["stored_pvz_id"])
    src.is_active = False
    db.commit()
    dst = next(p for p in client.get("/api/v1/pvz").json() if p["id"] != src.id and not p["is_hub"])
    r = client.post("/api/v1/orders", json={"item_id": item["id"], "qty": 1, "to_pvz_id": dst["id"]})
    assert r.status_code == 409, r.text
    assert "выведен из строя" in r.json()["detail"]


def test_relocate_to_disabled_pvz_rejected(client):
    client.post("/api/v1/pvz/3/disable")
    r = client.post("/api/v1/items/1/relocate", json={"to_pvz_id": 3})
    assert r.status_code == 409
    assert client.get("/api/v1/items/1").json()["stored_pvz_id"] != 3


def test_anonymous_order_list_hides_public_token(client):
    create = client.post("/api/v1/orders", json={"item_id": 1, "qty": 1, "to_pvz_id": 3})
    order_id = create.json()["order"]["id"]
    anon = client.__class__(app)
    with anon as a:
        listed = a.get("/api/v1/orders").json()
        assert listed and all(o["public_token"] is None for o in listed)
        assert a.get(f"/api/v1/orders/{order_id}").json()["public_token"] is None
        assert a.get(f"/api/v1/track/{create.json()['order']['public_token']}").status_code == 200
    keyed = client.get("/api/v1/orders").json()
    assert next(o for o in keyed if o["id"] == order_id)["public_token"]


def test_return_after_audit_surplus_keeps_units(client):
    items = client.get("/api/v1/items").json()
    item = next(i for i in items if i["sku"] == "SM-2002")
    qty_before = item["qty"]
    order = client.post("/api/v1/orders", json={"item_id": item["id"], "qty": 1, "to_pvz_id": 2}).json()
    oid = order["order"]["id"]
    assert client.post(f"/api/v1/orders/{oid}/assemble").status_code == 200
    assert client.post(f"/api/v1/orders/{oid}/issue").status_code == 200
    left = client.get(f"/api/v1/items/{item['id']}").json()["qty"]
    client.post(f"/api/v1/pvz/2/audit", json={"actual": {str(item["id"]): left + 5}})
    after_audit = client.get(f"/api/v1/items/{item['id']}").json()["qty"]
    assert after_audit == left + 5
    assert client.post(f"/api/v1/orders/{oid}/return").status_code == 200
    assert client.get(f"/api/v1/items/{item['id']}").json()["qty"] == after_audit + 1
