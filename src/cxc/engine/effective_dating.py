"""Effective dating — selección de la fila vigente a una fecha (sección 8.4).

Sin esto, cambiar un descuento rompería la conciliación de órdenes anteriores
con falsos rojos: una orden de hace dos semanas debe auditarse con el % que
regía entonces, no con el de hoy.
"""

from __future__ import annotations

from datetime import date

from ..models import (
    Condicion,
    DescuentoMarcaCategoria,
    ReglaRecurrencia,
    TipoDescuento,
)


def _vigente(
    vigencia_desde: date,
    vigencia_hasta: date | None,
    activo: bool,
    fecha: date,
) -> bool:
    if not activo:
        return False
    if fecha < vigencia_desde:
        return False
    if vigencia_hasta is not None and fecha > vigencia_hasta:
        return False
    return True


def _especificidad(regla: DescuentoMarcaCategoria) -> int:
    """Prioridad de comodines: marca exacta pesa más que categoría exacta.

    (marca exacta, categoría exacta) = 3 > (marca exacta, '*') = 2 >
    ('*', categoría exacta) = 1 > ('*', '*') = 0.
    """
    score = 0
    if regla.marca != "*":
        score += 2
    if regla.categoria != "*":
        score += 1
    return score


def descuento_vigente(
    reglas: list[DescuentoMarcaCategoria],
    *,
    marca: str,
    categoria: str,
    tipo: TipoDescuento,
    fecha: date,
) -> DescuentoMarcaCategoria | None:
    """Fila de DescuentosMarcaCategoria vigente para (marca, categoría) a ``fecha``.

    Resuelve comodines por especificidad. Empates (configuración inconsistente)
    se rompen de forma conservadora: menor porcentaje (no regalar descuento),
    luego ``regla_id`` para determinismo.
    """
    candidatas = [
        r
        for r in reglas
        if r.tipo_descuento == tipo
        and (r.marca == marca or r.marca == "*")
        and (r.categoria == categoria or r.categoria == "*")
        and _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha)
    ]
    if not candidatas:
        return None
    return min(
        candidatas,
        key=lambda r: (-_especificidad(r), r.porcentaje, r.regla_id),
    )


def regla_recurrencia_vigente(
    reglas: list[ReglaRecurrencia],
    *,
    condicion: Condicion,
    fecha: date,
) -> ReglaRecurrencia | None:
    """Regla de recurrencia vigente para la condición dada a ``fecha``.

    Empate: gana la de ``vigencia_desde`` más reciente (la regla más nueva
    aplicable), luego menor ``valor`` por conservadurismo.
    """
    candidatas = [
        r
        for r in reglas
        if r.condicion == condicion
        and _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha)
    ]
    if not candidatas:
        return None
    return max(
        candidatas,
        key=lambda r: (r.vigencia_desde, -r.valor),
    )
