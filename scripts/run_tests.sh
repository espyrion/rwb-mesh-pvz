#!/usr/bin/env bash
# Полный прогон проверок: экономическая модель + API + пересборка отчёта.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "Нет .venv: python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt" >&2
  exit 1
fi

echo "== экономическая модель =="
"$PY" -m pytest "$ROOT/economics/tests" -q

echo "== отчёт =="
"$PY" "$ROOT/economics/report.py"

echo "== API =="
cd "$ROOT/api"
DATABASE_URL="sqlite:///$ROOT/.pytest-run.db" "$PY" -m pytest tests -q

echo "== OpenAPI =="
"$PY" "$ROOT/scripts/gen_openapi.py"

echo "OK"
