"""Catálogo y configuración: las cuatro filas de la sexta familia.

Estas cuatro no tocan una orden en particular: cambian la base sobre la que
TODAS se valoran. Por eso dos de ellas verifican lo contrario de lo habitual
-- que el teórico **no** se mueva -- porque está congelado a propósito.
"""

from __future__ import annotations

import pytest

from escenarios.sistema import olvidar_precios


@pytest.mark.escenario("Renombran o fusionan un cliente")
def test_renombrar_un_cliente_consolida_su_saldo(escenario, sistema, odoo):
    """Los saldos deben consolidarse, no duplicarse ni perderse.

    Un rename es el caso benigno y el que hay que asegurar primero: el id no
    cambia, así que el saldo tiene que seguir pegado al mismo cliente con el
    nombre nuevo.
    """
    situacion = escenario.entregada()
    sistema.sync_y_motor()
    antes = sistema.espejo("clientes", "cliente_id = :c", c=str(situacion.cliente_id))
    assert antes, "El cliente no llegó al espejo."
    nombre_antes = antes[0]["nombre"]

    odoo.ex(
        "res.partner",
        "write",
        [[situacion.cliente_id], {"name": f"{nombre_antes} RENOMBRADO"}],
    )
    sistema.sync()

    despues = sistema.espejo("clientes", "cliente_id = :c", c=str(situacion.cliente_id))
    assert len(despues) == 1, (
        f"El rename produjo {len(despues)} filas de cliente: el saldo se duplicaría."
    )
    assert despues[0]["nombre"] != nombre_antes, "El espejo no siguió el rename."
    ordenes = sistema.espejo("ordenes_venta", "cliente_id = :c", c=str(situacion.cliente_id))
    assert ordenes, "La orden se despegó del cliente al renombrarlo."
    odoo.ex("res.partner", "write", [[situacion.cliente_id], {"name": nombre_antes}])
    odoo.cancelar_orden(situacion.so)


@pytest.mark.escenario("Cambian el precio de un producto retroactivamente")
def test_cambiar_un_precio_no_mueve_los_teoricos_ya_calculados(escenario, sistema, odoo):
    """Los teóricos ya calculados están congelados en su tabla a propósito.

    Hay que confirmar que efectivamente no se mueven: una orden vieja se
    valoró con el precio que regía cuando se vendió, y un cambio de precio de
    hoy no puede reescribir el pasado.
    """
    situacion = escenario.entregada()
    sistema.sync_y_motor()
    teorico_antes = sistema.teorico(situacion.nombre)
    assert teorico_antes is not None
    valor_antes = float(teorico_antes["teorico_ves"]), float(teorico_antes["teorico_usd"])
    assert any(v > 0 for v in valor_antes), "El teórico quedó en cero; no hay qué congelar."

    linea = odoo.ex(
        "sale.order.line", "read", [[situacion.orden.lineas[0]]], {"fields": ["product_id"]}
    )[0]
    producto = linea["product_id"][0]
    plantilla = odoo.ex(
        "product.product", "read", [[producto]], {"fields": ["product_tmpl_id"]}
    )[0]["product_tmpl_id"][0]
    reglas = odoo.ex(
        "product.pricelist.item",
        "search_read",
        [
            [
                ["pricelist_id", "=", situacion.lista_id],
                ["product_tmpl_id", "=", plantilla],
                ["compute_price", "=", "fixed"],
            ]
        ],
        {"fields": ["id", "fixed_price"]},
    )
    assert reglas, "El producto del escenario no tiene regla de precio fijo."
    precio_original = float(reglas[0]["fixed_price"])
    odoo.ex(
        "product.pricelist.item",
        "write",
        [[reglas[0]["id"]], {"fixed_price": precio_original * 2}],
    )
    try:
        olvidar_precios()
        sistema.sync_y_motor()
        teorico_despues = sistema.teorico(situacion.nombre)
        assert teorico_despues is not None
        valor_despues = (
            float(teorico_despues["teorico_ves"]),
            float(teorico_despues["teorico_usd"]),
        )
        assert valor_despues == valor_antes, (
            f"El teórico se movió de {valor_antes} a {valor_despues} al duplicar el "
            "precio de lista. Está congelado a propósito: si se mueve, el precio de "
            "hoy reescribe lo que se vendió ayer."
        )
    finally:
        odoo.ex(
            "product.pricelist.item",
            "write",
            [[reglas[0]["id"]], {"fixed_price": precio_original}],
        )
        olvidar_precios()


@pytest.mark.escenario("Archivan una lista de precios en uso")
def test_archivar_una_lista_no_le_saca_la_lista_a_sus_ordenes(escenario, sistema, odoo, listas):
    """Las órdenes nacidas con ella deben conservarla.

    Ya pasó con la lista 4 inactiva -- y de hecho es el estado actual de esta
    base: las listas 4 y 5 están archivadas y 635 de las 950 órdenes las usan.
    """
    situacion = escenario.entregada()
    sistema.sync_y_motor()
    lista_antes = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0][
        "lista_precios"
    ]
    assert str(lista_antes) == str(situacion.lista_id)

    odoo.ex("product.pricelist", "write", [[situacion.lista_id], {"active": False}])
    try:
        sistema.sync_y_motor()
        orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
        assert str(orden["lista_precios"]) == str(lista_antes), (
            "Archivar la lista le cambió la lista a una orden que nació con ella."
        )
        teorico = sistema.teorico(situacion.nombre)
        assert teorico is not None, (
            "El teórico desapareció al archivarse la lista de la orden."
        )
        assert float(teorico["teorico_ves"]) > 0 or float(teorico["teorico_usd"]) > 0, (
            "El teórico se fue a cero al archivarse la lista: una lista archivada "
            "sigue siendo la que valoró esa venta."
        )
    finally:
        odoo.ex("product.pricelist", "write", [[situacion.lista_id], {"active": True}])
        olvidar_precios()


@pytest.mark.escenario("Cargan una tasa equivocada en Odoo")
def test_la_partida_de_tasa_dice_cuantas_facturas_pudo_comparar(escenario, sistema):
    """Las dos partidas de tasa son las más fuertes del balance -- las únicas
    que comparan contra el BCV y no contra Odoo. El plan pedía provocar una
    tasa equivocada «para confirmar que las partidas lo agarran».

    Provocarlo encontró otra cosa, y más grave que la fila original. La partida
    despeja la tasa implícita de cada factura y la compara contra **nuestro**
    BCV de esa fecha; cuando no tenemos tasa para esa fecha, saltea la factura
    con un ``continue`` silencioso. Si no tenemos ninguna, saltea todas y
    reporta **cero divergencias** — que se lee exactamente igual que «verifiqué
    y está todo bien».

    O sea: la partida que existe para detectar una tasa mal cargada da verde
    justo cuando menos puede opinar. Es la misma trampa de «sin datos no es
    cero», dentro del instrumento que audita a los demás.

    Lo que este escenario fija ahora es que la partida **diga cuántas
    comparó**, que es lo único que permite distinguir los dos casos.
    """
    balance = sistema.balance()
    if not balance.get("evaluable", True):
        pytest.skip(f"El balance se abstiene: {balance.get('motivo')}")

    de_tasa = [
        p
        for p in balance.get("partidas") or []
        if "tasa de Odoo" in str(p.get("concepto", ""))
    ]
    assert de_tasa, "Las dos partidas de tasa de Odoo no están en el balance."

    for partida in de_tasa:
        nota = str(partida.get("nota", ""))
        assert "Comparadas" in nota or "NO SE COMPARÓ NINGUNA" in nota, (
            f"«{partida['concepto']}» reporta {partida['derecha']['valor']} divergencias "
            "sin decir sobre cuántos documentos. Un cero ahí puede significar «está "
            f"todo bien» o «no pude mirar nada», y no hay forma de saber cuál. Nota: {nota!r}"
        )
        if "NO SE COMPARÓ NINGUNA" in nota:
            assert partida["cuadra"], (
                "Se decidió que no poder comparar no vuelve roja la partida, pero "
                "sí tiene que decirlo en la nota."
            )
