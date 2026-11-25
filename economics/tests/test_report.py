"""Отчёт пересобирается и содержит обязательные разделы и числа."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from economics.report import build  # noqa: E402


def test_report_sections_and_numbers():
    text = build()
    for section in (
        "Экономическая модель и отчёт о расчётах",
        "Городской заказ",
        "Межгородской заказ",
        "Выгодность ПВЗ",
        "Устойчивость модели",
        "Что не включено",
    ):
        assert section in text
    assert "135,50" in text  # стоимость схемы через хаб
    assert "90,00" in text
    assert "39" in text  # порог загрузки ПВЗ
    assert "не являются действующими тарифами" in text
    assert "mesh" in text and "viable = True" in text


def test_report_file_is_current():
    """docs/ECON_REPORT.md должен совпадать с выводом build(): руками не правится."""
    from economics.report import OUT

    assert OUT.exists()
    assert OUT.read_text(encoding="utf-8") == build()


def test_report_is_written():
    from economics.report import main

    path = main()
    assert path.exists() and path.stat().st_size > 5000
