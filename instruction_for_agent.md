# Инструкция для агента: проект Mesh-ПВЗ

Локальный репозиторий: `/home/stingcorpse/mesh-pvz`, ветка `master`, remote `origin` =
`https://github.com/espyrion/rwb-mesh-pvz.git`.
Это учебный MVP хакатона RWB: заказ собирается из остатка, уже лежащего в ПВЗ, маршрут идёт по mesh
(без хаба) там, где это выгодно, а хаб/магистраль/платёжный шлюз — явно помеченные заглушки.
Реальных платежей и перевозок нет.

Состояние проекта, эталонные числа и правила работы — в `.local/СОСТОЯНИЕ.md` (каталог в `.gitignore`,
но на этой машине он есть). Читать его первым делом.

## 1. Дерево файлов

```
mesh-pvz/
├── docker-compose.yml          # production: api :8000 + web :5173, том meshdata
├── docker-compose.dev.yml      # overlay для разработки: --reload, монтирование исходников
├── .env.example                # шаблон переменных (демо-ключи, DATABASE_URL, API_URL)
├── .github/workflows/ci.yml    # CI: api (py3.11/3.12), economics, web-lint, contract, simulation
├── README.md                   # главный документ: запуск, роли, экраны
│
├── api/                        # FastAPI + SQLAlchemy + SQLite
│   ├── Dockerfile              # python:3.12-slim, копирует api/ и economics/
│   ├── requirements.txt        # fastapi, uvicorn[standard], SQLAlchemy, PyYAML, pytest, httpx
│   ├── pytest.ini
│   ├── app/
│   │   ├── main.py             # сборка приложения, подключение роутеров под /api/v1
│   │   ├── models.py           # таблицы: ПВЗ, партии, заказы, маршруты, ledger, инциденты
│   │   ├── db.py  config.py  auth.py   # сессия, настройки, роли по X-API-Key
│   │   ├── routing.py          # выбор mesh/hub, overflow, переадресация
│   │   ├── money.py            # проводки ledger и сходимость по сторонам
│   │   ├── storage.py  clock.py  notify.py  node_failure.py  placement.py  audit.py
│   │   ├── seed.py             # первичное наполнение: 8 ПВЗ, хаб, 2 продавца, 6 товаров
│   │   ├── params_schema.py    # валидация economics/params.yaml
│   │   ├── schemas.py  serializers.py  responses.py  paging.py
│   │   └── routers/            # orders, returns, catalog, economics, incidents, clock_api,
│   │                           # routes_api, analytics, notifications, track
│   └── tests/                  # 135 тестов: flow, маршруты, деньги, сценарии, контракт OpenAPI
│
├── economics/                  # экономическая модель (общий пакет, импортируется API)
│   ├── params.yaml             # ЕДИНСТВЕННЫЙ источник тарифов и порогов
│   ├── model.py  scenario.py  network.py  report.py
│   └── tests/                  # 14 тестов
│
├── web/                        # UI без сборки: nginx + vanilla JS
│   ├── Dockerfile  nginx.conf  nginx.dev.conf
│   └── src/  index.html  app.js  map.js  styles.css
│
├── scripts/
│   ├── demo.py                 # 7 сценариев PASS/FAIL из терминала
│   ├── simulate.py             # симуляция дня сети → docs/SCALE.md
│   ├── gen_openapi.py          # docs/openapi.json
│   ├── backup_db.py            # копия SQLite на ходу
│   ├── record_demo.py          # запись docs/demo-backup.mp4 (Playwright)
│   └── run_tests.sh            # всё сразу: экономика + отчёт + API + OpenAPI
│
├── docs/                       # 15 файлов
│   ├── API.md  ECON_MODEL.md  ECON_REPORT.md  SCENARIOS.md  SCENARIOS_REPORT.md
│   ├── PROVENANCE.md  REDTEAM.md  PILOT.md  SCALE.md  REPLICATION.md  SUBMISSION.md
│   ├── openapi.json            # генерируется, CI сверяет с приложением
│   ├── VIDEO_DEMO_SCRIPT.md    # сценарий видео
│   ├── demo-backup.mp4         # резервная запись (2,5 МБ, 1:59)
│   ├── pitch/                  # slides.html, SPEECH.md, PDF
│   └── screenshots/            # 11 PNG для README и презентации
│
├── .local/                     # НЕ в git: СОСТОЯНИЕ.md, обзор для ревью, база до доработки
├── .venv/                      # НЕ в git: Python 3.14, все зависимости + playwright
└── tests/                      # пустой каталог-артефакт, реальные тесты в api/tests и economics/tests
```

Всего 115 файлов под контролем версий. `*.db`, WAL/SHM, `.venv/`, `.local/`, `.env`, `__pycache__` — в `.gitignore`.

## 2. Зависимости

Обязательные:

| Что | Версия на этой машине | Зачем |
|---|---|---|
| Docker + Compose | 29.10 / 5.8 | основной способ запуска (api + web) |
| Python | 3.14.9 в `.venv` (CI гоняет 3.11 и 3.12) | API, тесты, скрипты |
| `api/requirements.txt` | fastapi≥0.115, uvicorn[standard]≥0.30, SQLAlchemy≥2.0, PyYAML≥6.0, pytest≥8.0, httpx≥0.27 | уже стоят в `.venv` |
| Node.js | 26.13 | только `node --check` для `web/src/*.js`, сборки нет |
| ffmpeg / ffprobe | 9.1 | перекодирование видео в `record_demo.py` |

Опционально (только для перезаписи видео):

```bash
.venv/bin/pip install playwright
.venv/bin/python -m playwright install chromium   # ~126 МБ в ~/.cache/ms-playwright
```

`playwright` и `pillow` стоят в `.venv`, но в `api/requirements.txt` их нет — образ они не нужны.

## 3. Как запустить

### Docker (рекомендуется)

```bash
cd /home/stingcorpse/mesh-pvz
docker compose up -d --build          # production: код запекается в образ
# или dev: исходники смонтированы, uvicorn --reload
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

* UI — http://localhost:5173, API и Swagger — http://localhost:8000/docs
* Проверка: `curl -sf http://localhost:8000/health` и
  `curl -sf http://localhost:8000/api/v1/health/deep` (там `status` и `counts`)
* Первый запуск сам создаёт БД в томе `meshdata` (`/data/mesh.db`) и наполняет её
* Сброс к чистому состоянию: `docker compose down -v && docker compose up -d --build`
* После правок `api/` или `web/src` в production-режиме **обязательна пересборка** — иначе на портах
  останется старый код. В dev-режиме пересборка нужна только при изменении зависимостей или Dockerfile.

### Без Docker

```bash
python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt
cd api && DATABASE_URL=sqlite:///../mesh.db ../.venv/bin/uvicorn app.main:app --reload --port 8000
```

UI без nginx не откроется (`web/src/index.html` ходит в `/api/...`) — нужен прокси на :8000,
проще пользоваться Docker-вариантом `docker compose up -d web`.

### Роли API

Запись — по заголовку `X-API-Key`; в UI роль переключается селектором в левом нижнем углу.

| Роль | Ключ | Права |
|---|---|---|
| ПВЗ | `demo-pvz-key` | сборка, транзит, выдача, возврат, перемещение остатка, инциденты |
| Продавец | `demo-seller-key` | создание заказа и партии, отмена |
| Маркетплейс (admin) | `demo-admin-key` | всё, включая сегменты сети и виртуальные часы |

Ключи меняются переменными `MESH_PVZ_KEY`, `MESH_SELLER_KEY`, `MESH_ADMIN_KEY`. Это демо-значения,
не секреты.

## 4. Как проверить, что всё живо

```bash
bash scripts/run_tests.sh        # экономика + отчёт + API (149 тестов) + перегенерация OpenAPI
curl -sf http://localhost:8000/api/v1/health/deep
.venv/bin/python scripts/demo.py # 7 сценариев, ожидается PASS по всем (на чистой БД)
```

Тесты по отдельности:

```bash
cd api && DATABASE_URL=sqlite:////tmp/mesh-pytest.db ../.venv/bin/python -m pytest tests -q --tb=short
cd .. && .venv/bin/python -m pytest economics/tests -q --tb=short
node --check web/src/app.js && node --check web/src/map.js
```

## 5. Правила работы

* Числа — только из `economics/params.yaml` или из ответа API. Никаких «примерно» и зашитых в вёрстку
  констант: пороги и ставки приходят с сервера.
* Живой стенд :8000/:5173 не ломать. Прогоны, меняющие состояние (демо-кокпит, симуляции), — на копии
  базы или на отдельном API :8001 с `DATABASE_URL=sqlite:////tmp/mesh-demo.db`.
* Одна фича — одна ветка, слияние в `master` после зелёных тестов.
* После правок эндпоинтов: `.venv/bin/python scripts/gen_openapi.py` (иначе упадёт джоба `contract`).
* После правок симуляции: `.venv/bin/python scripts/simulate.py --write-md docs/SCALE.md`
  (иначе упадёт `simulation-smoke`).
* Числа в документации и презентации должны совпадать с API: эталон — 2 649 ₽ для покупателя (mesh),
  149 ₽ доставка (mesh), 90 ₽ / 3 ч mesh против 135,50 ₽ / 30 ч через хаб.

## 6. Грабли, на которые уже наступали

* **Заказ в тот же ПВЗ, где лежит товар** → `422 «точка выдачи совпадает с местом хранения»`.
  В форме продавца по умолчанию выбран YOL-01, а товар TD-1001 лежит там же: выбирайте другой пункт.
* **`pip install` падает с `PoolKey.__new__() got an unexpected keyword argument 'key_proxy_ssl_context'`** —
  в шелле висят `ALL_PROXY`/`HTTPS_PROXY=socks5://127.0.0.1:10808`. Ставить пакеты так:
  `env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy .venv/bin/pip install …`
* **`docker compose up -d` из агентской сессии блокируется guard'ом** как «long-lived server».
  Запускать в фоне (`background=true`) и отдельной командой проверять health.
* **Push в GitHub напрямую не идёт** (403). Только через прокси и токен из `~/.hermes/.env`
  (`GITHUB_TOKEN`), не сохраняя его в файлы репозитория:
  `git -c http.proxy=socks5h://127.0.0.1:10808 push "https://x-access-token:$TOKEN@github.com/espyrion/rwb-mesh-pvz.git" master:master`
* **Скоринг размещения отдаёт 403**, если роль API — «ПВЗ». Для скоринга и отказа узла нужна роль
  «Маркетплейс».
* **Демо-кокпит и запись видео требуют чистой БД**: на грязной базе шаг 1 падает по overflow, а шаг 4
  показывает «эвакуировано партий: 0». Перед прогоном — `docker compose down -v && docker compose up -d`.
* **Старые заказы не пересчитываются** после смены `params.yaml`: числа в БД остаются прежними,
  пересчёт только у новых операций.
* **Python 3.14 локально против 3.11/3.12 в CI** — если тест зелёный локально, а в CI красный, смотрите
  на версионные различия, а не на логику.

## 7. Куда смотреть дальше

* `README.md` — запуск, роли, описание всех экранов.
* `docs/API.md` — контракт, коды ошибок, скоринг и аудит.
* `docs/ECON_MODEL.md` — модель четырёх сторон и формулы.
* `docs/PROVENANCE.md` — откуда взято каждое число (раздел 5 — честные ограничения).
* `docs/VIDEO_DEMO_SCRIPT.md` — что и в каком порядке показывать в демо.
* `.local/СОСТОЯНИЕ.md` — что готово, известные хвосты, быстрые команды.
