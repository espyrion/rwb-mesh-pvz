"""Генерация docs/ECON_REPORT.md из модели (единый источник чисел).

Запуск:  python economics/report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from economics.model import compare, load_params, order_economics, pvz_pnl, route_cost, storage_cost

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "ECON_REPORT.md"


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def pct(value: float) -> str:
    return f"{value:+.2f}%".replace(".", ",")


def party_rows(scenario: dict) -> str:
    pvz, market, seller, buyer = scenario["pvz"], scenario["marketplace"], scenario["seller"], scenario["buyer"]
    chain = scenario["chain"]["profit_rub"]
    return "\n".join(
        [
            f"| ПВЗ | {money(pvz['revenue_total'])} | {money(pvz['costs_total'])} | {money(pvz['profit_rub'])} |",
            f"| Маркетплейс | {money(market['revenue_total'])} | {money(market['costs_total'])} | {money(market['profit_rub'])} |",
            f"| Продавец | {money(seller['revenue_total'])} | {money(seller['costs_total'])} | {money(seller['profit_rub'])} |",
            f"| Покупатель (платит) | — | {money(buyer['total_rub'])} | срок {buyer['hours']:.1f} ч |",
            f"| **Цепочка (без покупателя)** | | | **{money(chain)}** |",
        ]
    )


def pvz_breakeven_ops(params: dict) -> int:
    fixed = float(params["pvz"]["fixed_cost_day_rub"])
    cells = float(params["pvz"]["avg_cells_per_order"])
    days = float(params["scenario_defaults"]["storage_days_typical"])
    for ops in range(1, 500):
        result = pvz_pnl(
            params,
            days=1,
            ops=ops,
            qty=ops,
            storage_cell_days=ops * cells * days,
            mesh_ops=int(round(ops * 0.3)),
        )
        if result["profit_rub"] >= 0:
            return ops
    return -1


def storage_breakeven_days(params: dict, kind: str = "mesh", goods: float = 2500.0) -> float:
    """Срок хранения, при котором прибыль маркетплейса по заказу обнуляется."""
    cells = float(params["pvz"]["avg_cells_per_order"])
    rate = float(params["pvz"]["storage_rate_day_rub"])
    base = order_economics(params, kind=kind, goods_rub=goods, cells=cells, storage_days=0.0)
    margin = base["marketplace"]["profit_rub"]
    return round(margin / (rate * cells), 2)


def build() -> str:
    params = load_params()
    goods = float(params["scenario_defaults"]["goods_price_rub"])
    cells = float(params["pvz"]["avg_cells_per_order"])
    storage_days = float(params["scenario_defaults"]["storage_days_typical"])

    mesh = order_economics(params, kind="mesh", goods_rub=goods, cells=cells, storage_days=storage_days)
    hub = order_economics(params, kind="hub", goods_rub=goods, cells=cells, storage_days=storage_days)
    cmp = compare(params, hub, mesh)
    city_be = pvz_breakeven_ops(params)
    storage_be = storage_breakeven_days(params)

    low_load = pvz_pnl(params, days=1, ops=20, qty=20, storage_cell_days=20 * cells * storage_days, mesh_ops=6)
    normal_load = pvz_pnl(params, days=1, ops=60, qty=60, storage_cell_days=60 * cells * storage_days, mesh_ops=18)
    long_storage = order_economics(params, kind="mesh", goods_rub=goods, cells=cells, storage_days=25.0)

    reverse_fee = float(params["fees"]["return_reverse_fee_rub"])
    ret_pvz = round(mesh["pvz"]["profit_rub"] - reverse_fee, 2)
    ret_mkt = round(mesh["marketplace"]["profit_rub"] - route_cost(params, "mesh") + reverse_fee, 2)
    incident_mkt = round(mesh["marketplace"]["profit_rub"] - route_cost(params, "mesh"), 2)

    intercity_route = route_cost(params, "mesh") + float(params["logistics"]["trunk_cost_per_order_rub"])
    intercity_profit = round(
        goods * float(params["fees"]["commission_pct"]) / 100 + float(params["fees"]["buyer_delivery_fee_local_rub"])
        - intercity_route - float(params["pvz"]["op_fee_rub"]) - storage_cost(params, cells, storage_days)
        - float(params["pvz"]["mesh_bonus_rub"]),
        2,
    )

    text = f"""# Экономическая модель и отчёт о расчётах

> **Дисклеймер.** Все тарифы, бонусы, штрафы и лимиты ответственности в этом документе — параметры
> предложенной командой модели. Они не являются действующими тарифами RWB или его партнёров.
> Данные синтетические; реальные перевозки, платежи и доступы к производственным системам не используются.
> Источник параметров: `economics/params.yaml` (версия в репозитории). Отчёт пересобирается командой
> `python economics/report.py`.

## 1. Что считается

Модель считает четыре стороны одного заказа и их результат:

| Сторона | Что считаем |
|---|---|
| ПВЗ | вознаграждение за операции, плата за хранение, бонус за заказ без хаба; затраты на приёмку/сборку, удержания, условно-постоянные затраты |
| Маркетплейс | комиссия и плата за доставку; логистика, плата за хранение, вознаграждение ПВЗ |
| Продавец | выручка от товара минус комиссия маркетплейса |
| Покупатель | цена товара + доставка, срок в часах |

Переводы между участниками (комиссия, вознаграждение ПВЗ, плата за хранение) **не создают прибыли цепочки**:
результат цепочки = прибыль ПВЗ + прибыль маркетплейса + прибыль продавца = оплата покупателя − внешние
затраты логистики − операционные затраты ПВЗ. Это проверяется тестом `economics/tests/test_model.py`.

## 2. Допущения и единицы

* валюта — рубли с НДС, точность 2 знака; время — часы (срок) и дни (хранение);
* ёмкость ПВЗ — ячейки; заказ занимает `cell_size × qty` ячеек;
* плата за хранение — ₽ за ячейку в день, начисляется маркетплейсом в пользу ПВЗ;
* магистраль считается консолидированно: 420 ₽ за рейс ÷ 40 отправлений = **{params['logistics']['trunk_cost_per_order_rub']} ₽/заказ**;
* локальная доставка — {params['logistics']['local_delivery_cost_rub']} ₽ на заказ (город, 3 ч);
* схема через хаб — {params['logistics']['hub_handling_rub']} ₽ обработка + {params['logistics']['trunk_cost_per_order_rub']} ₽ магистраль
  + {params['logistics']['last_mile_cost_rub']} ₽ последняя миля = **{route_cost(params, 'hub')} ₽**, 30 ч;
* комиссия маркетплейса — {params['fees']['commission_pct']}% от товара; доставка для покупателя —
  {params['fees']['buyer_delivery_fee_local_rub']} ₽ (mesh) / {params['fees']['buyer_delivery_fee_hub_rub']} ₽ (hub);
* условно-постоянные затраты ПВЗ — {params['pvz']['fixed_cost_day_rub']} ₽/день (аренда, ФОТ, оборудование);
* средний заказ: товар {money(goods)} ₽, {cells} ячейки, {storage_days:g} дня хранения, срок возврата
  {params['fees']['return_window_days']} дней.

## 3. Формулы

```
логистика(mesh) = local_delivery_cost
логистика(hub)  = hub_handling + trunk_per_order + last_mile
хранение        = ячейки × дни × storage_rate_day
ПВЗ: доход   = op_fee × операции + хранение + mesh_bonus × mesh-операции
     затраты = acceptance × единицы + assemble × операции + fixed × дни + удержания
маркетплейс: доход   = товар × commission_pct + доставка_покупателя
             затраты = логистика + хранение + op_fee + mesh_bonus
продавец:    прибыль = товар − товар × commission_pct
покупатель:  платёж  = товар + доставка_покупателя
```

Все формулы реализованы в `economics/model.py`: `route_cost`, `storage_cost`, `pvz_pnl`,
`marketplace_pnl`, `seller_pnl`, `buyer_cost`, `order_economics`, `compare`.

## 4. Городской заказ: базовая схема (хаб) и предложенная (mesh)

Товар {money(goods)} ₽, {cells} ячейки, {storage_days:g} дня хранения, один заказ.

### 4.1 Базовая схема «хаб + магистраль»

| Сторона | Доход, ₽ | Затраты, ₽ | Результат |
|---|---|---|---|
{party_rows(hub)}
| Показатель | Значение |
|---|---|
| Логистика | {money(hub['route']['cost_rub'])} ₽ / {hub['route']['hours']:.1f} ч |
| Хранение | {money(hub['storage']['cost_rub'])} ₽ |
| Покупатель платит | {money(hub['buyer']['total_rub'])} ₽ |

### 4.2 Предложенная схема mesh (локально, без хаба)

| Сторона | Доход, ₽ | Затраты, ₽ | Результат |
|---|---|---|---|
{party_rows(mesh)}
| Показатель | Значение |
|---|---|
| Логистика | {money(mesh['route']['cost_rub'])} ₽ / {mesh['route']['hours']:.1f} ч |
| Хранение | {money(mesh['storage']['cost_rub'])} ₽ |
| Покупатель платит | {money(mesh['buyer']['total_rub'])} ₽ |

### 4.3 Сравнение

| Показатель | База (hub) | Предложение (mesh) | Изменение |
|---|---|---|---|
| Логистика на заказ, ₽ | {money(hub['route']['cost_rub'])} | {money(mesh['route']['cost_rub'])} | {money(mesh['route']['cost_rub'] - hub['route']['cost_rub'])} |
| Срок, ч | {hub['route']['hours']:.1f} | {mesh['route']['hours']:.1f} | {mesh['route']['hours'] - hub['route']['hours']:+.1f} |
| Платёж покупателя, ₽ | {money(hub['buyer']['total_rub'])} | {money(mesh['buyer']['total_rub'])} | {money(mesh['buyer']['total_rub'] - hub['buyer']['total_rub'])} ({pct(cmp['buyer_delta_pct'])}) |
| Прибыль ПВЗ, ₽ | {money(hub['pvz']['profit_rub'])} | {money(mesh['pvz']['profit_rub'])} | {money(mesh['pvz']['profit_rub'] - hub['pvz']['profit_rub'])} |
| Прибыль маркетплейса, ₽ | {money(hub['marketplace']['profit_rub'])} | {money(mesh['marketplace']['profit_rub'])} | {money(mesh['marketplace']['profit_rub'] - hub['marketplace']['profit_rub'])} |
| Прибыль цепочки, ₽ | {money(hub['chain']['profit_rub'])} | {money(mesh['chain']['profit_rub'])} | {money(mesh['chain']['profit_rub'] - hub['chain']['profit_rub'])} |

**Границы приемлемости (заданы командой):** удорожание для покупателя не более
{params['buyer_acceptance']['max_cost_increase_pct']}% и удлинение не более
{params['buyer_acceptance']['max_time_increase_hours']} ч. Для городского заказа mesh **дешевле на
{money(-cmp['buyer_delta_rub'])} ₽ ({pct(-cmp['buyer_delta_pct'])}) и быстрее на {-cmp['buyer_delta_hours']:.1f} ч**, поэтому
схема приемлема: `viable = {cmp['viable']}`.

## 5. Межгородской заказ

Локального маршрута между городами нет, поэтому «в лоб» заказ идёт через хаб: {money(hub['buyer']['total_rub'])} ₽
и {hub['route']['hours']:.0f} ч. Предложение mesh для межгорода — **предварительное размещение остатка** в ПВЗ города
получателя: магистраль оплачивается консолидированно ({params['logistics']['trunk_cost_per_order_rub']} ₽/заказ),
а выдача обслуживается локально.

| Показатель | База: каждый заказ через хаб | Предложение: остаток заранее в городе |
|---|---|---|
| Логистика на заказ, ₽ | {money(route_cost(params, 'hub'))} | {money(intercity_route)} |
| Срок, ч | 30.0 | 3.0 |
| Платёж покупателя, ₽ | {money(goods + params['fees']['buyer_delivery_fee_hub_rub'])} | {money(goods + params['fees']['buyer_delivery_fee_local_rub'])} |
| Прибыль маркетплейса, ₽ | {money(hub['marketplace']['profit_rub'])} | {money(intercity_profit)} |

Изменение для покупателя: **−{money(params['fees']['buyer_delivery_fee_hub_rub'] - params['fees']['buyer_delivery_fee_local_rub'])} ₽
(−{(params['fees']['buyer_delivery_fee_hub_rub'] - params['fees']['buyer_delivery_fee_local_rub']) / (goods + params['fees']['buyer_delivery_fee_hub_rub']) * 100:.1f}%)**
и **−27 ч**. Условие: остаток должен быть размещён заранее; при отсутствии остатка схема недоступна и заказ
идёт через хаб (это ограничение явно показывается в UI и API).

## 6. Выгодность ПВЗ и безубыточность маркетплейса

### 6.1 Порог загрузки ПВЗ

При фиксированных затратах {money(params['pvz']['fixed_cost_day_rub'])} ₽/день и вкладе с заказа
(вознаграждение + хранение + доля mesh-бонуса − переменные затраты) ПВЗ выходит в ноль при
**{city_be} операциях в день**. Пропускная способность — {params['pvz']['ops_per_day_capacity']} операций/день,
то есть запас {params['pvz']['ops_per_day_capacity'] - city_be} операций.

| Загрузка ПВЗ | Операций/день | Прибыль ПВЗ/день, ₽ |
|---|---|---|
| Низкая | 20 | {money(low_load['profit_rub'])} |
| Рабочая | 60 | {money(normal_load['profit_rub'])} |
| Порог безубыточности | {city_be} | ≈ 0 |

### 6.2 Порог длительного хранения для маркетплейса

Плата за хранение растёт линейно: {params['pvz']['storage_rate_day_rub']} ₽ × {cells} ячейки = {money(params['pvz']['storage_rate_day_rub'] * cells)} ₽/день.
При сроке {storage_days:g} дня маркетплейс зарабатывает {money(mesh['marketplace']['profit_rub'])} ₽ на заказе, а при
**{storage_be} днях** прибыль обнуляется. Отсюда правило: на {params['pvz']['long_storage_threshold_days']} день —
уведомление ПВЗ, предложение перемещения; дальше — решение оператора (перемещение/уценка/возврат).
Для сравнения, при 25 днях хранения прибыль маркетплейса падает до {money(long_storage['marketplace']['profit_rub'])} ₽.

## 7. Устойчивость модели

| Сценарий | ПВЗ | Маркетплейс | Покупатель | Вывод |
|---|---|---|---|---|
| Низкая загрузка (20/день) | {money(low_load['profit_rub'])} ₽/день | без изменений на заказ | без изменений | ПВЗ убыточен → нужен порог партнёра или субсидия |
| Длительное хранение (25 дней) | +{money(long_storage['pvz']['profit_rub'] - mesh['pvz']['profit_rub'])} ₽ к заказу | {money(long_storage['marketplace']['profit_rub'])} ₽ на заказ | без изменений | нужен порог и перемещение остатка |
| Возврат после выдачи | {money(ret_pvz)} ₽ за заказ (удержание {money(reverse_fee)} ₽) | {money(ret_mkt)} ₽ на заказ | теряет доставку {money(params['fees']['buyer_delivery_fee_local_rub'])} ₽ | удержание покрывает обратную логистику |
| Инцидент: сегмент отключён | без изменений | {money(incident_mkt)} ₽ (перепробег {money(route_cost(params, 'mesh'))} ₽) | срок +3 ч | автоперевыбор маршрута сохраняет SLA |

## 8. Что не включено в прототип

* реальные платежи, эквайринг и налоги (платёжный шлюз — заглушка, операции помечены в UI);
* аренда/ФОТ сверх условно-постоянной ставки, амортизация оборудования, маркетинг;
* страховка товара, потери и недостачи сверх удержаний модели;
* магистральные рейсы как расписание (стоимость консолидирована в пересчёте на заказ);
* клиентское приложение покупателя (интерфейс покупателя — только расчёт стоимости/срока в API).

## 9. Выводы

1. В пределах города mesh выгоден всем: ПВЗ получает бонус и хранение, маркетплейс экономит
   {money(hub['route']['cost_rub'] - mesh['route']['cost_rub'])} ₽ логистики на заказ, покупатель платит меньше и ждёт 3 ч вместо 30 ч.
2. Для межгорода схема работает только при предварительном размещении остатка; иначе — базовая схема через хаб.
3. ПВЗ становится устойчиво выгодным при загрузке от **{city_be} операций/день**; ниже порога схема требует
   субсидии или отказа от партнёрства.
4. Длительное хранение свыше **{storage_be} дней** делает заказ убыточным для маркетплейса — порог и правило
   перемещения встроены в продукт.
5. Переводы между сторонами не увеличивают прибыль цепочки: выигрыш возникает только за счёт исключения
   лишнего хаба/магистрали и роста утилизации ПВЗ.
"""
    return text


def main() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print(main())
