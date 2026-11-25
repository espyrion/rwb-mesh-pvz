#!/usr/bin/env python3
"""Резервная копия базы без остановки API (SQLite backup API).

    python3 scripts/backup_db.py                     # mesh.db → backups/mesh-YYYYmmdd-HHMMSS.db
    BACKUP_DIR=/path python3 scripts/backup_db.py --db /path/mesh.db

Копия проверяется до вывода результата: `PRAGMA integrity_check` должен вернуть `ok`,
а в `sqlite_master` обязаны быть обе ключевые таблицы (`order`, `ledger`). Неполная или
битая копия удаляется, скрипт печатает причину в stderr и возвращает код 2.

Восстановление: остановить api, заменить файл базы копией, запустить api.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TABLES = ("order", "ledger")


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def verify(target: Path) -> str | None:
    """Проверяет копию; возвращает текст ошибки или None, если копия пригодна."""
    check = None
    try:
        check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        row = check.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            return f"integrity_check не пройден: {row[0] if row else 'нет ответа'}"
        tables = {
            name
            for (name,) in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    except sqlite3.Error as error:
        return f"не удалось прочитать копию: {error}"
    finally:
        if check is not None:
            check.close()
    missing = [name for name in REQUIRED_TABLES if name not in tables]
    if missing:
        return "в копии нет таблиц: " + ", ".join(missing)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Резервная копия SQLite через backup API.")
    parser.add_argument("--db", default=os.environ.get("MESH_DB", str(ROOT / "mesh.db")))
    parser.add_argument("--dir", default=os.environ.get("BACKUP_DIR", str(ROOT / "backups")))
    args = parser.parse_args()

    source = Path(args.db)
    if not source.exists():
        print(f"Нет базы {source}; укажите --db на существующий файл", file=sys.stderr)
        return 2
    if not source.is_file():
        print(f"{source} — не файл базы; укажите путь к файлу SQLite", file=sys.stderr)
        return 2

    target_dir = Path(args.dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"Не удалось создать каталог копий {target_dir}: {error}", file=sys.stderr)
        return 2
    target = target_dir / f"mesh-{datetime.now():%Y%m%d-%H%M%S}.db"

    try:
        with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
    except sqlite3.Error as error:
        target.unlink(missing_ok=True)
        print(f"Копирование {source} не удалось: {error}", file=sys.stderr)
        return 2

    problem = verify(target)
    if problem is not None:
        target.unlink(missing_ok=True)
        print(f"Копия {target} неполная или повреждена ({problem}); файл удалён", file=sys.stderr)
        return 2

    size = target.stat().st_size
    print(f"backup: {target} ({size} Б), integrity_check ok, таблицы {', '.join(REQUIRED_TABLES)} на месте")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
