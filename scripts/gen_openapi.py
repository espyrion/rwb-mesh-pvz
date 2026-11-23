#!/usr/bin/env python3
"""Сохраняет OpenAPI-схему в docs/openapi.json (для жюри и клиентов)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Сохраняет OpenAPI-схему в docs/openapi.json или --out.")
    parser.add_argument("--out", type=Path, default=None, help="куда записать JSON (по умолчанию docs/openapi.json)")
    args = parser.parse_args()
    target = args.out or ROOT / "docs" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths = len(app.openapi()["paths"])
    print(f"{target}: {paths} путей, {target.stat().st_size} Б")


if __name__ == "__main__":
    main()
