"""Загрузка экономических параметров модели (economics/params.yaml).

Все тарифы прототипа — параметры модели, а не действующие тарифы RWB.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

_CANDIDATES = [
    Path("/app/economics/params.yaml"),
    Path(__file__).resolve().parents[2] / "economics" / "params.yaml",
    Path.cwd() / "economics" / "params.yaml",
    Path.cwd().parent / "economics" / "params.yaml",
]


def params_path() -> Path:
    env = os.getenv("ECON_PARAMS")
    if env and Path(env).exists():
        return Path(env)
    for c in _CANDIDATES:
        if c.exists():
            return c
    raise FileNotFoundError("economics/params.yaml not found; set ECON_PARAMS")


@lru_cache(maxsize=1)
def params() -> dict:
    with open(params_path(), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def reload_params() -> dict:
    params.cache_clear()
    return params()


def p(*keys: str, default=None):
    node = params()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
