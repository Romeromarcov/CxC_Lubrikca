"""Helpers de ``Decimal`` — dinero exacto, sin float.

Todo el sistema mueve plata: las tasas y equivalentes se manejan en ``Decimal``
y se redondea con ROUND_HALF_UP de forma explícita y centralizada.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
SEIS = Decimal("0.000001")


def to_decimal(value: object) -> Decimal:
    """Convierte a Decimal sin pasar por float (acepta str/int/Decimal)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool es subclase de int — evitar ambigüedad
        raise TypeError("No se convierte bool a Decimal")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(f"No se puede convertir {type(value)!r} a Decimal de forma segura")


def q2(value: Decimal) -> Decimal:
    """Redondea a 2 decimales (montos finales de factura)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def q6(value: Decimal) -> Decimal:
    """Redondea a 6 decimales (equivalentes congelados, preserva precisión)."""
    return value.quantize(SEIS, rounding=ROUND_HALF_UP)
