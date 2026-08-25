"""Tests unitarios para la auditoría de descuentos y notas de crédito (discount_audit.py)."""

from decimal import Decimal

from cxc.engine.discount_audit import (
    EstadoAuditoria,
    TipoAuditoria,
    auditar_descuento_factura,
    auditar_descuento_orden,
    auditar_nota_credito,
)


def test_auditar_descuento_orden_coincide():
    res = auditar_descuento_orden(
        so_id="SO001",
        motor_total_descuentos=Decimal("10.00"),
        odoo_descuento_aplicado=Decimal("10.00"),
        tolerance_rounding=Decimal("0.01"),
        tolerance_red=Decimal("1.00"),
    )
    assert res.estado == EstadoAuditoria.OK
    assert not res.enviar_a_bandeja
    assert res.diferencia_usd == Decimal("0.00")
    assert res.descuento_adicional_a_aplicar == Decimal("0.00")


def test_auditar_descuento_orden_motor_mayor():
    # Motor calcula 15$, Odoo tiene 10$ -> dif +5.00$ -> DISCREPANCIA (enviar a bandeja)
    res = auditar_descuento_orden(
        so_id="SO002",
        motor_total_descuentos=Decimal("15.00"),
        odoo_descuento_aplicado=Decimal("10.00"),
        tolerance_rounding=Decimal("0.01"),
        tolerance_red=Decimal("1.00"),
    )
    assert res.estado == EstadoAuditoria.DISCREPANCIA
    assert res.enviar_a_bandeja
    assert res.diferencia_usd == Decimal("5.00")
    assert res.descuento_adicional_a_aplicar == Decimal("5.00")


def test_auditar_descuento_orden_odoo_mayor():
    # Odoo tiene 20$, Motor calcula 15$ -> dif -5.00$ -> DISCREPANCIA
    # (enviar a bandeja, pero adicional=0)
    res = auditar_descuento_orden(
        so_id="SO003",
        motor_total_descuentos=Decimal("15.00"),
        odoo_descuento_aplicado=Decimal("20.00"),
        tolerance_rounding=Decimal("0.01"),
        tolerance_red=Decimal("1.00"),
    )
    assert res.estado == EstadoAuditoria.DISCREPANCIA
    assert res.enviar_a_bandeja
    assert res.diferencia_usd == Decimal("-5.00")
    assert res.descuento_adicional_a_aplicar == Decimal("0.00")


def test_auditar_descuento_factura_discrepancia_menor():
    # Dif = 0.50$ -> entre rounding (0.01) y red (1.00) -> DISCREPANCIA_MENOR
    res = auditar_descuento_factura(
        so_id="SO004",
        motor_total_descuentos=Decimal("10.50"),
        odoo_descuento_factura=Decimal("10.00"),
        tolerance_rounding=Decimal("0.01"),
        tolerance_red=Decimal("1.00"),
    )
    assert res.estado == EstadoAuditoria.DISCREPANCIA_MENOR
    assert res.enviar_a_bandeja
    assert res.tipo == TipoAuditoria.DESCUENTO_FACTURA


def test_auditar_nota_credito():
    res_ok = auditar_nota_credito(
        so_id="SO005",
        motor_ncs_calculadas=Decimal("50.00"),
        odoo_nc_monto=Decimal("50.00"),
    )
    assert res_ok.estado == EstadoAuditoria.OK
    assert not res_ok.enviar_a_bandeja

    res_dif = auditar_nota_credito(
        so_id="SO006",
        motor_ncs_calculadas=Decimal("0.00"),
        odoo_nc_monto=Decimal("50.00"),
    )
    assert res_dif.estado == EstadoAuditoria.DISCREPANCIA
    assert res_dif.enviar_a_bandeja
    assert res_dif.tipo == TipoAuditoria.NOTA_CREDITO


