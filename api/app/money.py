"""Начисления и ledger: выдача, возврат, хранение, перемещение.

Правило: каждая строка ledger — денежный поток стороны заказа
(+ доход стороны, − расход). Сумма строк = −внешние затраты логистики:
переводы между участниками взаимно компенсируются и не создают прибыли цепочки.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import clock
from .config import p
from .models import Item, Ledger, Order, Pvz, Route, Seller


def add_ledger(
    session: Session,
    *,
    order_id: int | None,
    item_id: int | None,
    party: str,
    party_id: int | None,
    amount_rub: float,
    kind: str,
    note: str = "",
) -> Ledger:
    row = Ledger(
        ts=clock.now(session),
        order_id=order_id,
        item_id=item_id,
        party=party,
        party_id=party_id,
        amount_rub=round(float(amount_rub), 2),
        kind=kind,
        note=note,
    )
    session.add(row)
    session.flush()
    return row


def seller_pct(session: Session, item: Item) -> float:
    seller = session.get(Seller, item.seller_id)
    return float(seller.commission_pct if seller else p("fees", "commission_pct", default=12.0))


def delivery_fee(kind: str) -> float:
    if kind == "mesh":
        return float(p("fees", "buyer_delivery_fee_local_rub", default=149.0))
    return float(p("fees", "buyer_delivery_fee_hub_rub", default=159.0))


def quote(session: Session, item: Item, qty: int, route_kind: str) -> dict:
    goods = round(float(item.price_rub) * qty, 2)
    commission = round(goods * seller_pct(session, item) / 100.0, 2)
    fee = delivery_fee(route_kind)
    return {
        "goods_rub": goods,
        "delivery_fee_rub": fee,
        "buyer_total_rub": round(goods + fee, 2),
        "commission_rub": commission,
        "seller_net_rub": round(goods - commission, 2),
    }


def post_issue(session: Session, order: Order, item: Item, route: Route) -> list[Ledger]:
    """Выдача: оплата покупателя, комиссия, логистика, вознаграждение ПВЗ."""
    q = quote(session, item, order.qty, route.kind)
    dst = session.get(Pvz, order.to_pvz)
    reward = float(dst.op_fee)
    bonus = float(p("pvz", "mesh_bonus_rub", default=12.0)) if route.kind == "mesh" else 0.0
    logistics = float(route.total_cost_rub)
    goods = q["goods_rub"]
    commission = q["commission_rub"]
    fee = q["delivery_fee_rub"]

    rows = [
        add_ledger(session, order_id=order.id, item_id=item.id, party="buyer", party_id=None,
                   amount_rub=-goods, kind="goods", note="оплата товара (имитация платежа)"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="buyer", party_id=None,
                   amount_rub=-fee, kind="delivery", note="оплата доставки покупателем"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="seller", party_id=item.seller_id,
                   amount_rub=goods, kind="goods", note="выручка продавца от продажи"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="seller", party_id=item.seller_id,
                   amount_rub=-commission, kind="commission", note="комиссия маркетплейса"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="marketplace", party_id=None,
                   amount_rub=commission, kind="commission", note="комиссия маркетплейса"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="marketplace", party_id=None,
                   amount_rub=fee, kind="delivery", note="доставка, оплаченная покупателем"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="marketplace", party_id=None,
                   amount_rub=-logistics, kind="delivery", note=f"себестоимость маршрута {route.kind}"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="marketplace", party_id=None,
                   amount_rub=-reward, kind="pickup_fee", note="вознаграждение ПВЗ за выдачу"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="pvz", party_id=dst.id,
                   amount_rub=reward, kind="pickup_fee", note="вознаграждение за операцию выдачи"),
    ]
    if bonus:
        rows.append(
            add_ledger(session, order_id=order.id, item_id=item.id, party="marketplace", party_id=None,
                       amount_rub=-bonus, kind="bonus", note="бонус ПВЗ за заказ без хаба")
        )
        rows.append(
            add_ledger(session, order_id=order.id, item_id=item.id, party="pvz", party_id=dst.id,
                       amount_rub=bonus, kind="bonus", note="бонус за заказ без хаба")
        )
    order.buyer_total_rub = q["buyer_total_rub"]
    order.seller_net_rub = q["seller_net_rub"]
    session.flush()
    return rows


def post_return(session: Session, order: Order, item: Item, reverse_route: Route) -> list[Ledger]:
    """Возврат: покупателю — товар, продавцу — минус выручка, ПВЗ — удержание."""
    goods = round(float(item.price_rub) * order.qty, 2)
    commission = round(goods * seller_pct(session, item) / 100.0, 2)
    seller_net = round(goods - commission, 2)
    reverse_fee = float(p("fees", "return_reverse_fee_rub", default=60.0))
    reverse_cost = float(reverse_route.total_cost_rub)

    rows = [
        add_ledger(session, order_id=order.id, item_id=item.id, party="buyer", party_id=None,
                   amount_rub=goods, kind="refund", note="возврат стоимости товара покупателю"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="seller", party_id=item.seller_id,
                   amount_rub=-seller_net, kind="refund", note="сторно выручки продавца"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="marketplace", party_id=None,
                   amount_rub=-commission, kind="refund", note="возврат комиссии продавцу"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="pvz", party_id=order.to_pvz,
                   amount_rub=-reverse_fee, kind="penalty", note="удержание за обратную логистику"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="marketplace", party_id=None,
                   amount_rub=reverse_fee, kind="penalty", note="компенсация обратной логистики"),
        add_ledger(session, order_id=order.id, item_id=item.id, party="marketplace", party_id=None,
                   amount_rub=-reverse_cost, kind="delivery", note="себестоимость обратного маршрута"),
    ]
    session.flush()
    return rows


def charge_storage(session: Session, item: Item, days: float, pvz_id: int | None = None) -> float:
    """Плата за хранение за days дней; возвращает сумму и пишет ledger."""
    pvz_id = pvz_id or item.stored_pvz_id
    pvz = session.get(Pvz, pvz_id)
    if pvz is None or days <= 0:
        return 0.0
    amount = round(float(pvz.storage_rate_day) * float(item.cell_size) * float(item.qty) * float(days), 2)
    if amount <= 0:
        return 0.0
    add_ledger(session, order_id=None, item_id=item.id, party="marketplace", party_id=None,
               amount_rub=-amount, kind="storage", note=f"хранение {days:g} дн. на {pvz.code}")
    add_ledger(session, order_id=None, item_id=item.id, party="pvz", party_id=pvz.id,
               amount_rub=amount, kind="storage", note=f"хранение {days:g} дн., {item.cell_size}×{item.qty} яч.")
    return amount


def charge_relocation(session: Session, item: Item, from_pvz: Pvz, to_pvz: Pvz) -> float:
    amount = float(p("pvz", "relocation_cost_rub", default=60.0))
    add_ledger(session, order_id=None, item_id=item.id, party="marketplace", party_id=None,
               amount_rub=-amount, kind="relocation",
               note=f"перемещение {from_pvz.code}→{to_pvz.code} (внешний перевозчик)")
    return amount


def ledger_sum(session: Session, order_id: int, party: str | None = None) -> float:
    q = session.query(Ledger).filter(Ledger.order_id == order_id)
    if party:
        q = q.filter(Ledger.party == party)
    return round(sum(row.amount_rub for row in q.all()), 2)
