"""Auditoría de descuentos y notas de crédito — lógica pura (sin I/O).

Compara lo que el motor calcula contra lo que Odoo tiene registrado
(descuentos en líneas de orden/factura y notas de crédito).

Regla de negocio (principio rector):
  |diferencia| <= tolerance_rounding  → OK       (coincide)
  tolerance_rounding < |dif| <= tolerance_red → DISCREPANCIA_MENOR (va a bandeja)
  |diferencia| > tolerance_red        → DISCREPANCIA (va a bandeja)

Las NCs siempre reducen el saldo deudor independientemente del resultado.
Los descuentos de la orden: si el motor calcula más que Odoo, la diferencia
se aplica; si Odoo tiene más que el motor, va a bandeja sin penalizar al cliente.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class EstadoAuditoria(str, Enum):
    OK = "ok"
    DISCREPANCIA_MENOR = "discrepancia_menor"
    DISCREPANCIA = "discrepancia"


class TipoAuditoria(str, Enum):
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
    diferencia_usd: Decimal          # motor - odoo  (positivo = motor > odoo)
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
