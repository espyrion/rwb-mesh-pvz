"""Общие фикстуры: tmp-SQLite, сид и TestClient."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_tmp = tempfile.mkdtemp(prefix="mesh-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ECON_PARAMS"] = str(ROOT / "economics" / "params.yaml")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def client():
    init_db(seed_if_empty=False)
    with SessionLocal() as session:
        seed(session)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db(client):
    with SessionLocal() as session:
        yield session


def create_order(client, item_id=1, qty=1, to_pvz=3, **extra):
    response = client.post(
        "/api/v1/orders",
        json={"item_id": item_id, "qty": qty, "to_pvz_id": to_pvz, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def issue_order(client, item_id=1, qty=1, to_pvz=3):
    data = create_order(client, item_id, qty, to_pvz)
    order_id = data["order"]["id"]
    assert client.post(f"/api/v1/orders/{order_id}/assemble").status_code == 200
    assert client.post(f"/api/v1/orders/{order_id}/issue").status_code == 200
    return order_id, data
