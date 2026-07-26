"""Effective dating — selección de la fila vigente a una fecha (sección 8.4).

Sin esto, cambiar un descuento rompería la conciliación de órdenes anteriores
con falsos rojos: una orden de hace dos semanas debe auditarse con el % que
regía entonces, no con el de hoy.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..models import (
    Condicion,
    DescuentoBCVCompleto,
    DescuentoMarcaCategoria,
    DescuentoVolumen,
    PromocionPrimeraCompra,
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
    return not (vigencia_hasta is not None and fecha > vigencia_hasta)


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


def _match_categoria(regla_cat: str, target_cat: str, presentacion: str = "") -> bool:
    if not regla_cat or regla_cat == "*":
        return True
    rcs = [x.strip().upper() for x in regla_cat.split(",") if x.strip()]
    tc = (target_cat or "").strip().upper()
    pres = (presentacion or "").strip().upper()
    if "*" in rcs or tc in rcs or pres in rcs or any(rc in tc for rc in rcs) or any(tc in rc for rc in rcs):
        return True
    if any(rc in ("CAJA", "COMERCIAL") for rc in rcs) and (tc in ("CAJA", "COMERCIAL") or "COMERCIAL" in tc or "CAJA" in tc or pres == "CAJA"):
        return True
    if any(rc in ("PAILA", "INDUSTRIAL") for rc in rcs) and (tc in ("PAILA", "INDUSTRIAL") or "INDUSTRIAL" in tc or "PAILA" in tc or pres == "PAILA"):
        return True
    if any(rc in ("TAMBOR", "INDUSTRIAL") for rc in rcs) and (tc in ("TAMBOR", "INDUSTRIAL") or "INDUSTRIAL" in tc or "TAMBOR" in tc or pres == "TAMBOR"):
        return True
    return False


def _match_producto_especial(regla: DescuentoMarcaCategoria, producto_nombre: str, categoria: str) -> bool:
    regla_id = (regla.regla_id or "").upper()
    rc = (regla.categoria or "").upper()

    # If the rule specifically targets ELITE or SS
    if "ELITE" in regla_id or "SS" in regla_id or "ELITE" in rc or "SS" in rc:
        p_upper = (producto_nombre or "").upper()
        c_upper = (categoria or "").upper()
        keywords = ["ELITE", "SS", "EXTRA PROTECCION", "EXTRA PROTECCIÓN"]
        if not any(k in p_upper or k in c_upper for k in keywords):
            return False
    return True


def descuento_vigente(
    reglas: list[DescuentoMarcaCategoria],
    *,
    marca: str,
    categoria: str,
    tipo: TipoDescuento,
    fecha: date,
    lista_precios: str = "*",
    producto: str = "",
    moneda_pago: str = "USD",
    presentacion: str = "",
) -> DescuentoMarcaCategoria | None:
    """Fila de DescuentosMarcaCategoria vigente para (marca, categoría) a ``fecha``, lista y moneda."""
    candidatas = []
    for r in reglas:
        if r.tipo_descuento != tipo:
            continue
        if r.marca != "*" and r.marca != marca:
            continue
        if not _match_categoria(r.categoria, categoria, presentacion):
            continue
        if not _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha):
            continue
        if r.listas_aplicables != "*" and r.listas_aplicables != lista_precios:
            continue
        if r.monedas_aplicables and r.monedas_aplicables != "*":
            valid_monedas = [m.strip().upper() for m in r.monedas_aplicables.split(",") if m.strip()]
            if moneda_pago.upper() not in valid_monedas:
                continue
        if not _match_producto_especial(r, producto, categoria):
            continue
        candidatas.append(r)

    if not candidatas:
        return None
    return min(
        candidatas,
        key=lambda r: (-_especificidad(r), -r.porcentaje, r.regla_id),
    )


def promocion_primera_compra_vigente(
    promos: list[PromocionPrimeraCompra],
    *,
    fecha: date,
    cantidad_comercial: Decimal,
) -> PromocionPrimeraCompra | None:
    """Promoción de primera compra vigente a ``fecha`` con suficiente cantidad comercial."""
    candidatas = [
        p
        for p in promos
        if _vigente(p.vigencia_desde, p.vigencia_hasta, p.activo, fecha)
        and cantidad_comercial >= p.compra_minima
    ]
    if not candidatas:
        return None
    return max(candidatas, key=lambda p: (p.compra_minima, p.vigencia_desde))


def tasa_bcv_completo_vigente(
    reglas: list[DescuentoBCVCompleto],
    *,
    fecha: date,
) -> Decimal | None:
    """Tasa de descuento BCV-completo que fijó la gerencia, vigente a ``fecha``.

    Empate: gana la de ``vigencia_desde`` más reciente; luego el menor porcentaje
    (conservador). None si no hay tasa configurada → el motor no otorga el
    descuento (no se regala sin instrucción explícita).
    """
    candidatas = [
        r
        for r in reglas
        if _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha)
    ]
    if not candidatas:
        return None
    elegida = max(candidatas, key=lambda r: (r.vigencia_desde, -r.porcentaje))
    return elegida.porcentaje


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


def descuento_volumen_vigente(
    reglas: list[DescuentoVolumen],
    *,
    marca: str,
    categoria: str,
    litros: Decimal,
    fecha: date = date(2026, 1, 1),
    lista_precios: str = "*",
) -> DescuentoVolumen | None:
    """Retorna la regla de descuento por volumen aplicable para la marca/categoría."""
    candidatas = [
        r
        for r in reglas
        if (r.marca == marca or r.marca == "*")
        and _match_categoria(r.categoria, categoria)
        and litros >= r.litros_minimo
        and _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha)
        and (r.listas_aplicables == "*" or r.listas_aplicables == lista_precios)
    ]
    if not candidatas:
        return None

    def specificity(r: DescuentoVolumen) -> int:
        score = 0
        if r.marca != "*":
            score += 2
        if r.categoria != "*":
            score += 1
        return score

    return max(
        candidatas,
        key=lambda r: (specificity(r), r.porcentaje),
    )
