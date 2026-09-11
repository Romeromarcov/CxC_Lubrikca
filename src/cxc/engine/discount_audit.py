"""Auditoría de DESCUENTOS y notas de crédito — lógica pura (sin I/O).

Compara lo que el motor calcula contra lo que Odoo tiene registrado
(descuentos en líneas de orden/factura y notas de crédito). Este es un
concepto DISTINTO de ``cxc_routing.BandejaDestino.AUDITORIA_PRECIOS``:
este módulo audita si el MONTO de descuento aplicado en Odoo coincide con
lo que el motor calcula (independiente de si la orden está pagada);
``cxc_routing`` audita si la orden salió de CxC por la LISTA de precios
correcta cuando el pago real cubrió la factura pero ningún teórico
(BS/USD) quedó pagado — cobertura de pago, no monto de descuento. Ambas
alimentan la misma tabla persistida (``repo.all_auditoria()`` /
``GET /api/auditoria-descuentos``) pero por rutas distintas del código.

Regla de negocio (principio rector):
  |diferencia| <= tolerance_rounding  → OK       (coincide)
  tolerance_rounding < |dif| <= tolerance_red → DISCREPANCIA_MENOR (va a bandeja)
  |diferencia| > tolerance_red        → DISCREPANCIA (va a bandeja)

Las NCs siempre reducen el saldo deudor independientemente del resultado.
Los descuentos de la orden: si el motor calcula más que Odoo
(``diferencia_usd > 0``), es descuento PENDIENTE por aplicar y la
diferencia se resta del saldo; si Odoo tiene más que el motor
(``diferencia_usd < 0``), es SOBRE-DESCUENTO -- Odoo aplicó más de lo que
el motor aprobaría. Un sobre-descuento va a bandeja para que se revise
por qué se aplicó (sin bloquear nada más del flujo), y bloquea aprobar
un descuento de sistema ADICIONAL sobre esa orden hasta que se revise
(ver ``_detectar_sobre_descuento_vigente`` en ``web/app.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any


class EstadoAuditoria(StrEnum):
    OK = "ok"
    DISCREPANCIA_MENOR = "discrepancia_menor"
    DISCREPANCIA = "discrepancia"


class TipoAuditoria(StrEnum):
    DESCUENTO_ORDEN = "descuento_orden"
    DESCUENTO_FACTURA = "descuento_factura"
    NOTA_CREDITO = "nota_credito"


@dataclass
class ResultadoAuditoria:
    """Resultado de la comparación motor vs Odoo para un campo concreto."""

    so_id: str
    tipo: TipoAuditoria
    motor_calcula_usd: Decimal
    odoo_registrado_usd: Decimal
    diferencia_usd: Decimal  # motor - odoo  (positivo = motor > odoo)
    estado: EstadoAuditoria
    enviar_a_bandeja: bool
    descuento_adicional_a_aplicar: Decimal  # solo para descuentos: max(0, motor - odoo)
    detalle_odoo: str = ""
    detalle_motor: str = ""


def _clasificar(
    diferencia: Decimal,
    tolerance_rounding: Decimal,
    tolerance_red: Decimal,
) -> EstadoAuditoria:
    mag = abs(diferencia)
    if mag <= tolerance_rounding:
        return EstadoAuditoria.OK
    if mag <= tolerance_red:
        return EstadoAuditoria.DISCREPANCIA_MENOR
    return EstadoAuditoria.DISCREPANCIA


def auditar_descuento_orden(
    so_id: str,
    motor_total_descuentos: Decimal,
    odoo_descuento_aplicado: Decimal,
    tolerance_rounding: Decimal = Decimal("0.01"),
    tolerance_red: Decimal = Decimal("1.00"),
    detalle_odoo: str = "",
    detalle_motor: str = "",
) -> ResultadoAuditoria:
    """Compara el descuento total calculado por el motor con el aplicado en
    la orden de Odoo (campo `discount` en `sale.order.line`).

    Lógica:
    - Si motor > odoo: hay descuento adicional que aplicar = motor - odoo.
    - Si odoo > motor: Odoo tiene más descuento del que el motor aprobaría → bandeja.
    - Si coinciden (dentro de tolerancia): OK, no hay diferencia que aplicar.

    El campo `descuento_adicional_a_aplicar` refleja solo la parte positiva:
    cuánto falta restar del saldo además de lo que Odoo ya tiene.
    """
    diferencia = motor_total_descuentos - odoo_descuento_aplicado
    estado = _clasificar(diferencia, tolerance_rounding, tolerance_red)
    enviar = estado != EstadoAuditoria.OK

    # Solo aplica diferencia positiva (motor calcula más); si odoo tiene más,
    # no sumamos de vuelta — el descuento ya está materializado en Odoo.
    adicional = max(Decimal("0"), diferencia)

    return ResultadoAuditoria(
        so_id=so_id,
        tipo=TipoAuditoria.DESCUENTO_ORDEN,
        motor_calcula_usd=motor_total_descuentos,
        odoo_registrado_usd=odoo_descuento_aplicado,
        diferencia_usd=diferencia,
        estado=estado,
        enviar_a_bandeja=enviar,
        descuento_adicional_a_aplicar=adicional,
        detalle_odoo=detalle_odoo,
        detalle_motor=detalle_motor,
    )


def auditar_descuento_factura(
    so_id: str,
    motor_total_descuentos: Decimal,
    odoo_descuento_factura: Decimal,
    tolerance_rounding: Decimal = Decimal("0.01"),
    tolerance_red: Decimal = Decimal("1.00"),
    detalle_odoo: str = "",
    detalle_motor: str = "",
) -> ResultadoAuditoria:
    """Compara el descuento del motor contra los descuentos en líneas de
    factura de Odoo (`account.move.line.discount`).

    Misma lógica que `auditar_descuento_orden` pero para facturas.
    """
    diferencia = motor_total_descuentos - odoo_descuento_factura
    estado = _clasificar(diferencia, tolerance_rounding, tolerance_red)
    enviar = estado != EstadoAuditoria.OK
    adicional = max(Decimal("0"), diferencia)

    return ResultadoAuditoria(
        so_id=so_id,
        tipo=TipoAuditoria.DESCUENTO_FACTURA,
        motor_calcula_usd=motor_total_descuentos,
        odoo_registrado_usd=odoo_descuento_factura,
        diferencia_usd=diferencia,
        estado=estado,
        enviar_a_bandeja=enviar,
        descuento_adicional_a_aplicar=adicional,
        detalle_odoo=detalle_odoo,
        detalle_motor=detalle_motor,
    )


def auditar_nota_credito(
    so_id: str,
    motor_ncs_calculadas: Decimal,
    odoo_nc_monto: Decimal,
    tolerance_rounding: Decimal = Decimal("0.01"),
    tolerance_red: Decimal = Decimal("1.00"),
    detalle_odoo: str = "",
    detalle_motor: str = "",
) -> ResultadoAuditoria:
    """Audita si la NC real de Odoo corresponde con lo que el motor calcula.

    La NC SIEMPRE se aplica al saldo (odoo_nc_monto reduce el deudor
    independientemente del resultado). El resultado solo determina si va
    a la bandeja de auditoría o no.

    diferencia = motor_ncs - odoo_nc:
      > 0 → el motor esperaba más NC de la que Odoo emitió → bandeja.
      < 0 → Odoo emitió más NC de la que el motor calcula → bandeja (NC inesperada).
      ≈ 0 → coincide → OK.
    """
    diferencia = motor_ncs_calculadas - odoo_nc_monto
    estado = _clasificar(diferencia, tolerance_rounding, tolerance_red)
    enviar = estado != EstadoAuditoria.OK

    return ResultadoAuditoria(
        so_id=so_id,
        tipo=TipoAuditoria.NOTA_CREDITO,
        motor_calcula_usd=motor_ncs_calculadas,
        odoo_registrado_usd=odoo_nc_monto,
        diferencia_usd=diferencia,
        estado=estado,
        enviar_a_bandeja=enviar,
        descuento_adicional_a_aplicar=Decimal("0"),  # NC no agrega diferencia al saldo
        detalle_odoo=detalle_odoo,
        detalle_motor=detalle_motor,
    )


def hay_sobre_descuento(*resultados: ResultadoAuditoria | None) -> ResultadoAuditoria | None:
    """El primero de ``resultados`` que sea un SOBRE-descuento, o ``None``.

    Decimocuarta pieza de la Fase 2.4, y la elegio una medicion: es la regla que
    decide si se bloquea la aprobacion de un descuento de sistema mas sobre una
    orden, y no tenia ninguna prueba -- ninguna de las 1.718 nombraba a
    ``_detectar_sobre_descuento_vigente``.

    **Sobre-descuento es la conjuncion de DOS condiciones**, y quedarse con una
    sola invierte el bloqueo:

    * ``enviar_a_bandeja``: la auditoria encontro una divergencia digna de
      revisarse. Se enciende en LAS DOS direcciones.
    * ``diferencia_usd < 0``: la divergencia va en la direccion mala, o sea Odoo
      aplico MAS descuento del que el motor dice que corresponde.

    Sin la segunda, una orden SUB-descontada --a la que se le dio MENOS de lo que
    le toca-- bloquearia la aprobacion de nuevos descuentos. Es exactamente el
    caso opuesto al que la guarda quiere frenar: a esa orden habria que darle mas,
    no menos.

    El orden de los argumentos importa y es el del llamador: primero la auditoria
    de la ORDEN, despues la de la FACTURA. Se devuelve el primero que califique,
    no una lista, porque quien llama solo necesita saber si bloquear y con que
    motivo mostrarlo.
    """
    for r in resultados:
        if r is None:
            continue
        if r.enviar_a_bandeja and r.diferencia_usd < Decimal("0"):
            return r
    return None


# Los dos patrones con los que Lubrikca materializa un descuento en una linea de
# Odoo. No son alternativas de estilo: conviven en los datos.
#
#   1. el campo ``discount`` con un porcentaje sobre la linea del producto
#   2. una linea aparte de un producto llamado "Descuento" con ``price_subtotal``
#      NEGATIVO
def monto_de_descuento_de_linea(linea: dict[str, Any]) -> float:
    """Cuanta plata de descuento representa una linea, con los dos patrones.

    Vigesimoprimera pieza de la Fase 2.4. La regla estaba escrita DOS VECES en
    ``_leer_descuentos_lineas_odoo`` --una para ``sale.order.line`` y otra para
    ``account.move.line``-- con una precedencia que ninguna prueba fijaba.

    **La precedencia: si hay porcentaje, el subtotal negativo se ignora.** Una
    linea con ``discount = 10`` y ``price_subtotal = -50`` cuenta 10 % de su
    importe, no 50. Es lo correcto para el patron 1 (donde el subtotal ya viene
    descontado y sumar los dos contaria el descuento dos veces), y hay que tenerlo
    presente si algun dia una linea llega con los dos.

    El porcentaje se aplica sobre ``cantidad x precio_unitario`` y no sobre el
    subtotal, justamente porque el subtotal ya lo tiene restado.

    ``cantidad`` viaja con dos nombres segun el modelo: ``product_uom_qty`` en las
    lineas de orden y ``quantity`` en las de factura. Se aceptan los dos, que es lo
    que permite que una sola funcion sirva a las dos mitades.

    **Nada de esto convierte moneda.** El llamador de las lineas de FACTURA aplica
    despues un ratio a dolares; el de las lineas de ORDEN no, porque las listas de
    precio estan fijadas en dolares por definicion de negocio. Esa asimetria es
    correcta y estaba sin escribir.
    """
    def _f(valor: Any) -> float:
        if valor is None or valor is False or valor == "":
            return 0.0
        try:
            return float(valor)
        except (TypeError, ValueError):
            return 0.0

    pct = _f(linea.get("discount"))
    if pct > 0:
        cantidad = _f(linea.get("product_uom_qty"))
        if cantidad == 0.0:
            cantidad = _f(linea.get("quantity"))
        return cantidad * _f(linea.get("price_unit")) * (pct / 100.0)
    # Patron 2: la linea ES el descuento, con su importe en negativo.
    return abs(_f(linea.get("price_subtotal")))
