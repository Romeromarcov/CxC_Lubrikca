"""Normalización de montos y fechas con los formatos mixtos de los bancos venezolanos."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

CENT = Decimal("0.01")


def parse_monto(valor: Any) -> Decimal | None:
    """None si vacío. Acepta float/int, '1.002.945,86' (es), '58,509.32' (en), '69161775,62'.

    Los floats de Excel traen ruido binario (810744.25 vs 5277.489999999991): se
    redondea a centavos con ROUND_HALF_EVEN sobre su repr decimal.
    """
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        raise ValueError(f"monto inválido: {valor!r}")
    if isinstance(valor, int | float):
        return Decimal(repr(float(valor))).quantize(CENT)
    s = str(valor).strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("-()+")
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        mil = "." if dec == "," else ","
        s = s.replace(mil, "").replace(dec, ".")
    elif "," in s:
        # solo coma: decimal si tiene 1-2 dígitos al final, si no separador de miles
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif s.count(".") > 1 or re.search(r"^\d{1,3}\.\d{3}$", s):
        # '1.002.945' o '1.234': punto de miles. Los bancos siempre dan 2 decimales,
        # así que un único punto seguido de exactamente 3 dígitos no es decimal.
        s = s.replace(".", "")
    try:
        d = Decimal(s).quantize(CENT)
    except InvalidOperation as e:
        raise ValueError(f"monto inválido: {valor!r}") from e
    return -d if neg else d


def parse_fecha(valor: Any, datemode: int = 0) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, int | float):
        # serial de Excel (sistema 1900); xlrd.xldate_as_datetime hace lo mismo
        base = datetime(1899, 12, 30) if datemode == 0 else datetime(1904, 1, 1)
        return (base + timedelta(days=float(valor))).date()
    s = str(valor).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"fecha inválida: {valor!r}")


def limpiar_ref(valor: Any) -> str:
    """Referencias vienen como float (640065766.0), con apóstrofe ('19763674) o vacías."""
    if valor is None or valor == "":
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip().lstrip("'").strip()
