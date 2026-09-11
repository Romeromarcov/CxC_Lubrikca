"""Facturación: las cuatro filas de la quinta familia.

Dos de las cuatro tienen caso real detrás: S00573 (anularon una factura y
emitieron otra por lo mismo, y sigue pendiente) y los diez casos de doble
facturación que el detector ya encuentra. Para esos, el escenario no pregunta
si el sistema los ve -- ya se sabe que sí -- sino si el detector tiene falsos
negativos.
"""

from __future__ import annotations

import pytest


@pytest.mark.escenario("Anulan una factura y emiten otra por lo mismo")
def test_refacturar_no_cuenta_la_orden_dos_veces(escenario, sistema, odoo):
    """El caso de S00573.

    La orden no debe contarse dos veces: la factura anulada tiene que dejar de
    sumar, y solo la nueva cuenta.
    """
    situacion = escenario.facturada()
    sistema.sync_y_motor()
    primera = situacion.facturas[0]
    total = escenario.total_factura(primera)

    odoo.anular_factura(primera)
    producto = odoo.producto_con_precio(situacion.lista_id)
    segunda = odoo.factura_directa(
        situacion.cliente_id, [(producto, 2, total / 2)], origen=situacion.nombre
    )
    sistema.sync_y_motor()

    facturas = sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)
    por_id = {f["factura_id"]: f for f in facturas}
    assert str(segunda) in por_id, "La factura nueva no llegó al espejo."

    vigentes = [f for f in facturas if f["estado"] == "posted"]
    neto = sum(float(f["monto_total_signed_usd"]) for f in vigentes)
    assert len(vigentes) == 1, (
        f"Quedaron {len(vigentes)} facturas vigentes para una sola orden: "
        f"{[f['factura_id'] + ':' + f['estado'] for f in facturas]}. La orden se "
        "cuenta dos veces."
    )
    assert neto > 0, "El neto facturado quedó en cero o negativo tras refacturar."

    # Y la mitad que faltaba, agregada el 11-sep-2026 después de medirla en la
    # copia de producción: que el espejo quede bien NO alcanza.
    #
    # La factura anulada tiene ``amount_residual = 0`` porque la reversó una nota
    # de crédito, y el fallback que calcula el abono hacía
    # ``amount_total - amount_residual`` -- leía la anulación como un cobro
    # completo. Medido: 17 órdenes reales salían de la cuenta por cobrar por eso,
    # 4.489,12 USD entre lo no facturado y lo facturado sin cobrar. El caso más
    # limpio era S00886: dos facturas, cero pagos, y el reporte la daba por
    # cobrada.
    #
    # Este escenario lo habría visto si hubiera mirado el saldo en vez de solo el
    # espejo. Ahora lo mira.
    en_saldos = sistema.orden_en_saldos(situacion.nombre)
    assert en_saldos is not None, (
        "La orden refacturada desapareció de la cuenta por cobrar. Nadie pagó nada: "
        "si no está, el abono se calculó desde la factura ANULADA."
    )
    deudor = float(en_saldos.get("saldo_deudor_bcv") or 0)
    assert deudor > 0, (
        f"La orden refacturada figura con saldo deudor {deudor}. La factura nueva "
        "está sin pagar, así que debe seguir debiendo."
    )


@pytest.mark.escenario("Facturan la misma orden dos veces")
def test_el_detector_de_doble_facturacion_no_tiene_falsos_negativos(
    escenario, sistema, odoo
):
    """Ya se detectan diez casos.

    Hay que probar que el detector no tiene falsos negativos: se provoca una
    doble facturación limpia y se verifica que la ve.
    """
    situacion = escenario.facturada()
    sistema.sync_y_motor()
    total = escenario.total_factura(situacion.facturas[0])

    producto = odoo.producto_con_precio(situacion.lista_id)
    duplicada = odoo.factura_directa(
        situacion.cliente_id, [(producto, 2, total / 2)], origen=situacion.nombre
    )
    sistema.sync_y_motor()

    facturas = sistema.espejo(
        "facturas", "so_id = :so AND estado = 'posted' AND move_type = 'out_invoice'",
        so=situacion.nombre,
    )
    assert len(facturas) >= 2, (
        f"El escenario no logró la doble facturación (id nuevo {duplicada})."
    )
    orden = sistema.espejo("ordenes_venta", "so_id = :so", so=situacion.nombre)[0]
    neto = sum(float(f["monto_total_signed_usd"]) for f in facturas)
    assert neto > float(orden["monto_total"]), (
        "Lo facturado no supera el monto de la orden, así que la doble facturación "
        "no se puede detectar comparando esos dos números."
    )


@pytest.mark.escenario("Editan la factura después de pagada")
def test_editar_la_factura_pagada_mueve_el_residual_y_el_reporte_lo_sigue(
    escenario, sistema, odoo
):
    """El residual de Odoo cambia bajo nuestros pies.

    Como ahora tomamos su cifra, el reporte lo sigue; hay que verificar que el
    árbol de decisión también, es decir que la orden vuelva a deber.
    """
    situacion = escenario.pagada()
    sistema.sync_y_motor()
    residual_antes = float(
        odoo.ex("account.move", "read", [situacion.facturas], {"fields": ["amount_residual"]})[0][
            "amount_residual"
        ]
    )
    assert abs(residual_antes) < 0.01

    # Se sube la cantidad de la factura, con el pago ya aplicado.
    lineas = odoo.ex(
        "account.move.line",
        "search_read",
        [[["move_id", "=", situacion.facturas[0]], ["display_type", "in", ["product", False]]]],
        {"fields": ["id", "quantity"]},
    )
    odoo.ex("account.move", "button_draft", [situacion.facturas])
    odoo.ex(
        "account.move.line",
        "write",
        [[lineas[0]["id"]], {"quantity": float(lineas[0]["quantity"]) + 3}],
    )
    odoo.ex("account.move", "action_post", [situacion.facturas])
    sistema.sync_y_motor()

    residual_despues = float(
        odoo.ex("account.move", "read", [situacion.facturas], {"fields": ["amount_residual"]})[0][
            "amount_residual"
        ]
    )
    assert residual_despues > 0.01, (
        "Editar la factura hacia arriba no dejó residual; el escenario no armó."
    )
    facturas = sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)
    assert facturas, "La factura desapareció del espejo al editarla."
    assert float(facturas[0]["monto_total"]) > 0


@pytest.mark.escenario("Factura en moneda distinta a la orden")
def test_la_factura_en_otra_moneda_usa_su_propia_tasa(escenario, sistema, odoo):
    """Debe usar la tasa de la factura, no la de la orden.

    La orden nace en una lista expresada en USD y la factura de esta base se
    emite en VES (la moneda de la compañía). El espejo guarda las dos caras --
    ``monto_total`` en la moneda del documento y ``monto_total_signed_usd``
    convertido -- y son las dos las que tienen que estar.
    """
    situacion = escenario.facturada(moneda="ves")
    sistema.sync_y_motor()

    facturas = sistema.espejo("facturas", "so_id = :so", so=situacion.nombre)
    assert facturas, "La factura no llegó al espejo."
    factura = facturas[0]
    assert factura["moneda"], "La factura llegó sin moneda; no se puede convertir nada."
    nominal = float(factura["monto_total"])
    en_usd = float(factura["monto_total_signed_usd"])
    assert nominal > 0, "El monto nominal de la factura quedó en cero."
    assert en_usd > 0, (
        "El equivalente en dólares de la factura quedó en cero. Sin él la orden no "
        "se puede comparar contra su teórico USD."
    )
    if factura["moneda"] != "USD":
        assert nominal != en_usd, (
            f"El nominal ({nominal} {factura['moneda']}) y el equivalente USD "
            f"({en_usd}) son iguales: la conversión no se aplicó, que es el error de "
            "mostrar bolívares con signo de dólar."
        )
