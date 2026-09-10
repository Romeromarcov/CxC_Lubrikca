"""Devoluciones y notas: las cinco filas de la cuarta familia.

El eje de la familia es la relación entre lo ENTREGADO y lo FACTURADO. Cuando
lo facturado supera lo entregado hay una nota de crédito pendiente; cuando lo
entregado supera lo facturado hay mercancía que nadie cobró. El sistema mira
hoy el primer caso y no el segundo.
"""

from __future__ import annotations

import pytest


@pytest.mark.escenario("Devolución parcial sin nota de crédito")
def test_una_devolucion_sin_nota_de_credito_cae_en_la_bandeja(escenario, sistema, odoo):
    """Lo facturado supera lo entregado.

    Debe caer en la bandeja de NC pendientes: el cliente devolvió mercancía y
    la factura sigue pidiéndole el total.
    """
    situacion = escenario.facturada(cantidad=4)
    sistema.sync_y_motor()
    total_facturado = escenario.total_factura(situacion.facturas[0])

    devueltas = odoo.devolver(situacion.so, cantidad=2)
    assert devueltas, "El escenario no logró la devolución parcial."
    sistema.sync_y_motor()

    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    lineas = sistema.espejo("lineas_orden", "so_id = :so", so=situacion.nombre)
    facturado_ahora = sum(
        float(f["monto_total"])
        for f in sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)
    )

    assert orden["tiene_devolucion"] is True, "La devolución no se detectó."
    assert orden.get("requiere_revision", True), (
        "Hay devolucion sin nota de credito y la orden no quedo marcada para "
        "revision: la NC pendiente no aparece en la bandeja."
    )
    entregado = sum(float(ln["cantidad_entregada"]) for ln in lineas)
    pedido = sum(float(ln["cantidad"]) for ln in lineas)
    assert entregado < pedido, (
        f"Tras devolver la mitad, lo entregado ({entregado}) debería ser menor que "
        f"lo pedido ({pedido}); ``cantidad_entregada`` es neta de devoluciones."
    )
    assert facturado_ahora >= total_facturado - 0.01, (
        "Lo facturado bajó solo por la devolución, sin nota de crédito: la NC "
        "pendiente desaparecería de la bandeja."
    )


@pytest.mark.escenario("Devolución de mercancía de una orden ya saldada")
def test_una_devolucion_sobre_una_orden_saldada_genera_saldo_a_favor(escenario, sistema, odoo):
    """Genera saldo a favor sobre una orden que salió de CxC.

    Hay que ver si el FIFO lo toma: la orden ya no está en la cuenta por
    cobrar, así que el crédito no tiene dónde imputarse salvo como saldo a
    favor del cliente.
    """
    situacion = escenario.pagada()
    sistema.sync_y_motor()
    assert situacion.pagos, "El escenario no llegó a cobrar la orden."

    odoo.devolver(situacion.so)
    sistema.sync_y_motor()

    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    pagos = sistema.espejo("pagos", "cliente_id = :c", c=str(situacion.cliente_id))
    assert orden["tiene_devolucion"] is True, (
        "Una devolución sobre una orden ya cobrada no quedó marcada: el crédito del "
        "cliente no aparece en ningún lado."
    )
    assert pagos, "El pago desapareció; el saldo a favor no tendría de dónde salir."


@pytest.mark.escenario("Nota de crédito por más que la factura")
def test_una_nota_de_credito_mayor_que_la_factura_se_senala(escenario, sistema, odoo):
    """Deja la factura en negativo.

    Debe rechazarse o señalarse. Hay un techo implementado pero no se había
    probado contra Odoo real: acá se prueba.
    """
    situacion = escenario.facturada()
    sistema.sync_y_motor()
    total = escenario.total_factura(situacion.facturas[0])

    notas = odoo.nota_credito(situacion.facturas[0])
    assert notas, "No se generó la nota de crédito."
    # Se infla la NC al doble de la factura.
    lineas_nc = odoo.ex(
        "account.move.line",
        "search_read",
        [[["move_id", "=", notas[0]], ["display_type", "in", ["product", False]]]],
        {"fields": ["id", "quantity"]},
    )
    odoo.ex("account.move", "button_draft", [notas])
    for ln in lineas_nc:
        odoo.ex("account.move.line", "write", [[ln["id"]], {"quantity": float(ln["quantity"]) * 2}])
    odoo.ex("account.move", "action_post", [notas])
    sistema.sync_y_motor()

    ncs = sistema.espejo("facturas", "factura_id = :f", f=str(notas[0]))
    assert ncs, "La nota de crédito no llegó al espejo."
    monto_nc = abs(float(ncs[0]["monto_total"]))
    assert monto_nc > total + 0.01, (
        f"El escenario no logró inflar la NC: {monto_nc} contra una factura de {total}."
    )
    # Lo que importa: que el neto quede señalado, no que se compense a cero.
    facturas = sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)
    neto = sum(float(f["monto_total_signed_usd"]) for f in facturas)
    assert neto < 0, (
        "Una NC mayor que la factura debería dejar el neto en negativo y visible; "
        f"quedó en {neto}."
    )


@pytest.mark.escenario("Nota de crédito sin factura de origen")
def test_una_nota_de_credito_sin_factura_de_origen_se_lista_aparte(escenario, sistema, odoo):
    """No se puede imputar a ninguna orden.

    Debe listarse aparte: es un crédito del cliente que existe aunque no
    cuelgue de nada.
    """
    cliente = odoo.cliente(f"{escenario.etiqueta} nc suelta")
    producto = odoo.producto_con_precio(escenario.listas["ves"])
    nc = odoo.factura_directa(
        cliente, [(producto, 1, 50.0)], move_type="out_refund"
    )
    sistema.sync()

    filas = sistema.espejo("facturas", "factura_id = :f", f=str(nc))
    assert filas, (
        "Una nota de crédito sin factura de origen no llegó al espejo: el crédito "
        "del cliente no existe para el sistema."
    )
    assert filas[0]["move_type"] == "out_refund"
    assert not filas[0]["so_id"], (
        f"La NC suelta quedó atada a la orden {filas[0]['so_id']!r}."
    )
    assert not filas[0]["factura_origen_id"], "La NC suelta quedó con factura de origen."


@pytest.mark.escenario("Nota de débito posterior al pago")
def test_una_nota_de_debito_reabre_una_orden_cobrada(escenario, sistema, odoo):
    """La orden vuelve a deber después de estar cobrada.

    Debe reabrirse: la nota de débito es plata nueva que el cliente debe, y
    una orden que salió de CxC tiene que volver a entrar.
    """
    situacion = escenario.pagada()
    sistema.sync_y_motor()
    residual_antes = float(
        odoo.ex("account.move", "read", [situacion.facturas], {"fields": ["amount_residual"]})[0][
            "amount_residual"
        ]
    )
    assert abs(residual_antes) < 0.01, "El escenario no llegó a dejar la orden cobrada."

    producto = odoo.producto_con_precio(situacion.lista_id)
    nd = odoo.factura_directa(
        situacion.cliente_id,
        [(producto, 1, 30.0)],
        move_type="out_invoice",
        origen=situacion.nombre,
    )
    sistema.sync_y_motor()

    facturas = sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)
    ids = {f["factura_id"] for f in facturas}
    assert str(nd) in ids, (
        "La nota de débito no quedó asociada a la orden: la orden sigue dada por "
        "cobrada y el cargo nuevo no aparece en ningún lado."
    )
    neto = sum(float(f["monto_total_signed_usd"]) for f in facturas)
    assert neto > 0, "El neto facturado de la orden debería subir con la nota de débito."
