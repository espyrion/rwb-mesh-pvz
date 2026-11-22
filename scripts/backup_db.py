#!/usr/bin/env python3
"""Резервная копия базы без остановки API (SQLite backup API).

    python3 scripts/backup_db.py                     # mesh.db → backups/mesh-YYYYmmdd-HHMMSS.db
    BACKUP_DIR=/path python3 scripts/backup_db.py --db /path/mesh.db

Восстановление: остановить api, заменить файл базы копией, запустить api.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("MESH_DB", str(ROOT / "mesh.db")))
    parser.add_argument("--dir", default=os.environ.get("BACKUP_DIR", str(ROOT / "backups")))
    args = parser.parse_args()

    source = Path(args.db)
    if not source.exists():
        print(f"Нет базы {source}; укажите --db")
        return 1
    target_dir = Path(args.dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"mesh-{datetime.now():%Y%m%d-%H%M%S}.db"

    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    size = target.stat().st_size
    print(f"backup: {target} ({size} Б)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
