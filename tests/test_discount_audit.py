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




# --- sobre-descuento: la conjunción de dos condiciones (11-sep-2026) --------


def test_sin_resultados_no_hay_sobre_descuento():
    from cxc.engine.discount_audit import hay_sobre_descuento

    assert hay_sobre_descuento() is None
    assert hay_sobre_descuento(None, None) is None


def test_odoo_aplico_mas_descuento_es_sobre_descuento():
    """El caso que la guarda existe para frenar."""
    from cxc.engine.discount_audit import hay_sobre_descuento

    res = auditar_descuento_orden(
        so_id="SO1",
        motor_total_descuentos=Decimal("10.00"),
        odoo_descuento_aplicado=Decimal("50.00"),
    )
    assert res.enviar_a_bandeja and res.diferencia_usd < 0
    assert hay_sobre_descuento(res) is res


def test_una_orden_SUB_descontada_NO_bloquea():
    """La mitad de la regla que es fácil de perder, y que invierte el bloqueo.

    ``enviar_a_bandeja`` se enciende en las DOS direcciones: también cuando Odoo
    aplicó MENOS descuento del que corresponde. Si la condición fuera sólo ésa,
    una orden a la que se le dio de menos bloquearía la aprobación de nuevos
    descuentos — justo al revés de lo que hay que hacer con ella.
    """
    from cxc.engine.discount_audit import hay_sobre_descuento

    res = auditar_descuento_orden(
        so_id="SO1",
        motor_total_descuentos=Decimal("50.00"),
        odoo_descuento_aplicado=Decimal("10.00"),
    )
    assert res.enviar_a_bandeja, "la divergencia sí va a la bandeja"
    assert res.diferencia_usd > 0, "pero en la dirección buena"
    assert hay_sobre_descuento(res) is None, "y por lo tanto NO bloquea"


def test_una_divergencia_que_no_va_a_la_bandeja_tampoco_bloquea():
    """Las dos condiciones son necesarias, no sólo la del signo."""
    from cxc.engine.discount_audit import hay_sobre_descuento

    res = auditar_descuento_orden(
        so_id="SO1",
        motor_total_descuentos=Decimal("10.00"),
        odoo_descuento_aplicado=Decimal("10.005"),
    )
    assert not res.enviar_a_bandeja, "medio centavo es redondeo"
    assert hay_sobre_descuento(res) is None


def test_gana_el_primero_que_califica_y_el_orden_es_orden_luego_factura():
    """El llamador pasa la auditoría de la ORDEN primero, y eso decide el motivo.

    Importa porque lo que se devuelve es lo que la pantalla muestra como razón del
    bloqueo: si el orden se invirtiera, una orden con exceso en los dos lados
    reportaría el de la factura y mandaría a mirar el lugar equivocado.
    """
    from cxc.engine.discount_audit import hay_sobre_descuento

    de_orden = auditar_descuento_orden(
        so_id="SO1",
        motor_total_descuentos=Decimal("10.00"),
        odoo_descuento_aplicado=Decimal("40.00"),
    )
    de_factura = auditar_descuento_factura(
        so_id="SO1",
        motor_total_descuentos=Decimal("10.00"),
        odoo_descuento_factura=Decimal("90.00"),
    )
    assert hay_sobre_descuento(de_orden, de_factura) is de_orden
    # Y si la de la orden no califica, pasa a la siguiente.
    sana = auditar_descuento_orden(
        so_id="SO1",
        motor_total_descuentos=Decimal("10.00"),
        odoo_descuento_aplicado=Decimal("10.00"),
    )
    assert hay_sobre_descuento(sana, de_factura) is de_factura


def test_los_none_se_saltean_sin_romper():
    """El llamador puede no tener una de las dos auditorías."""
    from cxc.engine.discount_audit import hay_sobre_descuento

    res = auditar_descuento_factura(
        so_id="SO1",
        motor_total_descuentos=Decimal("10.00"),
        odoo_descuento_factura=Decimal("99.00"),
    )
    assert hay_sobre_descuento(None, res, None) is res
