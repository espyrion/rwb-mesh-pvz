# API и модели данных

Базовый URL: `http://localhost:8000/api/v1` (в Docker UI проксирует `/api/`).
Интерактивная спецификация: `/docs` (Swagger UI), машиночитаемая: `/openapi.json`.

## Модели данных

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `pvz` | точка выдачи или хаб | `code`, `city`, `capacity_cells`, `used_cells`, `is_hub`, `storage_rate_day`, `op_fee` |
| `edge` | сегмент сети | `from_pvz`, `to_pvz`, `kind` (`local`/`trunk`), `cost_rub`, `hours`, `is_active` |
| `seller` | продавец | `name`, `commission_pct` |
| `item` | партия товара на хранении | `sku`, `seller_id`, `qty`, `cell_size`, `stored_pvz_id`, `status`, `price_rub`, `accepted_at` |
| `order` | заказ | `code`, `item_id`, `qty`, `from_pvz`, `to_pvz`, `status`, `route_id`, `buyer_total_rub`, `seller_net_rub`, `idempotency_key` |
| `route` | выбранный маршрут | `kind`, `segments_json`, `total_cost_rub`, `total_hours`, `reason` |
| `ledger` | денежные начисления | `party` (`pvz`/`marketplace`/`seller`/`buyer`), `party_id`, `amount_rub`, `kind`, `order_id`, `item_id` |
| `incident` | нештатная ситуация | `type`, `order_id`, `edge_id`, `status`, `resolved_route_id` |
| `notification` | лента событий | `audience` (`pvz`/`seller`), `text`, `payload_json` |
| `clock` | виртуальное время | `virtual_now` |

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
| POST | `/pvz/{id}/disable`, `/enable` | отключение/включение узла (создаёт инциденты по активным заказам) |
| GET | `/sellers`, `/sellers/{id}` | продавцы |
| POST | `/items` | приёмка товара: `sku`, `seller_id`, `title`, `qty`, `cell_size`, `pvz_id`, `price_rub` |
| GET | `/items?pvz_id=&status=` | остатки с резервом и доступным количеством |
| GET | `/items/{id}` | карточка товара |
| POST | `/items/{id}/relocate` | перемещение остатка между ПВЗ одного города |
| GET | `/ledger?order_id=&party=` | начисления (по заказу или стороне) |

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

**Списки и пагинация.** `/orders`, `/items`, `/ledger` принимают `limit` (1…500) и `offset`; без `limit`
отдаётся всё. Общее число подходящих строк — в заголовке `X-Total-Count`. Фильтры: у заказов `status`,
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
| GET | `/routes/compare?from=&to=&accept_latency=` | сравнение mesh и hub: сегменты, стоимость, часы, основание выбора |
| GET | `/routes`, `/routes/{id}` | сохранённые маршруты |
| GET | `/edges?kind=&active_only=` | сегменты сети |
| POST | `/edges/{id}/disable` | отключить сегмент; ответ содержит затронутые инциденты |
| POST | `/edges/{id}/enable` | включить сегмент |

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
| GET | `/economics/scenario?mesh_bonus=&storage_rate=&fixed_cost=&commission_pct=&mesh_share=&ops=&goods=` | калькулятор «что если»: P&L четырёх сторон для mesh и хаба, точка безубыточности ПВЗ. Все параметры необязательны, вне диапазона — 422; считает `economics/scenario.py` поверх `params.yaml` |

## Пример: сквозной заказ

```bash
curl -X POST localhost:8000/api/v1/orders -H 'Content-Type: application/json' \
  -d '{"item_id":1,"qty":1,"to_pvz_id":3}'
# → order: routed, route.kind=mesh, cost 90, buyer_total 19019

curl -X POST localhost:8000/api/v1/orders/1/assemble
curl -X POST localhost:8000/api/v1/orders/1/issue
curl localhost:8000/api/v1/ledger?order_id=1
# → buyer −19019, seller 16632, pvz 40, marketplace 2257; сумма = −90
```

## Ошибки

| Код | Когда |
|---|---|
| 404 | объект не найден |
| 409 | недостаточно остатка или ячеек, недопустимый переход статуса, нет маршрута, истёк срок возврата, нет места на ПВЗ возврата |
| 422 | невалидное тело запроса (Pydantic) |

Сообщения на русском и содержат причину и контекст (код ПВЗ, свободные ячейки, статусы).
