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
    r = clasificar_estado_cxc(
        so_id="S00357",
        facturada=True,
        teorico_bs_pagado=False,
        teorico_usd_pagado=False,
        factura_real_pagada=True,
    )
    assert r.sale_de_cxc is False
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
