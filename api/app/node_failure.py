"""Отказ узла сети: блокировка ПВЗ, эвакуация остатков, переадресация заказов (UC-5).

Процедура при выводе точки из строя:
1. точка помечается неактивной — маршрутизатор перестаёт строить пути через неё;
2. свободные остатки (status=stored) эвакуируются на соседний ПВЗ того же города
   с наименьшей загрузкой и свободной ёмкостью; перемещение 60 ₽ — на маркетплейс;
3. активные заказы с точкой выдачи в отказавшем узле переадресуются на соседний ПВЗ
   (товар в сборке переезжает вместе с заказом, ячейки переносятся);
4. активные заказы с источником в отказавшем узле: партия перемещается целиком,
   источник заказа обновляется, маршрут перевыбирается;
5. то, что решить автоматически нельзя (нет ёмкости у соседей, нет пути),
   остаётся открытым инцидентом для оператора.

Числа: relocation_cost_rub и пороги — параметры economics/params.yaml.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import money
from .clock import now
from .models import Incident, Item, Order, Pvz
from .notify import notify
from .routers.incidents import ACTIVE, _open_incident, _touch_pvz, reroute_order
from .serializers import incident_dict, item_dict, order_dict, pvz_dict, reserved_qty, route_dict
from .storage import cells_for, occupy, release


def _candidate(session: Session, city: str, exclude_ids: set[int], needed_cells: int) -> Pvz | None:
    """Соседний ПВЗ того же города: активный, не хаб, с свободной ёмкостью.

    Политика та же, что у предложения о перемещении при длительном хранении:
    наименьшая загрузка, при равенстве — меньший id (детерминированность).
    """
    rows = (
        session.query(Pvz)
        .filter(
            Pvz.city == city,
            Pvz.is_active.is_(True),
            Pvz.is_hub.is_(False),
        )
        .all()
    )
    rows = [z for z in rows if z.id not in exclude_ids and z.capacity_cells - z.used_cells >= needed_cells]
    if not rows:
        return None
    return min(rows, key=lambda z: (z.used_cells / z.capacity_cells if z.capacity_cells else 1.0, z.id))


def _move_item(session: Session, item: Item, dst: Pvz) -> float:
    """Перенести партию между точками: ячейки, адрес хранения, проводка 60 ₽."""
    src = session.get(Pvz, item.stored_pvz_id)
    cells = cells_for(item)
    release(src, cells)
    occupy(dst, cells)
    item.stored_pvz_id = dst.id
    # Хранение после эвакуации начинается заново с новой даты приёмки,
    # как и при обычном перемещении остатка (storage.relocate).
    item.accepted_at = now(session)
    item.last_accrued_at = now(session)
    item.relocate_alert_sent = False
    return money.charge_relocation(session, item, src, dst)


def _handle_order(
    session: Session,
    order: Order,
    pvz: Pvz,
    reason: str,
    handled: list[dict],
    unresolved: list[dict],
    opened: list[dict] | None = None,
    note: str = "",
) -> None:
    """Открыть инцидент по заказу и перевыбрать маршрут; неудача — в unresolved."""
    inc = _open_incident(
        session,
        order,
        "node_down",
        f"Узел {pvz.code} недоступен. {reason}" + (f" {note}" if note else ""),
    )
    route = reroute_order(session, order, inc)
    if opened is not None:
        opened.append(
            {
                "incident": incident_dict(inc),
                "order": order_dict(session, order),
                "new_route": route_dict(route) if route else None,
            }
        )
    if route is None:
        unresolved.append(
            {
                "kind": "order",
                "order_id": order.id,
                "code": order.code,
                "reason": "перевыбор маршрута невозможен",
                "incident_id": inc.id,
            }
        )
    else:
        handled.append(
            {
                "order": order_dict(session, order),
                "action": note or "перевыбор маршрута",
                "route": route_dict(route),
            }
        )


def node_down(session: Session, pvz: Pvz, reason: str) -> dict:
    """Вывести ПВЗ из строя и отработать последствия. Идемпотентно."""
    if not pvz.is_active:
        return {
            "pvz": pvz_dict(pvz),
            "already_down": True,
            "incident": None,
            "evacuated": [],
            "orders": [],
            "incidents": [],
            "unresolved": [],
            "relocation_total_rub": 0.0,
        }
    pvz.is_active = False

    evacuated: list[dict] = []
    handled: list[dict] = []
    unresolved: list[dict] = []
    opened: list[dict] = []
    relocation_total = 0.0

    # 1. Эвакуация свободных остатков. Зарезервированная партия может иметь
    #    свободный хвост: он отделяется в отдельную строку и эвакуируется тоже,
    #    зарезервированная часть уедет вместе со своим заказом (шаг 3).
    node_items = (
        session.query(Item)
        .filter(Item.stored_pvz_id == pvz.id, Item.status.in_(("stored", "returned", "reserved")))
        .order_by(Item.id)
        .all()
    )
    for item in node_items:
        moving = item
        if item.status == "reserved":
            has_source_order = (
                session.query(Order)
                .filter(Order.item_id == item.id, Order.status.in_(ACTIVE), Order.from_pvz == pvz.id)
                .first()
                is not None
            )
            if has_source_order:
                continue  # партия уедет целиком вместе со своими заказами (шаг 3)
            available = item.qty - reserved_qty(session, item.id)
            if available <= 0:
                continue
            # Отделяем свободную часть (как при сборке частичного заказа).
            ts = now(session)
            remainder = Item(
                sku=item.sku,
                seller_id=item.seller_id,
                title=item.title,
                qty=available,
                cell_size=item.cell_size,
                stored_pvz_id=pvz.id,
                accepted_at=ts,
                last_accrued_at=ts,
                status="stored",
                price_rub=item.price_rub,
            )
            session.add(remainder)
            session.flush()
            item.qty -= available
            session.flush()
            moving = remainder
        needed = cells_for(moving)
        dst = _candidate(session, pvz.city, {pvz.id}, needed)
        if dst is None:
            unresolved.append(
                {
                    "kind": "item",
                    "item_id": moving.id,
                    "sku": moving.sku,
                    "cells": needed,
                    "reason": "нет свободной ёмкости у соседних ПВЗ",
                }
            )
            continue
        cost = _move_item(session, moving, dst)
        relocation_total += cost
        evacuated.append(
            {
                "item": item_dict(session, moving),
                "sku": moving.sku,
                "to_pvz_id": dst.id,
                "to_code": dst.code,
                "cells": needed,
                "cost_rub": cost,
            }
        )
        notify(
            session,
            "pvz",
            f"Эвакуация с {pvz.code}: остаток {moving.sku} ({needed} яч.) перемещён в {dst.code}, "
            f"{cost:.0f} ₽ отнесены на маркетплейс.",
            payload={"kind": "node_failure_relocation", "item_id": moving.id, "from_pvz_id": pvz.id, "to_pvz_id": dst.id},
        )

    processed: set[int] = set()

    # 2. Заказы с точкой выдачи в отказавшем узле: переадресация на соседний ПВЗ.
    to_node = (
        session.query(Order)
        .filter(Order.status.in_(ACTIVE), Order.to_pvz == pvz.id)
        .order_by(Order.id)
        .all()
    )
    for order in to_node:
        processed.add(order.id)
        item = session.get(Item, order.item_id)
        cells = (item.cell_size if item else 1) * order.qty
        dst = _candidate(session, pvz.city, {pvz.id, order.from_pvz}, cells)
        if dst is None:
            inc = _open_incident(
                session,
                order,
                "node_down",
                f"Узел {pvz.code} недоступен. {reason} | переадресация невозможна: нет ёмкости у соседей",
            )
            unresolved.append(
                {
                    "kind": "order",
                    "order_id": order.id,
                    "code": order.code,
                    "reason": "нет свободной ёмкости для переадресации точки выдачи",
                    "incident_id": inc.id,
                }
            )
            notify(
                session,
                "seller",
                f"Заказ {order.code}: точка выдачи {pvz.code} недоступна, свободной ёмкости у соседей нет — "
                f"требуется решение оператора.",
                order_id=order.id,
                payload={"kind": "node_failure", "order_id": order.id, "pvz_id": pvz.id},
            )
            continue
        old_code = pvz.code
        order.to_pvz = dst.id
        if item is not None and item.stored_pvz_id == pvz.id:
            # Заказ в сборке: товар уже лежит в ячейках отказавшего узла — едет с заказом.
            cost = _move_item(session, item, dst)
            relocation_total += cost
            evacuated.append(
                {
                    "item": item_dict(session, item),
                    "sku": item.sku,
                    "to_pvz_id": dst.id,
                    "to_code": dst.code,
                    "cells": cells_for(item),
                    "cost_rub": cost,
                    "with_order": order.code,
                }
            )
        notify(
            session,
            "pvz",
            f"Заказ {order.code}: точка выдачи изменена {old_code} → {dst.code} (отказ узла). "
            f"Покупатель получит новый пункт по ссылке отслеживания.",
            order_id=order.id,
            payload={"kind": "pickup_reassigned", "order_id": order.id, "from_pvz_id": pvz.id, "to_pvz_id": dst.id},
        )
        _handle_order(
            session, order, pvz, reason, handled, unresolved, opened,
            note=f"точка выдачи изменена: {old_code} → {dst.code}",
        )

    # 3. Заказы с источником в отказавшем узле: партия переезжает целиком.
    from_node = (
        session.query(Order)
        .filter(Order.status.in_(ACTIVE), Order.from_pvz == pvz.id, Order.to_pvz != pvz.id)
        .order_by(Order.id)
        .all()
    )
    moved_batches: set[int] = set()
    for order in from_node:
        processed.add(order.id)
        item = session.get(Item, order.item_id)
        if item is None:
            _handle_order(session, order, pvz, reason, handled, unresolved, opened)
            continue
        if item.stored_pvz_id != pvz.id:
            # Товар уже уехал с узла (заказ в сборке в другой точке): маршрут строим
            # от фактического места хранения, а не от исторического источника.
            order.from_pvz = item.stored_pvz_id
            _handle_order(session, order, pvz, reason, handled, unresolved, opened)
            continue
        if item.id in moved_batches:
            _handle_order(session, order, pvz, reason, handled, unresolved, opened)
            continue
        # Партия одна на всех: кандидат не должен совпасть ни с отказавшим узлом,
        # ни с точкой выдачи любого из заказов этой партии (иначе маршрут 0 км).
        siblings = (
            session.query(Order)
            .filter(Order.item_id == item.id, Order.status.in_(ACTIVE), Order.from_pvz == pvz.id)
            .order_by(Order.id)
            .all()
        )
        needed = cells_for(item)
        exclude = {pvz.id} | {o.to_pvz for o in siblings}
        dst = _candidate(session, pvz.city, exclude, needed)
        if dst is None:
            inc = _open_incident(
                session,
                order,
                "node_down",
                f"Узел {pvz.code} недоступен. {reason} | эвакуация партии {item.sku} невозможна: нет ёмкости у соседей",
            )
            unresolved.append(
                {
                    "kind": "order",
                    "order_id": order.id,
                    "code": order.code,
                    "reason": f"нет ёмкости для эвакуации партии {item.sku}",
                    "incident_id": inc.id,
                }
            )
            continue
        cost = _move_item(session, item, dst)
        relocation_total += cost
        moved_batches.add(item.id)
        evacuated.append(
            {
                "item": item_dict(session, item),
                "sku": item.sku,
                "to_pvz_id": dst.id,
                "to_code": dst.code,
                "cells": needed,
                "cost_rub": cost,
                "with_orders": [
                    o.code for o in from_node if o.item_id == item.id
                ],
            }
        )
        # Все активные заказы этой партии теперь стартуют из нового места хранения.
        for sib in siblings:
            sib.from_pvz = dst.id
            processed.add(sib.id)
        notify(
            session,
            "pvz",
            f"Эвакуация с {pvz.code}: партия {item.sku} ({needed} яч.) перемещена в {dst.code}, "
            f"заказы отправляются оттуда. {cost:.0f} ₽ отнесены на маркетплейс.",
            payload={"kind": "node_failure_relocation", "item_id": item.id, "from_pvz_id": pvz.id, "to_pvz_id": dst.id},
        )
        _handle_order(
            session, order, pvz, reason, handled, unresolved, opened,
            note=f"партия {item.sku} перемещена в {dst.code}, источник заказа обновлён",
        )
        for sib in siblings:
            if sib.id == order.id:
                continue
            _handle_order(
                session, sib, pvz, reason, handled, unresolved, opened,
                note=f"партия {item.sku} перемещена в {dst.code}, источник заказа обновлён",
            )

    # 4. Заказы, чей маршрут проходит через узел (транзит).
    for order in (
        session.query(Order).filter(Order.status.in_(ACTIVE)).order_by(Order.id).all()
    ):
        if order.id in processed:
            continue
        if not _touch_pvz(session, order, pvz.id):
            continue
        processed.add(order.id)
        _handle_order(session, order, pvz, reason, handled, unresolved, opened)

    # 5. Сводный инцидент по узлу.
    summary = Incident(
        ts=now(session),
        order_id=None,
        type="node_down",
        description=(
            f"Узел {pvz.code} ({pvz.name}) выведен из строя. {reason}. "
            f"Эвакуировано партий: {len(evacuated)}; обработано заказов: {len(handled)}; "
            f"нерешённого: {len(unresolved)}; затраты на перемещения: {relocation_total:.0f} ₽."
        ),
        status="resolved" if not unresolved else "open",
    )
    session.add(summary)
    session.flush()

    return {
        "pvz": pvz_dict(pvz),
        "already_down": False,
        "incident": incident_dict(summary),
        "evacuated": evacuated,
        "orders": handled,
        "incidents": opened,
        "unresolved": unresolved,
        "relocation_total_rub": round(relocation_total, 2),
    }


def node_up(session: Session, pvz: Pvz) -> dict:
    """Вернуть ПВЗ в строй: маршрутизатор снова строит пути через точку."""
    pvz.is_active = True
    notify(
        session,
        "pvz",
        f"ПВЗ {pvz.code} возвращён в строй: маршрутизатор снова строит пути через точку. "
        f"Открытые инциденты можно закрыть перевыбором маршрута.",
        payload={"kind": "node_up", "pvz_id": pvz.id},
    )
    session.flush()
    return {"pvz": pvz_dict(pvz)}
