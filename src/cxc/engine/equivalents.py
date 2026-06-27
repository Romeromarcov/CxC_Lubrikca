"""Equivalentes congelados por abono y regla de mezcla (sección 3.9b).

Cada abono registra su equivalente en ambas tasas, calculado UNA sola vez contra
la tasa estampada de su bucket horario, y NUNCA recalculado. Esto convierte la
valoración en dato auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..decimal_utils import q6
from ..models import Moneda, TipoTasa, Vinculacion


@dataclass(frozen=True)
class Equivalentes:
    equiv_usd_bcv: Decimal
    equiv_usd_binance: Decimal
    equiv_ves_bcv: Decimal
    equiv_ves_binance: Decimal


def calcular_equivalentes(
    monto_aplicado: Decimal,
    moneda_abono: Moneda,
    tasa_bcv: Decimal,
    tasa_binance: Decimal,
) -> Equivalentes:
    """Congela los cuatro equivalentes de un abono (ver fórmulas 3.9b)."""
    if tasa_bcv <= 0 or tasa_binance <= 0:
        raise ValueError("Las tasas estampadas deben ser positivas")
    m = monto_aplicado
    if moneda_abono == Moneda.VES:
        return Equivalentes(
            equiv_usd_bcv=q6(m / tasa_bcv),
            equiv_usd_binance=q6(m / tasa_binance),
            equiv_ves_bcv=q6(m),
            equiv_ves_binance=q6(m),
        )
    # Abono en USD
    return Equivalentes(
        equiv_usd_bcv=q6(m),
        equiv_usd_binance=q6(m),
        equiv_ves_bcv=q6(m * tasa_bcv),
        equiv_ves_binance=q6(m * tasa_binance),
    )


def congelar_en_vinculacion(vinc: Vinculacion) -> Vinculacion:
    """Calcula y estampa los cuatro equivalentes en la vinculación, UNA vez.

    Si ya estaban congelados, no los recalcula (inmutabilidad 3.9b).
    """
    if vinc.equiv_usd_bcv is not None:
        return vinc
    eq = calcular_equivalentes(
        vinc.monto_aplicado,
        vinc.moneda_abono,
        vinc.tasa_bcv_aplicada,
        vinc.tasa_binance_aplicada,
    )
    vinc.equiv_usd_bcv = eq.equiv_usd_bcv
    vinc.equiv_usd_binance = eq.equiv_usd_binance
    vinc.equiv_ves_bcv = eq.equiv_ves_bcv
    vinc.equiv_ves_binance = eq.equiv_ves_binance
    return vinc


def es_ruta_bcv_pura(vinculaciones: list[Vinculacion]) -> bool:
    """True si TODOS los abonos fueron en ruta BCV (sección 3.9b, mezcla).

    Sin abonos no se puede afirmar "completo en BCV" → False (conservador).
    """
    if not vinculaciones:
        return False
    return all(v.tipo_tasa_abono == TipoTasa.BCV for v in vinculaciones)


def valor_pagado_usd(vinculaciones: list[Vinculacion]) -> Decimal:
    """Σ de los equivalentes USD congelados, cada uno según la ruta del abono.

    No recalcula: suma lo ya estampado (sección 4.4).
    """
    total = Decimal("0")
    for v in vinculaciones:
        if v.tipo_tasa_abono == TipoTasa.BCV:
            eq = v.equiv_usd_bcv
        elif v.tipo_tasa_abono == TipoTasa.BINANCE:
            eq = v.equiv_usd_binance
        else:  # USD directo — ambos equivalentes USD son el monto
            eq = v.equiv_usd_binance
        if eq is None:
            raise ValueError(
                f"Vinculación {v.vinc_id} sin equivalentes congelados; "
                "llamar congelar_en_vinculacion primero"
            )
        total += eq
    return total
