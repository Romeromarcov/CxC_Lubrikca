"""Tests de días hábiles y ventana de contado (sección 4.6)."""

from __future__ import annotations

from datetime import date

import pytest

from cxc.engine.business_days import (
    es_dia_habil,
    fin_ventana_contado,
    sumar_dias_habiles,
)


def test_ejemplo_spec_viernes_mas_3_habiles_es_miercoles() -> None:
    # Entrega viernes 5-jun-2026 + 3 hábiles → miércoles 10-jun (salta sáb/dom).
    entrega = date(2026, 6, 5)
    assert fin_ventana_contado(entrega, 3, frozenset()) == date(2026, 6, 10)


def test_dia_habil_con_feriado_extiende_la_ventana() -> None:
    # Con feriado el lunes 8-jun, la ventana corre al jueves 11-jun.
    entrega = date(2026, 6, 5)
    feriados = frozenset({date(2026, 6, 8)})
    assert fin_ventana_contado(entrega, 3, feriados) == date(2026, 6, 11)


def test_es_dia_habil() -> None:
    assert es_dia_habil(date(2026, 6, 5), frozenset())  # viernes
    assert not es_dia_habil(date(2026, 6, 6), frozenset())  # sábado
    assert not es_dia_habil(date(2026, 6, 7), frozenset())  # domingo
    assert not es_dia_habil(date(2026, 6, 5), frozenset({date(2026, 6, 5)}))


def test_sumar_cero_dias_devuelve_inicio() -> None:
    assert sumar_dias_habiles(date(2026, 6, 5), 0, frozenset()) == date(2026, 6, 5)


def test_sumar_negativo_falla() -> None:
    with pytest.raises(ValueError):
        sumar_dias_habiles(date(2026, 6, 5), -1, frozenset())
