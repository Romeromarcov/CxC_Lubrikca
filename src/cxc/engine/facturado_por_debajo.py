"""Alerta de auditoria: se esta ordenando o facturando por debajo de lo que corresponde.

Decision del usuario (8-oct-2026): la base de los descuentos pasa a ser el MONTO REAL de la
orden (precio de cada linea menos su descuento de linea) y no el precio de lista. Eso es lo
correcto para calcular la NC, pero deja de delatar por si solo las ordenes que se vendieron o
facturaron por debajo de la lista. Esta alerta lo hace explicito, en dos niveles:

- ``orden_menor_que_lista``: lo que vale la orden (lineas con su descuento) es menor que su
  teorico a precio de lista. Normalmente es un descuento de linea; puede ser un precio mal
  cargado. Caso real: S00719 (3% por linea) y S00542 (4,59%).
- ``factura_menor_que_orden``: la factura emitida vale menos que la orden. Es el caso «se
  factura menos de lo que se debio facturar»: una linea omitida, un precio cambiado al facturar.

Es solo informativa: no cambia ningun monto. Funcion pura sobre datos ya cargados.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..models import Factura, LineaOrden, OrdenVenta, VentasTeorico

ORDEN_MENOR_QUE_LISTA = "orden_menor_que_lista"
FACTURA_MENOR_QUE_ORDEN = "factura_menor_que_orden"

# Margenes: un descuento de linea de hasta 5% es corriente (Quiroz: 3% y 4,59%) y no se alerta; la
# factura contra la orden esta en USD convertido a la tasa de la fecha de factura, asi que admite
# mas ruido (2%).
TOLERANCIA_LISTA = Decimal("0.05")
TOLERANCIA_FACTURA = Decimal("0.02")
TOLERANCIA_ABSOLUTA = Decimal("25")
_OBSEQUIO = Decimal("99.9")
_ESTADOS_FUERA = frozenset({"cancel", "cancelled", "draft", "sent"})


@dataclass(frozen=True)
class AlertaFacturadoPorDebajo:
    so_id: str
    tipo: str
    esperado: Decimal  # lo que deberia valer (lista, o la orden)
    real: Decimal  # lo que vale (la orden, o la factura)
    diferencia: Decimal  # esperado - real, positiva
    pct: Decimal
    detalle: str


def _valor_orden(lineas: list[LineaOrden]) -> tuple[Decimal, Decimal]:
    """(valor con el descuento de linea, valor bruto) -- un obsequio se valora completo."""
    neto = Decimal("0")
    bruto = Decimal("0")
    for ln in lineas:
        linea_bruta = ln.cantidad * ln.precio_unitario
        bruto += linea_bruta
        if ln.descuento <= 0 or ln.descuento >= _OBSEQUIO:
            neto += linea_bruta
        else:
            neto += linea_bruta * (Decimal("1") - ln.descuento / Decimal("100"))
    return neto, bruto


def evaluar_facturado_por_debajo(
    ordenes: list[OrdenVenta],
    lineas: list[LineaOrden],
    teoricos: list[VentasTeorico],
    facturas: list[Factura],
    listas_usd: set[str],
) -> list[AlertaFacturadoPorDebajo]:
    lineas_por_so: dict[str, list[LineaOrden]] = {}
    for ln in lineas:
        lineas_por_so.setdefault(ln.so_id, []).append(ln)
    teorico_por_so = {t.so_id: t for t in teoricos}
    facturado_por_so: dict[str, Decimal] = {}
    consolidadas: set[str] = set()
    for f in facturas:
        if f.move_type != "out_invoice" or f.estado != "posted" or not f.so_id:
            continue
        sos = [s.strip() for s in f.so_id.split(",") if s.strip()]
        if len(sos) > 1:
            consolidadas.update(sos)  # no se puede atribuir el monto a una sola orden
            continue
        facturado_por_so[sos[0]] = (
            facturado_por_so.get(sos[0], Decimal("0")) + f.monto_sin_impuestos_signed_usd
        )

    alertas: list[AlertaFacturadoPorDebajo] = []
    for o in ordenes:
        if (o.estado_orden or "").lower() in _ESTADOS_FUERA or o.tiene_devolucion:
            continue
        lns = lineas_por_so.get(o.so_id, [])
        if not lns:
            continue
        real_orden, bruto = _valor_orden(lns)
        if bruto <= 0:
            continue

        t = teorico_por_so.get(o.so_id)
        if t is not None:
            usd = str(o.lista_precios) in listas_usd
            usa_fallback = t.usa_fallback_usd if usd else t.usa_fallback_ves
            esperado = t.teorico_usd if usd else t.teorico_ves
            diferencia = esperado - real_orden
            if (
                not usa_fallback
                and esperado > 0
                and diferencia > TOLERANCIA_ABSOLUTA
                and diferencia > esperado * TOLERANCIA_LISTA
            ):
                descuento_linea = (bruto - real_orden) / bruto
                causa = (
                    f"descuento de linea de {descuento_linea * 100:.1f}%"
                    if descuento_linea > Decimal("0.001")
                    else "precio de linea menor que la lista"
                )
                alertas.append(
                    AlertaFacturadoPorDebajo(
                        so_id=o.so_id,
                        tipo=ORDEN_MENOR_QUE_LISTA,
                        esperado=esperado,
                        real=real_orden,
                        diferencia=diferencia,
                        pct=diferencia / esperado,
                        detalle=(
                            f"La orden vale {real_orden:,.2f} y la lista da {esperado:,.2f} "
                            f"(sin IVA): {causa}."
                        ),
                    )
                )

        facturado = facturado_por_so.get(o.so_id)
        # Una factura sin equivalente USD (0) es un dato que falta, no una factura de cero.
        if facturado is not None and facturado > 0 and o.so_id not in consolidadas:
            diferencia_f = real_orden - facturado
            if (
                diferencia_f > TOLERANCIA_ABSOLUTA
                and diferencia_f > real_orden * TOLERANCIA_FACTURA
            ):
                alertas.append(
                    AlertaFacturadoPorDebajo(
                        so_id=o.so_id,
                        tipo=FACTURA_MENOR_QUE_ORDEN,
                        esperado=real_orden,
                        real=facturado,
                        diferencia=diferencia_f,
                        pct=diferencia_f / real_orden,
                        detalle=(
                            f"La factura vale {facturado:,.2f} y la orden {real_orden:,.2f} "
                            f"(sin IVA, USD): se facturo menos de lo que se vendio."
                        ),
                    )
                )
    alertas.sort(key=lambda a: a.diferencia, reverse=True)
    return alertas
