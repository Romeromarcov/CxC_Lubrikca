"""Tests del árbol de enrutamiento de CxC (src/cxc/engine/cxc_routing.py)."""

from __future__ import annotations

from cxc.engine.cxc_routing import BandejaDestino, clasificar_estado_cxc


def test_pagado_vs_teorico_usd_no_facturada_va_a_bandeja_1():
    r = clasificar_estado_cxc(
        so_id="S00001",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=True,
        factura_real_pagada=False,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_1


def test_pagado_vs_teorico_usd_facturada_va_a_bandeja_2():
    r = clasificar_estado_cxc(
        so_id="S00002",
        facturada=True,
        teorico_bs_pagado=False,
        teorico_usd_pagado=True,
        factura_real_pagada=False,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_2


def test_pagado_vs_teorico_bs_no_facturada_va_a_bandeja_1():
    r = clasificar_estado_cxc(
        so_id="S00003",
        facturada=False,
        teorico_bs_pagado=True,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_1


def test_pagado_vs_teorico_bs_facturada_va_a_bandeja_2():
    r = clasificar_estado_cxc(
        so_id="S00004",
        facturada=True,
        teorico_bs_pagado=True,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_2


def test_pagado_ambos_teoricos_prioriza_usd():
    r = clasificar_estado_cxc(
        so_id="S00005",
        facturada=True,
        teorico_bs_pagado=True,
        teorico_usd_pagado=True,
        factura_real_pagada=False,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_2
    assert "USD" in r.motivo


def test_pagado_solo_vs_factura_real_no_vs_teoricos_va_a_auditoria_precios():
    """Corrección del usuario (agosto 2026, artefacto de verificación):

    legalmente lo que vale es la factura -- si el cliente ya la pagó, no
    hay mucho que reclamarle, así que la orden SALE de CxC activa (antes
    permanecía). Se enruta ADEMÁS a Auditoría de Precios para revisión
    interna del precio/lista aplicado -- eso es un tema de control
    posterior, no una condición para cerrar la cobranza.
    """
    r = clasificar_estado_cxc(
        so_id="S00357",
        facturada=True,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=True,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.AUDITORIA_PRECIOS


def test_factura_real_pagada_pero_no_facturada_no_dispara_auditoria_precios():
    # No debería poder ocurrir en datos reales (factura_real_pagada implica
    # que existe factura), pero el árbol debe ser explícito sobre facturada=False.
    r = clasificar_estado_cxc(
        so_id="S00006",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=True,
    )
    assert r.sale_de_cxc is False
    assert r.bandeja_destino is None


def test_nacida_en_lista_usd_pagada_solo_bs_no_sale_de_cxc():
    r = clasificar_estado_cxc(
        so_id="S00020",
        facturada=False,
        teorico_bs_pagado=True,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        nacio_en_lista_usd=True,
    )
    assert r.sale_de_cxc is False
    assert r.bandeja_destino is None


def test_nacida_en_lista_usd_pagada_usd_si_sale_de_cxc():
    r = clasificar_estado_cxc(
        so_id="S00021",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=True,
        factura_real_pagada=False,
        nacio_en_lista_usd=True,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_1


def test_nacida_en_lista_ves_pagada_solo_usd_si_sale_de_cxc():
    r = clasificar_estado_cxc(
        so_id="S00022",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=True,
        factura_real_pagada=False,
        nacio_en_lista_usd=False,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_1


def test_nacida_en_lista_ves_pagada_solo_bs_si_sale_de_cxc():
    r = clasificar_estado_cxc(
        so_id="S00023",
        facturada=False,
        teorico_bs_pagado=True,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        nacio_en_lista_usd=False,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_1


def test_sin_pago_suficiente_permanece_en_cxc_sin_bandeja():
    r = clasificar_estado_cxc(
        so_id="S00007",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
    )
    assert r.sale_de_cxc is False
    assert r.bandeja_destino is None


def test_venta_real_pagada_no_facturada_va_a_bandeja_1_sin_cubrir_teoricos():
    """Corrección del usuario (agosto 2026): después de emitida la factura,

    la segunda fuente de verdad es la propia orden real -- si el pago
    cubre el monto real de la orden (Col 3) y todavía no está facturada,
    debe pasar a Bandeja 1 aunque no cubra ningún teórico. Los teóricos
    son solo referencia de descuento/auditoría, no un requisito para
    facturar.
    """
    r = clasificar_estado_cxc(
        so_id="S00008",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        venta_real_pagada=True,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_1


def test_venta_real_pagada_no_aplica_si_ya_esta_facturada():
    """``venta_real_pagada`` solo tiene efecto en órdenes SIN facturar --

    una vez facturada, lo que manda es ``factura_real_pagada`` (regla 4),
    no la venta real de la orden.
    """
    r = clasificar_estado_cxc(
        so_id="S00009",
        facturada=True,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        venta_real_pagada=True,
    )
    assert r.sale_de_cxc is False
    assert r.bandeja_destino is None


def test_teorico_usd_pendiente_va_en_proceso_de_pago_no_confirmado():
    """Precedente citado por el usuario: Odoo tiene un estado "en proceso

    de pago" para facturas cuyo cobro ya se aplicó pero falta la
    conciliación bancaria -- ESE estado ya saca la factura de CxC en
    Odoo, aunque quede visiblemente distinto de "pagado". Se replica: una
    Vinculación PENDIENTE (vinculada a esta orden, sin confirmar por
    Odoo) que cubre el Teórico USD saca la orden de CxC activa, pero con
    su propio destino y confirmado=False -- nunca se confunde con un
    pago realmente CONCILIADO.
    """
    r = clasificar_estado_cxc(
        so_id="S00010",
        facturada=True,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        teorico_usd_pagado_incl_pendiente=True,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.EN_PROCESO_DE_PAGO
    assert r.confirmado is False


def test_confirmado_real_tiene_prioridad_sobre_pendiente():
    """Si el Teórico USD ya está CONCILIADO, no debe caer nunca en la

    rama "en proceso de pago" aunque también se pase incl_pendiente=True
    -- la versión confirmada siempre gana."""
    r = clasificar_estado_cxc(
        so_id="S00011",
        facturada=True,
        teorico_bs_pagado=False,
        teorico_usd_pagado=True,
        factura_real_pagada=False,
        teorico_usd_pagado_incl_pendiente=True,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_2
    assert r.confirmado is True


def test_venta_real_pendiente_no_facturada_va_a_facturacion_1_no_confirmada():
    """Corrección del usuario (2026-08-22): antes de facturar, CONCILIADO

    es estructuralmente imposible (Odoo solo reconcilia contra un
    documento real, y no hay ninguno hasta facturar) -- si "en proceso de
    pago" mandara esta orden a una lista pasiva, nunca habría ninguna
    señal que la hiciera avanzar. Por eso, para una orden SIN facturar,
    "en proceso de pago" enruta a Facturación 1 igual que un pago
    confirmado -- con confirmado=False para que quede visible que el
    pago aún no está reconciliado.
    """
    r = clasificar_estado_cxc(
        so_id="S00012",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        venta_real_pagada_incl_pendiente=True,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_1
    assert r.confirmado is False


def test_nacio_en_lista_usd_bloquea_en_proceso_de_pago_via_bs_tambien():
    """La restricción de lista nativa (regla 1/2) también aplica a la

    versión "en proceso de pago" -- una orden nacida en USD no sale de
    CxC solo porque el Teórico BS esté pendiente-cubierto."""
    r = clasificar_estado_cxc(
        so_id="S00013",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        nacio_en_lista_usd=True,
        teorico_bs_pagado_incl_pendiente=True,
    )
    assert r.sale_de_cxc is False
    assert r.bandeja_destino is None


def test_sin_ninguna_referencia_pendiente_permanece_en_cxc():
    r = clasificar_estado_cxc(
        so_id="S00014",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
    )
    assert r.sale_de_cxc is False
    assert r.bandeja_destino is None
    assert r.confirmado is True


def test_teorico_usd_pendiente_no_facturada_va_a_facturacion_1():
    """Mismo matiz que venta_real: si el Teórico USD está cubierto vía

    Vinculación PENDIENTE y la orden aún no está facturada, debe enrutar
    a Facturación 1 -- no a la lista pasiva "en proceso de pago", porque
    antes de facturar esa es la única señal que hará avanzar la orden.
    """
    r = clasificar_estado_cxc(
        so_id="S00015",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        teorico_usd_pagado_incl_pendiente=True,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.FACTURACION_1
    assert r.confirmado is False


def test_teorico_bs_pendiente_facturada_va_en_proceso_de_pago_no_facturacion():
    """Espejo del anterior: para una orden YA facturada, la misma señal

    pendiente NO debe re-enrutarla a Facturación (ya está facturada) --
    debe quedar en la lista pasiva "en proceso de pago", esperando a que
    el resync automático de Odoo la confirme.
    """
    r = clasificar_estado_cxc(
        so_id="S00016",
        facturada=True,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        teorico_bs_pagado_incl_pendiente=True,
    )
    assert r.sale_de_cxc is True
    assert r.bandeja_destino == BandejaDestino.EN_PROCESO_DE_PAGO
    assert r.confirmado is False
