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
    if (
        "*" in rcs
        or tc in rcs
        or pres in rcs
        or any(rc in tc for rc in rcs)
        or any(tc in rc for rc in rcs)
    ):
        return True
    if any(rc in ("CAJA", "COMERCIAL") for rc in rcs) and (
        tc in ("CAJA", "COMERCIAL") or "COMERCIAL" in tc or "CAJA" in tc or pres == "CAJA"
    ):
        return True
    if any(rc in ("PAILA", "INDUSTRIAL") for rc in rcs) and (
        tc in ("PAILA", "INDUSTRIAL") or "INDUSTRIAL" in tc or "PAILA" in tc or pres == "PAILA"
    ):
        return True
    return bool(
        any(rc in ("TAMBOR", "INDUSTRIAL") for rc in rcs)
        and (
            tc in ("TAMBOR", "INDUSTRIAL")
            or "INDUSTRIAL" in tc
            or "TAMBOR" in tc
            or pres == "TAMBOR"
        )
    )


def _match_marca(regla_marca: str, target_marca: str) -> bool:
    if not regla_marca or regla_marca == "*":
        return True
    if not target_marca:
        return False
    valid_marcas = [m.strip().upper() for m in str(regla_marca).split(",") if m.strip()]
    target_u = str(target_marca).strip().upper()
    return (
        "*" in valid_marcas
        or target_u in valid_marcas
        or any(vm in target_u or target_u in vm for vm in valid_marcas)
    )


def _match_lista(
    regla_listas: str,
    target_lista: str,
    valid_ves: list[str] | None = None,
    valid_usd: list[str] | None = None,
) -> bool:
    if not regla_listas or regla_listas == "*":
        return True
    if not target_lista:
        return True
    valid_listas = [ln.strip() for ln in str(regla_listas).split(",") if ln.strip()]
    if "*" in valid_listas:
        return True
    target_str = str(target_lista).strip()
    if target_str in valid_listas:
        return True
    if "LISTAS_VES" in valid_listas:
        ves_lists = [str(v) for v in (valid_ves or ["5", "3"])]
        if target_str in ves_lists:
            return True
    if "LISTAS_USD" in valid_listas:
        usd_lists = [str(u) for u in (valid_usd or ["4", "7", "8", "USD"])]
        if target_str in usd_lists:
            return True
    return False


def _match_producto_especial(
    regla: DescuentoMarcaCategoria, producto_nombre: str, categoria: str
) -> bool:
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
    valid_ves: list[str] | None = None,
    valid_usd: list[str] | None = None,
) -> DescuentoMarcaCategoria | None:
    """Fila de DescuentosMarcaCategoria vigente para (marca, categoría) a ``fecha``, lista y
    moneda."""
    candidatas = []
    for r in reglas:
        if r.tipo_descuento != tipo:
            continue
        if not _match_marca(r.marca, marca):
            continue
        if not _match_categoria(r.categoria, categoria, presentacion):
            continue
        if not _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha):
            continue
        if not _match_lista(r.listas_aplicables, lista_precios, valid_ves, valid_usd):
            continue
        if r.monedas_aplicables and r.monedas_aplicables != "*":
            valid_monedas = [
                m.strip().upper() for m in r.monedas_aplicables.split(",") if m.strip()
            ]
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
        r for r in reglas if _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha)
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
    marca: str,
    categoria: str,
    litros: Decimal,
    cantidad_unidades: Decimal = Decimal("0"),
    fecha: date = date(2026, 1, 1),
    lista_precios: str = "*",
) -> DescuentoVolumen | None:
    """Retorna la regla de descuento por volumen aplicable para la marca/categoría."""
    candidatas = []
    for r in reglas:
        if not _match_marca(r.marca, marca):
            continue
        if not _match_categoria(r.categoria, categoria):
            continue
        if not _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha):
            continue
        if not _match_lista(r.listas_aplicables, lista_precios):
            continue

        unidad = str(r.unidad_medida or "").upper()
        is_liters_rule = (unidad == "LITROS") or (
            r.litros_minimo > 0 and (r.min_cantidad is None or r.min_cantidad == 0)
        )
        if is_liters_rule:
            if litros < r.litros_minimo:
                continue
        else:
            val_eval = cantidad_unidades if cantidad_unidades > 0 else litros
            thresh = r.min_cantidad if (r.min_cantidad and r.min_cantidad > 0) else r.litros_minimo
            if val_eval < thresh:
                continue
            if r.max_cantidad and r.max_cantidad < 999999 and val_eval > r.max_cantidad:
                continue

        candidatas.append(r)

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
