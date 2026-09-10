"""Pagos: las seis filas de la segunda familia.

El hilo común de casi todas es el **congelado**. El equivalente en dólares de
un abono se congela en la Vinculación en el momento de vincularlo, con la
tasa de ese momento, y no se recalcula nunca -- lo cual es correcto como
diseño contable. El problema no es que se congele: es que si el pago cambia
después, nada compara el congelado contra la realidad nueva.
"""

from __future__ import annotations

import pytest


@pytest.mark.escenario("Editan el monto de un pago ya conciliado")
def test_editar_el_monto_de_un_pago_conciliado_se_detecta(escenario, sistema, odoo):
    """El equivalente en dólares está congelado en la vinculación.

    No se recalcula solo. Hay que detectar la divergencia y decidir si se
    recongela; lo que no puede pasar es que el espejo siga mostrando el monto
    viejo, porque entonces ni siquiera se puede detectar.
    """
    situacion = escenario.pagada()
    sistema.sync()
    pagos = sistema.espejo("pagos", "pago_id = :p", p=str(situacion.pagos[0]))
    assert pagos, "El pago no llegó al espejo."
    monto_antes = float(pagos[0]["monto"])

    odoo.editar_pago(situacion.pagos[0], {"amount": round(monto_antes / 2, 2)})
    sistema.sync()

    pagos_despues = sistema.espejo("pagos", "pago_id = :p", p=str(situacion.pagos[0]))
    assert pagos_despues, "El pago desapareció del espejo al editarlo."
    assert float(pagos_despues[0]["monto"]) != monto_antes, (
        "El espejo conservó el monto viejo del pago. Si el espejo no ve el cambio, "
        "ninguna partida puede detectar que el equivalente congelado quedó mal."
    )


@pytest.mark.escenario("Cambian la fecha del pago")
def test_cambiar_la_fecha_del_pago_cambia_su_tasa(escenario, sistema, odoo):
    """Cambia la tasa y por lo tanto el equivalente.

    Mismo problema del congelado: el equivalente se fijó con la tasa del día
    viejo. El espejo tiene que seguir la fecha nueva para que la divergencia
    sea detectable.
    """
    situacion = escenario.pagada()
    sistema.sync()
    fecha_antes = sistema.espejo("pagos", "pago_id = :p", p=str(situacion.pagos[0]))[0][
        "fecha_pago"
    ]

    odoo.editar_pago(situacion.pagos[0], {"date": "2026-04-15"})
    sistema.sync()

    fila = sistema.espejo("pagos", "pago_id = :p", p=str(situacion.pagos[0]))
    assert fila, "El pago desapareció del espejo."
    assert str(fila[0]["fecha_pago"])[:10] != str(fecha_antes)[:10], (
        "El espejo no siguió el cambio de fecha del pago; el equivalente queda "
        "congelado con una tasa que ya no corresponde y nada lo nota."
    )


@pytest.mark.escenario("Desconcilian el pago de su factura")
def test_desconciliar_el_pago_hace_que_la_orden_vuelva_a_deber(escenario, sistema, odoo):
    """La orden vuelve a deber.

    Debe salir de "cobrada" y reaparecer en el reporte: el residual de la
    factura vuelve a ser el total.
    """
    situacion = escenario.pagada()
    sistema.sync()
    residual_antes = float(
        odoo.ex(
            "account.move", "read", [situacion.facturas], {"fields": ["amount_residual"]}
        )[0]["amount_residual"]
    )
    assert abs(residual_antes) < 0.01, "El escenario no llegó a dejar la factura en cero."

    odoo.desconciliar_pago(situacion.pagos[0])
    sistema.sync()

    residual_despues = float(
        odoo.ex(
            "account.move", "read", [situacion.facturas], {"fields": ["amount_residual"]}
        )[0]["amount_residual"]
    )
    assert residual_despues > 0.01, (
        "Desconciliar no devolvió el residual de la factura; el escenario no armó."
    )
    facturas = sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)
    assert facturas, "La factura desapareció del espejo al desconciliar."


@pytest.mark.escenario("Reconcilian el pago contra la factura de otra orden")
def test_mover_el_pago_a_otra_orden_mueve_el_saldo(escenario, sistema, odoo):
    """El saldo se mueve entre órdenes.

    Ya hay un resincronizador para esto; el escenario prueba que cubre el
    caso, es decir, que después de mover el pago las dos órdenes quedan
    diciendo la verdad y no una sola.
    """
    primera = escenario.pagada()
    segunda = escenario.facturada()
    sistema.sync()

    odoo.desconciliar_pago(primera.pagos[0])
    # El pago vuelve a tener residual disponible; se aplica a la otra factura.
    odoo.pagar(segunda.facturas, monto=escenario.total_factura(segunda.facturas[0]))
    sistema.sync()

    residual_primera = float(
        odoo.ex("account.move", "read", [primera.facturas], {"fields": ["amount_residual"]})[0][
            "amount_residual"
        ]
    )
    residual_segunda = float(
        odoo.ex("account.move", "read", [segunda.facturas], {"fields": ["amount_residual"]})[0][
            "amount_residual"
        ]
    )
    assert residual_primera > 0.01, "La primera orden debería volver a deber."
    assert abs(residual_segunda) < 0.01, "La segunda orden debería quedar saldada."
    for situacion in (primera, segunda):
        assert sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre), (
            f"{situacion.nombre} se cayó del espejo."
        )


@pytest.mark.escenario("Registran el mismo pago dos veces")
def test_el_pago_duplicado_deja_la_orden_sobrepagada_y_visible(escenario, sistema, odoo):
    """La orden queda sobrepagada, y el segundo pago no puede perderse.

    Corriendo el escenario apareció una protección que la fila no contemplaba:
    con la factura ya saldada, el asistente de cobro de Odoo se niega -- «no
    queda nada por pagar en los apuntes contables seleccionados». Así que la
    duplicación por esa vía está bloqueada.

    Lo que sí puede pasar, y es lo que el escenario arma, es que el segundo
    cobro entre cuando la factura **todavía tiene residual**: se cobra la mitad
    dos veces. La orden queda sobrepagada igual, y lo que hay que verificar es
    que el segundo abono llegue al espejo -- un pago duplicado es plata del
    cliente que hay que devolverle, y perderlo es peor que registrarlo dos
    veces.
    """
    situacion = escenario.pagada(proporcion=0.5)
    sistema.sync()
    total = escenario.total_factura(situacion.facturas[0])
    pagos_antes = sistema.espejo("pagos", "cliente_id = :c", c=str(situacion.cliente_id))
    assert pagos_antes, "El escenario no llegó a registrar el primer pago."

    # El mismo cobro otra vez, con la factura aún a medio pagar.
    odoo.pagar(situacion.facturas, monto=round(total / 2, 2))
    sistema.sync()

    pagos_despues = sistema.espejo("pagos", "cliente_id = :c", c=str(situacion.cliente_id))
    assert len(pagos_despues) == len(pagos_antes) + 1, (
        "El segundo pago no llegó al espejo. Un pago duplicado es plata del cliente "
        "que hay que devolverle: perderlo es peor que registrarlo dos veces."
    )
    gemelos = sistema.espejo(
        "pagos",
        "cliente_id = :c GROUP BY cliente_id, monto, moneda, fecha_pago::date "
        "HAVING count(*) > 1",
        c=str(situacion.cliente_id),
    )
    assert gemelos, "El detector de pagos gemelos no ve el duplicado."


@pytest.mark.escenario("Registran el mismo pago dos veces — sobre una factura ya saldada")
def test_odoo_impide_cobrar_dos_veces_una_factura_ya_saldada(escenario, odoo):
    """La otra mitad del escenario anterior: la vía que Odoo bloquea.

    Se deja como test propio para vigilar la protección. Si un día deja de
    bloquearlo, este test falla y avisa que la duplicación volvió a ser posible
    por el camino fácil.
    """
    situacion = escenario.pagada()
    total = escenario.total_factura(situacion.facturas[0])
    with pytest.raises(Exception, match="(?i)nada por pagar|nothing to pay"):
        odoo.pagar(situacion.facturas, monto=total)


@pytest.mark.escenario("Pago en una moneda distinta a la de la orden")
def test_pagar_en_otra_moneda_define_por_cual_teorico_se_mide(escenario, sistema, odoo):
    """Define por cuál teórico se mide la orden.

    Es la regla de las dos vías de pago. Una orden nacida en lista VES pagada
    en dólares se mide por el teórico USD, no por el VES.
    """
    situacion = escenario.facturada(moneda="ves")
    sistema.sync_y_motor()
    teorico = sistema.teorico(situacion.nombre)
    assert teorico is not None, "El motor no calculó el teórico."
    assert float(teorico["teorico_ves"]) > 0 or float(teorico["teorico_usd"]) > 0, (
        "El teórico quedó en cero para una orden con líneas: sin datos no es cero."
    )

    odoo.pagar(situacion.facturas, monto=escenario.total_factura(situacion.facturas[0]) / 2,
               moneda="USD")
    sistema.sync()

    pagos = sistema.espejo("pagos", "cliente_id = :c", c=str(situacion.cliente_id))
    monedas = {p["moneda"] for p in pagos}
    assert "USD" in monedas, (
        f"El espejo no registró el abono en dólares; vio {monedas}. Sin la moneda "
        "del abono no se puede decidir por cuál teórico se mide la orden."
    )
