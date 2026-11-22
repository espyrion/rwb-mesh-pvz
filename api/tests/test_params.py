"""Валидация economics/params.yaml: опечатки и бессмысленные ставки падают сразу."""
import copy

import pytest
from pydantic import ValidationError

from app import params_schema
from app.config import params, reload_params


def _raw():
    return copy.deepcopy(params())


def test_repo_params_are_valid():
    data = reload_params()
    assert data["logistics"]["local_delivery_cost_rub"] == 90.0
    assert data["pvz"]["storage_rate_day_rub"] == 5.0


def test_unknown_key_is_rejected():
    raw = _raw()
    raw["pvz"]["storage_rate_day"] = 5.0  # опечатка: потерян суффикс _rub
    with pytest.raises(ValidationError) as exc:
        params_schema.validate(raw)
    assert "storage_rate_day" in str(exc.value)


def test_negative_rate_is_rejected():
    raw = _raw()
    raw["pvz"]["storage_rate_day_rub"] = -1
    with pytest.raises(ValidationError) as exc:
        params_schema.validate(raw)
    assert "storage_rate_day_rub" in str(exc.value)


def test_missing_section_is_rejected():
    raw = _raw()
    del raw["fees"]
    with pytest.raises(ValidationError) as exc:
        params_schema.validate(raw)
    assert "fees" in str(exc.value)


def test_hub_cheaper_than_local_is_rejected():
    raw = _raw()
    raw["logistics"]["local_delivery_cost_rub"] = 500.0
    with pytest.raises(ValidationError) as exc:
        params_schema.validate(raw)
    assert "дешевле" in str(exc.value)


def test_disclaimer_must_name_rwb():
    raw = _raw()
    raw["meta"]["disclaimer"] = "тарифы прототипа"
    with pytest.raises(ValidationError):
        params_schema.validate(raw)
