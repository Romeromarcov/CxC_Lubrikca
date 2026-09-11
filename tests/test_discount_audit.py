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


# --- los dos patrones de descuento de línea (Fase 2.4, pieza 21) ------------


def test_el_patron_del_porcentaje_se_aplica_sobre_cantidad_por_precio():
    """Y no sobre el subtotal, porque el subtotal ya lo tiene restado.

    Aplicarlo sobre el subtotal contaría el descuento dos veces.
    """
    from cxc.engine.discount_audit import monto_de_descuento_de_linea

    linea = {"discount": 10, "product_uom_qty": 5, "price_unit": 100, "price_subtotal": 450}
    assert monto_de_descuento_de_linea(linea) == 50.0


def test_el_patron_de_la_linea_negativa_toma_su_importe():
    """Lubrikca también carga descuentos como una línea de producto «Descuento»
    con el subtotal en negativo."""
    from cxc.engine.discount_audit import monto_de_descuento_de_linea

    assert monto_de_descuento_de_linea({"discount": 0, "price_subtotal": -75.5}) == 75.5


def test_si_hay_PORCENTAJE_el_subtotal_negativo_se_IGNORA():
    """La precedencia que ninguna prueba fijaba, y que hay que tener presente.

    Una línea con las dos cosas cuenta el porcentaje, no el subtotal. Es correcto
    para el patrón 1 —donde el subtotal ya viene descontado— pero significa que si
    algún día una línea llega con ambos, el segundo no se suma.
    """
    from cxc.engine.discount_audit import monto_de_descuento_de_linea

    linea = {"discount": 10, "product_uom_qty": 1, "price_unit": 100, "price_subtotal": -999}
    assert monto_de_descuento_de_linea(linea) == 10.0, "gana el porcentaje"


def test_la_cantidad_viaja_con_dos_nombres_segun_el_modelo():
    """``product_uom_qty`` en las líneas de orden, ``quantity`` en las de factura.

    Aceptar los dos es lo que permite que una sola función sirva a las dos mitades
    de ``_leer_descuentos_lineas_odoo``, que antes tenían la regla duplicada.
    """
    from cxc.engine.discount_audit import monto_de_descuento_de_linea

    de_orden = {"discount": 50, "product_uom_qty": 2, "price_unit": 100}
    de_factura = {"discount": 50, "quantity": 2, "price_unit": 100}
    assert monto_de_descuento_de_linea(de_orden) == monto_de_descuento_de_linea(de_factura) == 100.0


def test_un_descuento_del_100_por_ciento_da_el_importe_completo():
    """El caso del obsequio, visto desde este lado."""
    from cxc.engine.discount_audit import monto_de_descuento_de_linea

    linea = {"discount": 100, "quantity": 1, "price_unit": 35.81}
    assert monto_de_descuento_de_linea(linea) == 35.81


def test_los_campos_ausentes_o_ilegibles_valen_cero_y_no_revientan():
    """XML-RPC manda ``False`` por un campo vacío, no ``None``."""
    from cxc.engine.discount_audit import monto_de_descuento_de_linea

    assert monto_de_descuento_de_linea({}) == 0.0
    assert monto_de_descuento_de_linea({"discount": False, "price_subtotal": False}) == 0.0
    assert monto_de_descuento_de_linea({"discount": "ilegible", "price_subtotal": "-10"}) == 10.0


def test_una_linea_sin_descuento_ni_subtotal_negativo_no_aporta():
    """El filtro de Odoo no debería traerla, pero la función no lo asume."""
    from cxc.engine.discount_audit import monto_de_descuento_de_linea

    assert monto_de_descuento_de_linea({"discount": 0, "price_subtotal": 0}) == 0.0


# --- la regla entera del camino vivo (Fase 2.4, pieza 23) --------------------


class TestDescuentoDeLinea:
    """`descuento_de_linea` nace de un error propio, y el docstring lo dice.

    La pieza 21 extrajo la mitad del monto y la cableó en
    `_leer_descuentos_lineas_odoo`, que **no tiene ningún llamador**. El camino vivo
    (`_descuentos_lineas_desde_espejo`) se quedó con su copia inline, así que la
    extracción no redujo la duplicación: la movió a código que no corre. Medido el
    11-sep-2026.
    """

    def test_una_linea_sin_descuento_devuelve_None_y_no_cero(self) -> None:
        """Cero sería un descuento de cero; `None` es «esta línea no es descuento».

        El llamador suma `d.monto`, así que devolver 0.0 lo haría sumar una línea
        que no corresponde y, peor, agregar su fragmento al detalle que el Reporte
        de Saldos muestra.
        """
        from cxc.engine.discount_audit import descuento_de_linea

        assert (
            descuento_de_linea(
                descuento_pct=0.0,
                cantidad=3,
                precio_unitario=10.0,
                subtotal=30.0,
                nombre_linea="SINOCO SAE 50",
                nombre_producto="sinoco sae 50",
            )
            is None
        )

    def test_el_porcentaje_se_aplica_sobre_cantidad_por_precio(self) -> None:
        """Y no sobre el subtotal, que ya lo tiene restado."""
        from cxc.engine.discount_audit import PATRON_PORCENTAJE, descuento_de_linea

        d = descuento_de_linea(descuento_pct=10.0, cantidad=3, precio_unitario=10.0, subtotal=27.0)
        assert d is not None
        assert d.monto == 3.0
        assert d.patron == PATRON_PORCENTAJE

    def test_con_porcentaje_el_subtotal_negativo_se_ignora(self) -> None:
        """La precedencia de la pieza 21, preservada: sumar los dos contaría doble."""
        from cxc.engine.discount_audit import descuento_de_linea

        d = descuento_de_linea(
            descuento_pct=10.0,
            cantidad=1,
            precio_unitario=100.0,
            subtotal=-50.0,
            nombre_producto="descuento",
        )
        assert d is not None
        assert d.monto == 10.0, "10 % de 100, no los 50 del subtotal"

    def test_el_hallazgo_de_S00003_Odoo_nombra_la_linea_en_INGLES(self) -> None:
        """`Discount 20.00%`, en inglés, sin importar el idioma de la UI.

        Mirar solo el nombre del producto pierde esta línea. El parity check contra
        las 819 órdenes reales dio 0 diffs recién cuando se miraron los dos nombres.
        """
        from cxc.engine.discount_audit import PATRON_LINEA_NEGATIVA, descuento_de_linea

        d = descuento_de_linea(
            descuento_pct=0.0,
            cantidad=1,
            precio_unitario=0.0,
            subtotal=-42.5,
            nombre_linea="Discount 20.00%",
            nombre_producto="Descuento ",
        )
        assert d is not None
        assert d.monto == 42.5
        assert d.patron == PATRON_LINEA_NEGATIVA

    def test_el_nombre_del_producto_alcanza_cuando_la_linea_no_dice_nada(self) -> None:
        """El vendedor la nombró a mano; el producto vinculado sí trae la palabra."""
        from cxc.engine.discount_audit import descuento_de_linea

        d = descuento_de_linea(
            descuento_pct=0.0,
            cantidad=1,
            precio_unitario=0.0,
            subtotal=-30.0,
            nombre_linea="Ajuste acordado",
            nombre_producto="Descuento ",
        )
        assert d is not None and d.monto == 30.0

    def test_un_subtotal_negativo_que_no_es_descuento_NO_cuenta(self) -> None:
        """Una devolución o un ajuste con subtotal negativo no es un descuento.

        Es la mitad que la pieza 21 no tenía: ella devolvía `abs(subtotal)` sin
        preguntar el nombre, porque el dominio de Odoo ya había filtrado. En el
        espejo no hay dominio que filtre, así que la pregunta tiene que estar acá.
        """
        from cxc.engine.discount_audit import descuento_de_linea

        assert (
            descuento_de_linea(
                descuento_pct=0.0,
                cantidad=1,
                precio_unitario=0.0,
                subtotal=-99.0,
                nombre_linea="Devolución parcial",
                nombre_producto="SINOCO SAE 50",
            )
            is None
        )

    def test_el_detalle_conserva_el_formato_que_el_reporte_muestra_hoy(self) -> None:
        """Dos formatos distintos con dos palabras de respaldo distintas.

        El del Reporte de Saldos: porcentaje con una decimal, monto con signo de
        dólar y dos. Y el nombre vacío cae en «línea» para el patrón 1 y en
        «Descuento» para el 2 -- no es un detalle de estilo, es lo que la pantalla
        muestra hoy y esta pieza no lo cambia.
        """
        from cxc.engine.discount_audit import descuento_de_linea

        pct = descuento_de_linea(
            descuento_pct=12.5, cantidad=2, precio_unitario=10.0, subtotal=17.5
        )
        neg = descuento_de_linea(
            descuento_pct=0.0,
            cantidad=1,
            precio_unitario=0.0,
            subtotal=-8.0,
            nombre_producto="descuento",
        )
        assert pct is not None and neg is not None
        assert pct.detalle == "línea: 12.5%"
        assert neg.detalle == "Descuento: $8.00"

    def test_un_nombre_largo_se_recorta_a_40(self) -> None:
        from cxc.engine.discount_audit import descuento_de_linea

        d = descuento_de_linea(
            descuento_pct=5.0,
            cantidad=1,
            precio_unitario=1.0,
            subtotal=0.95,
            nombre_linea="X" * 60,
        )
        assert d is not None
        assert d.detalle == "X" * 40 + ": 5.0%"

    def test_es_linea_de_descuento_acepta_los_dos_nombres_vacios(self) -> None:
        """Sin ninguno de los dos nombres no se puede afirmar que sea un descuento."""
        from cxc.engine.discount_audit import es_linea_de_descuento

        assert not es_linea_de_descuento("", "")
        assert es_linea_de_descuento("DESCUENTO por volumen", "")
        assert es_linea_de_descuento("", "Descuento ")
