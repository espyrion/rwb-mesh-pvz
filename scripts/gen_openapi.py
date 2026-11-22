#!/usr/bin/env python3
"""Сохраняет OpenAPI-схему в docs/openapi.json (для жюри и клиентов)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from app.main import app  # noqa: E402


def main() -> None:
    target = ROOT / "docs" / "openapi.json"
    target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths = len(app.openapi()["paths"])
    print(f"docs/openapi.json: {paths} путей, {target.stat().st_size} Б")


if __name__ == "__main__":
    main()
