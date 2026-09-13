"""Que la fontanería del banco funcione, antes de creerle a un escenario.

Si algo de acá falla, ningún resultado de los otros archivos significa nada:
un escenario que no puede armar su orden no está diciendo que el sistema
ande mal, está diciendo que el banco anda mal. Por eso este archivo va
primero por orden alfabético inverso... no: va primero porque se corre
primero a propósito (``./scripts/escenarios.sh escenarios/test_humo.py``).
"""

from __future__ import annotations


def test_el_diario_de_pruebas_no_tiene_imprenta_digital(odoo):
    """La barrera que impide emitir un documento fiscal real."""
    jid = odoo.diario_pruebas
    fila = odoo.ex(
        "account.journal", "read", [[jid]], {"fields": ["code", "type", "invoicing_digital_conn"]}
    )[0]
    assert fila["code"] == "ZZPRU"
    assert fila["type"] == "sale"
    assert fila["invoicing_digital_conn"] is False


def test_el_diario_de_ventas_de_produccion_si_la_tiene(odoo):
    """El contraste que explica por qué existe el diario de pruebas.

    Si este test empieza a fallar es una buena noticia -- alguien desconectó
    la imprenta del diario real en QA -- pero conviene enterarse, porque
    entonces el diario aparte deja de ser necesario.
    """
    reales = odoo.ex(
        "account.journal",
        "search_read",
        [[["type", "=", "sale"], ["code", "!=", "ZZPRU"]]],
        {"fields": ["code", "invoicing_digital_conn"]},
    )
    con_imprenta = [j for j in reales if j["invoicing_digital_conn"]]
    assert con_imprenta, (
        "Ningún diario de ventas tiene imprenta digital conectada. Si es cierto, "
        "el diario ZZPRU ya no hace falta; verificar antes de quitarlo."
    )


def test_las_listas_vigentes_no_son_las_del_env(listas):
    """El mapeo, no ``ENGINE_LISTA_*``.

    En esta base ``ENGINE_LISTA_USD=4`` y ``ENGINE_LISTA_BCV=5`` apuntan a
    listas ARCHIVADAS. Las vigentes son otras. El motor lo resuelve por el
    mapeo unificado, y el banco tiene que hacer lo mismo o mediría precios de
    una lista que ya nadie usa.
    """
    assert listas["ves"] not in (4, 5)
    assert listas["usd"] not in (4, 5)
    activas = set(listas["todas_ves"]) | set(listas["todas_usd"])
    assert len(activas) >= 2


def test_armar_una_orden_pedida(escenario, odoo):
    situacion = escenario.pedida()
    assert situacion.nombre.startswith("S")
    assert escenario.estado_orden(situacion.so) == "sale"
    assert escenario.total_orden(situacion.so) > 0
    # Se limpia: una orden confirmada y sin entregar es la que más ensucia el
    # resto del banco, porque queda como cuenta por cobrar viva.
    odoo.cancelar_orden(situacion.so)


def test_armar_una_orden_entregada_completa(escenario, odoo):
    situacion = escenario.entregada()
    assert odoo.estado_entrega(situacion.so) == "full", (
        "La entrega es de tres pasos (PICK/PACK/OUT) y el último exige vehículo; "
        f"quedó en {odoo.estado_entrega(situacion.so)!r} tras validar "
        f"{situacion.entregas}"
    )


def test_armar_una_orden_facturada_y_pagada(escenario, odoo):
    situacion = escenario.pagada()
    factura = odoo.ex(
        "account.move",
        "read",
        [situacion.facturas],
        {"fields": ["name", "state", "amount_residual", "journal_id", "is_digital_invoicing"]},
    )[0]
    assert factura["state"] == "posted"
    assert factura["is_digital_invoicing"] is False
    assert str(factura["name"]).startswith("ZZPRU/"), (
        f"La factura salió por el diario {factura['journal_id']}, no por el de pruebas."
    )
    assert abs(float(factura["amount_residual"])) < 0.01, "El pago no dejó la factura en cero."


def test_el_sync_trae_la_orden_al_espejo(escenario, sistema, odoo):
    situacion = escenario.entregada()
    sistema.sync()
    filas = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)
    assert len(filas) == 1, f"El sync no trajo {situacion.nombre} al espejo."
    fila = filas[0]
    assert fila["entregada_completa"] is True
    assert fila["fecha_entrega"] is not None
    assert float(fila["monto_total"]) > 0
    odoo.cancelar_orden(situacion.so)


def test_los_cuatro_reportes_responden(sistema):
    """Que los endpoints contesten 200 antes de creerle a una aserción."""
    for nombre, datos in [
        ("saldos", sistema.saldos()),
        ("ventas", sistema.ventas()),
        ("bandeja", sistema.bandeja()),
        ("balance", sistema.balance()),
    ]:
        assert isinstance(datos, dict), nombre
