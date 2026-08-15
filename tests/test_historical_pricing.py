"""cargar_mapa_historico -- crosswalk código->product_id de Odoo.

Bug real (auditoría agosto 2026): el mapa se indexaba por el "código"
crudo del CSV origen (numeración interna de Lubrikca, ej. "1", "7"...),
pero ``_precio_unitario_linea`` busca por ``linea.producto``, que es el
``product_id`` de Odoo (ej. "1059") -- nunca hacían match. Ahora el mapa
se indexa por ``producto_id_odoo`` (columna resuelta por
``scripts/cruzar_codigos_lista_historica.py``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.config import EngineConfig
from cxc.engine.discounts import EngineInputs, calcular_teorico_orden_con_fallback
from cxc.engine.historical_pricing import cargar_mapa_historico
from cxc.engine.price_resolver import DictPriceResolver
from cxc.models import LineaOrden, OrdenVenta

CFG = EngineConfig(cash_window_business_days=3, bcv_complete_formula="differential_over_binance")


def test_mapa_se_indexa_por_producto_id_odoo_no_por_codigo():
    rows = [
        {
            "codigo": "1",
            "producto_nombre": "ELITE API SP SAE 0W-20 (1x6)",
            "precio_usd": "42.24",
            "precio_bcv_euro": "52.59",
            "producto_id_odoo": "1059",
        }
    ]
    mapa = cargar_mapa_historico(rows)
    assert "1059" in mapa
    assert "1" not in mapa
    assert mapa["1059"]["usd"] == Decimal("42.24")


def test_filas_sin_producto_id_odoo_resuelto_se_excluyen():
    rows = [
        {
            "codigo": "999",
            "producto_nombre": "Producto sin match",
            "precio_usd": "10.00",
            "precio_bcv_euro": "12.00",
            "producto_id_odoo": "",
        }
    ]
    mapa = cargar_mapa_historico(rows)
    assert mapa == {}


def test_teorico_ves_usa_lista_historica_pero_usd_usa_lista_7():
    """Aclaratoria del usuario (agosto 2026, auditoría S00020): la Lista

    Histórica de Auditoría SOLO sustituye la lista VES en ese período --
    el teórico USD debe seguir usando la lista USD vigente entonces
    (pricelist Odoo id 7, "Pago USD Marzo"), NO el mismo precio histórico.
    Bug real #1: antes ambos teóricos salían idénticos (mismo precio
    histórico para las dos listas).

    Bug real #2 (auditoría agosto 2026, S00059/S00020): el teórico VES
    leía la columna ``precio_usd`` (== ``hist_info["usd"]``) del CSV
    histórico, que en los datos reales resultó ser el MISMO precio ya
    usado por la lista USD vigente -- no el precio VES/BCV-Euro real
    (~25% más alto, columna ``precio_bcv_euro`` == ``hist_info["eur"]``,
    nunca leída). Por eso teórico VES y teórico USD salían casi
    idénticos en vez de reflejar la brecha real de ese período."""
    orden = OrdenVenta(
        so_id="SO_HIST",
        cliente_id="C_HIST",
        vendedor_email="v@lubrikca.com",
        fecha=date(2026, 3, 9),
        fecha_entrega=None,
        monto_total=Decimal("100.00"),
        lista_precios="3",
        es_primera_compra=False,
        facturada=False,
    )
    lineas = [
        LineaOrden(
            linea_id="L1",
            so_id="SO_HIST",
            producto="1059",
            marca="Sinoco",
            categoria="Comercial",
            cantidad=Decimal("1"),
            precio_unitario=Decimal("53.45"),
        )
    ]
    # Precio histórico VES/BCV-Euro (precio_bcv_euro) = 66.81 (~25% mas alto
    # que el USD, consistente con la brecha real del período); precio real
    # en la lista USD vigente entonces (id "7") = 86.21 -- las 3 cifras
    # deliberadamente distintas para exponer cualquiera de los 2 bugs si
    # algún día se reintroduce.
    resolver = DictPriceResolver({("1059", "7"): Decimal("86.21")})
    inputs = EngineInputs(
        orden=orden,
        lineas=lineas,
        abonos=[],
        descuentos=[],
        descuentos_volumen=[],
        reglas_recurrencia=[],
        promociones_primera_compra=[],
        feriados_tabla=[],
        price_resolver=resolver,
        engine_config=CFG,
        fecha_calculo=date(2026, 3, 9),
        valid_usd=["8"],
        valid_ves=["5"],
        orden_es_historica=True,
        historical_price_map={
            "1059": {"nombre": "x", "usd": Decimal("53.45"), "eur": Decimal("66.81")}
        },
    )

    resultado = calcular_teorico_orden_con_fallback(inputs)

    assert resultado["lista_usd_id"] == "7"
    assert resultado["teorico_ves"] == Decimal("66.81")
    assert resultado["teorico_usd"] == Decimal("86.21")
    assert resultado["teorico_ves"] != resultado["teorico_usd"]
