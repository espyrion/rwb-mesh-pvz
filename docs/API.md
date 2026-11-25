# API и модели данных

Базовый URL: `http://localhost:8000/api/v1` (в Docker UI проксирует `/api/`).
Интерактивная спецификация: `/docs` (Swagger UI), машиночитаемая: `/openapi.json`.

## Модели данных

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `pvz` | точка выдачи или хаб | `code`, `city`, `capacity_cells`, `used_cells`, `is_hub`, `lat`, `lng`, `storage_rate_day`, `op_fee`, `audit_accuracy_pct`, `audit_sla_rating`, `audit_count`, `last_audit_at` |
| `edge` | сегмент сети | `from_pvz`, `to_pvz`, `kind` (`local`/`trunk`), `cost_rub`, `hours`, `is_active` |
| `seller` | продавец | `name`, `commission_pct` |
| `item` | партия товара на хранении | `sku`, `seller_id`, `qty`, `cell_size`, `stored_pvz_id`, `status`, `price_rub`, `accepted_at`, `parent_batch_id` |
| `order` | заказ | `code`, `item_id`, `qty`, `from_pvz`, `to_pvz`, `status`, `route_id`, `buyer_total_rub`, `seller_net_rub`, `idempotency_key` |
| `route` | выбранный маршрут | `kind`, `segments_json`, `total_cost_rub`, `total_hours`, `reason`, `reason_code`, `overflow` |
| `ledger` | денежные начисления | `party` (`pvz`/`marketplace`/`seller`/`buyer`), `party_id`, `amount_rub`, `kind`, `order_id`, `item_id` |
| `incident` | нештатная ситуация | `type` (`node_down`/`late`/`no_route`/`stock_mismatch`), `order_id`, `edge_id`, `status`, `resolved_route_id` |
| `notification` | лента событий | `audience` (`pvz`/`seller`), `text`, `payload_json` |
| `clock` | виртуальное время | `virtual_now` |

> `seller_logistics_fee_rub` (60 ₽) учтён в экономической модели P&L, тогда как транзакционный `ledger` MVP удерживает с продавца только комиссию 12% (упрощение прототипа).

Статусы заказа и допустимые переходы:

```
created → routed → assembled → in_transit → issued → returned
                 ↘ rerouted ↗        ↘ issued
```

Недопустимый переход возвращает `409` с текстом «недопустимый переход статуса X → Y».

## Эндпоинты

### Справочники и остатки
| Метод | Путь | Описание |
|---|---|---|
| GET | `/pvz?city=&include_hub=` | список ПВЗ (по умолчанию без хаба) |
| GET | `/pvz/{id}` | карточка ПВЗ: ёмкость, загрузка, тарифы |
| POST | `/pvz/{id}/disable` | вывод узла из строя: блокировка, эвакуация остатков, переадресация заказов |
| POST | `/pvz/{id}/enable` | возврат точки в строй |
| GET | `/sellers`, `/sellers/{id}` | продавцы |
| POST | `/items` | приёмка товара: `sku`, `seller_id`, `title`, `qty`, `cell_size`, `pvz_id`, `price_rub` |
| GET | `/items?pvz_id=&status=` | остатки с резервом и доступным количеством |
| GET | `/items/{id}` | карточка товара |
| POST | `/items/{id}/relocate` | перемещение остатка между ПВЗ одного города |
| POST | `/catalog/placement-score` | скоринг размещения SKU: `mesh_pvz` или `hub` с причинами (роль seller/admin) |
| POST | `/pvz/{id}/audit` | инвентаризация остатков: факт по партиям, корректировка, инцидент, удержание (роль pvz/admin) |
| GET | `/ledger?order_id=&viewer=` | начисления (по заказу или стороне; `viewer` — `pvz`/`marketplace`/`seller`/`buyer`) |

### Заказы
| Метод | Путь | Описание |
|---|---|---|
| POST | `/orders` | создать заказ: `item_id`, `qty`, `to_pvz_id`, `accept_latency`, `idempotency_key` (≤64 симв.) |
| GET | `/orders?status=` | список заказов |
| GET | `/orders/{id}` | заказ с маршрутом и сроками |
| POST | `/orders/{id}/assemble` | сборка: переносит ячейки на ПВЗ выдачи, без повторных списаний |
| POST | `/orders/{id}/transit` | передача в магистраль (для схемы hub) |
| POST | `/orders/{id}/issue` | выдача: пишет ledger, уведомляет продавца |
| POST | `/orders/{id}/return` | возврат в срок 7 дней: обратный маршрут, удержание, остатки |
| POST | `/orders/{id}/cancel` | отмена до выдачи: снимает резерв, возвращает ячейки; идемпотентна |
| GET | `/health/deep` | инварианты данных: ячейки, резервы, маршруты, проводки |
| GET | `/track/{token}` | публичная карточка заказа по токену из ссылки покупателя: статус, история, сроки |

### Отказ узла

`POST /pvz/{id}/disable` — идемпотентная процедура: точка исключается из маршрутизации →
свободные остатки (`stored`/`returned`, включая свободный хвост зарезервированных партий)
эвакуируются к соседу того же города с наименьшей загрузкой и свободной ёмкостью → заказы с
выдачей в узле переадресуются, уже собранный товар едет вместе с заказом → партии-источники
переезжают целиком, заказы получают новый `from_pvz` → маршруты, проходившие через узел,
перевыбираются. При дроблении партии части сохраняют `parent_batch_id` корневой партии —
по нему видно, из какого остатка они появились. Каждое перемещение стоит 60 ₽ и относится на маркетплейс
(`pvz.relocation_cost_rub` в `params.yaml`).

Ответ: `pvz`, `already_down`, `evacuated[]` (партия, куда, ячейки, ₽), `orders[]`
(обработанные заказы с новыми маршрутами), `incidents[]`, `unresolved[]` (что нельзя решить
автоматически: нет ёмкости у соседей, нет пути), `relocation_total_rub` и сводный `incident`
(закрыт, если нерешённых нет). Остатки при нехватке ёмкости не бросаются — открывается
инцидент для оператора. `POST /pvz/{id}/enable` возвращает точку в граф.

**Списки и пагинация.** `/orders`, `/items`, `/ledger` принимают `limit` и `offset`; без `limit`
отдаётся всё. Для `/orders` и `/items` предел `limit` — 1…500, для `/ledger` — 1…1000. Общее число
подходящих строк — в заголовке `X-Total-Count`. Фильтры: у заказов `status`,
`pvz_id` (источник или получатель), `code`; у остатков `pvz_id`, `status`, `seller_id`; у ledger —
`order_id`, `viewer`, `party_id`, `kind`.

**Контракт в /docs.** Все `GET`-ответы описаны Pydantic-схемами (`OrderOut`, `RouteOut`, `TrackOut`…),
а запись помечена схемой `ApiKey` — в Swagger есть рабочая кнопка **Authorize** с полем `X-API-Key`.

Повторный вызов `assemble`/`issue`/`return` идемпотентен: возвращается `idempotent_replay: true`
без новых начислений. Повтор `POST /orders` с тем же `idempotency_key` возвращает исходный заказ.

**Авторизация.** `GET`-запросы и `/track/{token}` открыты. Запись требует заголовок `X-API-Key`:
`demo-pvz-key` (сборка, выдача, возврат, перемещение), `demo-seller-key` (заказ, партия, отмена),
`demo-admin-key` (всё, включая сеть и часы). Без ключа — 401, с чужой ролью — 403.

### Маршруты и сеть
| Метод | Путь | Описание |
|---|---|---|
| GET | `/routes/compare?from=&to=&accept_latency=` | сравнение mesh и hub: сегменты, стоимость, часы, `chosen`, `reason`, `reason_code`, `overflow`, `load` |
| GET | `/routes`, `/routes/{id}` | сохранённые маршруты |
| GET | `/edges?kind=&active_only=` | сегменты сети |
| POST | `/edges/{id}/disable` | отключить сегмент; ответ содержит затронутые инциденты |
| POST | `/edges/{id}/enable` | включить сегмент |

**Основание выбора маршрута.** `GET /routes/compare` и объект `route` в ответах заказа отдают
машиночитаемый `reason_code` и булев `overflow`:

| `reason_code` | Когда выставляется |
|---|---|
| `mesh_same_city` | оба ПВЗ в одном городе: магистраль не используется, выбран mesh |
| `mesh_cheapest` | межгород, mesh дешевле и срок приемлем |
| `hub_cheaper` | межгород, mesh дороже или медленнее при запрете удлинения срока |
| `mesh_no_hub` | хаб недоступен (сегменты отключены), локальный путь есть |
| `hub_required` | локального пути нет (межгород или сегменты отключены) |
| `overflow_to_hub` | точка-источник или получатель загружена ≥ порога `network.overflow_utilization_threshold` (85%), заказ перелит на хаб |
| `overflow_hub_unavailable` | перелив нужен, но магистраль недоступна — в `reason` явно сказано, что запасного пути нет |
| `no_route` | нет активного пути ни по одной схеме |

`overflow = true` только при `chosen = hub` и `reason_code = overflow_to_hub`. Блок `load` содержит
`threshold_pct`, загрузку сторон `from`/`to`, флаг `triggered` и список `triggered_by` с кодом точки
и фактической загрузкой.

### Инциденты, время, аналитика
| Метод | Путь | Описание |
|---|---|---|
| GET | `/incidents?status=` | инциденты |
| POST | `/incidents/{id}/reroute` | повторный перевыбор маршрута (`accept_latency`) |
| GET | `/clock` | виртуальное «сейчас» |
| POST | `/clock/advance?days=` | сдвиг времени: начисляет хранение, рассылает уведомления |
| GET | `/notifications?audience=&order_id=` | лента уведомлений |
| GET | `/analytics/pvz?pvz_id=&from=&to=` | доходы, удержания, прибыль с вычетом операционных и постоянных затрат, ячейко-дни, ledger ПВЗ |
| GET | `/analytics/seller?seller_id=&from=&to=` | выручка, комиссия, прибыль, разрез по заказам |
| GET | `/analytics/summary` | сводка: заказы по статусам, ledger по сторонам, логистика, инциденты |
| GET | `/economics/scenario?mesh_bonus=&storage_rate=&fixed_cost=&commission_pct=&seller_logistics_fee=&mesh_share=&ops=&goods=&storage_days=` | калькулятор «что если»: P&L четырёх сторон (ПВЗ, маркетплейс, продавец, покупатель) для города и межгорода, точка безубыточности ПВЗ, чувствительность по доле локальных пар, очередь. Все параметры необязательны и по умолчанию берутся из `params.yaml`, вне диапазона — 422; считает `economics/scenario.py` |
| GET | `/economics/report` | тот же расчёт по умолчанию, что в `docs/ECON_REPORT.md`: город, межгород, день ПВЗ, чувствительность, очередь |

## Скоринг размещения SKU

`POST /catalog/placement-score` (роль seller/admin) отвечает на вопрос: держать ли
партию в ПВЗ города (`mesh_pvz`) или провезти через хаб (`hub`). Модель
интерпретируемая: score 0..100 — сумма трёх нормированных критериев, каждый вклад
и каждая причина видны в ответе, все числа — в `economics/params.yaml`.

```
score = 100 × (0,35 × спрос + 0,45 × оборачиваемость + 0,20 × габарит)
```

* **спрос** = min(локальный спрос ÷ 4,0 заказов/день; 1) — частый SKU уходит быстро;
* **оборачиваемость** = (21 − ожидаемый срок) ÷ (21 − 3), обрезано в 0..1. Хранение
  стоит 5 ₽/яч./день, поэтому у критерия самый большой вес;
* **габарит** = 1 − (ячеек/шт − 1) ÷ (3 − 1), обрезано в 0..1 — крупный товар ест ёмкость;
* ожидаемый срок хранения = партия ÷ спрос в день (закон Литтла) либо наблюдённая
  оборачиваемость `turnover_days`; сверху ограничен 60 днями;
* `score ≥ 60` → `mesh_pvz`, иначе `hub`. Если передан `pvz_id` и свободных ячеек
  меньше, чем нужно партии, рекомендация принудительно `hub` (`capacity_override=true`).
* уверенность = |score − порог| ÷ 25 очков, ограничена 100%: чем дальше решение от
  границы, тем оно устойчивее.

Запрос (обязательны `sku`, `qty`, `cell_size`; остальное необязательно):

```bash
curl -X POST localhost:8000/api/v1/catalog/placement-score \
  -H 'Content-Type: application/json' -H 'X-API-Key: demo-seller-key' \
  -d '{"sku":"TD-1001","qty":6,"cell_size":2,"demand_per_day":2.0,"pvz_id":1}'
```

`demand_per_day` можно не передавать: спрос наблюдается по заказам этого SKU в городе
за `pvz.placement_demand_window_days` (14 дней), а при отсутствии заказов берётся
допущение `pvz.placement_default_demand_per_day`. Источник — в `demand_source`
(`operator` | `observed` | `assumed`). `turnover_days` — наблюдённая оборачиваемость.

Ответ: `score`, `threshold`, `recommendation`, `confidence_pct`,
`expected_storage_days`, `expected_storage_cost_rub`, `components[]`
(`id`, `weight`, `raw`, `points`, `max_points`), `reasons[]` (русский, с числами и
именем параметра), `params_used` (все применённые числа), `capacity_override`.

Авторекомендация при приёмке: `POST /items` добавляет в ответ блок `placement`
(формат тот же) только при `pvz.placement_auto_recommend: true`. По умолчанию флаг
выключен — контракт приёмки не меняется.

### Параметры скоринга (`pvz.*`)

| Ключ | Значение | Обоснование |
|---|---|---|
| `placement_demand_weight` | 0.35 | спрос важен, но вторичен к скорости оборота |
| `placement_turnover_weight` | 0.45 | хранение — главный расход ПВЗ (5 ₽/яч./день), поэтому наибольший вес |
| `placement_footprint_weight` | 0.20 | габарит влияет на ёмкость, но не на деньги напрямую |
| `placement_demand_target_per_day` | 4.0 | 4 заказа/день на SKU — уверенный локальный спрос для точки сети |
| `placement_fast_turnover_days` | 3.0 | совпадает с типичным сроком хранения в `scenario_defaults.storage_days_typical` |
| `placement_slow_turnover_days` | 21.0 | совпадает с порогом длительного хранения `long_storage_threshold_days` |
| `placement_footprint_max_cells` | 3 | от 3 ячеек на единицу товар считается крупногабаритным |
| `placement_score_threshold` | 60.0 | ниже 60 баллов выгоды от ПВЗ нет — дешевле провезти через хаб |
| `placement_confidence_span` | 25.0 | 25 очков от порога — решение «вне зоны сомнения» |
| `placement_max_expected_days` | 60.0 | потолок срока при нулевом спросе (2 месяца — предел разумного хранения) |
| `placement_default_demand_per_day` | 1.0 | допущение при отсутствии наблюдений |
| `placement_demand_window_days` | 14 | окно наблюдения спроса по заказам |
| `placement_auto_recommend` | false | чтобы не менять поведение приёмки без явного решения |

## Инвентаризация остатков ПВЗ

`POST /pvz/{id}/audit` (роль pvz/admin): оператор передаёт фактические количества
по партиям, `item_id → qty`.

```bash
curl -X POST localhost:8000/api/v1/pvz/1/audit \
  -H 'Content-Type: application/json' -H 'X-API-Key: demo-pvz-key' \
  -d '{"actual":{"1":4,"5":15},"note":"плановая сверка"}'
```

Что происходит при расхождении:

1. остаток партии корректируется условным `UPDATE` (проверяется rowcount; параллельное
   изменение → `409`), ячейки ПВЗ двигаются тем же способом в границах `[0, capacity_cells]`;
2. на каждую расхождённую партию открывается инцидент `stock_mismatch` с текстом
   «учёт N шт., факт M шт. (недостача/излишек)»;
3. удержание с ПВЗ: недостача × 30 ₽ + излишек × 10 ₽, но не больше 200 ₽ за проверку;
   проводка — пара строк ledger: ПВЗ `−удержание`, маркетплейс `+компенсация` (kind `penalty`);
4. точность проверки = 100 × (1 − Σ|Δ| ÷ Σ учётных единиц); сглаженная точность ПВЗ =
   0,3 × свежая + 0,7 × прежняя (до первой сверки «прежняя» = `audit_initial_accuracy_pct`,
   то есть 100%); SLA-рейтинг по границам 98 / 95 / 90 % → `A`/`B`/`C`/`D`;
5. факт ниже резерва активных заказов отклоняется (`409`) — сначала решаются заказы;
   корректировка, выводящая ячейки за ёмкость, тоже отклоняется с `409` и полным откатом
   (ни инцидента, ни проводки).

Идемпотентность: повтор с теми же `actual` и неизменным учётом возвращает сохранённый
ответ с `idempotent=true` и не создаёт второго инцидента/проводки.

Ответ: `lines[]` (`expected_qty`, `actual_qty`, `delta`, `cells_delta`, `status`
(`matched`/`shortage`/`surplus`), `incident_id`), `matched`/`shortage`/`surplus`,
`incident_ids`, `penalty_total_rub`, `accuracy_pct` (сглаженная), `audit_accuracy_pct`
(эта проверка), `accuracy_before_pct`, `sla_rating`, `audits_total`, `used_cells`,
`free_cells`, `idempotent`.

### Параметры инвентаризации (`pvz.*`)

| Ключ | Значение | Зачем |
|---|---|---|
| `audit_accuracy_ema_alpha` | 0.3 | свежая проверка весит 30%: рейтинг реагирует, но не скачет от одной сверки |
| `audit_shortage_penalty_rub` | 30.0 | недостача — прямой убыток сети, ставка выше |
| `audit_surplus_penalty_rub` | 10.0 | излишек — ошибка учёта, убытка почти нет |
| `audit_penalty_cap_rub` | 200.0 | потолок, чтобы одна ошибка не обнулила экономику точки |
| `audit_sla_a_pct` / `b` / `c` | 98 / 95 / 90 | границы рейтинга SLA ПВЗ |
| `audit_initial_accuracy_pct` | 100.0 | до первой сверки точка считается точной |

## Пример: сквозной заказ

```bash
# 1. Заказ создаёт продавец (ключ роли seller); id заказа берём из ответа, а не зашиваем
ORDER=$(curl -s -X POST localhost:8000/api/v1/orders \
  -H 'Content-Type: application/json' -H 'X-API-Key: demo-seller-key' \
  -d '{"item_id":1,"qty":1,"to_pvz_id":3}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["order"]["id"])')
# → ответ содержит order (status=routed), route (kind=mesh, total_cost_rub=90) и comparison

# 2. Сборку и выдачу делает оператор ПВЗ (ключ роли pvz)
curl -X POST localhost:8000/api/v1/orders/$ORDER/assemble -H 'X-API-Key: demo-pvz-key'
curl -X POST localhost:8000/api/v1/orders/$ORDER/issue -H 'X-API-Key: demo-pvz-key'

# 3. Проводки по заказу — чтение открыто, ключ не нужен
curl "localhost:8000/api/v1/ledger?order_id=$ORDER"
# → по сторонам: buyer −19 029, seller 16 632, pvz 40, marketplace 2 267; сумма = −90 (логистика)
```

## Ошибки

| Код | Когда |
|---|---|
| 401 | нет заголовка `X-API-Key` на write-запросе |
| 403 | ключ есть, но роль не подходит (например, `placement-score` от роли pvz) |
| 404 | объект не найден (в том числе ПВЗ в аудите или маршруте сравнения) |
| 409 | недостаточно остатка или ячеек, недопустимый переход статуса, нет маршрута, истёк срок возврата, нет места на ПВЗ возврата; в `POST /pvz/{id}/audit` — факт ниже резерва, выход ячеек за ёмкость, параллельное изменение строки |
| 422 | невалидное тело или query-параметры (Pydantic): неизвестный SKU, отрицательное количество, параметр сценария вне диапазона |

Сообщения на русском и содержат причину и контекст (код ПВЗ, свободные ячейки, статусы).
