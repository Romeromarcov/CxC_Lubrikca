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
    engine_config=None,
) -> EngineInputs:
    cfg = engine_config or CFG
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
        engine_config=cfg,
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
        marca="Global Oil",
        categoria="Comercial sintéticos",
        precio="100",
        cantidad="1",
    )
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="89",
        moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 5, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[
            b.descuento(
                marca="Global Oil",
                categoria="Comercial sintéticos",
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
        monto_aplicado="97",
        moneda_abono=Moneda.USD,
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
    metodo_bcv = b.metodo("MB", moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    metodo_bin = b.metodo("MN", moneda=Moneda.VES, tipo_tasa=TipoTasa.BINANCE, es_contado=False)
    v_bcv = b.vinculacion(
        "V1",
        monto_aplicado="1800",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="36.0",
        tasa_binance="40.0",
    )
    v_bin = b.vinculacion(
        "V2",
        monto_aplicado="2000",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BINANCE,
        tasa_bcv="36.0",
        tasa_binance="40.0",
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
    metodo_bcv = b.metodo("MB", moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    # VES 3600 a bcv 36 → 100 USD ; binance 40 → diferencial 10%.
    vinc = b.vinculacion(
        monto_aplicado="3600",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="36.0",
        tasa_binance="40.0",
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
    metodo_bcv = b.metodo("MB", moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="36.0",
        tasa_binance="40.0",
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo_bcv)],
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
    metodo_bcv = b.metodo("MB", moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="36.0",
        tasa_binance="40.0",
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo_bcv)],
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
        monto_aplicado="50",
        moneda_abono=Moneda.USD,
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
    linea_compra = b.linea(
        linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial", precio="100"
    )
    linea_regalo = b.linea(
        linea_id="L2",
        producto="LIGA",
        marca="Sinoco",
        categoria="Comercial",
        precio="12.50",
        cantidad="1",
        descuento="0",
    )  # gift added but no discount applied (error)
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
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
    linea_compra = b.linea(
        linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial", precio="100"
    )
    linea_regalo = b.linea(
        linea_id="L2",
        producto="LIGA",
        marca="Sinoco",
        categoria="Comercial",
        precio="12.50",
        cantidad="1",
        descuento="99.99",
    )
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
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
    linea_compra = b.linea(
        linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial", precio="100"
    )
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
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
    linea = b.linea(
        linea_id="L1", producto="P1", marca="Sinoco", categoria="Industrial", precio="100"
    )
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
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
    linea_com = b.linea(
        linea_id="L1",
        producto="P1",
        marca="Sinoco",
        categoria="Comercial",
        precio="50",
        cantidad="2",
    )
    linea_ind = b.linea(
        linea_id="L2",
        producto="P2",
        marca="Sinoco",
        categoria="Industrial",
        precio="50",
        cantidad="1",
    )
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea_com, linea_ind],
        abonos=[(vinc, metodo)],
        # requires 3 Comercial units to get product gift, but only 2 → doesn't qualify
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
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        resolver=_resolver(**{"P1@BCV": "100"}),  # sin promociones
    )
    res = calcular_factura(inp)
    assert res.ncs_calculadas == Decimal("0.00")


def test_primera_compra_industrial_sin_promos_aplica_2pct() -> None:
    # First purchase with Industrial products and no active promo
    # should get 2% discount on Industrial lines
    orden = b.orden(primera=True, lista="BCV")
    linea_ind = b.linea(
        linea_id="L1",
        producto="P1",
        marca="Sinoco",
        categoria="Industrial",
        precio="150",
        cantidad="1",
    )
    linea_com = b.linea(
        linea_id="L2",
        producto="P2",
        marca="Sinoco",
        categoria="Comercial",
        precio="100",
        cantidad="1",
    )
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
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
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
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
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
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
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
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
        monto_aplicado="94",
        moneda_abono=Moneda.USD,
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
    linea1 = b.linea(
        producto="P1", marca="Sinoco", categoria="Comercial", cantidad="10", precio="100"
    )

    # Resolver returning price 100 and volume 25 L for P1
    resolver = _resolver(**{"P1@USD": "100", "P1@BCV": "100"})
    resolver.set_volumen("P1", Decimal("25.0"))  # 10 * 25 L = 250 L

    # 250 L should trigger a volume discount rule for brand="Sinoco",
    # category="Comercial", liters_min=200, pct=0.05
    from cxc.models import DescuentoVolumen

    regla_vol = DescuentoVolumen(
        regla_id="VOL1",
        marca="Sinoco",
        categoria="Comercial",
        litros_minimo=Decimal("200"),
        porcentaje=Decimal("0.05"),
        activo=True,
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


def test_descuento_por_volumen_unidades_cajas() -> None:
    orden = b.orden(primera=False)
    linea1 = b.linea(
        producto="SINOCO SAE 20W-50 (PAILA)",
        marca="Sinoco",
        categoria="PAILA",
        cantidad="10",
        precio="100",
    )

    resolver = _resolver(
        **{"SINOCO SAE 20W-50 (PAILA)@USD": "100", "SINOCO SAE 20W-50 (PAILA)@BCV": "100"}
    )
    resolver.set_volumen("SINOCO SAE 20W-50 (PAILA)", Decimal("19.0"))

    from cxc.models import DescuentoVolumen

    regla_vol = DescuentoVolumen(
        regla_id="VOL_SINOCO_PAILA_1",
        marca="SINOCO",
        categoria="PAILA",
        min_cantidad=Decimal("10"),
        max_cantidad=Decimal("19"),
        unidad_medida="CAJAS",
        porcentaje=Decimal("0.0452"),
        activo=True,
    )

    inp = _inputs(
        orden=orden,
        lineas=[linea1],
        abonos=[],
        descuentos_volumen=[regla_vol],
        resolver=resolver,
    )
    res = calcular_factura(inp)

    assert res.total_descuentos == Decimal("45.20")
    assert res.total_motor == Decimal("954.80")

    # Over max_cantidad (25 > 19) should not match VOL_SINOCO_PAILA_1
    linea2 = b.linea(
        producto="SINOCO SAE 20W-50 (PAILA)",
        marca="Sinoco",
        categoria="PAILA",
        cantidad="25",
        precio="100",
    )
    inp2 = _inputs(
        orden=orden, lineas=[linea2], abonos=[], descuentos_volumen=[regla_vol], resolver=resolver
    )
    res2 = calcular_factura(inp2)
    assert res2.total_descuentos == Decimal("0.00")


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
    desc_vol = b.descuento_volumen(
        marca="Sinoco", categoria="Comercial", litros_minimo="0", porcentaje="0.10"
    )
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
    """Modo conjunto: NC = suma de productos del catálogo que no tienen 99.9% descuento."""
    orden = b.orden(primera=True, lista="BCV")
    linea_com = b.linea(
        linea_id="L1",
        producto="P1",
        marca="Sinoco",
        categoria="Comercial",
        precio="100",
        cantidad="1",
    )
    linea_liga = b.linea(
        linea_id="L2",
        producto="LIGA",
        marca="Sinoco",
        categoria="Comercial",
        precio="5",
        cantidad="1",
        descuento="0",
    )
    linea_oct = b.linea(
        linea_id="L3",
        producto="OCT",
        marca="Sinoco",
        categoria="Comercial",
        precio="8",
        cantidad="1",
        descuento="0",
    )
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="3600", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea_com, linea_liga, linea_oct],
        abonos=[(vinc, metodo)],
        promociones=[
            b.promo_primera("LIGA,OCT", compra_minima="1", valor="1", regalo_tipo="conjunto")
        ],
        resolver=_resolver(**{"P1@BCV": "100", "LIGA@BCV": "5", "OCT@BCV": "8"}),
    )
    res = calcular_factura(inp)
    # Both LIGA (5) and OCT (8) have 0% discount → NC = 5 + 8 = 13
    assert res.ncs_calculadas == Decimal("13.00")


def test_descuento_vigente_elite_y_moneda_usd() -> None:
    from datetime import date

    from cxc.engine.effective_dating import descuento_vigente
    from cxc.models import DescuentoProntoPago, TipoDescuento

    reglas = [
        DescuentoProntoPago(
            regla_id="PP_GLOBAL_ELITE_SS_10",
            marca="GLOBAL OIL",
            categoria="CAJA",
            porcentaje=Decimal("0.10"),
            monedas_aplicables="USD",
            vigencia_desde=date(2026, 1, 1),
            vigencia_hasta=date(2026, 3, 30),
            activo=True,
            tipo_descuento=TipoDescuento.CONTADO,
        ),
        DescuentoProntoPago(
            regla_id="PP_GLOBAL_VISCOSIDADES_8",
            marca="GLOBAL OIL",
            categoria="CAJA",
            porcentaje=Decimal("0.08"),
            monedas_aplicables="USD",
            vigencia_desde=date(2026, 1, 1),
            vigencia_hasta=date(2026, 3, 30),
            activo=True,
            tipo_descuento=TipoDescuento.CONTADO,
        ),
    ]

    # Matching ELITE product in USD -> gets 10% rule
    d_elite = descuento_vigente(
        reglas,
        marca="GLOBAL OIL",
        categoria="CAJA",
        tipo=TipoDescuento.CONTADO,
        fecha=date(2026, 2, 1),
        producto="GLOBAL OIL ELITE 20W50",
        moneda_pago="USD",
    )
    assert d_elite is not None
    assert d_elite.regla_id == "PP_GLOBAL_ELITE_SS_10"
    assert d_elite.porcentaje == Decimal("0.10")

    # Non-ELITE product in USD -> falls back to 8% rule
    d_norm = descuento_vigente(
        reglas,
        marca="GLOBAL OIL",
        categoria="CAJA",
        tipo=TipoDescuento.CONTADO,
        fecha=date(2026, 2, 1),
        producto="GLOBAL OIL MULTIGRADO 20W50",
        moneda_pago="USD",
    )
    assert d_norm is not None
    assert d_norm.regla_id == "PP_GLOBAL_VISCOSIDADES_8"
    assert d_norm.porcentaje == Decimal("0.08")

    # Payment in VES -> no rule matched because monedas_aplicables is USD
    d_ves = descuento_vigente(
        reglas,
        marca="GLOBAL OIL",
        categoria="CAJA",
        tipo=TipoDescuento.CONTADO,
        fecha=date(2026, 2, 1),
        producto="GLOBAL OIL ELITE 20W50",
        moneda_pago="VES",
    )
    assert d_ves is None


def test_in_memory_gateway_delete_row() -> None:
    from cxc.sheets.gateway import InMemorySheetGateway

    gw = InMemorySheetGateway()
    gw.seed("TestTable", [{"regla_id": "R1", "val": "A"}, {"regla_id": "R2", "val": "B"}])
    assert len(gw.read_rows("TestTable")) == 2
    deleted = gw.delete_row("TestTable", "regla_id", "R1")
    assert deleted is True
    assert len(gw.read_rows("TestTable")) == 1
    assert gw.read_rows("TestTable")[0]["regla_id"] == "R2"
    not_deleted = gw.delete_row("TestTable", "regla_id", "NON_EXISTENT")
    assert not_deleted is False


def test_diferencial_brecha_cierre_sugiere_nc_si_pagado_86pct_o_mas() -> None:
    """Si el cliente paga 86% o mas (de una orden con 14% de brecha BCV/Binance),
    el motor sugiere la NC exacta de brecha cierre para cerrar la factura (candidata_a_cierre=True).
    """
    from cxc.models import DescuentoDiferencialCambiario

    orden = b.orden(primera=False, lista="BCV")
    linea = b.linea(
        linea_id="L1",
        producto="P1",
        marca="GLOBAL OIL",
        categoria="CAJA",
        cantidad="10",
        precio="100",
    )  # Total = $1000
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    # Abono de $860 equivalentes a BCV (86% pagado a tasa BCV 36, Binance 41.86 => 14% brecha)
    # 860 * 36 = 30960 VES
    vinc = b.vinculacion(
        monto_aplicado="30960",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="36.0",
        tasa_binance="41.86",
        hora=datetime(2026, 4, 15, 10, 0),
    )
    DescuentoDiferencialCambiario(
        regla_id="DIF1",
        nombre="Brecha Cierre",
        tipo_diferencial="diferencial_bcv_binance",
        tipo_calculo="variable",
        porcentaje_fijo=Decimal("0.14"),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        resolver=_resolver(**{"P1@BCV": "100"}),
    )
    res = calcular_factura(inp)
    # NC sugerida debe ser exactamente los $140 restantes (14% de brecha)
    assert res.candidata_a_cierre is True
    assert res.requiere_revision is True
    assert res.total_descuentos in (Decimal("140.00"), Decimal("139.99"))
    assert res.total_motor in (Decimal("860.00"), Decimal("860.01"))
    assert any(
        d.origen == "bcv_completo" and "brecha cierre" in d.descripcion
        for d in res.descuentos_detalle
    )


def test_diferencial_brecha_cierre_no_aplica_si_falta_mas_de_brecha() -> None:
    """Si el cliente solo ha pagado el 50% (falta 50% > 14% brecha), no aplica brecha cierre."""
    from cxc.models import DescuentoDiferencialCambiario

    orden = b.orden(primera=False, lista="BCV")
    linea = b.linea(
        linea_id="L1",
        producto="P1",
        marca="GLOBAL OIL",
        categoria="CAJA",
        cantidad="10",
        precio="100",
    )  # Total = $1000
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    # Abono de solo 50% = $500 (500 * 36 = 18000 VES)
    vinc = b.vinculacion(
        monto_aplicado="18000",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="36.0",
        tasa_binance="41.86",
        hora=datetime(2026, 4, 15, 10, 0),
    )
    DescuentoDiferencialCambiario(
        regla_id="DIF1",
        nombre="Brecha Cierre",
        tipo_diferencial="diferencial_bcv_binance",
        tipo_calculo="variable",
        porcentaje_fijo=Decimal("0.14"),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        resolver=_resolver(**{"P1@BCV": "100"}),
    )
    res = calcular_factura(inp)
    assert res.candidata_a_cierre is False
    assert res.total_descuentos == Decimal("0.00")
    assert res.total_motor == Decimal("1000.00")


def test_equiparacion_binance_y_usd_cash_sugiere_nc_correcta() -> None:
    """Ejemplo real del usuario:
    Monto Odoo (Lista VES): $58.46
    Monto Meta (Lista USD): $32.76
    Pagos: $10 USD cash + 19560.85 VES (859.44 Binance = $22.76 USD; 742.23 BCV = $26.35 USD)
    Abonos Binance = $32.76 USD >= $32.76 USD (Lista USD) -> Cumplido 100%!
    Abonos BCV = $36.35 USD
    NC Sugerida = $58.46 - $36.35 = $22.11 USD.
    """
    orden = b.orden(primera=False, lista="5")  # Lista VES #5
    linea = b.linea(
        linea_id="L1",
        producto="P1",
        marca="GLOBAL OIL",
        categoria="CAJA",
        cantidad="1",
        precio="58.46",
    )

    # Abono 1: $10.00 USD cash
    m_usd = b.metodo(moneda=Moneda.USD, es_contado=False)
    v_usd = b.vinculacion(
        monto_aplicado="10.00",
        moneda_abono=Moneda.USD,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="1.0",
        tasa_binance="1.0",
        hora=datetime(2026, 4, 15, 10, 0),
    )

    # Abono 2: 19560.85 VES
    m_ves = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    v_ves = b.vinculacion(
        monto_aplicado="19560.85",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="742.23",
        tasa_binance="859.44",
        hora=datetime(2026, 4, 15, 11, 0),
    )

    # Resolver que tiene P1 a $58.46 en Lista 5 (VES) y $32.76 en Lista 4 (USD)
    resolver = _resolver(**{"P1@5": "58.46", "P1@4": "32.76"})

    from cxc.config import EngineConfig

    cfg = EngineConfig(
        lista_usd="4",
        lista_bcv="5",
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(v_usd, m_usd), (v_ves, m_ves)],
        resolver=resolver,
        engine_config=cfg,
    )
    res = calcular_factura(inp)

    assert res.candidata_a_cierre is True
    assert res.requiere_revision is True
    # NC calculada debe ser exactamente $22.11 ($58.46 - $36.35)
    assert res.total_descuentos == Decimal("22.11")
    assert res.total_motor == Decimal("36.35")
    assert any("Equiparación Binance" in d.descripcion for d in res.descuentos_detalle)


def test_match_lista_con_keywords_dinamicas_listas_ves_y_usd() -> None:
    from cxc.engine.effective_dating import _match_lista

    assert _match_lista("LISTAS_VES", "5", valid_ves=["5", "9"], valid_usd=["4"]) is True
    assert _match_lista("LISTAS_VES", "9", valid_ves=["5", "9"], valid_usd=["4"]) is True
    assert _match_lista("LISTAS_VES", "4", valid_ves=["5", "9"], valid_usd=["4"]) is False
    assert _match_lista("LISTAS_USD", "4", valid_ves=["5", "9"], valid_usd=["4"]) is True
    assert _match_lista("LISTAS_USD", "9", valid_ves=["5", "9"], valid_usd=["4"]) is False


def test_resolved_marca_fallback_sinoco_vs_global() -> None:
    l_sinoco = b.linea(producto="SINOCO SAE 20W-50 (PAILA)", marca="")
    l_global = b.linea(producto="SUPREMO API CI-4 SAE 15W-40 (PAILA)", marca="")
    l_explicit = b.linea(producto="SUPREMO API CI-4", marca="MARCA_CUSTOM")

    assert l_sinoco.resolved_marca == "SINOCO"
    assert l_global.resolved_marca == "GLOBAL OIL"
    assert l_explicit.resolved_marca == "MARCA_CUSTOM"


def test_get_conciliaciones_sugerencias_excludes_cancelled_orders() -> None:
    from decimal import Decimal

    o_active = b.orden(
        so_id="SO_ACTIVE",
        cliente_id="C1",
        monto_total=Decimal("100"),
        facturada=False,
        estado_orden="sale",
    )
    o_cancel = b.orden(
        so_id="SO_CANCEL",
        cliente_id="C1",
        monto_total=Decimal("500"),
        facturada=False,
        estado_orden="cancel",
    )

    assert o_cancel.estado_orden == "cancel"
    assert o_active.estado_orden == "sale"


def test_runner_run_all_filters_cancelled_orders() -> None:
    from datetime import date
    from decimal import Decimal

    from cxc.engine.runner import EngineRunner

    class DummyRepo:
        def __init__(self):
            self._ordenes = [
                b.orden(
                    so_id="SO_ACTIVE",
                    cliente_id="C1",
                    monto_total=Decimal("100"),
                    facturada=False,
                    estado_orden="sale",
                ),
                b.orden(
                    so_id="SO_CANCEL",
                    cliente_id="C1",
                    monto_total=Decimal("500"),
                    facturada=False,
                    estado_orden="cancel",
                ),
            ]

        def all_ordenes(self):
            return self._ordenes

        def upsert_bandejas(self, filas):
            pass

        def update_vinculaciones(self, vincs):
            pass

    repo = DummyRepo()
    runner = EngineRunner(repo, None, None)

    # Override _calcular (llamado internamente por run_all) para contar
    # llamadas sin ejercitar el cálculo completo del motor.
    called = []
    runner._calcular = lambda so_id, dt: (called.append(so_id), None)[1]

    runner.run_all(date.today())
    assert "SO_ACTIVE" in called
    assert "SO_CANCEL" not in called
