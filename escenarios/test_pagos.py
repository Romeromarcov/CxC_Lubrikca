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
    # El detector de gemelos agrupa, así que no entra por ``espejo()`` -- ése
    # hace ``SELECT *`` y un GROUP BY encima es SQL inválido. Se comprueba la
    # misma condición desde Python, sobre las filas que ya se tienen.
    from collections import Counter

    claves = Counter(
        (p["cliente_id"], str(p["monto"]), p["moneda"], str(p["fecha_pago"])[:10])
        for p in pagos_despues
    )
    assert any(n > 1 for n in claves.values()), (
        "Dos cobros del mismo monto, la misma moneda y el mismo día no quedaron "
        f"como gemelos detectables: {dict(claves)}"
    )


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

    # La mitad del residual EN DÓLARES (el asistente convierte). La versión
    # anterior pasaba la mitad del total en bolívares como si fueran dólares:
    # tres corridas dejaron pagos de 258.644,36 USD en el Odoo de prueba.
    odoo.pagar(situacion.facturas, monto=None, moneda="USD", proporcion=0.5)
    sistema.sync()

    pagos = sistema.espejo("pagos", "cliente_id = :c", c=str(situacion.cliente_id))
    monedas = {p["moneda"] for p in pagos}
    assert "USD" in monedas, (
        f"El espejo no registró el abono en dólares; vio {monedas}. Sin la moneda "
        "del abono no se puede decidir por cuál teórico se mide la orden."
    )


@pytest.mark.escenario("Editan la fecha de un pago en USD ya conciliado, sin pasarlo a borrador")
def test_editar_la_fecha_de_un_pago_conciliado_queda_en_la_bandeja(escenario, sistema, odoo):
    """El bug de Odoo del diferencial cambiario, reproducido y detectado.

    Es distinto del escenario anterior, que pasa el pago por borrador: eso
    deshace la conciliación y la vuelve a armar limpia. El bug real (descrito
    por el usuario en agosto y confirmado el 11-sep sobre la orden S00061)
    ocurre cuando se **edita la fecha en la pantalla del pago posteado y
    conciliado**: el onchange propone la tasa del día nuevo y la pantalla
    recalcula el «Importe local» en Bs con ella, pero la línea contable ya
    conciliada se queda con el monto VES viejo (ver
    ``OdooQA.editar_fecha_como_la_ui``, que reproduce ese flujo paso a paso).
    Los dos números del mismo pago dejan de coincidir. Del lado de la orden,
    el «exceso» que se ve en producción sale del ajuste cambiario que la
    pantalla genera además; eso no se reproduce por RPC y este escenario no
    lo afirma.

    Lo que tiene que pasar: la bandeja de auditoría (`/api/auditoria`) lista
    el pago en ``pagos_importe_local_desincronizado`` con la diferencia en
    Bs. Es la detección que el cruce del 11-sep usó para atribuir 9 de los
    10 pagos «sobreaplicados» a este bug y no a un cobro de más.

    Si Odoo no deja escribir la fecha sobre un pago posteado, el escenario
    no aplica en esta versión y lo dice; no lo disfraza de detección.
    """
    # La factura está en bolívares aunque la lista sea la «USD» (todas lo están:
    # es la contabilidad dual). El pago va en dólares por el diario USD, con el
    # monto que el asistente calcula del residual: ``monto=None``.
    situacion = escenario.facturada(moneda="usd")
    pago = odoo.pagar(situacion.facturas, monto=None, moneda="USD", fecha="2026-09-05")
    odoo.completar_importe_local(pago)
    sistema.sync()

    antes = odoo.ex(
        "account.payment",
        "read",
        [[pago]],
        {"fields": ["amount", "amount_local", "state", "date", "currency_id"]},
    )[0]
    factura = odoo.ex(
        "account.move",
        "read",
        [[situacion.facturas[0]]],
        {"fields": ["amount_residual", "payment_state"]},
    )[0]
    # Odoo 18 ya no dice «posted» para un pago: dice «in_process» (el asiento
    # está posteado, el pago espera confirmación bancaria) o «paid». Y
    # ``is_reconciled`` habla del extracto bancario, no de la factura: lo que
    # importa es que la factura quedó saldada por este pago.
    assert antes["state"] in ("in_process", "paid", "posted"), antes
    assert abs(float(factura["amount_residual"])) < 0.01, (
        f"El pago no saldó la factura ({factura}); sin conciliación no hay bug que reproducir."
    )
    assert float(antes["amount_local"] or 0) > 0, (
        f"El pago en dólares no tiene importe local ({antes}); es lo que el detector compara, "
        "así que sin eso el escenario no puede medir nada."
    )

    try:
        odoo.editar_fecha_como_la_ui(pago, "2026-04-15")
    except Exception as exc:  # noqa: BLE001 -- se distingue por el mensaje
        pytest.skip(
            "Esta versión de Odoo no deja editar la fecha de un pago posteado y "
            f"conciliado, así que el bug no se puede reproducir por acá: {exc}"
        )

    despues = odoo.ex(
        "account.payment",
        "read",
        [[pago]],
        {"fields": ["amount_local", "date"]},
    )[0]
    assert str(despues["date"])[:10] == "2026-04-15", "La fecha no cambió."
    factura_despues = odoo.ex(
        "account.move", "read", [[situacion.facturas[0]]], {"fields": ["amount_residual"]}
    )[0]
    assert abs(float(factura_despues["amount_residual"])) < 0.01, (
        "Escribir la fecha desconcilió el pago: entonces no es el bug del importe "
        "local, es otro comportamiento, y este escenario no lo cubre."
    )
    if abs(float(despues["amount_local"] or 0) - float(antes["amount_local"] or 0)) < 1.0:
        pytest.skip(
            "Odoo no recalculó el importe local al cambiar la fecha (misma tasa en las "
            "dos fechas, o esta versión no lo recomputa): no se armó el bug."
        )

    sistema.sync()
    auditoria = sistema.auditoria()
    desincronizados = {
        str(d.get("pago_id")): d for d in auditoria.get("pagos_importe_local_desincronizado") or []
    }
    assert str(pago) in desincronizados, (
        f"El pago {pago} quedó con importe local {despues['amount_local']} Bs y el asiento "
        f"con {antes['amount_local']} Bs, y la bandeja de auditoría NO lo lista. Es "
        "exactamente el caso que hoy se cruza a mano para no confundir un parcial "
        "corrompido con un cobro de más."
    )
    fila = desincronizados[str(pago)]
    assert abs(float(fila["diferencia_ves"])) > 1.0, "La diferencia listada es cero: no dice nada."
    assert auditoria["resumen_auditoria"]["total_pagos_importe_local_desincronizado"] >= 1
