"""NC de descuento emitidas en Odoo contra lo que dice el motor.

Nació de la revisión del 30-sep-2026 sobre las NC de la última semana: 5 de 21 NC
de descuento coincidían con el motor al centavo, 9 caían cerca, y 3 lo excedían de
forma clara (S00531, S00714, S00913: NC del 73%, 59% y 58% de la base, más que el
tope del 35% del Diferencial). Nadie las había señalado porque no existía la
comparación. Esto la hace reejecutable.

Solo compara NC de DESCUENTO (las líneas del producto «Descuento» suman el monto total de
la NC): una NC de producto es
una devolución o una corrección de facturación, no un descuento, y compararla contra
el motor da un falso hallazgo. Las facturas que consolidan varias órdenes
(``so_id`` con coma) se dejan fuera: el monto de la NC no se puede atribuir a una sola
orden sin repartirlo.

Función pura: no lee el repositorio ni Odoo; recibe las listas ya cargadas.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..models import BandejaFacturacion, Factura, LineaFactura, Producto

COINCIDE = "coincide"
NC_MAYOR = "nc_mayor_que_motor"
NC_MENOR = "nc_menor_que_motor"
DECISION_COMERCIAL = "decision_comercial"

TOLERANCIA_ABSOLUTA = Decimal("1.00")
TOLERANCIA_RELATIVA = Decimal("0.02")  # sobre la base de la orden


@dataclass(frozen=True)
class HallazgoNC:
    so_id: str
    ncs: list[str]  # números de las NC de descuento de la orden
    ultima_nc: str  # fecha ISO de la más reciente
    nc_usd: Decimal  # suma sin IVA, a la tasa de la factura original
    motor_usd: Decimal  # total_descuentos + ncs_calculadas del motor
    precio_base: Decimal
    diferencia: Decimal  # nc_usd - motor_usd
    pct_base: Decimal  # nc_usd / precio_base (0 si no hay base)
    veredicto: str
    # Si la diferencia con el motor esta documentada como decision comercial: motivo y quien.
    decision_motivo: str = ""
    decidido_por: str = ""


def _ids_producto_descuento(catalogo: list[Producto]) -> set[str]:
    return {p.producto_id for p in catalogo if "descuento" in p.nombre.lower()}


def evaluar_ncs_contra_motor(
    facturas: list[Factura],
    lineas_factura: list[LineaFactura],
    catalogo: list[Producto],
    bandejas: list[BandejaFacturacion],
    decisiones_comerciales: dict[str, dict[str, str]] | None = None,
    descuentos_no_otorgados: dict[str, dict[str, str]] | None = None,
) -> list[HallazgoNC]:
    decisiones = decisiones_comerciales or {}
    no_otorgados = descuentos_no_otorgados or {}
    ids_descuento = _ids_producto_descuento(catalogo)
    if not ids_descuento:
        return []
    por_id = {f.factura_id: f for f in facturas}
    lineas_por_factura: dict[str, list[LineaFactura]] = {}
    for ln in lineas_factura:
        lineas_por_factura.setdefault(ln.factura_id, []).append(ln)
    bandeja_por_so = {b.so_id: b for b in bandejas}

    nc_por_so: dict[str, list[Factura]] = {}
    for f in facturas:
        if f.move_type != "out_refund" or f.estado != "posted":
            continue
        lineas = lineas_por_factura.get(f.factura_id, [])
        de_descuento = [ln for ln in lineas if ln.producto_id in ids_descuento]
        suma_descuento = sum((ln.subtotal for ln in de_descuento), Decimal("0"))
        # Es NC de descuento cuando las lineas «Descuento» explican TODO el monto de
        # la NC. No se mira el resto de las lineas: las NC reales traen ademas lineas
        # con producto que no son de venta (valuacion de inventario) y tienen
        # subtotal, asi que «las demas en cero» daba falsos negativos (S00531, S00913).
        tolerancia_monto = max(Decimal("0.01"), f.monto_sin_impuestos * Decimal("0.01"))
        if not de_descuento or abs(suma_descuento - f.monto_sin_impuestos) > tolerancia_monto:
            continue
        origen = por_id.get(f.factura_origen_id or "")
        so_id = (origen.so_id if origen else None) or f.so_id
        if not so_id or "," in so_id:
            continue
        nc_por_so.setdefault(so_id, []).append(f)

    hallazgos: list[HallazgoNC] = []
    for so_id, ncs in nc_por_so.items():
        bandeja = bandeja_por_so.get(so_id)
        if bandeja is None:
            continue
        nc_usd = sum((-f.monto_sin_impuestos_signed_usd for f in ncs), Decimal("0"))
        motor = bandeja.total_descuentos + bandeja.ncs_calculadas
        base = bandeja.precio_base_calculado
        diferencia = nc_usd - motor
        tolerancia = max(TOLERANCIA_ABSOLUTA, base * TOLERANCIA_RELATIVA)
        if diferencia > tolerancia:
            veredicto = NC_MAYOR
        elif diferencia < -tolerancia:
            veredicto = NC_MENOR
        else:
            veredicto = COINCIDE
        # Una diferencia documentada ya no es una anomalia: o se decidio comercialmente
        # (en cualquier direccion), o se marco que el remanente NO se otorgo (la NC quedo
        # por debajo del motor a proposito).
        decision = decisiones.get(so_id)
        if decision is None and veredicto == NC_MENOR:
            decision = no_otorgados.get(so_id)
        if decision is not None and veredicto != COINCIDE:
            veredicto = DECISION_COMERCIAL
        else:
            decision = None
        hallazgos.append(
            HallazgoNC(
                so_id=so_id,
                ncs=sorted(f.numero for f in ncs),
                ultima_nc=max(f.fecha for f in ncs).isoformat(),
                nc_usd=nc_usd.quantize(Decimal("0.01")),
                motor_usd=motor.quantize(Decimal("0.01")),
                precio_base=base.quantize(Decimal("0.01")),
                diferencia=diferencia.quantize(Decimal("0.01")),
                pct_base=(nc_usd / base).quantize(Decimal("0.0001")) if base > 0 else Decimal("0"),
                veredicto=veredicto,
                decision_motivo=(decision or {}).get("motivo", ""),
                decidido_por=(decision or {}).get("marcado_por", ""),
            )
        )
    hallazgos.sort(key=lambda h: abs(h.diferencia), reverse=True)
    return hallazgos
