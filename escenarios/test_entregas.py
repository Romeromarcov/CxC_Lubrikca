"""Entregas: las cuatro filas de la tercera familia.

La entrega es lo que hace nacer la cuenta por cobrar, así que todo lo que le
pase a la entrega le pasa a la CxC. Y la fecha de entrega es además el ancla
de la ventana de contado, de la que cuelga si el cliente se gana o no el
descuento.
"""

from __future__ import annotations

import pytest


@pytest.mark.escenario("Entrega parcial")
def test_una_entrega_parcial_no_es_ni_total_ni_nula(escenario, sistema, odoo):
    """Una entrega parcial debería generar una CxC parcial.

    Ni total (no salió todo) ni nula (algo salió). Y como el plazo de contado
    arranca con la entrega COMPLETA, con una parcial la ``fecha_entrega`` debe
    quedar vacía: el plazo no arrancó, así que el contado no se evalúa.
    """
    situacion = escenario.entregada(completa=False)
    estado = odoo.estado_entrega(situacion.so)
    assert estado not in ("full",), (
        f"El escenario no logró una entrega parcial: quedó en {estado!r}."
    )
    sistema.sync_y_motor()

    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    assert orden["entregada_completa"] is False, (
        "Una entrega parcial quedó marcada como completa; la CxC nacería entera "
        "sobre mercancía que no salió."
    )
    assert orden["fecha_entrega"] is None, (
        "Con entrega parcial hay fecha de entrega, así que la ventana de contado "
        "arranca sin que la entrega haya terminado."
    )
    odoo.cancelar_orden(situacion.so)


@pytest.mark.escenario("Entrega sin orden asociada")
def test_una_entrega_sin_orden_queda_listada(escenario, sistema, odoo):
    """No hay contra qué cobrarla.

    Debe aparecer listada, nunca ignorada en silencio: mercancía que salió del
    depósito sin una orden detrás es una pérdida hasta que alguien la explique.
    """
    cliente = odoo.cliente(f"{escenario.etiqueta} suelta")
    picking = int(
        odoo.ex(
            "stock.picking",
            "create",
            [
                {
                    "partner_id": cliente,
                    "picking_type_id": 2,
                    "location_id": 8,
                    "location_dest_id": odoo.ubicacion_pruebas,
                }
            ],
        )
    )
    sistema.sync()

    filas = sistema.espejo("entregas", "entrega_id = :e", e=str(picking))
    assert filas, (
        "Una entrega sin orden asociada no llegó al espejo: la mercancía sale del "
        "depósito y el sistema no la ve."
    )
    assert not filas[0]["so_id"], (
        f"La entrega sin orden quedó atada a la orden {filas[0]['so_id']!r}."
    )
    odoo.ex("stock.picking", "action_cancel", [[picking]])


@pytest.mark.escenario("Anulan la entrega después de facturar")
def test_anular_la_entrega_despues_de_facturar_se_senala(escenario, sistema, odoo):
    """Queda factura sin entrega.

    Es una devolución encubierta y debe señalarse: se cobró mercancía que
    volvió al depósito.
    """
    situacion = escenario.facturada()
    sistema.sync_y_motor()
    assert sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)

    devueltas = odoo.devolver(situacion.so)
    assert devueltas, "El escenario no logró revertir la entrega."
    sistema.sync_y_motor()

    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    facturas = sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)
    assert facturas, "La factura desapareció; sin ella no se ve que se cobró de más."
    assert orden["tiene_devolucion"] is True, (
        "La devolución no quedó marcada en la orden. Una factura sin entrega detrás "
        "es una devolución encubierta y nadie la va a mirar."
    )


@pytest.mark.escenario("Orden entregada sin fecha de entrega")
def test_una_orden_entregada_sin_fecha_usa_la_de_la_orden(escenario, sistema, odoo):
    """Ya se cubrió: usa la fecha de la orden.

    Hay que verificar que sigue funcionando y que deja rastro. Es el único
    escenario de la tabla marcado como de severidad baja, justamente porque
    tiene una defensa; el punto es que la defensa siga ahí.
    """
    situacion = escenario.entregada()
    sistema.sync_y_motor()
    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    assert orden["entregada_completa"] is True
    assert orden["fecha_entrega"] is not None, "La entrega completa no dejó fecha."

    # Se borra la fecha de la entrega en Odoo, dejando el estado en 'done'.
    pickings = odoo.ex(
        "stock.picking",
        "search_read",
        [[["sale_id", "=", situacion.so], ["state", "=", "done"]]],
        {"fields": ["id"]},
    )
    odoo.ex("stock.picking", "write", [[p["id"] for p in pickings], {"date_done": False}])
    sistema.sync_y_motor()

    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    assert orden["entregada_completa"] is True, "La orden dejó de estar entregada."
    assert orden["fecha_entrega"] is not None, (
        "Sin fecha de entrega en Odoo, el espejo quedó sin fecha: la ventana de "
        "contado no tiene ancla y el descuento no se evalúa nunca."
    )
    assert str(orden["fecha_entrega"]) == str(orden["fecha"]), (
        "El respaldo debería usar la fecha de la orden; usó "
        f"{orden['fecha_entrega']} contra una orden del {orden['fecha']}."
    )
