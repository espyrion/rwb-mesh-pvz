"""Синтетические данные стенда: 8 ПВЗ в двух городах, хаб, рёбра, продавцы, товары.

Данные детерминированы (никакого random), чтобы тесты и демо были воспроизводимы.
seed(session) идемпотентна: полностью стирает и пересоздаёт содержимое БД.
Никаких персональных данных здесь нет.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import clock
from .config import p
from .models import (
    Clock,
    Edge,
    Incident,
    Item,
    Ledger,
    Notification,
    Order,
    Pvz,
    Route,
    Seller,
)

PVZ_ROWS = [
    # id, code, name, city, capacity, other_stock, lat, lng
    # Координаты — приближённые центры районов (публичные карты, погрешность ~0,5 км).
    # Это данные стенда, а не тариф: они нужны только для выбора ближайшего соседа
    # при отказе узла и для оценки времени перемещения.
    (1, "YOL-01", "ПВЗ Центральный", "Йошкар-Ола", 80, 8, 56.6317, 47.8966),
    (2, "YOL-02", "ПВЗ Дубки", "Йошкар-Ола", 60, 8, 56.6550, 47.9290),
    (3, "YOL-03", "ПВЗ Сомбатхей", "Йошкар-Ола", 70, 8, 56.6420, 47.8590),
    (4, "YOL-04", "ПВЗ Заречный", "Йошкар-Ола", 50, 8, 56.6100, 47.9500),
    (5, "KZN-01", "ПВЗ Баумана", "Казань", 90, 20, 55.7900, 49.1200),
    (6, "KZN-02", "ПВЗ Горки", "Казань", 60, 10, 55.8300, 49.1300),
    (7, "KZN-03", "ПВЗ Азино", "Казань", 55, 25, 55.7600, 49.2000),
    (8, "KZN-04", "ПВЗ Ново-Савиновский", "Казань", 65, 15, 55.8200, 49.1400),
]

HUB = (9, "HUB-KZN", "Хаб Казань (заглушка)", "Казань", 0, 0, 55.7500, 49.1000)

SELLER_ROWS = [
    (1, "ООО «ТехноДом»", 12.0),
    (2, "ИП Смирнова А. В.", 14.0),
]

ITEM_ROWS = [
    # id, sku, seller, title, qty, cell, pvz, price
    (1, "TD-1001", 1, "Робот-пылесос CleanBot S5", 6, 2, 1, 18900.0),
    (2, "TD-1002", 1, "Кофемашина BaristaPro 2", 4, 3, 2, 27900.0),
    (3, "TD-1003", 1, "Наушники TWS AirSound", 20, 1, 3, 4900.0),
    (4, "SM-2001", 2, "Электросамокат CityGlide", 3, 3, 4, 15900.0),
    (5, "SM-2002", 2, "Рюкзак городской 25 л", 15, 1, 1, 3200.0),
    (6, "SM-2003", 2, "Термокружка 450 мл", 25, 1, 2, 1200.0),
]


def seed(session: Session) -> None:
    # Чистим всё в порядке зависимостей. Ссылки заказ↔маршрут и инцидент↔маршрут
    # циклические, поэтому сначала обнуляем их, иначе SQLite блокирует DELETE.
    session.query(Order).update({Order.route_id: None, Order.return_route_id: None})
    session.query(Incident).update({Incident.resolved_route_id: None})
    session.flush()
    for model in (Ledger, Notification, Incident, Route, Order, Item, Edge, Pvz, Seller, Clock):
        session.query(model).delete()
    session.flush()

    start = clock.START
    session.add(Clock(id=1, virtual_now=start))

    for pid, code, name, city, capacity, other_stock, lat, lng in PVZ_ROWS:
        session.add(
            Pvz(
                id=pid,
                code=code,
                name=name,
                city=city,
                capacity_cells=capacity,
                used_cells=other_stock,
                is_active=True,
                is_hub=False,
                storage_rate_day=float(p("pvz", "storage_rate_day_rub")),
                op_fee=float(p("pvz", "op_fee_rub")),
                lat=lat,
                lng=lng,
            )
        )
    hid, hcode, hname, hcity, _, _, hlat, hlng = HUB
    session.add(
        Pvz(
            id=hid,
            code=hcode,
            name=hname,
            city=hcity,
            capacity_cells=0,
            used_cells=0,
            is_active=True,
            is_hub=True,
            storage_rate_day=0.0,
            op_fee=0.0,
            lat=hlat,
            lng=hlng,
        )
    )
    session.flush()

    for sid, sname, pct in SELLER_ROWS:
        session.add(Seller(id=sid, name=sname, commission_pct=pct))
    session.flush()

    local_cost = float(p("logistics", "local_delivery_cost_rub"))
    local_hours = float(p("logistics", "local_delivery_hours"))
    for city_pvz in ((1, 2, 3, 4), (5, 6, 7, 8)):
        for a in city_pvz:
            for b in city_pvz:
                if a == b:
                    continue
                session.add(
                    Edge(from_pvz=a, to_pvz=b, kind="local", cost_rub=local_cost, hours=local_hours, is_active=True)
                )

    # Магистраль через единственный хаб (ставки — из params.yaml):
    # ПВЗ→хаб — обработка; хаб→ПВЗ — магистраль + последняя миля.
    hub_cost = float(p("logistics", "hub_handling_rub"))
    hub_hours = float(p("logistics", "hub_handling_hours"))
    trunk_cost = float(p("logistics", "trunk_cost_per_order_rub")) + float(p("logistics", "last_mile_cost_rub"))
    trunk_hours = float(p("logistics", "trunk_hours")) + float(p("logistics", "last_mile_hours"))
    for pid in (1, 2, 3, 4, 5, 6, 7, 8):
        session.add(Edge(from_pvz=pid, to_pvz=hid, kind="trunk", cost_rub=hub_cost, hours=hub_hours, is_active=True))
        session.add(Edge(from_pvz=hid, to_pvz=pid, kind="trunk", cost_rub=trunk_cost, hours=trunk_hours, is_active=True))

    accepted = start
    for iid, sku, seller_id, title, qty, cell, pvz_id, price in ITEM_ROWS:
        session.add(
            Item(
                id=iid,
                sku=sku,
                seller_id=seller_id,
                title=title,
                qty=qty,
                cell_size=cell,
                stored_pvz_id=pvz_id,
                accepted_at=accepted,
                status="stored",
                price_rub=price,
                last_accrued_at=accepted,
                relocate_alert_sent=False,
            )
        )

    session.flush()
    # used_cells выводится из партий, а не задаётся руками: иначе загрузка ПВЗ расходится с остатками.
    for pvz in session.query(Pvz).filter(Pvz.is_hub.is_(False)).all():
        own = sum(
            i.cell_size * i.qty
            for i in session.query(Item).filter(Item.stored_pvz_id == pvz.id).all()
        )
        pvz.used_cells += own
        if pvz.used_cells > pvz.capacity_cells:
            raise ValueError(f"seed: {pvz.code} переполнен ({pvz.used_cells} > {pvz.capacity_cells})")

    session.commit()
