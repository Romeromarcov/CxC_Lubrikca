"""Los dos techos de la nota de crédito sugerida (Fase 2.4, pieza 34).

Salió de ``get_bandeja_facturacion`` (638 líneas), donde las ocho líneas del techo por
brecha no las ejecutaba ninguna prueba -- y son las que deciden cuánto se le acredita a
un cliente. Los casos de abajo son los del docstring del módulo: S00010 (TERA), las 92
de 215 que sugerían más que su brecha, y la regla general del usuario sobre lo
facturado por debajo del teórico.
"""

from __future__ import annotations

import pytest

from cxc.engine.notas_de_credito import (
    MOTIVO_CABE_EN_LA_BRECHA,
    MOTIVO_FACTURADO_BAJO_TEORICO,
    MOTIVO_SIN_BRECHA,
    MOTIVO_SIN_FACTURADO,
    MOTIVO_TOPADA_POR_BRECHA,
    factor_de_impuesto,
    topar_nota_de_credito,
)

IVA = 0.16


def _topar(**kw):
    base = {
        "descuento_pendiente": 100.0,
        "facturado_neto": 1160.0,  # con impuesto
        "pagado_usd": 900.0,
        "teorico_de_referencia": 1000.0,
        "facturado_sin_impuestos": 1000.0,
        "facturado_con_impuestos": 1160.0,
        "iva_configurado": IVA,
    }
    base.update(kw)
    return topar_nota_de_credito(**base)


# --- la regla previa: sin facturado no hay techo -------------------------------


def test_sin_saber_cuanto_se_facturo_la_sugerencia_sale_entera() -> None:
    """«No sé» no es «cero»: tratarlo como cero vaciaba la bandeja cuando Odoo no
    respondía (lo detectaron los e2e 13 y 14e)."""
    r = _topar(facturado_neto=0.0)
    assert r.subtotal == 100.0
    assert r.motivo == MOTIVO_SIN_FACTURADO
    assert r.brecha_con_impuesto is None and r.factor_iva is None
    assert r.hay_nc


# --- el techo del teórico --------------------------------------------------------


def test_facturado_por_debajo_del_teorico_no_sugiere_nada() -> None:
    """Ya entregó el descuento y de más; una NC encima descontaría dos veces. Va a
    Auditoría, donde administración decide si corresponde una nota de débito."""
    r = _topar(facturado_neto=900.0, teorico_de_referencia=1000.0, pagado_usd=500.0)
    assert r.subtotal == 0.0
    assert r.motivo == MOTIVO_FACTURADO_BAJO_TEORICO
    assert not r.hay_nc


def test_facturado_igual_al_teorico_dentro_de_la_tolerancia_no_cuenta_como_bajo() -> None:
    r = _topar(facturado_neto=999.97, teorico_de_referencia=1000.0, pagado_usd=900.0)
    assert r.motivo != MOTIVO_FACTURADO_BAJO_TEORICO


def test_sin_teorico_de_referencia_no_se_afirma_que_este_bajo() -> None:
    r = _topar(teorico_de_referencia=None)
    assert r.motivo in (MOTIVO_CABE_EN_LA_BRECHA, MOTIVO_TOPADA_POR_BRECHA)


# --- el techo de la brecha --------------------------------------------------------


def test_el_caso_TERA_brecha_negativa_no_sugiere_nada() -> None:
    """S00010: NC sugerida de 3.949,79 con una brecha de −0,52. Pagaron la factura
    entera porque el 35 % ya iba en el precio."""
    r = _topar(
        descuento_pendiente=3949.79,
        facturado_neto=8000.0,
        pagado_usd=8000.52,
        teorico_de_referencia=7000.0,
    )
    assert r.subtotal == 0.0
    assert r.motivo == MOTIVO_SIN_BRECHA
    assert r.brecha_con_impuesto == -0.52


def test_la_nc_se_topa_a_la_brecha_llevada_a_subtotal() -> None:
    """Brecha 260 con impuesto → 260 / 1,16 = 224,14 de subtotal. El descuento
    pendiente de 300 no cabe: se topa."""
    r = _topar(descuento_pendiente=300.0, facturado_neto=1160.0, pagado_usd=900.0)
    assert r.motivo == MOTIVO_TOPADA_POR_BRECHA
    assert r.brecha_con_impuesto == 260.0
    assert r.factor_iva == pytest.approx(1.16)
    assert r.subtotal == pytest.approx(260.0 / 1.16)
    assert not r.iva_asumido


def test_si_el_descuento_cabe_en_la_brecha_sale_entero() -> None:
    r = _topar(descuento_pendiente=100.0, facturado_neto=1160.0, pagado_usd=900.0)
    assert r.motivo == MOTIVO_CABE_EN_LA_BRECHA
    assert r.subtotal == 100.0


def test_la_brecha_se_lleva_a_subtotal_con_el_factor_REAL_de_la_factura() -> None:
    """Una factura con retención o tasas mezcladas no rinde 1,16: manda su
    cociente, no la constante."""
    r = _topar(
        descuento_pendiente=300.0,
        facturado_neto=1080.0,
        pagado_usd=800.0,
        facturado_sin_impuestos=1000.0,
        facturado_con_impuestos=1080.0,
    )
    assert r.factor_iva == pytest.approx(1.08)
    assert r.subtotal == pytest.approx(280.0 / 1.08)
    assert not r.iva_asumido


def test_sin_los_dos_totales_se_usa_el_iva_configurado_y_se_dice() -> None:
    r = _topar(
        descuento_pendiente=300.0,
        facturado_neto=1160.0,
        pagado_usd=900.0,
        facturado_sin_impuestos=0.0,
        facturado_con_impuestos=0.0,
    )
    assert r.iva_asumido is True
    assert r.factor_iva == pytest.approx(1.16)


def test_el_factor_de_impuesto_respeta_el_iva_que_se_le_configura() -> None:
    assert factor_de_impuesto(0.0, 0.0, iva_configurado=0.08) == (pytest.approx(1.08), True)
    assert factor_de_impuesto(100.0, 112.0, iva_configurado=0.08) == (pytest.approx(1.12), False)


# --- y la bandeja lo usa ----------------------------------------------------------


def test_la_bandeja_llama_al_tope_y_publica_el_motivo() -> None:
    """Guarda de cableado: la pieza tiene que estar en el camino vivo, y el motivo
    tiene que viajar en la fila de la NC. Se lee el código, no se ejecuta la
    bandeja (que necesita medio sistema); las guardas de ``test_piezas_cableadas``
    hacen lo mismo para todas las piezas."""
    from pathlib import Path

    fuente = Path("src/cxc/web/app.py").read_text(encoding="utf-8")
    i = fuente.index("async def get_bandeja_facturacion")
    cuerpo = fuente[i : fuente.index("\n@app.", i)]
    assert "topar_nota_de_credito(" in cuerpo
    assert '"nc_motivo": nc_topada.motivo' in cuerpo
    assert "1.16" not in cuerpo, "el IVA sale de la configuración, no de una constante suelta"
