# Провенанс чисел и артефактов

Документ отвечает на вопрос жюри «откуда это число». Правило одно: **единственный источник
тарифов и допущений — `economics/params.yaml`**, производные отчёты генерируются скриптами,
а не набираются руками.

## 1. Источники по типам чисел

| Что | Источник | Как воспроизвести |
|---|---|---|
| Тарифы, бонусы, штрафы, SLA, очередь | `economics/params.yaml` | читать файл; `economics/tests` проверяют схему и границы |
| Экономика четырёх сторон, чувствительность, безубыточность | `economics/model.py`, `economics/scenario.py` | `python scripts/gen_openapi.py` не нужен; `python -m pytest economics/tests -q` |
| `docs/ECON_REPORT.md` | `economics/report.py::build()` | `python -c "from economics import report; open('docs/ECON_REPORT.md','w').write(report.build())"` |
| `docs/SCALE.md` (100/500/2000 заказов) | `scripts/simulate.py` | `python scripts/simulate.py --orders 100,500,2000 --seed 42 --write-md docs/SCALE.md` |
| `docs/openapi.json` (40 путей) | приложение FastAPI | `python scripts/gen_openapi.py` |
| Демо-прогон 5/5 | `scripts/demo.py` | API на :8001 с `DATABASE_URL=sqlite:////tmp/mesh-demo.db`, затем `python scripts/demo.py` |
| Тесты 131 + 14 | `api/tests`, `economics/tests` | команды из `README.md`, раздел «Тесты» |

`docs/ECON_REPORT.md` и `docs/SCALE.md` — генерируемые файлы. Правка чисел в них руками
запрещена: CI перегенерирует `SCALE.md` в `/tmp` и падает при расхождении; `ECON_REPORT.md`
проверяется тестами экономики на согласованность с моделью.

## 2. Ключевые числа (эталон: 2 500 ₽, 1,5 ячейки, 3 дня)

| Показатель | hub | mesh | Δ |
|---|---:|---:|---:|
| ПВЗ, ₽ | 27,50 | 42,50 | +15,00 |
| Маркетплейс, ₽ | 358,50 | 359,00 | +0,50 |
| Продавец, ₽ | 367,50 | 367,50 | 0,00 |
| Цепочка, ₽ | 753,50 | 769,00 | +15,50 |
| Покупатель, ₽ | 2 659,00 | 2 629,00 | −30,00 (−1,13%) |
| Срок, ч | 30 | 3 | −27 |

Межгород: цепочка 753,50 → 758,50 ₽, маркетплейс 358,50 → 348,50 ₽, срок 30 → 3 ч.
Безубыточность ПВЗ: 38 операций/день при базовой доле дня ПВЗ 30% (`pvz_day_mesh_share_pct`); при доле локальных пар 62% — 33 операции/день (диапазон 41–32 при 15–70%; `docs/ECON_REPORT.md`, 6.4). Очередь M/M/1 при цели 80%: 80% → 0,80 ч, 85% → 1,13 ч,
95% → 3,80 ч.

Симуляция 2000 заказов, seed 42: наивный mesh — 2000 опозданий, пик 226%, потери отмен
701 623,38 ₽, 107,15 ₽/заказ, маржа маркетплейса 5,75%; адаптивный с overflow — 953 опоздания,
136%, 351 902,12 ₽, 124,12 ₽/заказ, 10,00%. На 100 и 500 заказах режимы совпадают: перелив
включается только выше потолка сети 960 заказов/день.

## 3. Ветки и состояние репозитория

Актуальная ветка сдачи — `master`; финальные правки влиты merge-коммитом из `fix/final-review-and-ui-polish`
(точный хеш — `git log -1 master`). В `master` влиты и закрыты:

| Ветка | Что дала |
|---|---|
| `feat/economics-4sides-and-realism` | экономика четырёх сторон, калибровка 30 ₽ / 15 ₽ |
| `feat/load-aware-routing-and-evacuation` | overflow-роутинг, split-эвакуация, защита от гонок |
| `feat/placement-scorer-and-stock-audit` | скоринг размещения, аудит остатков, `parent_batch_id` |
| `feat/ui-green-zone-and-demo-cockpit` | зелёная зона 4 сторон, «Что если», демо-кокпит |
| `chore/ci-docs-and-submission-pack` | CI, синхронизация документов, submission pack |
| `fix/final-review-and-ui-polish` | правки по ревью (аудит, эвакуация, отчёты), демо-кокпит 5+2 сценария, визуал UI, состав команды |
| `feature/network-map`, `feature/node-failure` | карта сети, отказ узла |
| `fix/repo-docker-hygiene` | Dockerfile, `.dockerignore`, compose prod/dev |
| `docs/redteam-pilot` | red team и план пилота; содержимое вошло в `master` в `0bc6db1` и позже расширено, ветка удалена как устаревшая |

Все перечисленные ветки влиты в локальный `master`.

## 4. Контракт API

`docs/openapi.json` содержит 40 путей и генерируется из приложения. Формат отступов между
ветками менялся, поэтому сравнивать нужно **распарсенный JSON**, а не байты — именно это делает
job `contract` в `.github/workflows/ci.yml`.

Новые контракты после Веток 4–5:

| Метод | Путь | Ключевые поля |
|---|---|---|
| POST | `/api/v1/catalog/placement-score` | `sku`, `qty`, `cell_size`, `components[]` (вес, баллы), `score`, `threshold`, `recommendation`, `confidence_pct`, `expected_storage_cost_rub` |
| POST | `/api/v1/pvz/{pvz_id}/audit` | `actual{item_id: qty}`, `note`; ответ: `lines[]`, `matched`, `shortage`, `surplus`, `accuracy_pct`, `audit_accuracy_pct`, `sla_rating`, `incident_ids`, `penalty_total_rub`, `idempotent` |
| GET | `/api/v1/routes/compare` | `chosen`, `reason`, `reason_code`, `overflow`, `load` |
| GET | `/api/v1/orders/{id}` | `route.reason_code`, `route.overflow` |
| GET | `/api/v1/catalog` | `parent_batch_id` у партии |

`reason_code`: `mesh_same_city`, `mesh_cheapest`, `hub_cheaper`, `mesh_no_hub`, `hub_required`,
`overflow_to_hub`, `overflow_hub_unavailable`, `no_route`. Полные контракты — `docs/API.md`.

## 5. Честные ограничения провенанса

* Живой демо-стенд :8000/:5173 пересобран с чистого тома БД (`docker compose down -v && docker compose up -d --build`)
  и отдаёт текущие числа: `/economics/scenario` — доставка 129 ₽, итог 2 629 ₽.
* `docs/pitch/*.pptx` и `*.pdf` пересобраны под текущие параметры: слайд 5 — таблица четырёх
  сторон с продавцом (МП 358,50 → 359,00, покупатель 2 629,00, прибыль цепочки 753,50 → 769,00),
  слайд 8 — скриншот стенда.
* `docs/demo-backup.mp4` — видеозапись реального прогона; числа в кадре соответствуют версии
  приложения на момент записи, а не текущему `params.yaml`.

## 6. Состав и происхождение решения (Приложение 5)

Команда: Меркушев Михаил (капитан, архитектура, бэкенд-ядро, Docker/CI, интеграция и защита),
Казаков Евгений (маршрутизация и устойчивость, автотесты, контракт OpenAPI), Родионова Дарья
(экономическая модель и аналитика), Степанова Татьяна (фронтенд, UX, сценарии, презентация).
Подробное распределение — `docs/CONTRIBUTORS.md`. Коммиты пушились с основного рабочего
аккаунта команды по результатам совместной разработки.

**Написано в период хакатона (оригинальный код и материалы):** `api/app/*`, `api/tests/*`,
`economics/*` (модель, параметры, генератор отчётов), `scripts/*`, `web/src/*` (vanilla HTML/CSS/JS,
без сборки и CDN), `docker-compose*.yml`, Dockerfile, `.github/workflows/*`, `docs/*`, `docs/pitch/*`.

**Готовые открытые компоненты (не наш код):**

| Компонент | Версия (в окружении разработки) | Лицензия | Где используется |
|---|---|---|---|
| FastAPI | 0.165.0 | MIT | HTTP-слой API |
| Starlette (зависимость FastAPI) | 1.10.0 | BSD-3-Clause | роутинг, TestClient |
| Pydantic (зависимость FastAPI) | 2.14.4 | MIT | схемы запросов и ответов |
| Uvicorn | 0.54.0 | BSD-3-Clause | ASGI-сервер |
| SQLAlchemy | 2.1.9 | MIT | ORM и работа с БД |
| PyYAML | 6.0.4 | MIT | чтение `economics/params.yaml` |
| pytest | 9.2.0 | MIT | автотесты |
| httpx | 0.28.1 | BSD-3-Clause | тестовый клиент |
| nginx (образ `nginx:1.27-alpine`) | 1.27 | BSD-2-Clause | раздача статики и прокси `/api/v1` |
| Python (образ `python:3.12-slim`; локально 3.14) | 3.12 | PSF License | рантайм API |
| SQLite | встроена в Python | общественное достояние | БД прототипа |

Лицензии версий разработки проверены через `importlib.metadata`; для nginx, Python и SQLite
указаны лицензии проектов. Внешних шрифтов, CDN, фронтенд-фреймворков и иконочных наборов нет.

## 7. Использование ИИ-инструментов

При разработке использовались Hermes Agent (оболочка-агент) с моделями Claude Sonnet и DeepSeek:
рефакторинг и поиск расхождений между кодом и документами, генерация и расширение тестов,
черновики документации и вёрстка интерфейса. Продуктовые решения, тарифная модель и приёмка
результатов — за командой. Проверка результатов: автотесты (`api/tests`, `economics/tests`),
ручная сверка формул с `docs/ECON_MODEL.md`, перегенерация отчётов из `params.yaml` вместо
ручной правки, CI (контракт OpenAPI, симуляция) и проверка новых тестов на способность ловить
исходный дефект (откат исправления → тест краснеет).

## 8. Ограничения происхождения

Тексты, сгенерированные ИИ, правились и сверялись командой; ответственность за содержание несёт
команда, а не инструмент.
