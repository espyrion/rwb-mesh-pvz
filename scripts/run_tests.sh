#!/usr/bin/env bash
# Полный прогон проверок: сверка артефактов + экономическая модель + API.
#
# Генерируемые файлы (docs/ECON_REPORT.md, docs/openapi.json) скрипт молча не
# перезаписывает: они пересобираются во временный файл, а при расхождении скрипт
# выходит с кодом 1 и просит перегенерировать артефакт.
#
# Сверка артефактов идёт ДО тестов намеренно: тест economics/test_report.py
# (test_report_is_written) сам пересобирает docs/ECON_REPORT.md через report.main(),
# поэтому при обратном порядке устаревший файл был бы затёрт и расхождение
# замаскировалось бы.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [ ! -x "$PY" ]; then
  echo "Нет .venv: python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt" >&2
  exit 1
fi

echo "== отчёт ECON_REPORT.md =="
# report.py не умеет --out, поэтому собираем отчёт его же build() во временный файл
# и сравниваем с docs/ECON_REPORT.md — сам артефакт не перезаписывается.
cd "$ROOT"
"$PY" - "$TMP_DIR/ECON_REPORT.md" <<'PY'
import sys

from economics.report import build

with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write(build())
PY
if ! cmp -s "$TMP_DIR/ECON_REPORT.md" "$ROOT/docs/ECON_REPORT.md"; then
  echo "docs/ECON_REPORT.md — артефакт устарел, перегенерируйте: .venv/bin/python economics/report.py" >&2
  exit 1
fi
echo "docs/ECON_REPORT.md актуален"

echo "== OpenAPI =="
"$PY" "$ROOT/scripts/gen_openapi.py" --out "$TMP_DIR/openapi.json" >/dev/null
if ! "$PY" - "$TMP_DIR/openapi.json" "$ROOT/docs/openapi.json" <<'PY'
import json
import sys

fresh = json.load(open(sys.argv[1], encoding="utf-8"))
current = json.load(open(sys.argv[2], encoding="utf-8"))
if fresh != current:
    only_app = sorted(set(fresh.get("paths", {})) - set(current.get("paths", {})))
    only_docs = sorted(set(current.get("paths", {})) - set(fresh.get("paths", {})))
    print("только в приложении:", only_app, file=sys.stderr)
    print("только в docs:", only_docs, file=sys.stderr)
    raise SystemExit(1)
print(f"docs/openapi.json актуален: {len(current.get('paths', {}))} путей")
PY
then
  echo "docs/openapi.json — артефакт устарел, перегенерируйте: .venv/bin/python scripts/gen_openapi.py" >&2
  exit 1
fi

echo "== экономическая модель =="
"$PY" -m pytest "$ROOT/economics/tests" -q

echo "== API =="
# conftest.py сам выставляет DATABASE_URL на временный каталог — отдельная база
# прогона не нужна и боевой стенд не затрагивается.
cd "$ROOT/api"
"$PY" -m pytest tests -q

cd "$ROOT"
echo "OK"
