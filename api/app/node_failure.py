"""Отказ узла сети: блокировка ПВЗ, эвакуация остатков, переадресация заказов (UC-5).

Процедура при выводе точки из строя:
1. точка помечается неактивной — маршрутизатор перестаёт строить пути через неё;
2. свободные остатки (status=stored/returned, а также незарезервированный хвост
   партии) эвакуируются в тот же город; сосед выбирается по комплексному скору
   «свободные ячейки − расстояние» (см. `_rank_candidates`). Если партия целиком
   не влезает ни в одну точку — она дробится по ближайшим, части получают
   parent_batch_id;
3. активные заказы с точкой выдачи в отказавшем узле переадресуются на соседний ПВЗ
   (товар в сборке переезжает вместе с заказом, ячейки переносятся);
4. активные заказы с источником в отказавшем узле: партия перемещается (целиком или
   частями по заказам), источник заказа обновляется, маршрут перевыбирается;
5. в unresolved[] попадает только то, что действительно нельзя разместить в городе:
   заказы/партии, для которых после дробления не осталось свободных ячеек.

Числа: relocation_cost_rub, скорость и веса скора — параметры economics/params.yaml.
Тариф перемещения внутри города — единый (60 ₽ за рейс): расстояние влияет на выбор
соседа и на оценку времени, но не на сумму проводки.
"""
from __future__ import annotations

import math

from sqlalchemy import update
from sqlalchemy.orm import Session

from . import money
from .clock import now
from .config import p
from .models import Incident, Item, Order, Pvz
from .notify import notify
from .routers.incidents import ACTIVE, _open_incident, _touch_pvz, reroute_order
from .serializers import incident_dict, item_dict, order_dict, pvz_dict, reserved_qty, route_dict
from .storage import cells_for, occupy, release

_EARTH_RADIUS_KM = 6371.0088  # средний радиус Земли, км (физическая константа, не тариф)


def _distance_km(a: Pvz | None, b: Pvz | None) -> float:
    """Расстояние между точками по прямой с коэффициентом удлинения улиц.

    Координаты — приближённые центры районов из seed-данных. Если координат нет
    (старая БД без миграции), расстояние считается нулевым: выбор тогда идёт
    только по свободной ёмкости, как раньше.
    """
    if a is None or b is None or a.lat is None or a.lng is None or b.lat is None or b.lng is None:
        return 0.0
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dphi = phi2 - phi1
    dlambda = math.radians(b.lng - a.lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    straight = 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))
    return round(straight * float(p("pvz", "road_detour_factor")), 2)


def _leg(origin: Pvz | None, dst: Pvz) -> dict:
    """Оценка перегона: расстояние и время при тарифной скорости курьера."""
    distance = _distance_km(origin, dst)
    speed = float(p("pvz", "relocation_speed_kmh"))
    return {"distance_km": distance, "move_hours": round(distance / speed, 2) if speed else 0.0}


def _rank_candidates(session: Session, city: str, origin: Pvz | None, exclude_ids: set[int]) -> list[dict]:
    """Соседи того же города, отсортированные по скору (больше — лучше).

    Скор = свободные_ячейки × вес_ёмкости − расстояние_км × вес_расстояния.
    Абсолютные свободные ячейки важнее процента загрузки: точка с 40 свободными
    ячейками полезнее для эвакуации, чем маленькая с 30% загрузки (5 ячеек).
    """
    w_capacity = float(p("pvz", "evacuation_capacity_weight"))
    w_distance = float(p("pvz", "evacuation_distance_weight"))
    rows = (
        session.query(Pvz)
        .filter(
            Pvz.city == city,
            Pvz.is_active.is_(True),
            Pvz.is_hub.is_(False),
        )
        .all()
    )
    ranked: list[dict] = []
    for pvz in rows:
        if pvz.id in exclude_ids:
            continue
        free = pvz.capacity_cells - pvz.used_cells
        if free <= 0:
            continue
        leg = _leg(origin, pvz)
        score = round(free * w_capacity - leg["distance_km"] * w_distance, 2)
        ranked.append({"pvz": pvz, "free_cells": free, "score": score, **leg})
    ranked.sort(key=lambda c: (-c["score"], c["distance_km"], c["pvz"].id))
    return ranked


def _best(session: Session, city: str, origin: Pvz | None, exclude_ids: set[int], needed_cells: int) -> dict | None:
    """Лучший сосед, в которого целиком влезает needed_cells."""
    for cand in _rank_candidates(session, city, origin, exclude_ids):
        if cand["free_cells"] >= needed_cells:
            return cand
    return None


def _plan_split(candidates: list[dict], qty: int, cell_size: int) -> tuple[list[dict], int]:
    """Жадно разложить qty единиц по соседям; вернуть (части, остаток единиц).

    Сначала пробуем одну точку (её выбирает `_best`), сюда попадаем только если
    целиком не влезает никуда: тогда берём лучших по скору, пока не кончится
    товар или свободные ячейки. Остаток — реальный дефицит города.
    """
    allocation: list[dict] = []
    remaining = qty
    for cand in candidates:
        if remaining <= 0:
            break
        take = min(remaining, cand["free_cells"] // cell_size)
        if take <= 0:
            continue
        allocation.append(
            {
                "pvz": cand["pvz"],
                "qty": take,
                "cells": take * cell_size,
                "score": cand["score"],
                "distance_km": cand["distance_km"],
                "move_hours": cand["move_hours"],
                "orders": [],
            }
        )
        remaining -= take
    return allocation, remaining


def _plan_batch(
    session: Session,
    item: Item,
    siblings: list[Order],
    origin: Pvz,
    exclude_ids: set[int],
) -> tuple[list[dict], list[Order], int]:
    """План эвакуации партии с активными заказами.

    Заказ неделим: его qty целиком едет в одну точку. Если партия не влезает
    целиком, сначала раскладываются заказы (каждый — в лучшего соседа, где есть
    место), затем свободный хвост партии. Возвращает (части, заказы без места,
    остаток единиц без места).
    """
    cell_size = item.cell_size
    candidates = _rank_candidates(session, origin.city, origin, exclude_ids)
    needed = cell_size * item.qty
    for cand in candidates:
        if cand["free_cells"] >= needed:
            return (
                [
                    {
                        "pvz": cand["pvz"],
                        "qty": item.qty,
                        "cells": needed,
                        "score": cand["score"],
                        "distance_km": cand["distance_km"],
                        "move_hours": cand["move_hours"],
                        "orders": list(siblings),
                    }
                ],
                [],
                0,
            )

    free_left = {cand["pvz"].id: cand["free_cells"] for cand in candidates}
    buckets: dict[int, dict] = {}
    orphan_orders: list[Order] = []

    def bucket_for(cand: dict) -> dict:
        return buckets.setdefault(
            cand["pvz"].id,
            {
                "pvz": cand["pvz"],
                "qty": 0,
                "cells": 0,
                "score": cand["score"],
                "distance_km": cand["distance_km"],
                "move_hours": cand["move_hours"],
                "orders": [],
            },
        )

    for order in siblings:
        order_cells = cell_size * order.qty
        picked = None
        for cand in candidates:
            if cand["pvz"].id == order.to_pvz:
                continue  # точка выдачи этого заказа: маршрут был бы нулевой
            if free_left[cand["pvz"].id] >= order_cells:
                picked = cand
                break
        if picked is None:
            orphan_orders.append(order)
            continue
        part = bucket_for(picked)
        part["qty"] += order.qty
        part["cells"] += order_cells
        part["orders"].append(order)
        free_left[picked["pvz"].id] -= order_cells

    tail = item.qty - sum(order.qty for order in siblings)
    for cand in candidates:
        if tail <= 0:
            break
        take = min(tail, free_left[cand["pvz"].id] // cell_size)
        if take <= 0:
            continue
        part = bucket_for(cand)
        part["qty"] += take
        part["cells"] += take * cell_size
        free_left[cand["pvz"].id] -= take * cell_size
        tail -= take

    allocation = [buckets[cand["pvz"].id] for cand in candidates if cand["pvz"].id in buckets]
    return allocation, orphan_orders, tail + sum(o.qty for o in orphan_orders)


def _move_parts(
    session: Session,
    item: Item,
    allocation: list[dict],
    deficit_qty: int = 0,
) -> tuple[list[tuple[Item, dict]], float]:
    """Разместить партию по плану: ячейки, адреса, заказы, проводки.

    Первая часть переиспользует строку item (её id остаётся в истории и у заказов),
    остальные части создаются как новые строки с parent_batch_id корневой партии.
    Если есть deficit_qty, item остаётся на отказавшем узле с этим остатком.
    """
    src = session.get(Pvz, item.stored_pvz_id)
    total_cells = cells_for(item)
    release(src, total_cells)
    root_id = item.parent_batch_id or item.id
    ts = now(session)
    reuse_original = deficit_qty == 0
    made: list[tuple[Item, dict]] = []
    total_cost = 0.0

    for index, part_plan in enumerate(allocation):
        dst: Pvz = part_plan["pvz"]
        if reuse_original and index == 0:
            part = item
            part.qty = part_plan["qty"]
            part.stored_pvz_id = dst.id
        else:
            part = Item(
                sku=item.sku,
                seller_id=item.seller_id,
                title=item.title,
                qty=part_plan["qty"],
                cell_size=item.cell_size,
                stored_pvz_id=dst.id,
                accepted_at=ts,
                last_accrued_at=ts,
                status="stored" if item.status == "reserved" and not part_plan["orders"] else item.status,
                price_rub=item.price_rub,
                parent_batch_id=root_id,
            )
            session.add(part)
            session.flush()
        # Хранение после эвакуации начинается заново с новой даты приёмки,
        # как и при обычном перемещении остатка (storage.relocate).
        part.accepted_at = ts
        part.last_accrued_at = ts
        part.relocate_alert_sent = False
        occupy(dst, part_plan["cells"])
        total_cost += money.charge_relocation(session, part, src, dst)
        for order in part_plan["orders"]:
            order.item_id = part.id
            order.from_pvz = dst.id
        made.append((part, part_plan))

    if deficit_qty > 0:
        item.qty = deficit_qty
        occupy(src, item.cell_size * deficit_qty)

    return made, total_cost


def _move_item(session: Session, item: Item, dst: Pvz) -> float:
    """Перенести партию целиком между точками: ячейки, адрес хранения, проводка 60 ₽."""
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
    # Атомарный захват операции: два параллельных disable не должны дважды
    # эвакуировать остатки и дважды писать проводки. UPDATE ... WHERE is_active=1
    # выполняет ровно один запрос, второй видит rowcount=0 и выходит как already_down.
    claimed = session.execute(
        update(Pvz).where(Pvz.id == pvz.id, Pvz.is_active.is_(True)).values(is_active=False)
    )
    if claimed.rowcount != 1:
        session.expire(pvz)
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
                continue  # партия уедет целиком или частями вместе со своими заказами (шаг 3)
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
                parent_batch_id=item.parent_batch_id or item.id,
            )
            session.add(remainder)
            session.flush()
            item.qty -= available
            session.flush()
            moving = remainder

        needed = cells_for(moving)
        candidates = _rank_candidates(session, pvz.city, pvz, {pvz.id})
        whole = next((cand for cand in candidates if cand["free_cells"] >= needed), None)
        allocation = [
            {
                "pvz": whole["pvz"],
                "qty": moving.qty,
                "cells": needed,
                "score": whole["score"],
                "distance_km": whole["distance_km"],
                "move_hours": whole["move_hours"],
                "orders": [],
            }
        ] if whole else []
        deficit = 0
        if not allocation:
            allocation, deficit = _plan_split(candidates, moving.qty, moving.cell_size)
        if not allocation:
            unresolved.append(
                {
                    "kind": "item",
                    "item_id": moving.id,
                    "sku": moving.sku,
                    "cells": needed,
                    "qty": moving.qty,
                    "reason": "нет свободной ёмкости у соседних ПВЗ",
                }
            )
            continue
        parts, cost = _move_parts(session, moving, allocation, deficit)
        relocation_total += cost
        for part, plan in parts:
            evacuated.append(
                {
                    "item": item_dict(session, part),
                    "sku": part.sku,
                    "to_pvz_id": plan["pvz"].id,
                    "to_code": plan["pvz"].code,
                    "cells": plan["cells"],
                    "qty": plan["qty"],
                    "cost_rub": round(cost / len(parts), 2),
                    "distance_km": plan["distance_km"],
                    "move_hours": plan["move_hours"],
                    "parent_batch_id": part.parent_batch_id,
                    "split": len(allocation) > 1,
                }
            )
            notify(
                session,
                "pvz",
                f"Эвакуация с {pvz.code}: остаток {part.sku} ({plan['cells']} яч.) перемещён в "
                f"{plan['pvz'].code} ({plan['distance_km']:.1f} км). Стоимость рейса отнесена на маркетплейс.",
                payload={
                    "kind": "node_failure_relocation",
                    "item_id": part.id,
                    "parent_batch_id": part.parent_batch_id,
                    "from_pvz_id": pvz.id,
                    "to_pvz_id": plan["pvz"].id,
                    "distance_km": plan["distance_km"],
                },
            )
        if deficit:
            unresolved.append(
                {
                    "kind": "item",
                    "item_id": moving.id,
                    "sku": moving.sku,
                    "cells": moving.cell_size * deficit,
                    "qty": deficit,
                    "reason": "после дробления не хватило ёмкости в городе",
                }
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
        picked = _best(session, pvz.city, pvz, {pvz.id, order.from_pvz}, cells)
        if picked is None:
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
        dst: Pvz = picked["pvz"]
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
                    "qty": item.qty,
                    "cost_rub": cost,
                    "distance_km": picked["distance_km"],
                    "move_hours": picked["move_hours"],
                    "parent_batch_id": item.parent_batch_id,
                    "with_order": order.code,
                    "split": False,
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

    # 3. Заказы с источником в отказавшем узле: партия переезжает целиком или частями.
    from_node = (
        session.query(Order)
        .filter(Order.status.in_(ACTIVE), Order.from_pvz == pvz.id, Order.to_pvz != pvz.id)
        .order_by(Order.id)
        .all()
    )
    groups: dict[int, list[Order]] = {}
    for order in from_node:
        processed.add(order.id)
        item = session.get(Item, order.item_id)
        if item is None or item.stored_pvz_id != pvz.id:
            # Товара нет или он уже уехал с узла (заказ в сборке в другой точке):
            # маршрут строим от фактического места хранения, а не от исторического источника.
            if item is not None:
                order.from_pvz = item.stored_pvz_id
            _handle_order(session, order, pvz, reason, handled, unresolved, opened)
            continue
        groups.setdefault(item.id, []).append(order)

    for item_id, group in groups.items():
        item = session.get(Item, item_id)
        if item is None or item.stored_pvz_id != pvz.id:
            for order in group:
                if item is not None:
                    order.from_pvz = item.stored_pvz_id
                _handle_order(session, order, pvz, reason, handled, unresolved, opened)
            continue

        siblings = (
            session.query(Order)
            .filter(Order.item_id == item.id, Order.status.in_(ACTIVE), Order.from_pvz == pvz.id)
            .order_by(Order.id)
            .all()
        )
        needed = cells_for(item)
        # Кандидат не должен совпасть ни с отказавшим узлом, ни с точкой выдачи
        # любого из заказов этой партии (иначе маршрут 0 км).
        exclude = {pvz.id} | {o.to_pvz for o in siblings}
        allocation, orphan_orders, deficit_qty = _plan_batch(session, item, siblings, pvz, exclude)
        if not allocation:
            for order in group:
                inc = _open_incident(
                    session,
                    order,
                    "node_down",
                    f"Узел {pvz.code} недоступен. {reason} | эвакуация партии {item.sku} невозможна: "
                    "нет ёмкости у соседей",
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

        parts, cost = _move_parts(session, item, allocation, deficit_qty)
        relocation_total += cost
        for part, plan in parts:
            evacuated.append(
                {
                    "item": item_dict(session, part),
                    "sku": part.sku,
                    "to_pvz_id": plan["pvz"].id,
                    "to_code": plan["pvz"].code,
                    "cells": plan["cells"],
                    "qty": plan["qty"],
                    "cost_rub": round(cost / len(parts), 2),
                    "distance_km": plan["distance_km"],
                    "move_hours": plan["move_hours"],
                    "parent_batch_id": part.parent_batch_id,
                    "with_orders": [o.code for o in plan["orders"]],
                    "split": len(allocation) > 1,
                }
            )
        moved_codes = {o.id: plan for plan in allocation for o in plan["orders"]}
        for order in group:
            if order.id in moved_codes:
                plan = moved_codes[order.id]
                _handle_order(
                    session, order, pvz, reason, handled, unresolved, opened,
                    note=f"партия {item.sku} перемещена в {plan['pvz'].code}, источник заказа обновлён",
                )
            else:
                inc = _open_incident(
                    session,
                    order,
                    "node_down",
                    f"Узел {pvz.code} недоступен. {reason} | для заказа не хватило ёмкости при дроблении партии",
                )
                unresolved.append(
                    {
                        "kind": "order",
                        "order_id": order.id,
                        "code": order.code,
                        "reason": f"нет ёмкости для части партии {item.sku}",
                        "incident_id": inc.id,
                    }
                )
        notify(
            session,
            "pvz",
            f"Эвакуация с {pvz.code}: партия {item.sku} ({needed} яч.) "
            + (
                "перемещена целиком: " + allocation[0]["pvz"].code
                if len(allocation) == 1
                else "размещена частями: "
                + ", ".join(f"{plan['pvz'].code} — {plan['qty']} шт." for plan in allocation)
            )
            + ". Заказы отправляются из новых точек.",
            payload={
                "kind": "node_failure_relocation",
                "item_id": item.id,
                "parent_batch_id": parts[0][0].parent_batch_id,
                "from_pvz_id": pvz.id,
                "to_pvz_ids": [plan["pvz"].id for plan in allocation],
                "split": len(allocation) > 1,
            },
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