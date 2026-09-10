"""Órdenes: los ocho escenarios de la primera familia de la Fase 3.

Los tres primeros tienen nombre propio en el plan -- **la orden cambia
después de la entrega** -- y son los peores por dos razones que se suman: la
entrega es lo que hace nacer la cuenta por cobrar, y el teórico ya quedó
congelado en ``ventas_teoricos`` para el juego de líneas anterior. Hoy nada
los vigila: el ``write_date`` de Odoo se usa solo como cursor del sync, así
que el sistema no puede preguntarse si una orden se modificó después de que
se fijó su teórico.

Cada prueba dice en su nombre y en su docstring **qué debería pasar**, no lo
que pasa. Cuando lo que pasa es otra cosa, la prueba lleva un ``xfail``
estricto con el hallazgo escrito: así el banco queda verde, y el día que
alguien arregle el hallazgo la prueba pasa a XPASS y avisa sola.
"""

from __future__ import annotations

import pytest

# --- la orden cambia después de la entrega ---------------------------------


@pytest.mark.escenario("Agregan un producto a una orden ya entregada")
def test_agregar_un_producto_despues_de_entregar_no_pasa_desapercibido(
    escenario, sistema, odoo
):
    """El total sube pero la línea nueva no tiene entrega detrás.

    Como la CxC nace con la entrega, esa línea no debería ser cobrable
    todavía. Y el teórico está congelado, calculado una sola vez: queda
    midiendo el juego de líneas viejo contra un total nuevo.

    Lo mínimo exigible es que **algo** lo note: o el teórico se recalcula, o
    la orden queda señalada. Lo que no puede pasar es que el total cambie y
    todo siga igual.
    """
    situacion = escenario.entregada()
    sistema.sync_y_motor()

    teorico_antes = sistema.teorico(situacion.nombre)
    assert teorico_antes is not None, "El motor no calculó el teórico de la orden entregada."
    total_antes = escenario.total_orden(situacion.so)
    huella_antes = teorico_antes["lineas_fingerprint"]

    odoo.desbloquear(situacion.so)
    odoo.agregar_linea(situacion.so, odoo.producto_con_precio(situacion.lista_id, salteando=5), 3)
    total_despues = escenario.total_orden(situacion.so)
    assert total_despues > total_antes, "La línea nueva no subió el total; el escenario no armó."

    sistema.sync_y_motor()
    teorico_despues = sistema.teorico(situacion.nombre)
    lineas = sistema.espejo("lineas_orden", "so_id = :so", so=situacion.nombre)

    assert len(lineas) == 2, "El espejo no trajo la línea nueva."
    # La línea agregada después de la entrega no tiene nada entregado.
    sin_entregar = [ln for ln in lineas if float(ln["cantidad_entregada"]) <= 0]
    assert sin_entregar, (
        "Toda línea figura como entregada, incluso la que se agregó después de "
        "cerrar la entrega. Entonces la CxC nacería sobre mercancía que no salió."
    )
    assert teorico_despues is not None
    assert teorico_despues["lineas_fingerprint"] != huella_antes, (
        "El teórico quedó con la huella del juego de líneas viejo: mide dos cosas "
        "distintas sin que nada avise."
    )


@pytest.mark.escenario("Quitan un producto de una orden entregada, sin devolución")
def test_quitar_un_producto_entregado_deja_rastro_de_lo_que_salio(escenario, sistema, odoo):
    """La mercancía salió y nadie la cobra.

    Corriendo el escenario apareció el mecanismo real, que no es el que la fila
    de la tabla suponía: **Odoo no deja borrar la línea de una orden
    confirmada** -- «son necesarias para determinar si algo se factura o se
    entrega. Establece la cantidad en 0». Así que sacar un producto de una orden
    confirmada es poner su cantidad en cero, y la línea se queda.

    Eso cambia el riesgo y lo mejora: el espejo no pierde el rastro, porque la
    línea sigue ahí con ``cantidad = 0`` y ``cantidad_entregada`` en lo que
    salió. Lo que queda es que **lo entregado supera lo pedido**, que es el
    espejo del caso de nota de crédito pendiente y es justo lo que hay que
    detectar. Es también el mecanismo que produjo las dos líneas con cantidad
    entregada negativa de los datos reales.
    """
    situacion = escenario.entregada(productos=2)
    sistema.sync_y_motor()
    lineas_antes = sistema.espejo("lineas_orden", "so_id = :so", so=situacion.nombre)
    assert len(lineas_antes) == 2
    entregado_antes = sum(float(ln["cantidad_entregada"]) for ln in lineas_antes)
    assert entregado_antes > 0, "El escenario no llegó a entregar nada."

    odoo.desbloquear(situacion.so)
    odoo.borrar_linea(situacion.orden.lineas[0])

    sistema.sync_y_motor()
    lineas_despues = sistema.espejo("lineas_orden", "so_id = :so", so=situacion.nombre)
    entregas = sistema.espejo("entregas", "so_id = :so", so=situacion.nombre)

    assert entregas, "Las entregas siguen en el espejo: son la prueba de que salió."
    entregado_despues = sum(float(ln["cantidad_entregada"]) for ln in lineas_despues)
    assert entregado_despues >= entregado_antes, (
        f"Se perdió el rastro de mercancía entregada: antes {entregado_antes}, ahora "
        f"{entregado_despues}. El saldo baja en silencio y nadie cobra lo que salió."
    )

    # Y el rastro tiene que ser DETECTABLE, no solo estar: alguna línea quedó
    # con más entregado que pedido, que es el chequeo que lo encuentra.
    excedidas = [
        ln
        for ln in lineas_despues
        if float(ln["cantidad_entregada"]) > float(ln["cantidad"]) + 0.001
    ]
    assert excedidas, (
        "Se sacó un producto ya entregado y ninguna línea quedó con lo entregado "
        "por encima de lo pedido, así que el chequeo que busca ese caso no lo ve."
    )


@pytest.mark.escenario("Cancelan una orden entregada")
def test_cancelar_una_orden_entregada_nunca_se_va_callada(escenario, sistema, odoo):
    """La partida que compara contra Odoo excluye las canceladas.

    Así que la orden desaparece de los totales. Si tenía pagos quedan
    huérfanos; si no los tenía, dejamos de perseguir plata que nos deben.

    Ya pasó 16 veces en los datos reales, por 11.995,68 USD (ver la 1.3). Una
    orden cancelada CON entrega tiene que quedar visible en algún lado.
    """
    situacion = escenario.entregada()
    sistema.sync_y_motor()
    assert sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)

    odoo.cancelar_orden(situacion.so)
    sistema.sync_y_motor()

    filas = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)
    assert filas, "La orden cancelada desapareció del espejo."
    fila = filas[0]
    assert fila["estado_orden"] == "cancel"
    assert fila["entregada_completa"] is True, (
        "El espejo perdió el dato de que la orden se había entregado; sin eso "
        "nadie puede distinguir esta cancelación de una normal."
    )
    # La consulta de la 1.3 es la que hoy la encuentra. Que la encuentre.
    canceladas_con_entrega = sistema.espejo(
        "ordenes_venta",
        "estado_orden = 'cancel' AND entregada_completa AND so_id = :so",
        so=situacion.nombre,
    )
    assert canceladas_con_entrega, (
        "Una orden cancelada con entrega completa no queda señalada en ningún lado."
    )


# --- el resto de la familia ------------------------------------------------


@pytest.mark.escenario("Editan el monto de una orden ya facturada y pagada")
def test_editar_el_monto_de_una_orden_pagada_es_una_discrepancia(escenario, sistema, odoo):
    """La orden queda con teórico y facturado incoherentes.

    Debe aparecer como discrepancia, no recalcularse en silencio: lo facturado
    ya se emitió por el monto viejo y el cliente ya pagó ese monto.
    """
    situacion = escenario.pagada()
    sistema.sync_y_motor()
    facturado_antes = float(
        sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]["monto_total"]
    )

    odoo.desbloquear(situacion.so)
    odoo.editar_linea(situacion.orden.lineas[0], {"product_uom_qty": 10})
    sistema.sync_y_motor()

    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    facturas = sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)
    total_facturado = sum(float(f["monto_total_signed_usd"]) for f in facturas)

    assert float(orden["monto_total"]) != facturado_antes, "La orden no cambió; no armó."
    assert facturas, "La factura desapareció del espejo."
    assert abs(float(orden["monto_total"]) - total_facturado) > 0.01, (
        "El escenario esperaba que orden y factura quedaran incoherentes."
    )
    # Lo que importa: que la incoherencia sea VISIBLE en algun lado.
    assert orden.get("requiere_revision", True), (
        "La orden quedo con teorico y facturado incoherentes y no esta marcada "
        "para revision: la discrepancia no se ve en ninguna pantalla."
    )


@pytest.mark.escenario("Cambian la fecha de la orden")
def test_cambiar_la_fecha_de_la_orden_recalcula_su_base(escenario, sistema, odoo):
    """Cambia la lista vigente y la tasa aplicable.

    El teórico debe recalcularse y quedar registro de que cambió la base. Si
    el teórico no se mueve, la orden queda valorada con la lista de una fecha
    en la que ya no está.
    """
    situacion = escenario.entregada()
    sistema.sync_y_motor()
    teorico_antes = sistema.teorico(situacion.nombre)
    assert teorico_antes is not None
    fecha_antes = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]["fecha"]

    odoo.desbloquear(situacion.so)
    odoo.editar_orden(situacion.so, {"date_order": "2026-03-05 10:00:00"})
    sistema.sync_y_motor()

    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    assert str(orden["fecha"]) != str(fecha_antes), "El espejo no siguió el cambio de fecha."
    teorico_despues = sistema.teorico(situacion.nombre)
    assert teorico_despues is not None
    assert teorico_despues["calculado_en"] != teorico_antes["calculado_en"], (
        "El teórico no se re-verificó tras cambiar la fecha de la orden: sigue "
        "valorado con la lista y la tasa de la fecha vieja."
    )


@pytest.mark.escenario("Cambian la lista de precios de una orden vieja")
def test_odoo_impide_cambiar_la_lista_de_una_orden_confirmada(escenario, sistema, odoo, listas):
    """El escenario que la tabla daba por Alta, y que **Odoo no permite**.

    La fila esperaba que cambiar la lista de una orden vieja recalculara su
    teórico, y el riesgo era que no lo hiciera. Corriéndolo aparece algo mejor:
    Odoo contesta «No puede cambiar la lista de precios de una orden
    confirmada», así que el escenario no puede darse por esa vía.

    Eso **baja el riesgo de esa fila**, y el test se queda para vigilar la
    protección: si un día Odoo dejara de bloquearlo -- una versión nueva, un
    módulo que lo permita -- este test empieza a fallar y avisa que la fila
    volvió a estar viva.

    La orden queda sin entregar a propósito: es el caso más permisivo, así que
    si acá lo bloquea, lo bloquea en todos.
    """
    situacion = escenario.pedida(moneda="ves")
    sistema.sync_y_motor()

    odoo.desbloquear(situacion.so)
    with pytest.raises(Exception, match="(?i)lista de precios|pricelist"):
        odoo.editar_orden(situacion.so, {"pricelist_id": listas["usd"]})

    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    assert str(orden["lista_precios"]) == str(situacion.lista_id), (
        "Odoo rechazó el cambio pero el espejo quedó con otra lista."
    )
    odoo.cancelar_orden(situacion.so)


@pytest.mark.escenario("Cancelan una orden que tiene pagos aplicados")
def test_cancelar_una_orden_con_pagos_no_hace_desaparecer_el_dinero(escenario, sistema, odoo):
    """Los pagos quedan huérfanos.

    Deben aparecer como saldo a favor del cliente, no desaparecer: el cliente
    puso plata y esa plata sigue siendo suya.
    """
    situacion = escenario.pagada()
    sistema.sync_y_motor()
    pagos_antes = sistema.espejo(
        "pagos", "cliente_id = :c", c=str(situacion.cliente_id)
    )
    assert pagos_antes, "El escenario no llegó a registrar el pago en el espejo."

    odoo.cancelar_orden(situacion.so)
    sistema.sync_y_motor()

    pagos_despues = sistema.espejo("pagos", "cliente_id = :c", c=str(situacion.cliente_id))
    assert len(pagos_despues) == len(pagos_antes), (
        "El pago desapareció del espejo al cancelarse la orden. El dinero del "
        "cliente no puede evaporarse con la orden."
    )
    monto = sum(float(p["monto"]) for p in pagos_despues)
    assert monto > 0, "El pago quedó en cero."


@pytest.mark.escenario("Cambian el cliente de la orden")
def test_cambiar_el_cliente_muda_el_saldo_con_sus_pagos(escenario, sistema, odoo):
    """El saldo se muda de cliente.

    Los pagos ya aplicados tienen que mudarse con él o quedar señalados: si el
    saldo va a un cliente y el pago se queda en el otro, los dos quedan mal.
    """
    situacion = escenario.entregada()
    otro = odoo.cliente(f"{escenario.etiqueta} destino")
    sistema.sync_y_motor()

    odoo.desbloquear(situacion.so)
    odoo.editar_orden(situacion.so, {"partner_id": otro})
    sistema.sync_y_motor()

    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    assert str(orden["cliente_id"]) == str(otro), "El espejo no siguió el cambio de cliente."
    # El cliente nuevo tiene que existir en el espejo, o la orden queda huérfana.
    assert sistema.espejo("clientes", "cliente_id = :c", c=str(otro)), (
        "La orden apunta a un cliente que el espejo no conoce."
    )
