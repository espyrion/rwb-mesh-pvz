"""Генерация docs/ECON_REPORT.md из модели (единый источник чисел).

Запуск:  python economics/report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from economics.model import (
    intercity_economics,
    load_params,
    order_economics,
    pct_change,
    queue_wait_hours,
    route_cost,
)
from economics.network import queue_load_table, simulate_day
from economics.scenario import (
    chain_breakeven_share,
    pvz_breakeven_ops,
    pvz_day,
    share_sensitivity,
    storage_breakeven_days,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "ECON_REPORT.md"


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def pct(value: float) -> str:
    return f"{value:+.2f}%".replace(".", ",")


def pct_plain(value: float) -> str:
    return f"{value:.2f}%".replace(".", ",")


def hours(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def num(value: float) -> str:
    """Число без лишних нулей, но с десятичной запятой (как в UI)."""
    text = f"{value:g}"
    return text.replace(".", ",")


def signed(value: float) -> str:
    return f"{value:+,.2f}".replace(",", " ").replace(".", ",")


def party_rows(scenario: dict) -> str:
    pvz, market, seller, buyer = (
        scenario["pvz"],
        scenario["marketplace"],
        scenario["seller"],
        scenario["buyer"],
    )
    chain = scenario["chain"]["profit_rub"]
    return "\n".join(
        [
            f"| ПВЗ | {money(pvz['revenue_total'])} | {money(pvz['costs_total'])} | {money(pvz['profit_rub'])} | {pct_plain(pvz['margin_pct'])} |",
            f"| Маркетплейс | {money(market['revenue_total'])} | {money(market['costs_total'])} | {money(market['profit_rub'])} | {pct_plain(market['margin_pct'])} от GMV |",
            f"| Продавец | {money(seller['revenue_total'])} | {money(seller['costs_total'])} | {money(seller['profit_rub'])} | {pct_plain(seller['margin_pct'])} |",
            f"| Покупатель (платит) | — | {money(buyer['total_rub'])} | срок {hours(buyer['hours'])} ч | — |",
            f"| **Цепочка (без покупателя)** | | | **{money(chain)}** | {pct_plain(scenario['chain']['margin_pct'])} от GMV |",
        ]
    )


def build() -> str:
    params = load_params()
    fees = params["fees"]
    pv = params["pvz"]
    lg = params["logistics"]
    acc = params["buyer_acceptance"]
    defaults = params["scenario_defaults"]
    goods = float(defaults["goods_price_rub"])
    cells = float(pv["avg_cells_per_order"])
    storage_days = float(defaults["storage_days_typical"])
    local_share = float(defaults["local_share_pct"])
    day_ops = int(defaults["pvz_day_ops"])
    day_share = float(defaults["pvz_day_mesh_share_pct"]) / 100.0

    mesh = order_economics(params, kind="mesh", goods_rub=goods, cells=cells, storage_days=storage_days)
    hub = order_economics(params, kind="hub", goods_rub=goods, cells=cells, storage_days=storage_days)
    intercity = intercity_economics(params, goods_rub=goods, cells=cells, storage_days=storage_days)
    cmp_city = {
        "buyer_delta_rub": round(mesh["buyer"]["total_rub"] - hub["buyer"]["total_rub"], 2),
        "buyer_delta_pct": pct_change(mesh["buyer"]["total_rub"], hub["buyer"]["total_rub"]),
        "buyer_delta_hours": round(mesh["buyer"]["hours"] - hub["buyer"]["hours"], 2),
        "buyer_delta_hours_pct": pct_change(mesh["buyer"]["hours"], hub["buyer"]["hours"]),
    }
    cmp_ic = intercity["compare"]
    city_be = pvz_breakeven_ops(params, mesh_share=day_share) or 0
    storage_be = storage_breakeven_days(params)
    day = pvz_day(params, ops=day_ops, mesh_share=day_share)
    capacity = int(pv["ops_per_day_capacity"])
    queue = queue_load_table(params)
    threshold = {
        "target": float(pv["utilization_target_pct"]),
        "alert": float(pv["utilization_alert_pct"]),
        "cap": float(params["network"]["queue_cap_pct"]),
        "wait_target": queue_wait_hours(params, float(pv["utilization_target_pct"]) / 100.0),
        "wait_alert": queue_wait_hours(params, float(pv["utilization_alert_pct"]) / 100.0),
    }
    sensitivity = share_sensitivity(params)
    chain_be = chain_breakeven_share(params)
    long_storage = order_economics(
        params,
        kind="mesh",
        goods_rub=goods,
        cells=cells,
        storage_days=float(pv["long_storage_threshold_days"]),
    )
    returned = order_economics(
        params, kind="mesh", goods_rub=goods, cells=cells, storage_days=storage_days, returned=True
    )
    incident_mp = round(
        mesh["marketplace"]["profit_rub"] - route_cost(params, "mesh"), 2
    )

    sim_runs = [
        simulate_day(params, orders=int(volume), seed=int(params["network"]["simulation_seed"]))
        for volume in params["network"]["simulation_orders"]
    ]
    sim_rows = "\n".join(
        "| {orders} | {late} | {cancelled} | {penalty} | {reverse} | {chain} |".format(
            orders=run["orders"],
            late=run["modes"]["mesh"]["late_orders"],
            cancelled=run["modes"]["mesh"]["cancelled_orders"],
            penalty=money(run["modes"]["mesh"]["late_penalty_rub"]),
            reverse=money(run["modes"]["mesh"]["reverse_logistics_rub"]),
            chain=money(run["modes"]["mesh"]["chain_profit_rub"]),
        )
        for run in sim_runs
    )
    sens_rows = "\n".join(
        "| {share} | {chain} | {network} | {pvz} | {be} |".format(
            share=pct_plain(row["share_pct"]),
            chain=money(row["chain_per_order_rub"]),
            network=money(row["network_chain_rub"]),
            pvz=money(row["pvz_day_profit_rub"]),
            be=row["pvz_breakeven_ops"],
        )
        for row in sensitivity
    )
    queue_rows = "\n".join(
        "| {ops} | {util} | {wait} | {mult} |".format(
            ops=row["ops"],
            util=pct_plain(row["utilization"] * 100.0),
            wait=hours(row["wait_hours"]),
            mult=f"×{row['wait_multiplier']:.2f}".replace(".", ",")
            if row["wait_multiplier"] is not None
            else "—",
        )
        for row in queue
    )
    chain_words = "прибыльнее" if cmp_ic["chain_more_profitable"] else "менее прибыльна"
    city_chain_words = (
        "прибыльнее" if (mesh["chain"]["profit_rub"] - hub["chain"]["profit_rub"]) > 0 else "менее прибыльна"
    )
    mp_margin_pct = mesh["marketplace"]["margin_pct"]
    penalty_share_margin = round(float(fees["late_penalty_rub"]) / mesh["marketplace"]["profit_rub"] * 100.0, 2)
    penalty_share_gmv = round(float(fees["late_penalty_rub"]) / goods * 100.0, 2)

    text = f"""# Экономическая модель и отчёт о расчётах

> **Дисклеймер.** Все тарифы, бонусы, штрафы и лимиты ответственности в этом документе — параметры
> предложенной командой модели. Они не являются действующими тарифами RWB или его партнёров.
> Данные синтетические; реальные перевозки, платежи и доступы к производственным системам не используются.
> Источник параметров: `economics/params.yaml` (версия в репозитории). Отчёт пересобирается командой
> `python economics/report.py`; тест `economics/tests/test_report.py` сверяет файл с моделью.

## 1. Что считается

Модель считает **четыре стороны** одного заказа и их результат:

| Сторона | Что считаем |
|---|---|
| ПВЗ | вознаграждение за операции ({money(pv['op_fee_rub'])} ₽), плата за хранение ({money(pv['storage_rate_day_rub'])} ₽/ячейка·день), бонус за заказ без хаба ({money(pv['mesh_bonus_rub'])} ₽); затраты на приёмку ({money(lg['acceptance_cost_rub'])} ₽/ед.) и сборку ({money(lg['assemble_cost_rub'])} ₽/операция), условно-постоянные ({money(pv['fixed_cost_day_rub'])} ₽/день), удержания |
| Маркетплейс | комиссия ({money(fees['commission_pct'])}% от товара), плата за доставку покупателя ({money(fees['buyer_delivery_fee_local_rub'])} ₽ mesh / {money(fees['buyer_delivery_fee_hub_rub'])} ₽ hub), логистическое удержание продавца ({money(fees['seller_logistics_fee_rub'])} ₽) и хранение продавца; затраты — внешняя логистика, хранение в пользу ПВЗ, вознаграждение ПВЗ, mesh-бонус |
| Продавец | выручка — цена товара; затраты — закупка ({money(params['seller']['goods_cost_pct'])}% от цены), комиссия, логистическое удержание, хранение (фактические дни × ячейки), удержание за возврат |
| Покупатель | товар + доставка, срок в часах |

Переводы между участниками (комиссия, вознаграждение ПВЗ, хранение, логистическое удержание) **не создают
прибыли цепочки**: результат цепочки = прибыль ПВЗ + прибыль маркетплейса + прибыль продавца = оплата
покупателя − внешние затраты логистики − операционные затраты ПВЗ. Это проверяется тестом
`sum_parties_equal_chain` в `economics/tests/test_model.py` и `test_chain_identity_holds` в API.

**GMV и маржа — разные величины.** GMV — цена товара; маржа маркетплейса считается в процентах от GMV,
маржа продавца — от его выручки, маржа ПВЗ — от его дохода. Штраф SLA ({money(fees['late_penalty_rub'])} ₽)
сопоставляется с маржой маркетплейса ({money(mesh['marketplace']['profit_rub'])} ₽ на заказ), а не с ценой
товара: это {pct_plain(penalty_share_margin)} маржи и {pct_plain(penalty_share_gmv)} GMV.

## 2. Допущения и единицы

* валюта — рубли с НДС, точность 2 знака; время — часы (срок) и дни (хранение);
* ёмкость ПВЗ — ячейки; заказ занимает `avg_cells_per_order × qty` = {num(cells)} ячеек;
* плата за хранение — ₽ за ячейку в день, начисляется маркетплейсом в пользу ПВЗ и перевыставляется продавцу;
* магистраль считается консолидированно: `trunk_cost_per_order_rub` = {money(lg['trunk_cost_per_order_rub'])} ₽/заказ;
* локальная доставка — {money(lg['local_delivery_cost_rub'])} ₽ на заказ (город, {hours(lg['local_delivery_hours'])} ч);
* схема через хаб — {money(lg['hub_handling_rub'])} ₽ обработка + {money(lg['trunk_cost_per_order_rub'])} ₽ магистраль
  + {money(lg['last_mile_cost_rub'])} ₽ последняя миля = **{money(route_cost(params, 'hub'))} ₽**, {hours(lg['hub_handling_hours'] + lg['trunk_hours'] + lg['last_mile_hours'])} ч;
* комиссия маркетплейса — {money(fees['commission_pct'])}% от товара; доставка для покупателя —
  {money(fees['buyer_delivery_fee_local_rub'])} ₽ (mesh) / {money(fees['buyer_delivery_fee_hub_rub'])} ₽ (hub);
* условно-постоянные затраты ПВЗ — {money(pv['fixed_cost_day_rub'])} ₽/день (аренда, ФОТ, оборудование);
* средний заказ: товар {money(goods)} ₽, {num(cells)} ячейки, {num(storage_days)} дня хранения, срок возврата
  {fees['return_window_days']} дней;
* **допущение о доле локальных пар** — {pct_plain(local_share)} (`scenario_defaults.local_share_pct`);
* **допущение о доле отмен при просрочке** — {pct_plain(float(fees['late_cancel_rate']) * 100)} 
  (`fees.late_cancel_rate`): просроченный заказ отменяется, цепочка теряет маржу и платит обратную логистику;
* **пиковый коэффициент курьера** — ×{money(fees['peak_surge_multiplier'])} (`fees.peak_surge_multiplier`):
  обратная перевозка в пик дороже;
* **кривая очереди** — M/M/1 до {pct_plain(threshold['cap'])} загрузки, дальше линейно
  ({money(params['network']['overload_delay_hours_per_100pct'])} ч на каждые +100%);
* границы приемлемости для покупателя — удорожание не более {money(acc['max_cost_increase_pct'])}%,
  удлинение не более {money(acc['max_time_increase_hours'])} ч (`buyer_acceptance`).

## 3. Формулы

```
логистика(mesh)            = local_delivery_cost
логистика(intercity_mesh)  = local_delivery_cost + trunk_per_order
логистика(hub)             = hub_handling + trunk_per_order + last_mile
хранение                   = ячейки × дни × storage_rate_day
ПВЗ: доход   = op_fee × операции + хранение + mesh_bonus × mesh-операции
     затраты = acceptance × единицы + assemble × операции + fixed × дни + удержания
маркетплейс: доход   = комиссия + доставка_покупателя + логистическое_удержание + хранение_продавца
             затраты = логистика + хранение_в_пользу_ПВЗ + op_fee + mesh_bonus
продавец:    прибыль = товар − закупка − комиссия − логистическое_удержание − хранение − удержание_за_возврат
покупатель:  платёж  = товар + доставка_покупателя
цепочка:     прибыль = ПВЗ + маркетплейс + продавец
```

Все формулы реализованы в `economics/model.py`: `route_cost`, `storage_cost`, `pvz_pnl`,
`marketplace_pnl`, `seller_pnl`, `buyer_cost`, `order_economics`, `intercity_economics`, `compare`,
`queue_wait_hours`. Симуляция сети — `economics/network.py`.

## 4. Городской заказ: базовая схема (хаб) и предложенная (mesh)

Товар {money(goods)} ₽, {num(cells)} ячейки, {num(storage_days)} дня хранения, один заказ.

### 4.1 Базовая схема «хаб + магистраль»

| Сторона | Доход, ₽ | Затраты, ₽ | Результат, ₽ | Маржа |
|---|---|---|---|---|
{party_rows(hub)}

| Показатель | Значение |
|---|---|
| Логистика | {money(hub['route']['cost_rub'])} ₽ / {hours(hub['route']['hours'])} ч |
| Хранение | {money(hub['storage']['cost_rub'])} ₽ |
| Покупатель платит | {money(hub['buyer']['total_rub'])} ₽ |

### 4.2 Предложенная схема mesh (локально, без хаба)

| Сторона | Доход, ₽ | Затраты, ₽ | Результат, ₽ | Маржа |
|---|---|---|---|---|
{party_rows(mesh)}

| Показатель | Значение |
|---|---|
| Логистика | {money(mesh['route']['cost_rub'])} ₽ / {hours(mesh['route']['hours'])} ч |
| Хранение | {money(mesh['storage']['cost_rub'])} ₽ |
| Покупатель платит | {money(mesh['buyer']['total_rub'])} ₽ |

### 4.3 Сравнение: абсолютное и относительное изменение

| Показатель | База (hub) | Предложение (mesh) | Δ, ₽ | Δ, % |
|---|---|---|---|---|
| Платёж покупателя, ₽ | {money(hub['buyer']['total_rub'])} | {money(mesh['buyer']['total_rub'])} | {signed(cmp_city['buyer_delta_rub'])} | {pct(cmp_city['buyer_delta_pct'])} |
| Срок, ч | {hours(hub['buyer']['hours'])} | {hours(mesh['buyer']['hours'])} | {signed(cmp_city['buyer_delta_hours'])} | {pct(cmp_city['buyer_delta_hours_pct'])} |
| Прибыль ПВЗ, ₽ | {money(hub['pvz']['profit_rub'])} | {money(mesh['pvz']['profit_rub'])} | {signed(mesh['pvz']['profit_rub'] - hub['pvz']['profit_rub'])} | {pct(pct_change(mesh['pvz']['profit_rub'], hub['pvz']['profit_rub']))} |
| Маржа маркетплейса, ₽ | {money(hub['marketplace']['profit_rub'])} | {money(mesh['marketplace']['profit_rub'])} | {signed(mesh['marketplace']['profit_rub'] - hub['marketplace']['profit_rub'])} | {pct(pct_change(mesh['marketplace']['profit_rub'], hub['marketplace']['profit_rub']))} |
| Прибыль продавца, ₽ | {money(hub['seller']['profit_rub'])} | {money(mesh['seller']['profit_rub'])} | {signed(mesh['seller']['profit_rub'] - hub['seller']['profit_rub'])} | {pct(pct_change(mesh['seller']['profit_rub'], hub['seller']['profit_rub']))} |
| Прибыль цепочки, ₽ | {money(hub['chain']['profit_rub'])} | {money(mesh['chain']['profit_rub'])} | {signed(mesh['chain']['profit_rub'] - hub['chain']['profit_rub'])} | {pct(pct_change(mesh['chain']['profit_rub'], hub['chain']['profit_rub']))} |

**Покупатель:** mesh **дешевле на {money(abs(cmp_city['buyer_delta_rub']))} ₽ ({pct(cmp_city['buyer_delta_pct'])})**
и **быстрее на {hours(abs(cmp_city['buyer_delta_hours']))} ч ({pct(cmp_city['buyer_delta_hours_pct'])})**. Обе
величины отрицательны — это выгода покупателю, а не удорожание.

**Цепочка:** mesh **{city_chain_words} на {money(abs(mesh['chain']['profit_rub'] - hub['chain']['profit_rub']))} ₽**
({pct(pct_change(mesh['chain']['profit_rub'], hub['chain']['profit_rub']))}). Формулировка «дороже/дешевле»
к прибыли неприменима: знак проверяется по `chain_more_profitable` в `compare()`.

**Почему маркетплейс не в убытке.** Логистика mesh дешевле на
{money(hub['route']['cost_rub'] - mesh['route']['cost_rub'])} ₽, но покупателю отдано
{money(fees['buyer_delivery_fee_hub_rub'] - fees['buyer_delivery_fee_local_rub'])} ₽ скидки и
{money(pv['mesh_bonus_rub'])} ₽ бонуса ПВЗ: итог {signed(mesh['marketplace']['profit_rub'] - hub['marketplace']['profit_rub'])} ₽
к марже. Прежняя калибровка со скидкой больше бонуса и половины экономии логистики давала
отрицательную маржу; скидка уменьшена — покупатель всё равно платит меньше и получает заказ за
{hours(mesh['buyer']['hours'])} ч вместо {hours(hub['buyer']['hours'])} ч.

**Продавец** в городе не зависит от схемы: его удержания (комиссия
{money(mesh['seller']['costs']['commission'])} ₽, логистика {money(mesh['seller']['costs']['logistics'])} ₽,
хранение {money(mesh['seller']['costs']['storage'])} ₽) — переводы, а не новые затраты цепочки.
Итог продавца — {money(mesh['seller']['profit_rub'])} ₽ при марже {pct_plain(mesh['seller']['margin_pct'])}.

Схема приемлема: `viable = {cmp_city['buyer_delta_pct'] < 0 and cmp_city['buyer_delta_hours'] < 0}`;
лимиты `buyer_acceptance` — {money(acc['max_cost_increase_pct'])}% и {money(acc['max_time_increase_hours'])} ч.

## 5. Межгородской заказ

Локального маршрута между городами нет, поэтому «в лоб» заказ идёт через хаб: {money(intercity['base']['buyer']['total_rub'])} ₽
и {hours(intercity['base']['buyer']['hours'])} ч. Предложение mesh для межгорода — **предварительное размещение
остатка** в ПВЗ города получателя: магистраль оплачивается консолидированно
({money(lg['trunk_cost_per_order_rub'])} ₽/заказ), а выдача обслуживается локально.

| Показатель | База: каждый заказ через хаб | Предложение: остаток заранее | Δ, ₽ | Δ, % |
|---|---|---|---|---|
| Логистика на заказ, ₽ | {money(intercity['base']['route']['cost_rub'])} | {money(intercity['mesh']['route']['cost_rub'])} | {signed(intercity['mesh']['route']['cost_rub'] - intercity['base']['route']['cost_rub'])} | {pct(pct_change(intercity['mesh']['route']['cost_rub'], intercity['base']['route']['cost_rub']))} |
| Срок, ч | {hours(intercity['base']['buyer']['hours'])} | {hours(intercity['mesh']['buyer']['hours'])} | {signed(cmp_ic['buyer_delta_hours'])} | {pct(cmp_ic['buyer_delta_hours_pct'])} |
| Платёж покупателя, ₽ | {money(intercity['base']['buyer']['total_rub'])} | {money(intercity['mesh']['buyer']['total_rub'])} | {signed(cmp_ic['buyer_delta_rub'])} | {pct(cmp_ic['buyer_delta_pct'])} |
| Прибыль ПВЗ, ₽ | {money(intercity['base']['pvz']['profit_rub'])} | {money(intercity['mesh']['pvz']['profit_rub'])} | {signed(cmp_ic['pvz_delta_rub'])} | {pct(cmp_ic['pvz_delta_pct'])} |
| Маржа маркетплейса, ₽ | {money(intercity['base']['marketplace']['profit_rub'])} | {money(intercity['mesh']['marketplace']['profit_rub'])} | {signed(cmp_ic['marketplace_delta_rub'])} | {pct(cmp_ic['marketplace_delta_pct'])} |
| Прибыль продавца, ₽ | {money(intercity['base']['seller']['profit_rub'])} | {money(intercity['mesh']['seller']['profit_rub'])} | {signed(cmp_ic['seller_delta_rub'])} | {pct(cmp_ic['seller_delta_pct'])} |
| Прибыль цепочки, ₽ | {money(intercity['base']['chain']['profit_rub'])} | {money(intercity['mesh']['chain']['profit_rub'])} | {signed(cmp_ic['chain_delta_rub'])} | {pct(cmp_ic['chain_delta_pct'])} |

**Покупатель:** дешевле на {money(abs(cmp_ic['buyer_delta_rub']))} ₽ ({pct(cmp_ic['buyer_delta_pct'])}) и быстрее
на {hours(abs(cmp_ic['buyer_delta_hours']))} ч ({pct(cmp_ic['buyer_delta_hours_pct'])}). **Цепочка**
{chain_words} на {money(abs(cmp_ic['chain_delta_rub']))} ₽
({pct(cmp_ic['chain_delta_pct'])}). **Маркетплейс** теряет магистраль
{money(abs(cmp_ic['marketplace_delta_rub']))} ₽ ({pct(cmp_ic['marketplace_delta_pct'])}), но остаётся в плюсе
{money(intercity['mesh']['marketplace']['profit_rub'])} ₽: это цена предразмещения, а не убыток.
Условие: остаток должен быть размещён заранее; при его отсутствии схема недоступна и заказ идёт через хаб
(ограничение явно показывается в UI и API).

## 6. Выгодность ПВЗ, очередь и чувствительность

### 6.1 Порог загрузки ПВЗ

При фиксированных затратах {money(pv['fixed_cost_day_rub'])} ₽/день и вкладе с заказа
(вознаграждение + хранение + доля mesh-бонуса − переменные затраты) ПВЗ выходит в ноль при
**{city_be} операциях в день**. Пропускная способность — {capacity} операций/день,
то есть {pct_plain(city_be / capacity * 100)} мощности и запас {capacity - city_be} операций.

| Загрузка ПВЗ | Операций/день | Прибыль ПВЗ/день, ₽ |
|---|---|---|
| Рабочая | {day_ops} | {money(day['profit_rub'])} |
| Порог безубыточности | {city_be} | ≈ 0 |
| Полная мощность | {capacity} | {money(pvz_day(params, ops=capacity, mesh_share=day_share)['profit_rub'])} |

### 6.2 Порог длительного хранения для продавца

Плата за хранение — перевод: маркетплейс получает её с продавца и передаёт ПВЗ, поэтому его маржа от срока
не зависит, а риск несёт продавец. Плата растёт линейно: {money(pv['storage_rate_day_rub'])} ₽ × {num(cells)} ячейки =
{money(pv['storage_rate_day_rub'] * cells)} ₽/день. При сроке {num(storage_days)} дня продавец зарабатывает
{money(mesh['seller']['profit_rub'])} ₽ на заказе, а при **{money(storage_be)} днях** его прибыль
обнуляется. Отсюда правило: на {pv['long_storage_threshold_days']} день — уведомление ПВЗ и предложение
перемещения за {money(pv['relocation_cost_rub'])} ₽; дальше — решение оператора (перемещение/уценка/возврат).
Для сравнения, при {pv['long_storage_threshold_days']} днях хранения прибыль продавца падает до
{money(long_storage['seller']['profit_rub'])} ₽, а ПВЗ зарабатывает на хранении
{money(long_storage['pvz']['revenue']['storage'])} ₽.

### 6.3 Очередь: почему рабочий порог 80–85%

Кривая ожидания — M/M/1 до {pct_plain(threshold['cap'])} загрузки: `Wq = ρ/(1−ρ) × 24 ч / capacity`.
Среднее обслуживание — {hours(24 / capacity)} ч на операцию. Ожидание обычного покупателя
растёт гиперболически и задолго до 100% выходит за границы сервиса:

| Операций/день | Загрузка | Ожидание, ч | × к базовой загрузке |
|---:|---:|---:|---:|
{queue_rows}

При целевых {money(threshold['target'])}% ожидание — {hours(threshold['wait_target'])} ч, при пороге
{money(threshold['alert'])}% — {hours(threshold['wait_alert'])} ч, то есть в
{f"{threshold['wait_alert'] / threshold['wait_target']:.2f}".replace('.', ',')} раза больше.
После {money(threshold['alert'])}% каждая дополнительная операция стоит покупателю десятков минут, а система
теряет запас на всплеск: **поэтому рабочая цель — {money(threshold['target'])}%, предупреждение —
{money(threshold['alert'])}%** (`pvz.utilization_target_pct` / `utilization_alert_pct`). Выше
{money(threshold['cap'])}% M/M/1 заменяется линейной оценкой перегрузки — сингулярность в отчёте бесполезна.

### 6.4 Чувствительность к доле локальных пар

Расчёт по заказу при долях {pct_plain(sensitivity[0]['share_pct'])}–{pct_plain(sensitivity[-1]['share_pct'])}
с шагом {pct_plain(sensitivity[1]['share_pct'] - sensitivity[0]['share_pct'])} (`network.sensitivity_mesh_share_pct`),
объём сети — {params['network']['sensitivity_orders']} заказов/день.

| Доля mesh | Прибыль цепочки/заказ, ₽ | Прибыль сети, ₽ | Прибыль ПВЗ/день, ₽ | Безубыточность ПВЗ, оп/день |
|---:|---:|---:|---:|---:|
{sens_rows}

**Точка безубыточности mesh-схемы по доле локальных пар — {pct_plain(chain_be['share_pct'])}**:
прибыль цепочки на заказ линейна по доле, `hub + s × (mesh − hub)`, а разница
{money(chain_be['chain_delta_per_order_rub'])} ₽ положительна. То есть экономика заказа не ограничивает
долю: ограничение — мощность точек и очередь (раздел 6.3), а не выгода заказа. При доле
{pct_plain(sensitivity[-1]['share_pct'])} ПВЗ выходит в ноль уже при {sensitivity[-1]['pvz_breakeven_ops']}
операциях/день вместо {sensitivity[0]['pvz_breakeven_ops']} при {pct_plain(sensitivity[0]['share_pct'])}.

## 7. Срыв сроков: штраф, отмены и пиковый курьер

Штраф SLA — {money(fees['late_penalty_rub'])} ₽ — берётся с маркетплейса и сопоставляется с его маржой
({money(mesh['marketplace']['profit_rub'])} ₽ на заказ), а не с GMV ({money(goods)} ₽). Это
{pct_plain(penalty_share_margin)} маржи и {pct_plain(penalty_share_gmv)} GMV.

Просроченный заказ отменяется с вероятностью {pct_plain(float(fees['late_cancel_rate']) * 100)}
(`late_cancel_rate`). Отмена — не только штраф: цепочка теряет маржу всех сторон и платит обратную
логистику по пиковому коэффициенту ×{money(fees['peak_surge_multiplier'])}. Для среднего заказа это
{money(route_cost(params, 'mesh') * fees['peak_surge_multiplier'])} ₽ обратной перевозки (mesh) и
{money(route_cost(params, 'hub') * fees['peak_surge_multiplier'])} ₽ (hub).

Симуляция дня ({params['network']['simulation_seed']} seed, mesh-режим):

| Заказов/день | Опозданий | Отмен | Штрафы, ₽ | Обратная логистика, ₽ | Прибыль цепочки, ₽ |
|---:|---:|---:|---:|---:|---:|
{sim_rows}

На потолке мощности срок ломается раньше денег: логистика на заказ почти не растёт, а отмены и обратная
логистика съедают маржу. Лечится точками, слотами приёмки и буферными ячейками — операционными решениями,
а не пересмотром тарифа.

## 8. Устойчивость модели

| Сценарий | ПВЗ | Маркетплейс | Продавец | Покупатель | Вывод |
|---|---|---|---|---|---|
| Возврат после выдачи | {money(returned['pvz']['profit_rub'])} ₽ (удержание {money(fees['return_reverse_fee_rub'])} ₽) | {money(returned['marketplace']['profit_rub'])} ₽ (обратная логистика {money(route_cost(params, 'mesh'))} ₽) | {money(returned['seller']['profit_rub'])} ₽ | теряет доставку {money(fees['buyer_delivery_fee_local_rub'])} ₽ | обратная логистика — внешняя затрата цепочки; удержания — переводы |
| Длительное хранение ({pv['long_storage_threshold_days']} дней) | {money(long_storage['pvz']['profit_rub'])} ₽ | {money(long_storage['marketplace']['profit_rub'])} ₽ | {money(long_storage['seller']['profit_rub'])} ₽ | без изменений | нужен порог и перемещение остатка |
| Инцидент: сегмент отключён | без изменений | {money(incident_mp)} ₽ (перепробег {money(route_cost(params, 'mesh'))} ₽) | без изменений | срок +{hours(lg['local_delivery_hours'])} ч | автоперевыбор маршрута сохраняет SLA |
| Пиковая перегрузка | без изменений | штраф {money(fees['late_penalty_rub'])} ₽ + отмены | без изменений | отмена заказа | порог 80–85% и слоты приёмки |

## 9. Что не включено в прототип

* реальные платежи, эквайринг и налоги (платёжный шлюз — заглушка, операции помечены в UI);
* аренда/ФОТ сверх условно-постоянной ставки, амортизация оборудования, маркетинг;
* страховка товара, потери и недостачи сверх удержаний модели;
* магистральные рейсы как расписание (стоимость консолидирована в пересчёте на заказ);
* возврат комиссии и цены товара покупателю при возврате (моделируется только обратная логистика);
* клиентское приложение покупателя (интерфейс покупателя — только расчёт стоимости/срока в API).

## 10. Выводы

1. В пределах города mesh не хуже ни одной стороне: ПВЗ +{money(mesh['pvz']['profit_rub'] - hub['pvz']['profit_rub'])} ₽,
   маркетплейс {signed(mesh['marketplace']['profit_rub'] - hub['marketplace']['profit_rub'])} ₽,
   продавец без изменений ({money(mesh['seller']['profit_rub'])} ₽), покупатель платит на
   {money(-cmp_city['buyer_delta_rub'])} ₽ меньше и ждёт {hours(mesh['buyer']['hours'])} ч вместо
   {hours(hub['buyer']['hours'])} ч. Прибыль цепочки {city_chain_words} на
   {money(mesh['chain']['profit_rub'] - hub['chain']['profit_rub'])} ₽.
2. Для межгорода схема работает только при предварительном размещении остатка; маркетплейс платит за это
   {money(abs(cmp_ic['marketplace_delta_rub']))} ₽ магистрали, но остаётся в плюсе, а покупатель и цепочка
   выигрывают.
3. ПВЗ становится устойчиво выгодным при загрузке от **{city_be} операций/день**; ниже порога схема требует
   субсидии. Рабочий потолок — {money(threshold['target'])}–{money(threshold['alert'])}% мощности по очереди.
4. Длительное хранение свыше **{money(storage_be)} дней** делает заказ убыточным для продавца — порог и
   правило перемещения встроены в продукт.
5. Переводы между сторонами не увеличивают прибыль цепочки: выигрыш возникает только за счёт исключения
   лишнего хаба/магистрали и роста утилизации ПВЗ. Штраф и отмены — потери маржи, а не цены товара.
"""
    return text


def main() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print(main())
