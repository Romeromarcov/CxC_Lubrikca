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
#
# (La pieza 21, ``monto_de_descuento_de_linea``, que resolvia solo el monto para la
# lectura en vivo, se borro el 12-sep-2026 con esa lectura. La regla entera es
# ``descuento_de_linea``, abajo.)
PATRON_PORCENTAJE = "porcentaje"
PATRON_LINEA_NEGATIVA = "linea_negativa"


@dataclass(frozen=True)
class DescuentoDeLinea:
    """Cuanto descuento trae una linea, por que patron, y como se lee."""

    monto: float
    patron: str
    detalle: str


def es_linea_de_descuento(nombre_linea: str, nombre_producto: str) -> bool:
    """True si la linea ES un descuento (patron 2), por cualquiera de los dos nombres.

    **Los dos nombres, y el orden importa poco pero la union importa mucho.**
    Hallazgo real de agosto 2026 sobre la orden S00003: Odoo auto-genera el
    descuento por linea como una linea aparte cuyo NOMBRE PROPIO es
    ``"Discount 20.00%"`` --en ingles, sin importar el idioma de la UI-- mientras
    el producto vinculado ("Descuento ") si trae la palabra en espanol.

    Mirar solo el nombre del producto pierde las lineas que Odoo nombro en ingles;
    mirar solo el de la linea pierde las que el vendedor nombro a mano. El parity
    check contra las 819 ordenes reales dio 0 diffs recien con los dos.
    """
    return "descuento" in (nombre_linea or "").lower() or "descuento" in (
        nombre_producto or ""
    ).lower()


def descuento_de_linea(
    *,
    descuento_pct: float,
    cantidad: float,
    precio_unitario: float,
    subtotal: float,
    nombre_linea: str = "",
    nombre_producto: str = "",
) -> DescuentoDeLinea | None:
    """El descuento de una linea del espejo, o ``None`` si la linea no es un descuento.

    Vigesimotercera pieza de la Fase 2.4, y nace de un error propio. La pieza 21
    (``monto_de_descuento_de_linea``) extrajo la mitad del monto y la cableo en
    ``_leer_descuentos_lineas_odoo``, que **no tiene ningun llamador**: el camino
    vivo es ``_leer_descuentos_lineas_espejo``, que se quedo con su copia inline.
    O sea que la pieza 21 no redujo la duplicacion, la movio a codigo muerto.

    Y las dos copias no dicen lo mismo: la muerta decide si una linea es descuento
    por el nombre del PRODUCTO solamente --la regla vieja, la que perdia las lineas
    que Odoo nombra en ingles-- y la viva mira los dos nombres. Ver
    ``es_linea_de_descuento``.

    Esta pieza es la regla del camino vivo, entera: decide **si** la linea cuenta y
    **cuanto**, mas el fragmento legible que el Reporte de Saldos muestra. Devolver
    ``None`` en vez de cero es deliberado: una linea sin descuento no es una linea
    con descuento de cero, y el llamador tiene que saltearla sin sumar nada.

    La precedencia es la de la pieza 21 y no cambia: **con porcentaje, el subtotal
    negativo se ignora**, porque en el patron 1 el subtotal ya viene descontado y
    sumar los dos contaria el descuento dos veces.
    """
    if descuento_pct > 0:
        monto = cantidad * precio_unitario * (descuento_pct / 100.0)
        return DescuentoDeLinea(
            monto=monto,
            patron=PATRON_PORCENTAJE,
            detalle=f"{(nombre_linea or 'línea')[:40]}: {descuento_pct:.1f}%",
        )
    if subtotal < 0 and es_linea_de_descuento(nombre_linea, nombre_producto):
        monto = abs(subtotal)
        return DescuentoDeLinea(
            monto=monto,
            patron=PATRON_LINEA_NEGATIVA,
            detalle=f"{(nombre_linea or 'Descuento')[:40]}: ${monto:.2f}",
        )
    return None
