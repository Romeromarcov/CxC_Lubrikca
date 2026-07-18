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
    # NC = precio del producto-promo en la lista de nacimiento (BCV) de la orden.
    orden = b.orden(primera=True, lista="BCV")
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(monto_aplicado="3600", moneda_abono=Moneda.VES,
                         tipo_tasa_abono=TipoTasa.BCV)
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        promociones=[b.promo_primera("LIGA")],
        # Precio de la línea (P1@BCV) + precio del producto-promo (LIGA@BCV).
        resolver=_resolver(**{"P1@BCV": "100", "LIGA@BCV": "12.50"}),
    )
    res = calcular_factura(inp)
    assert res.lista_aplicada == "BCV"
    assert res.ncs_calculadas == Decimal("12.50")
    assert res.total_motor == Decimal("87.50")  # 100 - 0 desc - 12.50 NC
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "primera_compra" in origenes


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
