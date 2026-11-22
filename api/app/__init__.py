"""Пакет API.

Первым делом кладём корень репозитория в sys.path: модули экономики
(economics/model.py, economics/scenario.py) — общий код API и отчётов, и они
должны импортироваться, даже если рядом лежит пустой каталог-точка монтирования
(./economics:/app/economics в compose создаёт его внутри api/ при запуске docker).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
