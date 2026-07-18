"""Tests del motor de descuentos (secciones 4.x).

Cubre los escenarios obligatorios: apilamiento (Sinoco recompra contado = 6%,
GO sintético = 11%), contado vencido → crédito, mezcla → Binance, neto-objetivo
alcanzado → candidata a cierre, BCV-completo, y día hábil con feriado.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.config import EngineConfig
from cxc.engine.discounts import EngineInputs, calcular_factura
from cxc.engine.price_resolver import DictPriceResolver
from cxc.models import Moneda, TipoTasa

from . import builders as b

CFG = EngineConfig(
    cash_window_business_days=3,
    bcv_complete_formula="differential_over_binance",
    lista_usd="USD",
    lista_bcv="BCV",
)


def _resolver(**precios: str) -> DictPriceResolver:
    # precios: clave "<producto>@<lista>" -> precio
    mapa: dict[tuple[str, str], Decimal] = {}
    for clave, val in precios.items():
        producto, lista = clave.split("@")
        mapa[(producto, lista)] = Decimal(val)
    return DictPriceResolver(mapa)


def _inputs(
    *,
    orden,
    lineas,
    abonos,
    descuentos=(),
    descuentos_volumen=(),
    reglas=(),
    bcv_diario=(),
    promociones=(),
    feriados=(),
    resolver,
    fecha_calculo=date(2026, 6, 8),
    all_ordenes=None,
) -> EngineInputs:
    return EngineInputs(
        orden=orden,
        lineas=list(lineas),
        abonos=list(abonos),
        descuentos=list(descuentos),
        descuentos_volumen=list(descuentos_volumen),
        reglas_recurrencia=list(reglas),
        descuento_bcv_diario=list(bcv_diario),
        promociones_primera_compra=list(promociones),
        feriados_tabla=list(feriados),
        price_resolver=resolver,
        engine_config=CFG,
        fecha_calculo=fecha_calculo,
        all_ordenes=list(all_ordenes) if all_ordenes is not None else [],
    )


def test_apilamiento_sinoco_recompra_contado_6pct() -> None:
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    # Abono USD que liquida el neto optimista (100 - 6 = 94) dentro de ventana.
    vinc = b.vinculacion(
        monto_aplicado="94",
        moneda_abono=Moneda.USD,
        tipo_tasa_abono=TipoTasa.N_A,
        hora=datetime(2026, 6, 5, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[b.descuento(marca="Sinoco", categoria="*", porcentaje="0.03")],
        reglas=[b.regla_recompra("0.03")],
        resolver=_resolver(**{"P1@USD": "100"}),
    )
    res = calcular_factura(inp)
    assert res.lista_aplicada == "USD"
    # 3% recompra + 3% contado = 6%
    assert res.total_descuentos == Decimal("6.00")
    assert res.total_motor == Decimal("94.00")
    origenes = {d.origen for d in res.descuentos_detalle}
    assert origenes == {"recurrencia", "contado"}
    assert res.candidata_a_cierre is True


def test_apilamiento_global_oil_sintetico_recompra_contado_11pct() -> None:
    orden = b.orden(primera=False)
    linea = b.linea(
        marca="Global Oil", categoria="Comercial sintéticos",
        precio="100", cantidad="1",
    )
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="89", moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 5, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[
            b.descuento(
                marca="Global Oil", categoria="Comercial sintéticos",
                porcentaje="0.08",
            )
        ],
        reglas=[b.regla_recompra("0.03")],
        resolver=_resolver(**{"P1@USD": "100"}),
    )
    res = calcular_factura(inp)
    # 3% + 8% = 11%
    assert res.total_descuentos == Decimal("11.00")
    assert res.total_motor == Decimal("89.00")


def test_contado_vencido_pasa_a_credito_pierde_contado() -> None:
    orden = b.orden(primera=False)  # entrega 2026-06-05, ventana hasta 06-10
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    # Abono tardío (2026-06-15) y suficiente para el neto SIN contado (97).
    vinc = b.vinculacion(
        monto_aplicado="97", moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 15, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[b.descuento(marca="Sinoco", categoria="*", porcentaje="0.03")],
        reglas=[b.regla_recompra("0.03")],
        resolver=_resolver(**{"P1@USD": "100"}),
        fecha_calculo=date(2026, 6, 16),
    )
    res = calcular_factura(inp)
    # Solo queda recompra 3%; el contado se negó por vencimiento.
    assert res.total_descuentos == Decimal("3.00")
    assert res.total_motor == Decimal("97.00")
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "contado" not in origenes
    assert res.requiere_revision is True


def test_mezcla_de_rutas_migra_a_binance_y_pierde_bcv_completo() -> None:
    orden = b.orden(primera=False, lista="BCV")
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo_bcv = b.metodo("MB", moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV,
                          es_contado=False)
    metodo_bin = b.metodo("MN", moneda=Moneda.VES, tipo_tasa=TipoTasa.BINANCE,
                          es_contado=False)
    v_bcv = b.vinculacion(
        "V1", monto_aplicado="1800", moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV, tasa_bcv="36.0", tasa_binance="40.0",
    )
    v_bin = b.vinculacion(
        "V2", monto_aplicado="2000", moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BINANCE, tasa_bcv="36.0", tasa_binance="40.0",
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(v_bcv, metodo_bcv), (v_bin, metodo_bin)],
        reglas=[b.regla_recompra("0.03")],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "120"}),
    )
    res = calcular_factura(inp)
    # Mezcla → lista USD (la más conservadora) y sin BCV-completo.
    assert res.lista_aplicada == "USD"
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "bcv_completo" not in origenes


def test_bcv_completo_aplica_en_ruta_bcv_pura() -> None:
    orden = b.orden(primera=False, lista="BCV")
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo_bcv = b.metodo("MB", moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV,
                          es_contado=False)
    # VES 3600 a bcv 36 → 100 USD ; binance 40 → diferencial 10%.
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV, tasa_bcv="36.0", tasa_binance="40.0",
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo_bcv)],
        reglas=[b.regla_recompra("0.03")],
        bcv_diario=[b.regla_bcv_completo("0.15")],  # gerencia 15% > diferencial
        resolver=_resolver(**{"P1@BCV": "100"}),
    )
    res = calcular_factura(inp)
    assert res.lista_aplicada == "BCV"
    bcv = [d for d in res.descuentos_detalle if d.origen == "bcv_completo"]
    assert len(bcv) == 1
    # min(15%, diferencial 10%) = 10% sobre 100 USD = 10.00
    assert bcv[0].monto == Decimal("10.00")
    assert res.requiere_revision is True


def test_bcv_completo_topado_al_porcentaje_de_gerencia() -> None:
    # Gerencia fija 5% aunque el diferencial real sea 10% -> aplica 5%.
    orden = b.orden(primera=False, lista="BCV")
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo_bcv = b.metodo("MB", moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV,
                          es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV, tasa_bcv="36.0", tasa_binance="40.0",
    )
    inp = _inputs(
        orden=orden, lineas=[linea], abonos=[(vinc, metodo_bcv)],
        bcv_diario=[b.regla_bcv_completo("0.05")],
        resolver=_resolver(**{"P1@BCV": "100"}),
    )
    res = calcular_factura(inp)
    bcv = [d for d in res.descuentos_detalle if d.origen == "bcv_completo"]
    assert bcv[0].monto == Decimal("5.00")  # min(5%, 10%) = 5% sobre 100 USD


def test_bcv_completo_sin_tasa_diaria_no_se_otorga() -> None:
    # Sin porcentaje configurado para la fecha -> no se regala (conservador).
    orden = b.orden(primera=False, lista="BCV")
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo_bcv = b.metodo("MB", moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV,
                          es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV, tasa_bcv="36.0", tasa_binance="40.0",
    )
    inp = _inputs(
        orden=orden, lineas=[linea], abonos=[(vinc, metodo_bcv)],
        resolver=_resolver(**{"P1@BCV": "100"}),  # sin bcv_diario
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "bcv_completo" not in origenes


def test_neto_no_alcanzado_no_es_candidata() -> None:
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="50", moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 5, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[b.descuento(marca="Sinoco", categoria="*", porcentaje="0.03")],
        reglas=[b.regla_recompra("0.03")],
        resolver=_resolver(**{"P1@USD": "100"}),
    )
    res = calcular_factura(inp)
    assert res.candidata_a_cierre is False


def test_primera_compra_nc_es_precio_del_producto_promo() -> None:
    # When gift product IS in the order line with 0% discount → NC = quantity * price
    orden = b.orden(primera=True, lista="BCV")
    linea_compra = b.linea(linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial", precio="100")
    linea_regalo = b.linea(linea_id="L2", producto="LIGA", marca="Sinoco", categoria="Comercial",
                           precio="12.50", cantidad="1", descuento="0")  # gift added but no discount applied (error)
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    inp = _inputs(
        orden=orden,
        lineas=[linea_compra, linea_regalo],
        abonos=[(vinc, metodo)],
        # Gift promotion: buy 1 Comercial unit, get 1 LIGA (solo_uno)
        promociones=[b.promo_primera("LIGA", compra_minima="1", valor="1")],
        resolver=_resolver(**{"P1@BCV": "100", "LIGA@BCV": "12.50"}),
    )
    res = calcular_factura(inp)
    # NC should equal min(1, 1) * 12.50 = 12.50 because LIGA is in the order but no 99.9% discount
    assert res.ncs_calculadas == Decimal("12.50")
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "primera_compra" in origenes


def test_primera_compra_regalo_ya_descontado_no_genera_nc() -> None:
    # When gift line has 99.9% discount already → NC = 0 (correctly facturado)
    orden = b.orden(primera=True, lista="BCV")
    linea_compra = b.linea(linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial", precio="100")
    linea_regalo = b.linea(linea_id="L2", producto="LIGA", marca="Sinoco", categoria="Comercial",
                           precio="12.50", cantidad="1", descuento="99.99")
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    inp = _inputs(
        orden=orden,
        lineas=[linea_compra, linea_regalo],
        abonos=[(vinc, metodo)],
        promociones=[b.promo_primera("LIGA", compra_minima="1", valor="1")],
        resolver=_resolver(**{"P1@BCV": "100", "LIGA@BCV": "12.50"}),
    )
    res = calcular_factura(inp)
    assert res.ncs_calculadas == Decimal("0.00")


def test_primera_compra_regalo_fuera_de_orden_no_genera_nc() -> None:
    # When gift product is NOT in order lines → NC = 0 (assume inventory adjustment)
    orden = b.orden(primera=True, lista="BCV")
    linea_compra = b.linea(linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial", precio="100")
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    inp = _inputs(
        orden=orden,
        lineas=[linea_compra],  # LIGA is NOT in the order
        abonos=[(vinc, metodo)],
        promociones=[b.promo_primera("LIGA", compra_minima="1", valor="1")],
        resolver=_resolver(**{"P1@BCV": "100", "LIGA@BCV": "12.50"}),
    )
    res = calcular_factura(inp)
    assert res.ncs_calculadas == Decimal("0.00")


def test_primera_compra_porcentaje_aplica_2pct() -> None:
    # Percentage-based first purchase promotion (2%)
    orden = b.orden(primera=True, lista="BCV")
    linea = b.linea(linea_id="L1", producto="P1", marca="Sinoco", categoria="Industrial", precio="100")
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        promociones=[b.promo_primera(tipo_beneficio="porcentaje", valor="0.02", compra_minima="0")],
        resolver=_resolver(**{"P1@BCV": "100"}),
    )
    res = calcular_factura(inp)
    # 2% of 100 = 2.00 NC
    assert res.ncs_calculadas == Decimal("2.00")
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "primera_compra" in origenes


def test_primera_compra_solo_unidades_comerciales_califican() -> None:
    # Threshold check: only Comercial units count toward promo qualification
    orden = b.orden(primera=True, lista="BCV")
    # 2 Comercial + 1 Industrial → only 2 count. Promo requires 3 → should NOT qualify for product
    # But 2% porcentaje with compra_minima=0 should still apply
    linea_com = b.linea(linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial", precio="50", cantidad="2")
    linea_ind = b.linea(linea_id="L2", producto="P2", marca="Sinoco", categoria="Industrial", precio="50", cantidad="1")
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    inp = _inputs(
        orden=orden,
        lineas=[linea_com, linea_ind],
        abonos=[(vinc, metodo)],
        # requires 3 Comercial units to get product gift, but only 2 → doesn't qualify for product gift
        promociones=[b.promo_primera("LIGA", compra_minima="3", valor="1")],
        resolver=_resolver(**{"P1@BCV": "50", "P2@BCV": "50"}),
    )
    res = calcular_factura(inp)
    # Falls back to 2% since 2 < 3 Comercial units threshold. 2% of 150 = 3.00
    assert res.ncs_calculadas == Decimal("3.00")


def test_primera_compra_sin_promo_vigente_no_da_nc() -> None:
    orden = b.orden(primera=True, lista="BCV")
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(monto_aplicado="3600", moneda_abono=Moneda.VES,
                         tipo_tasa_abono=TipoTasa.BCV)
    inp = _inputs(
        orden=orden, lineas=[linea], abonos=[(vinc, metodo)],
        resolver=_resolver(**{"P1@BCV": "100"}),  # sin promociones
    )
    res = calcular_factura(inp)
    assert res.ncs_calculadas == Decimal("0.00")


def test_primera_compra_industrial_sin_promos_aplica_2pct() -> None:
    # First purchase with Industrial products and no active promo should get 2% discount on Industrial lines
    orden = b.orden(primera=True, lista="BCV")
    linea_ind = b.linea(linea_id="L1", producto="P1", marca="Sinoco", categoria="Industrial", precio="150", cantidad="1")
    linea_com = b.linea(linea_id="L2", producto="P2", marca="Sinoco", categoria="Comercial", precio="100", cantidad="1")
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    inp = _inputs(
        orden=orden,
        lineas=[linea_ind, linea_com],
        abonos=[(vinc, metodo)],
        resolver=_resolver(**{"P1@BCV": "150", "P2@BCV": "100"}),  # no promos configured
    )
    res = calcular_factura(inp)
    # Should get 2% of Industrial line (150) = 3.00 NC (nothing on Comercial line)
    assert res.ncs_calculadas == Decimal("3.00")
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "primera_compra" in origenes


def test_orden_con_devolucion_requiere_revision() -> None:
    orden = b.orden(primera=False)
    orden.tiene_devolucion = True
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(monto_aplicado="94", moneda_abono=Moneda.USD)
    inp = _inputs(
        orden=orden, lineas=[linea], abonos=[(vinc, metodo)],
        reglas=[b.regla_recompra("0.03")],
        resolver=_resolver(**{"P1@USD": "100"}),
    )
    res = calcular_factura(inp)
    assert res.requiere_revision is True


def test_devolucion_factura_sobre_cantidad_entregada() -> None:
    # Entregada completa con devolución: pidió 20, quedaron 15 → factura 15.
    orden = b.orden(primera=False)
    orden.entregada_completa = True
    orden.tiene_devolucion = True
    linea = b.linea(marca="Sinoco", categoria="*", precio="10", cantidad="20")
    linea.cantidad_entregada = Decimal("15")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(monto_aplicado="100", moneda_abono=Moneda.USD)
    inp = _inputs(
        orden=orden, lineas=[linea], abonos=[(vinc, metodo)],
        resolver=_resolver(**{"P1@USD": "10"}),
    )
    res = calcular_factura(inp)
    assert res.precio_base_calculado == Decimal("150.00")  # 15 × 10, no 20 × 10


def test_sin_devolucion_factura_sobre_cantidad_pedida() -> None:
    # Sin devolución se usa la cantidad pedida aunque entregada_completa.
    orden = b.orden(primera=False)
    orden.entregada_completa = True
    orden.tiene_devolucion = False
    linea = b.linea(marca="Sinoco", categoria="*", precio="10", cantidad="20")
    linea.cantidad_entregada = Decimal("20")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(monto_aplicado="100", moneda_abono=Moneda.USD)
    inp = _inputs(
        orden=orden, lineas=[linea], abonos=[(vinc, metodo)],
        resolver=_resolver(**{"P1@USD": "10"}),
    )
    res = calcular_factura(inp)
    assert res.precio_base_calculado == Decimal("200.00")  # 20 × 10


def test_dia_habil_con_feriado_mantiene_contado_dentro_de_ventana() -> None:
    # Feriado lunes 8-jun extiende la ventana al jueves 11; abono el 11 entra.
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="94", moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 11, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[b.descuento(marca="Sinoco", categoria="*", porcentaje="0.03")],
        reglas=[b.regla_recompra("0.03")],
        feriados=[b.feriado(date(2026, 6, 8))],
        resolver=_resolver(**{"P1@USD": "100"}),
        fecha_calculo=date(2026, 6, 11),
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "contado" in origenes
    assert res.total_descuentos == Decimal("6.00")


def test_descuento_por_volumen_litros() -> None:
    # Set up order lines
    orden = b.orden(primera=False)
    linea1 = b.linea(producto="P1", marca="Sinoco", categoria="Comercial", cantidad="10", precio="100")
    
    # Resolver returning price 100 and volume 25 L for P1
    resolver = _resolver(**{"P1@USD": "100", "P1@BCV": "100"})
    resolver.set_volumen("P1", Decimal("25.0")) # 10 * 25 L = 250 L
    
    # 250 L should trigger a volume discount rule for brand="Sinoco", category="Comercial", liters_min=200, pct=0.05
    from cxc.models import DescuentoVolumen
    regla_vol = DescuentoVolumen(
        regla_id="VOL1",
        marca="Sinoco",
        categoria="Comercial",
        litros_minimo=Decimal("200"),
        porcentaje=Decimal("0.05"),
        activo=True
    )
    
    inp = _inputs(
        orden=orden,
        lineas=[linea1],
        abonos=[],
        descuentos_volumen=[regla_vol],
        resolver=resolver,
    )
    res = calcular_factura(inp)
    
    # Base price: 10 * 100 = 1000 USD
    # Volume discount: 1000 * 0.05 = 50 USD
    # Total motor should be 950 USD
    assert res.total_descuentos == Decimal("50.00")
    assert res.total_motor == Decimal("950.00")


def test_recompra_aplica_solo_primera_compra_del_mes() -> None:
    # First purchase in month should get recompra, subsequent one should not
    orden1 = b.orden(primera=False, fecha=date(2026, 6, 5))
    orden1.so_id = "SO_FIRST"
    orden1.cliente_id = "C1"
    
    orden2 = b.orden(primera=False, fecha=date(2026, 6, 15))
    orden2.so_id = "SO_SECOND"
    orden2.cliente_id = "C1"
    
    linea = b.linea(marca="Sinoco", categoria="Comercial", precio="100")
    regla = b.regla_recompra("0.05")
    
    # 1. Calculation for first order (should apply 5% recompra)
    inp1 = _inputs(
        orden=orden1,
        lineas=[linea],
        abonos=[],
        reglas=[regla],
        all_ordenes=[orden1, orden2],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "100"}),
    )
    res1 = calcular_factura(inp1)
    recompra_d1 = [d for d in res1.descuentos_detalle if d.origen == "recurrencia"]
    assert len(recompra_d1) == 1
    assert recompra_d1[0].monto == Decimal("5.00")
    
    # 2. Calculation for second order (should NOT apply recompra since it's the second)
    inp2 = _inputs(
        orden=orden2,
        lineas=[linea],
        abonos=[],
        reglas=[regla],
        all_ordenes=[orden1, orden2],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "100"}),
    )
    res2 = calcular_factura(inp2)
    recompra_d2 = [d for d in res2.descuentos_detalle if d.origen == "recurrencia"]
    assert len(recompra_d2) == 0


def test_exclusion_mutua_volumen_vs_recompra() -> None:
    """Con exclusión activa entre volumen y recompra, se aplica el de mayor valor."""
    from cxc.models import ExclusionRegla
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="Comercial", precio="100", cantidad="1")
    desc_vol = b.descuento_volumen(marca="Sinoco", categoria="Comercial", litros_minimo="0", porcentaje="0.10")
    regla_rec = b.regla_recompra("0.03")  # 3% recurrencia < 10% volumen → volumen gana
    excl = ExclusionRegla(regla_tipo_a="volumen", regla_tipo_b="recurrencia", activo=True)
    inp = EngineInputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        descuentos=[],
        descuentos_volumen=[desc_vol],
        reglas_recurrencia=[regla_rec],
        descuento_bcv_diario=[],
        promociones_primera_compra=[],
        feriados_tabla=[],
        price_resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "100"}),
        engine_config=CFG,
        fecha_calculo=date(2026, 6, 8),
        all_ordenes=[orden],
        exclusiones=[excl],
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    # volumen (10%) > recompra (3%) → volumen should be applied, recompra excluded
    assert "volumen" in origenes
    assert "recurrencia" not in origenes
    assert res.total_descuentos == Decimal("10.00")  # 10% of 100


def test_primera_compra_regalo_conjunto() -> None:
    """Modo conjunto: NC = suma de todos los productos del catálogo que no tienen 99.9% descuento."""
    orden = b.orden(primera=True, lista="BCV")
    linea_com = b.linea(linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial", precio="100", cantidad="1")
    linea_liga = b.linea(linea_id="L2", producto="LIGA", marca="Sinoco", categoria="Comercial",
                         precio="5", cantidad="1", descuento="0")
    linea_oct = b.linea(linea_id="L3", producto="OCT", marca="Sinoco", categoria="Comercial",
                        precio="8", cantidad="1", descuento="0")
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    inp = _inputs(
        orden=orden,
        lineas=[linea_com, linea_liga, linea_oct],
        abonos=[(vinc, metodo)],
        promociones=[b.promo_primera("LIGA,OCT", compra_minima="1", valor="1", regalo_tipo="conjunto")],
        resolver=_resolver(**{"P1@BCV": "100", "LIGA@BCV": "5", "OCT@BCV": "8"}),
    )
    res = calcular_factura(inp)
    # Both LIGA (5) and OCT (8) have 0% discount → NC = 5 + 8 = 13
    assert res.ncs_calculadas == Decimal("13.00")

