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
    promociones=(),
    feriados=(),
    resolver,
    fecha_calculo=date(2026, 6, 8),
    all_ordenes=None,
    engine_config=None,
    valid_usd=(),
    valid_ves=(),
    descuentos_recompra=(),
    orden_anterior=None,
    orden_anterior_vincs=(),
    descuentos_producto=(),
    historial_cliente_lineas=(),
    descuentos_diferencial=(),
    cliente_tiene_pagos_huerfanos=False,
) -> EngineInputs:
    cfg = engine_config or CFG
    return EngineInputs(
        orden=orden,
        lineas=list(lineas),
        abonos=list(abonos),
        descuentos=list(descuentos),
        descuentos_volumen=list(descuentos_volumen),
        reglas_recurrencia=list(reglas),
        promociones_primera_compra=list(promociones),
        feriados_tabla=list(feriados),
        price_resolver=resolver,
        engine_config=cfg,
        fecha_calculo=fecha_calculo,
        all_ordenes=list(all_ordenes) if all_ordenes is not None else [],
        valid_usd=list(valid_usd),
        valid_ves=list(valid_ves),
        descuentos_recompra=list(descuentos_recompra),
        orden_anterior_cliente=orden_anterior,
        orden_anterior_cliente_vincs=list(orden_anterior_vincs),
        descuentos_producto=list(descuentos_producto),
        historial_cliente_lineas=list(historial_cliente_lineas),
        descuentos_diferencial=list(descuentos_diferencial),
        cliente_tiene_pagos_huerfanos=cliente_tiene_pagos_huerfanos,
    )


def _orden_anterior_pagada(
    *, so_id: str = "SO0", fecha: date = date(2026, 5, 1), dias_credito: int = 30
) -> tuple:
    """Orden anterior del cliente, totalmente pagada -- fixture compartida

    por los tests de Recompra (ventana = dias_credito + dias_gracia desde
    esta orden)."""
    orden_ant = b.orden(so_id, fecha=fecha, monto_total="500", dias_credito=dias_credito)
    vinc_ant = b.vinculacion(
        "V_ANT",
        so_id=so_id,
        monto_aplicado="500",
        moneda_abono=Moneda.USD,
        hora=datetime.combine(fecha, datetime.min.time()),
    )
    return orden_ant, [vinc_ant]


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
    orden_ant, vincs_ant = _orden_anterior_pagada()
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[b.descuento(marca="Sinoco", categoria="*", porcentaje="0.03")],
        descuentos_recompra=[b.descuento_recompra("REC1", marca="*", categoria="*")],
        resolver=_resolver(**{"P1@USD": "100"}),
        orden_anterior=orden_ant,
        orden_anterior_vincs=vincs_ant,
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
    orden_ant, vincs_ant = _orden_anterior_pagada()
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
        descuentos_recompra=[b.descuento_recompra("REC1", marca="*", categoria="*")],
        resolver=_resolver(**{"P1@USD": "100"}),
        orden_anterior=orden_ant,
        orden_anterior_vincs=vincs_ant,
    )
    res = calcular_factura(inp)
    # 3% + 8% = 11%
    assert res.total_descuentos == Decimal("11.00")
    assert res.total_motor == Decimal("89.00")
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "recurrencia" in origenes


def test_recompra_no_aplica_sin_orden_anterior() -> None:
    """Sin orden anterior del cliente (primer pedido), Recompra no puede

    evaluarse -- no hay pago previo que verificar ni ventana que contar."""
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="97",
        moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 5, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos_recompra=[b.descuento_recompra("REC1", marca="*", categoria="*")],
        resolver=_resolver(**{"P1@USD": "100"}),
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "recurrencia" not in origenes


def test_recompra_no_aplica_si_orden_anterior_no_pagada_completo() -> None:
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="97",
        moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 5, 10, 0),
    )
    orden_ant = b.orden("SO0", fecha=date(2026, 5, 1), monto_total="500", dias_credito=30)
    # Solo pagó 400 de 500 -- NO está totalmente pagada.
    vinc_ant = b.vinculacion(
        "V_ANT",
        so_id="SO0",
        monto_aplicado="400",
        moneda_abono=Moneda.USD,
        hora=datetime(2026, 5, 1, 0, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos_recompra=[b.descuento_recompra("REC1", marca="*", categoria="*")],
        resolver=_resolver(**{"P1@USD": "100"}),
        orden_anterior=orden_ant,
        orden_anterior_vincs=[vinc_ant],
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "recurrencia" not in origenes


def test_recompra_no_aplica_fuera_de_la_ventana_credito_mas_gracia() -> None:
    """Orden anterior pagada, pero esta orden llega DESPUÉS de

    (dias_credito + dias_gracia) desde esa orden anterior -- fuera de
    ventana, no aplica."""
    # orden_anterior: fecha 2026-01-01, 15 dias credito -- ventana real
    # (con dias_gracia=3 de la regla) = hasta el 2026-01-19. Esta orden
    # llega el 2026-06-01, muy fuera de ventana.
    orden = b.orden(primera=False, fecha=date(2026, 6, 1))
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="97",
        moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 5, 10, 0),
    )
    orden_ant, vincs_ant = _orden_anterior_pagada(
        fecha=date(2026, 1, 1), dias_credito=15
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos_recompra=[b.descuento_recompra("REC1", marca="*", categoria="*", dias_gracia=3)],
        resolver=_resolver(**{"P1@USD": "100"}),
        orden_anterior=orden_ant,
        orden_anterior_vincs=vincs_ant,
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "recurrencia" not in origenes


def test_recompra_aplica_justo_en_el_limite_de_la_ventana() -> None:
    """dias_credito + dias_gracia = 33; la orden nueva llega EXACTAMENTE

    33 dias despues de la orden anterior -- debe aplicar (limite inclusive)."""
    orden_ant, vincs_ant = _orden_anterior_pagada(
        fecha=date(2026, 5, 1), dias_credito=30
    )
    orden = b.orden(primera=False, fecha=date(2026, 6, 3))  # 33 dias despues
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        descuentos_recompra=[b.descuento_recompra("REC1", marca="*", categoria="*", dias_gracia=3)],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "100"}),
        orden_anterior=orden_ant,
        orden_anterior_vincs=vincs_ant,
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "recurrencia" in origenes


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
    orden_ant, vincs_ant = _orden_anterior_pagada()
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[b.descuento(marca="Sinoco", categoria="*", porcentaje="0.03")],
        descuentos_recompra=[b.descuento_recompra("REC1", marca="*", categoria="*")],
        resolver=_resolver(**{"P1@USD": "100"}),
        fecha_calculo=date(2026, 6, 16),
        orden_anterior=orden_ant,
        orden_anterior_vincs=vincs_ant,
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


def test_diferencial_regla1_fijo_pago_100pct_usd() -> None:
    """Regla 1 (fijo, agosto 2026): orden nacida VES, TODOS los abonos en

    USD puro (moneda_abono=USD), orden pagada 100% segun el teorico USD ->
    descuento fijo = porcentaje_fijo de la regla vigente tipo_diferencial=
    'fijo_35_ves_usd', aplicado sobre el precio_base de la lista VES."""
    from cxc.models import DescuentoDiferencialCambiario

    orden = b.orden(primera=False, lista="BCV")
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo_usd = b.metodo("MU", moneda=Moneda.USD, es_contado=False)
    # moneda_abono=USD (pagó en dólares en efectivo) pero tipo_tasa_abono=BCV
    # (para efectos de conciliación/cierre de la factura VES formal se
    # clasifica por la ruta BCV) -- misma "regla de mezcla" que ya usa el
    # resto del motor (ver equivalents.py), necesaria para que la pasada
    # REAL (no solo el teórico VES informativo) resuelva sobre la lista VES.
    vinc = b.vinculacion(
        monto_aplicado="100",
        moneda_abono=Moneda.USD,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="36.0",
        tasa_binance="40.0",
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo_usd)],
        resolver=_resolver(**{"P1@BCV": "100", "P1@USD": "100"}),
        valid_ves=["BCV"],
        valid_usd=["USD"],
        descuentos_diferencial=[
            DescuentoDiferencialCambiario(
                regla_id="DIF_MAX",
                nombre="Diferencial máximo",
                tipo_diferencial="fijo_35_ves_usd",
                porcentaje_fijo=Decimal("0.35"),
            )
        ],
    )
    res = calcular_factura(inp)
    bcv = [d for d in res.descuentos_detalle if d.origen == "bcv_completo"]
    assert len(bcv) == 1
    assert bcv[0].monto == Decimal("35.00")  # 35% de 100
    assert res.requiere_revision is True


def test_diferencial_regla1_no_aplica_con_pago_mixto_sin_regla_equiparar() -> None:
    """Pago mixto (parte VES) no califica para la regla 1 (exige 100% USD),

    y sin la regla 'equiparar_binance' habilitada tampoco cae en regla 2 ->
    no se otorga nada."""
    from cxc.models import DescuentoDiferencialCambiario

    orden = b.orden(primera=False, lista="BCV")
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo_ves = b.metodo("MV", moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
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
        abonos=[(vinc, metodo_ves)],
        resolver=_resolver(**{"P1@BCV": "100"}),
        valid_ves=["BCV"],
        descuentos_diferencial=[
            DescuentoDiferencialCambiario(
                regla_id="DIF_MAX",
                nombre="Diferencial máximo",
                tipo_diferencial="fijo_35_ves_usd",
                porcentaje_fijo=Decimal("0.35"),
            )
        ],  # sin fila 'equiparar_binance' -> regla 2 deshabilitada
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "bcv_completo" not in origenes


def test_diferencial_sin_regla_max_configurada_no_se_otorga() -> None:
    # Sin ninguna fila 'fijo_35_ves_usd' vigente -> no hay tope, no se
    # otorga nada (conservador), aunque el pago sea 100% USD.
    orden = b.orden(primera=False, lista="BCV")
    linea = b.linea(marca="Sinoco", categoria="*", precio="100")
    metodo_usd = b.metodo("MU", moneda=Moneda.USD, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="100", moneda_abono=Moneda.USD, tipo_tasa_abono=TipoTasa.BCV
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo_usd)],
        resolver=_resolver(**{"P1@BCV": "100", "P1@USD": "100"}),
        valid_ves=["BCV"],
        valid_usd=["USD"],
        # sin descuentos_diferencial
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
    linea = b.linea(marca="Sinoco", categoria="*", precio="10", cantidad="20", subtotal="150")
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


def test_linea_con_subtotal_cero_pero_totalmente_entregada_no_se_zanea() -> None:
    """Bug real corregido (SO 00133, "Inversiones La Bendición del

    Nazareno", agosto 2026): un intento anterior de ``_cantidad_efectiva``
    trataba ``linea.subtotal == 0`` como señal de que Odoo había reflejado
    la devolución en precio $0 -- pero ``subtotal`` (``price_subtotal``)
    está en 0 para el 81% de TODAS las líneas del sistema (hueco de
    backfill del sync sin relación con devoluciones), y en vivo Odoo
    mostraba ``price_subtotal`` real y ``qty_delivered == product_uom_qty``
    para esas mismas líneas de S00133 -- un falso positivo confirmado por
    el usuario (línea 100% entregada, no devuelta, con precio real en
    Odoo). ``cantidad_entregada`` (que Odoo SÍ neta correctamente cuando la
    devolución está aplicada) es la única señal -- ``subtotal`` nunca debe
    zanear una línea a $0.
    """
    orden = b.orden(primera=False)
    orden.entregada_completa = True
    orden.tiene_devolucion = True
    linea = b.linea(marca="Sinoco", categoria="*", precio="10", cantidad="18", subtotal="0")
    linea.cantidad_entregada = Decimal("18")  # entregada completa, sin faltante real
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(monto_aplicado="180", moneda_abono=Moneda.USD)
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        resolver=_resolver(**{"P1@USD": "10"}),
    )
    res = calcular_factura(inp)
    assert res.precio_base_calculado == Decimal("180.00")  # 18 × 10, no $0


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
    # Antes (agosto 2026) este total incluía $3 de "brecha cierre" que se
    # auto-aplicaba SOLO por el spread entre las tasas default del builder
    # de tests (36/40), sin ninguna regla de Diferencial Cambiario
    # configurada -- ese mecanismo se retiró (ver bloque "(c) Diferencial
    # Cambiario" en discounts.py); solo queda el 3% de contado.
    assert res.total_descuentos == Decimal("3.00")


def test_ventana_pago_de_la_regla_pronto_pago_confirma_contado_dentro_de_ventana() -> None:
    """Regla de Pronto Pago con ventana_pago_tipo="entrega" + 3 dias --

    abono el mismo dia de la entrega (2026-06-05), dentro de la ventana
    -- el contado debe confirmarse usando la ventana POR REGLA, no la
    global (cash_window_business_days)."""
    orden = b.orden(primera=False, fecha_entrega=date(2026, 6, 5))
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="97",
        moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 8, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[
            b.descuento(
                marca="Sinoco",
                categoria="*",
                porcentaje="0.03",
                ventana_pago_tipo="entrega",
                ventana_pago_dias=3,
            )
        ],
        resolver=_resolver(**{"P1@USD": "100"}),
        fecha_calculo=date(2026, 6, 8),
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "contado" in origenes
    assert res.total_descuentos == Decimal("3.00")


def test_ventana_pago_de_la_regla_pronto_pago_niega_contado_fuera_de_ventana() -> None:
    """Mismo escenario pero el abono llega el dia 9 (4 dias tras la

    entrega) -- fuera de la ventana "entrega"+3, el contado se niega aunque
    el neto se haya liquidado."""
    orden = b.orden(primera=False, fecha_entrega=date(2026, 6, 5))
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="97",
        moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 9, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[
            b.descuento(
                marca="Sinoco",
                categoria="*",
                porcentaje="0.03",
                ventana_pago_tipo="entrega",
                ventana_pago_dias=3,
            )
        ],
        resolver=_resolver(**{"P1@USD": "100"}),
        fecha_calculo=date(2026, 6, 9),
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "contado" not in origenes


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


def test_descuento_volumen_aplica_a_subtotal() -> None:
    """Con aplica_a="subtotal", el % se calcula sobre TODO precio_base de la

    orden, no solo sobre el subtotal del grupo marca/categoría que matcheó.
    """
    orden = b.orden(primera=False)
    linea_sinoco = b.linea(
        producto="P1", marca="Sinoco", categoria="Comercial", cantidad="10", precio="100"
    )
    linea_otra = b.linea(
        producto="P2", marca="GlobalOil", categoria="Industrial", cantidad="5", precio="100"
    )

    resolver = _resolver(
        **{"P1@USD": "100", "P1@BCV": "100", "P2@USD": "100", "P2@BCV": "100"}
    )
    resolver.set_volumen("P1", Decimal("25.0"))  # 10 * 25 L = 250 L
    resolver.set_volumen("P2", Decimal("1.0"))

    from cxc.models import DescuentoVolumen

    regla_vol = DescuentoVolumen(
        regla_id="VOL_SUBTOTAL_1",
        marca="Sinoco",
        categoria="Comercial",
        litros_minimo=Decimal("200"),
        porcentaje=Decimal("0.05"),
        activo=True,
        aplica_a="subtotal",
    )

    inp = _inputs(
        orden=orden,
        lineas=[linea_sinoco, linea_otra],
        abonos=[],
        descuentos_volumen=[regla_vol],
        resolver=resolver,
    )
    res = calcular_factura(inp)

    # precio_base total = 1000 (Sinoco) + 500 (GlobalOil) = 1500
    # Modo subtotal: descuento = 1500 * 0.05 = 75, NO 1000 * 0.05 = 50
    assert res.total_descuentos == Decimal("75.00")
    assert res.total_motor == Decimal("1425.00")


def test_descuento_volumen_subtotal_no_duplica_entre_grupos() -> None:
    """Una regla marca="*" en modo subtotal que matchea 2 grupos distintos

    (marca/categoría) debe aplicarse UNA sola vez sobre precio_base, no una
    vez por cada grupo que matchea.
    """
    orden = b.orden(primera=False)
    linea_a = b.linea(
        producto="P1", marca="Sinoco", categoria="Comercial", cantidad="10", precio="100"
    )
    linea_b = b.linea(
        producto="P2", marca="GlobalOil", categoria="Industrial", cantidad="10", precio="100"
    )

    resolver = _resolver(
        **{"P1@USD": "100", "P1@BCV": "100", "P2@USD": "100", "P2@BCV": "100"}
    )
    resolver.set_volumen("P1", Decimal("25.0"))  # 250 L
    resolver.set_volumen("P2", Decimal("25.0"))  # 250 L

    from cxc.models import DescuentoVolumen

    regla_vol = DescuentoVolumen(
        regla_id="VOL_WILDCARD",
        marca="*",
        categoria="*",
        litros_minimo=Decimal("200"),
        porcentaje=Decimal("0.05"),
        activo=True,
        aplica_a="subtotal",
    )

    inp = _inputs(
        orden=orden,
        lineas=[linea_a, linea_b],
        abonos=[],
        descuentos_volumen=[regla_vol],
        resolver=resolver,
    )
    res = calcular_factura(inp)

    # precio_base = 2000; la misma regla matchea ambos grupos pero solo debe
    # descontar 2000 * 0.05 = 100 una sola vez, NO 200 (doble conteo).
    assert res.total_descuentos == Decimal("100.00")
    assert res.total_motor == Decimal("1900.00")


def test_recompra_aplica_a_subtotal_deduplica_por_regla() -> None:
    """DescuentoRecompra con aplica_a="subtotal": el % se aplica sobre TODO

    precio_base una sola vez, aunque varias líneas de distinta marca/
    categoría matcheen la misma regla comodín.
    """
    from cxc.models import DescuentoRecompra

    orden = b.orden(primera=False, fecha=date(2026, 6, 5))
    orden.so_id = "SO_SUBTOTAL_REC"
    orden.cliente_id = "C_SUBTOTAL"

    linea_a = b.linea(
        producto="P1", marca="Sinoco", categoria="Comercial", cantidad="3", precio="100"
    )
    linea_b = b.linea(
        producto="P2", marca="GlobalOil", categoria="Industrial", cantidad="3", precio="100"
    )

    regla = DescuentoRecompra(
        regla_id="REC_SUBTOTAL",
        marca="*",
        categoria="*",
        min_cajas=1,
        max_cajas=999,
        porcentaje=Decimal("0.05"),
        aplica_a="subtotal",
    )

    orden_ant, vincs_ant = _orden_anterior_pagada(
        so_id="SO_SUBTOTAL_ANT", fecha=date(2026, 5, 5)
    )
    orden_ant.cliente_id = "C_SUBTOTAL"
    for v in vincs_ant:
        v.so_id = "SO_SUBTOTAL_ANT"

    inp = EngineInputs(
        orden=orden,
        lineas=[linea_a, linea_b],
        abonos=[],
        descuentos=[],
        descuentos_volumen=[],
        reglas_recurrencia=[],
        promociones_primera_compra=[],
        feriados_tabla=[],
        price_resolver=_resolver(
            **{"P1@USD": "100", "P1@BCV": "100", "P2@USD": "100", "P2@BCV": "100"}
        ),
        engine_config=CFG,
        fecha_calculo=date(2026, 6, 8),
        all_ordenes=[orden],
        descuentos_recompra=[regla],
        orden_anterior_cliente=orden_ant,
        orden_anterior_cliente_vincs=vincs_ant,
    )
    res = calcular_factura(inp)

    # precio_base = 600; ambas líneas matchean la misma regla comodín pero
    # el % solo debe aplicarse una vez: 600 * 0.05 = 30, NO 60.
    recompra_d = [d for d in res.descuentos_detalle if d.origen == "recurrencia"]
    assert len(recompra_d) == 1
    assert recompra_d[0].monto == Decimal("30.00")
    assert res.total_descuentos == Decimal("30.00")


def test_recompra_ya_no_exige_primera_compra_del_mes() -> None:
    """Regresión del rediseño de Recompra: antes solo la PRIMERA orden del

    mes calendario podía llevar recompra; ahora el criterio es la ventana
    (días de crédito + gracia) desde la orden anterior pagada, sin importar
    el mes -- una SEGUNDA orden en el mismo mes también debe calificar si
    la anterior (aunque sea la primera del mes) quedó pagada y la nueva
    llega dentro de ventana."""
    orden1 = b.orden(
        "SO_FIRST", cliente_id="C1", fecha=date(2026, 6, 5), monto_total="100", dias_credito=30
    )
    orden2 = b.orden("SO_SECOND", cliente_id="C1", fecha=date(2026, 6, 15))

    linea = b.linea(marca="Sinoco", categoria="Comercial", precio="100")
    regla = b.descuento_recompra("REC1", marca="*", categoria="*", porcentaje="0.05")

    # orden1 (la "anterior" de orden2) queda totalmente pagada.
    vinc1 = b.vinculacion(
        "V1", so_id="SO_FIRST", monto_aplicado="100", moneda_abono=Moneda.USD,
        hora=datetime(2026, 6, 5, 10, 0),
    )

    # 2. orden2 llega 10 días después (dentro de 30+3 días de ventana) --
    # ANTES esto hubiera sido negado por no ser la primera del mes.
    inp2 = _inputs(
        orden=orden2,
        lineas=[linea],
        abonos=[],
        descuentos_recompra=[regla],
        all_ordenes=[orden1, orden2],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "100"}),
        orden_anterior=orden1,
        orden_anterior_vincs=[vinc1],
    )
    res2 = calcular_factura(inp2)
    recompra_d2 = [d for d in res2.descuentos_detalle if d.origen == "recurrencia"]
    assert len(recompra_d2) == 1
    assert recompra_d2[0].monto == Decimal("5.00")


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


# NOTA (agosto 2026): "brecha cierre" (auto-aplicado por abono, sin ninguna
# regla configurada) y la vieja "equiparación" sin tope fueron RETIRADOS del
# motor -- ver bloque "(c) Diferencial Cambiario" en discounts.py. La
# "Regla 3" (candidatos a cierre) del usuario NO es un descuento automático
# de ``calcular_factura`` -- es un reporte de candidatos aparte, gerencia
# aprueba manualmente. Los tests de esas 2 rutas legacy se eliminan; el
# ejemplo real de equiparación se reescribe abajo con el tope explícito de
# la regla 1 y el requisito de "sin pagos huérfanos".


def test_diferencial_regla2_equiparar_sin_exceder_tope() -> None:
    """Ejemplo real del usuario (regla 2, "equiparar"), con un tope

    generoso (50%) que NO topa este caso -- confirma la fórmula base:
    Monto Odoo (Lista VES): $58.46
    Monto Meta (Lista USD): $32.76
    Pagos: $10 USD cash + 19560.85 VES (859.44 Binance = $22.76 USD; 742.23 BCV = $26.35 USD)
    Abonos Binance = $32.76 USD >= $32.76 USD (Lista USD) -> Cumplido 100%!
    Abonos BCV = $36.35 USD
    NC Sugerida = $58.46 - $36.35 = $22.11 USD (37.8% del teórico VES, por
    debajo del tope de 50% usado en este test).
    """
    from cxc.models import DescuentoDiferencialCambiario

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
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(v_usd, m_usd), (v_ves, m_ves)],
        resolver=resolver,
        valid_usd=["4"],
        valid_ves=["5"],
        descuentos_diferencial=[
            DescuentoDiferencialCambiario(
                regla_id="DIF_MAX",
                nombre="Diferencial máximo",
                tipo_diferencial="fijo_35_ves_usd",
                porcentaje_fijo=Decimal("0.50"),
            ),
            DescuentoDiferencialCambiario(
                regla_id="DIF_EQ",
                nombre="Equiparar",
                tipo_diferencial="equiparar_binance",
            ),
        ],
    )
    res = calcular_factura(inp)

    assert res.candidata_a_cierre is True
    assert res.requiere_revision is True
    assert res.total_descuentos == Decimal("22.11")
    assert res.total_motor == Decimal("36.35")
    assert any("Equiparación" in d.descripcion for d in res.descuentos_detalle)


def test_diferencial_regla2_equiparar_topada_al_diferencial_maximo() -> None:
    """Mismo escenario, pero con el tope real (35%) -- el gap real (37.8%)

    excede el tope, así que la NC se limita a 58.46 * 0.35 = 20.46, no a
    los $22.11 completos."""
    from cxc.models import DescuentoDiferencialCambiario

    orden = b.orden(primera=False, lista="5")
    linea = b.linea(
        linea_id="L1", producto="P1", marca="GLOBAL OIL", categoria="CAJA", precio="58.46"
    )
    m_usd = b.metodo(moneda=Moneda.USD, es_contado=False)
    v_usd = b.vinculacion(
        monto_aplicado="10.00",
        moneda_abono=Moneda.USD,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="1.0",
        tasa_binance="1.0",
        hora=datetime(2026, 4, 15, 10, 0),
    )
    m_ves = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    v_ves = b.vinculacion(
        monto_aplicado="19560.85",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="742.23",
        tasa_binance="859.44",
        hora=datetime(2026, 4, 15, 11, 0),
    )
    resolver = _resolver(**{"P1@5": "58.46", "P1@4": "32.76"})
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(v_usd, m_usd), (v_ves, m_ves)],
        resolver=resolver,
        valid_usd=["4"],
        valid_ves=["5"],
        descuentos_diferencial=[
            DescuentoDiferencialCambiario(
                regla_id="DIF_MAX",
                nombre="Diferencial máximo",
                tipo_diferencial="fijo_35_ves_usd",
                porcentaje_fijo=Decimal("0.35"),
            ),
            DescuentoDiferencialCambiario(
                regla_id="DIF_EQ", nombre="Equiparar", tipo_diferencial="equiparar_binance"
            ),
        ],
    )
    res = calcular_factura(inp)
    assert res.total_descuentos == Decimal("20.46")  # 58.46 * 0.35, no 22.11


def test_diferencial_regla2_bloqueada_por_pago_huerfano() -> None:
    """Mismo escenario que el tope generoso, pero el cliente tiene un pago

    huérfano abierto en Odoo -- la regla 2 debe bloquearse por completo
    (pedido explícito del usuario: "cualquier huérfano del cliente")."""
    from cxc.models import DescuentoDiferencialCambiario

    orden = b.orden(primera=False, lista="5")
    linea = b.linea(
        linea_id="L1", producto="P1", marca="GLOBAL OIL", categoria="CAJA", precio="58.46"
    )
    m_usd = b.metodo(moneda=Moneda.USD, es_contado=False)
    v_usd = b.vinculacion(
        monto_aplicado="10.00",
        moneda_abono=Moneda.USD,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="1.0",
        tasa_binance="1.0",
        hora=datetime(2026, 4, 15, 10, 0),
    )
    m_ves = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    v_ves = b.vinculacion(
        monto_aplicado="19560.85",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        tasa_bcv="742.23",
        tasa_binance="859.44",
        hora=datetime(2026, 4, 15, 11, 0),
    )
    resolver = _resolver(**{"P1@5": "58.46", "P1@4": "32.76"})
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(v_usd, m_usd), (v_ves, m_ves)],
        resolver=resolver,
        valid_usd=["4"],
        valid_ves=["5"],
        descuentos_diferencial=[
            DescuentoDiferencialCambiario(
                regla_id="DIF_MAX",
                nombre="Diferencial máximo",
                tipo_diferencial="fijo_35_ves_usd",
                porcentaje_fijo=Decimal("0.50"),
            ),
            DescuentoDiferencialCambiario(
                regla_id="DIF_EQ", nombre="Equiparar", tipo_diferencial="equiparar_binance"
            ),
        ],
        cliente_tiene_pagos_huerfanos=True,
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "bcv_completo" not in origenes
    assert res.total_descuentos == Decimal("0.00")


def test_match_lista_con_keywords_dinamicas_listas_ves_y_usd() -> None:
    from cxc.engine.effective_dating import _match_lista

    assert _match_lista("LISTAS_VES", "5", valid_ves=["5", "9"], valid_usd=["4"]) is True
    assert _match_lista("LISTAS_VES", "9", valid_ves=["5", "9"], valid_usd=["4"]) is True
    assert _match_lista("LISTAS_VES", "4", valid_ves=["5", "9"], valid_usd=["4"]) is False
    assert _match_lista("LISTAS_USD", "4", valid_ves=["5", "9"], valid_usd=["4"]) is True
    assert _match_lista("LISTAS_USD", "9", valid_ves=["5", "9"], valid_usd=["4"]) is False


def test_resolved_marca_fallback_sinoco_vs_global() -> None:
    """``producto`` es el ID de Odoo (no el nombre) -- SINOCO llega con

    ``marca`` ya poblada directo desde brand_id, nunca por sniffing del
    nombre. Solo las líneas SIN marca (brand_id vacío en Odoo, típico de
    algunos GLOBAL OIL) caen al fallback configurable (default GLOBAL OIL)."""
    l_sinoco = b.linea(producto="123", marca="SINOCO")
    l_global = b.linea(producto="456", marca="")
    l_explicit = b.linea(producto="789", marca="MARCA_CUSTOM")

    assert l_sinoco.resolved_marca == "SINOCO"
    assert l_global.resolved_marca == "GLOBAL OIL"
    assert l_explicit.resolved_marca == "MARCA_CUSTOM"


def test_resolved_marca_fallback_es_configurable() -> None:
    from cxc.models import set_marca_fallback

    l_sin_marca = b.linea(producto="456", marca="")
    set_marca_fallback("OTRA MARCA")
    try:
        assert l_sin_marca.resolved_marca == "OTRA MARCA"
    finally:
        set_marca_fallback("GLOBAL OIL")


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

        def all_lineas(self):
            return []

        def upsert_bandejas(self, filas):
            pass

        def update_vinculaciones(self, vincs):
            pass

    repo = DummyRepo()
    runner = EngineRunner(repo, None, None)

    # Override _calcular (llamado internamente por run_all) para contar
    # llamadas sin ejercitar el cálculo completo del motor.
    called = []
    runner._calcular = lambda so_id, dt, **kw: (called.append(so_id), None)[1]

    runner.run_all(date.today())
    assert "SO_ACTIVE" in called
    assert "SO_CANCEL" not in called


def test_lineas_con_precio_resuelve_por_linea_para_lista_dada() -> None:
    """Fase 5 (modal de detalle): lineas_con_precio devuelve una fila por

    línea con el precio unitario resuelto para la lista pedida, sin agregar
    -- reusa la misma resolución de precio que el motor usa para totales."""
    from cxc.engine.discounts import lineas_con_precio

    orden = b.orden(primera=False)
    linea1 = b.linea(
        producto="P1", marca="Sinoco", categoria="Comercial", cantidad="3", precio="100"
    )
    linea2 = b.linea(
        producto="P2", marca="GlobalOil", categoria="Industrial", cantidad="2", precio="50"
    )

    resolver = _resolver(
        **{
            "P1@USD": "100", "P1@BCV": "90",
            "P2@USD": "50", "P2@BCV": "45",
        }
    )
    resolver.set_volumen("P1", Decimal("20.0"))
    resolver.set_volumen("P2", Decimal("5.0"))

    inp = _inputs(orden=orden, lineas=[linea1, linea2], abonos=[], resolver=resolver)

    filas_usd = lineas_con_precio(inp, "USD")
    assert len(filas_usd) == 2
    assert filas_usd[0]["producto"] == "P1"
    assert filas_usd[0]["precio_unitario"] == Decimal("100")
    assert filas_usd[0]["cantidad"] == Decimal("3")
    assert filas_usd[0]["subtotal"] == Decimal("300")
    assert filas_usd[1]["subtotal"] == Decimal("100")
    # Litros: unitario tal cual el resolver, total = unitario * cantidad.
    assert filas_usd[0]["litros_unitario"] == Decimal("20.0")
    assert filas_usd[0]["litros_total"] == Decimal("60.0")
    assert filas_usd[1]["litros_unitario"] == Decimal("5.0")
    assert filas_usd[1]["litros_total"] == Decimal("10.0")

    filas_bcv = lineas_con_precio(inp, "BCV")
    assert filas_bcv[0]["precio_unitario"] == Decimal("90")
    assert filas_bcv[0]["subtotal"] == Decimal("270")


def test_conceptos_descuento_teorico_respeta_listas_aplicables() -> None:
    """Fase 6 (modal de detalle): conceptos_descuento_teorico devuelve los

    conceptos (recompra/contado/volumen) que explican descuentos_teorico_ves
    o _usd para ESA lista -- una regla de volumen con listas_aplicables="USD"
    solo debe aparecer en el desglose de la lista USD, no en BCV."""
    from cxc.engine.discounts import conceptos_descuento_teorico
    from cxc.models import DescuentoVolumen

    orden = b.orden(primera=False)
    linea = b.linea(
        producto="P1", marca="Sinoco", categoria="Comercial", cantidad="10", precio="100"
    )
    resolver = _resolver(**{"P1@USD": "100", "P1@BCV": "90"})
    resolver.set_volumen("P1", Decimal("25.0"))  # 250 L

    regla_vol = DescuentoVolumen(
        regla_id="VOL_USD_ONLY",
        marca="Sinoco",
        categoria="Comercial",
        litros_minimo=Decimal("200"),
        porcentaje=Decimal("0.05"),
        activo=True,
        listas_aplicables="USD",
    )

    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        descuentos_volumen=[regla_vol],
        resolver=resolver,
        valid_usd=["USD"],
        valid_ves=["BCV"],
    )

    conceptos_usd = conceptos_descuento_teorico(inp, "USD", pura_bcv=False)
    conceptos_bcv = conceptos_descuento_teorico(inp, "BCV", pura_bcv=True)

    assert len(conceptos_usd) == 1
    assert "Sinoco/Comercial" in conceptos_usd[0]["concepto"]
    assert conceptos_usd[0]["monto"] == Decimal("50.00")  # 10*100*0.05

    assert conceptos_bcv == []


def test_calcular_teorico_orden_con_fallback_detecta_fallback_por_lista() -> None:
    """Fase 10: calcular_teorico_orden_con_fallback devuelve los teóricos +

    si alguna línea usó precio de fallback (no encontrado en la lista
    específica) -- señal para saber cuándo re-verificar un teórico ya
    guardado en ventas_teoricos."""
    from cxc.engine.discounts import calcular_teorico_orden_con_fallback
    from cxc.engine.price_resolver import PriceResolver

    class _ResolverConFallback(PriceResolver):
        """Simula OdooPriceResolver: P2 no tiene precio propio en BCV,

        usa fallback (mismo valor que USD)."""

        def precio(self, producto, lista, fecha=None):
            precios = {
                ("P1", "USD"): Decimal("100"),
                ("P1", "BCV"): Decimal("90"),
                ("P2", "USD"): Decimal("50"),
            }
            clave = (producto, lista)
            if clave in precios:
                return precios[clave]
            # Fallback: P2 en BCV no existe, usa el de USD.
            return precios[(producto, "USD")]

        def volumen(self, producto):
            return Decimal("0")

        def fue_fallback(self, producto, lista):
            return producto == "P2" and lista == "BCV"

    orden = b.orden(primera=False)
    linea1 = b.linea(
        producto="P1", marca="Sinoco", categoria="Comercial", cantidad="1", precio="100"
    )
    linea2 = b.linea(
        producto="P2", marca="Sinoco", categoria="Comercial", cantidad="1", precio="50"
    )

    inp = _inputs(
        orden=orden,
        lineas=[linea1, linea2],
        abonos=[],
        resolver=_ResolverConFallback(),
        valid_usd=["USD"],
        valid_ves=["BCV"],
    )

    resultado = calcular_teorico_orden_con_fallback(inp)

    assert resultado["lista_ves_id"] == "BCV"
    assert resultado["lista_usd_id"] == "USD"
    assert resultado["usa_fallback_ves"] is True  # P2 usó fallback en BCV
    assert resultado["usa_fallback_usd"] is False  # ambos tienen precio propio en USD
    assert resultado["teorico_ves"] == Decimal("140.00")  # 90 (P1) + 50 (P2 fallback)
    assert resultado["teorico_usd"] == Decimal("150.00")  # 100 + 50


def test_descuento_por_producto_especifico_se_aplica() -> None:
    """DescuentoProducto (por SKU) hoy no se calculaba en absoluto -- este

    test confirma que quedó cableado: una regla por código de producto se
    aplica sobre la línea que matchea, independiente de marca/categoría."""
    orden = b.orden(primera=False)
    linea1 = b.linea(producto="P1", marca="Sinoco", categoria="*", precio="100", cantidad="1")
    linea2 = b.linea(producto="P2", marca="Sinoco", categoria="*", precio="50", cantidad="1")
    inp = _inputs(
        orden=orden,
        lineas=[linea1, linea2],
        abonos=[],
        descuentos_producto=[b.descuento_producto("PROD1", productos="P1", porcentaje="0.10")],
        resolver=_resolver(**{"P1@USD": "100", "P2@USD": "50", "P1@BCV": "100", "P2@BCV": "50"}),
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "producto" in origenes
    # 10% solo sobre P1 (100) -- P2 no matchea el código.
    assert res.total_descuentos == Decimal("10.00")


def test_descuento_por_producto_no_matchea_otro_sku() -> None:
    orden = b.orden(primera=False)
    linea = b.linea(producto="P2", marca="Sinoco", categoria="*", precio="50", cantidad="1")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        descuentos_producto=[b.descuento_producto("PROD1", productos="P1", porcentaje="0.10")],
        resolver=_resolver(**{"P2@USD": "50", "P2@BCV": "50"}),
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "producto" not in origenes


def test_descuento_por_volumen_acumulado_suma_historial_del_cliente() -> None:
    """tipo_evaluacion="acumulado" antes NO se leia en absoluto (hallazgo de

    auditoria, agosto 2026) -- este test confirma que quedo cableado: el
    umbral se evalua sumando litros de OTRAS ordenes del mismo cliente
    dentro de dias_evaluacion, no solo esta orden."""
    from cxc.models import DescuentoVolumen

    orden = b.orden("SO_NUEVA", cliente_id="C1", fecha=date(2026, 6, 20), primera=False)
    linea = b.linea(producto="P1", marca="Global Oil", categoria="Comercial", cantidad="10")
    resolver = _resolver(**{"P1@USD": "100", "P1@BCV": "100"})
    resolver.set_volumen("P1", Decimal("100.0"))  # 10 * 100L = 1000L esta orden

    orden_hist = b.orden("SO_VIEJA", cliente_id="C1", fecha=date(2026, 6, 1))
    linea_hist = b.linea(
        "L_HIST", so_id="SO_VIEJA", producto="P1", marca="Global Oil", categoria="Comercial",
        cantidad="20",
    )  # 20 * 100L = 2000L historicos, dentro de 30 dias

    regla_vol = DescuentoVolumen(
        regla_id="VOL_ACUM",
        marca="Global Oil",
        categoria="Comercial",
        litros_minimo=Decimal("2500"),
        unidad_medida="LITROS",
        porcentaje=Decimal("0.05"),
        tipo_evaluacion="acumulado",
        dias_evaluacion=30,
        activo=True,
    )

    # Sin historial: 1000L < 2500L -- no deberia matchear.
    inp_sin_historial = _inputs(
        orden=orden, lineas=[linea], abonos=[], descuentos_volumen=[regla_vol], resolver=resolver,
    )
    res_sin = calcular_factura(inp_sin_historial)
    assert res_sin.total_descuentos == Decimal("0.00")

    # Con historial: 1000L + 2000L = 3000L >= 2500L -- si deberia matchear.
    inp_con_historial = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        descuentos_volumen=[regla_vol],
        resolver=resolver,
        historial_cliente_lineas=[(orden_hist, [linea_hist])],
    )
    res_con = calcular_factura(inp_con_historial)
    # Base: 10 * 100 = 1000 USD; 5% de descuento = 50 USD.
    assert res_con.total_descuentos == Decimal("50.00")


def test_descuento_por_volumen_acumulado_respeta_ventana_dias_evaluacion() -> None:
    from cxc.models import DescuentoVolumen

    orden = b.orden("SO_NUEVA", cliente_id="C1", fecha=date(2026, 6, 20), primera=False)
    linea = b.linea(producto="P1", marca="Global Oil", categoria="Comercial", cantidad="10")
    resolver = _resolver(**{"P1@USD": "100", "P1@BCV": "100"})
    resolver.set_volumen("P1", Decimal("100.0"))  # 1000L esta orden

    # Orden historica de hace 60 dias -- fuera de la ventana de 30 dias.
    orden_hist_vieja = b.orden("SO_MUY_VIEJA", cliente_id="C1", fecha=date(2026, 4, 21))
    linea_hist_vieja = b.linea(
        "L_HIST2", so_id="SO_MUY_VIEJA", producto="P1", marca="Global Oil", categoria="Comercial",
        cantidad="20",
    )

    regla_vol = DescuentoVolumen(
        regla_id="VOL_ACUM2",
        marca="Global Oil",
        categoria="Comercial",
        litros_minimo=Decimal("2500"),
        unidad_medida="LITROS",
        porcentaje=Decimal("0.05"),
        tipo_evaluacion="acumulado",
        dias_evaluacion=30,
        activo=True,
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        descuentos_volumen=[regla_vol],
        resolver=resolver,
        historial_cliente_lineas=[(orden_hist_vieja, [linea_hist_vieja])],
    )
    res = calcular_factura(inp)
    # 1000L + historial fuera de ventana (ignorado) = 1000L < 2500L.
    assert res.total_descuentos == Decimal("0.00")


def test_descuento_por_volumen_orden_ignora_historial() -> None:
    """Reglas tipo_evaluacion="orden" (default) NO deben verse afectadas por

    historial_cliente_lineas -- cero regresion para las reglas ya activas
    en produccion que nunca usaron acumulado."""
    from cxc.models import DescuentoVolumen

    orden = b.orden("SO_NUEVA", cliente_id="C1", fecha=date(2026, 6, 20), primera=False)
    linea = b.linea(producto="P1", marca="Global Oil", categoria="Comercial", cantidad="10")
    resolver = _resolver(**{"P1@USD": "100", "P1@BCV": "100"})
    resolver.set_volumen("P1", Decimal("100.0"))  # 1000L esta orden

    orden_hist = b.orden("SO_VIEJA", cliente_id="C1", fecha=date(2026, 6, 1))
    linea_hist = b.linea(
        "L_HIST3", so_id="SO_VIEJA", producto="P1", marca="Global Oil", categoria="Comercial",
        cantidad="50",
    )  # 5000L historicos -- si se sumaran, dispararia la regla

    regla_vol = DescuentoVolumen(
        regla_id="VOL_ORDEN",
        marca="Global Oil",
        categoria="Comercial",
        litros_minimo=Decimal("2500"),
        unidad_medida="LITROS",
        porcentaje=Decimal("0.05"),
        tipo_evaluacion="orden",
        activo=True,
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        descuentos_volumen=[regla_vol],
        resolver=resolver,
        historial_cliente_lineas=[(orden_hist, [linea_hist])],
    )
    res = calcular_factura(inp)
    # Solo esta orden (1000L) cuenta -- el historial se ignora para "orden".
    assert res.total_descuentos == Decimal("0.00")


def test_promocion_recurrente_aplica_fuera_de_primera_compra() -> None:
    """Bug real corregido (auditoria agosto 2026, PROMO_12_MAS_1): una

    promocion con solo_primera_compra=False ("Recurrente") debe aplicar en
    CUALQUIER orden que cumpla la condicion, no solo en la primerisima
    orden del cliente -- antes toda la evaluacion de PromocionPrimeraCompra
    vivia detras de un "if es_primera_compra", asi que las reglas
    "Recurrente" nunca disparaban fuera de esa primera orden."""
    orden = b.orden(primera=False, lista="BCV")
    linea_com = b.linea(
        linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial",
        precio="100", cantidad="1",
    )
    linea_liga = b.linea(
        linea_id="L2", producto="LIGA", marca="Sinoco", categoria="Comercial",
        precio="5", cantidad="1", descuento="0",
    )
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="1", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea_com, linea_liga],
        abonos=[(vinc, metodo)],
        promociones=[
            b.promo_primera(
                "LIGA", compra_minima="1", valor="1", regalo_tipo="solo_uno",
                solo_primera_compra=False,
            )
        ],
        resolver=_resolver(**{"P1@BCV": "100", "LIGA@BCV": "5"}),
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "primera_compra" in origenes
    assert res.ncs_calculadas == Decimal("5.00")


def test_promocion_solo_primera_compra_no_aplica_fuera_de_primera() -> None:
    """Complemento del test anterior: una promo con solo_primera_compra=True

    SI debe seguir restringida a la primera orden del cliente (sin
    regresion)."""
    orden = b.orden(primera=False, lista="BCV")
    linea_com = b.linea(
        linea_id="L1", producto="P1", marca="Sinoco", categoria="Comercial",
        precio="100", cantidad="1",
    )
    linea_liga = b.linea(
        linea_id="L2", producto="LIGA", marca="Sinoco", categoria="Comercial",
        precio="5", cantidad="1", descuento="0",
    )
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="1", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea_com, linea_liga],
        abonos=[(vinc, metodo)],
        promociones=[
            b.promo_primera(
                "LIGA", compra_minima="1", valor="1", regalo_tipo="solo_uno",
                solo_primera_compra=True,
            )
        ],
        resolver=_resolver(**{"P1@BCV": "100", "LIGA@BCV": "5"}),
    )
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    assert "primera_compra" not in origenes
    assert res.ncs_calculadas == Decimal("0.00")


def test_promocion_recurrente_sin_promos_no_inventa_2pct_industrial() -> None:
    """El fallback "2% Industrial sin promos configuradas" es especifico del

    incentivo de primera compra -- una orden NO-primera sin promos
    "recurrente" configuradas no debe recibir ningun descuento por
    defecto."""
    orden = b.orden(primera=False, lista="BCV")
    linea_ind = b.linea(
        linea_id="L1", producto="P1", marca="Sinoco", categoria="Industrial",
        precio="100", cantidad="1",
    )
    metodo = b.metodo(moneda=Moneda.VES, tipo_tasa=TipoTasa.BCV, es_contado=False)
    vinc = b.vinculacion(
        monto_aplicado="1", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea_ind],
        abonos=[(vinc, metodo)],
        promociones=[],
        resolver=_resolver(**{"P1@BCV": "100"}),
    )
    res = calcular_factura(inp)
    assert res.ncs_calculadas == Decimal("0.00")


def test_descuento_volumen_por_subcategoria_especifica() -> None:
    """Hallazgo real (auditoria agosto 2026): antes el agrupado de Volumen

    era por (marca, categoria RAIZ) unicamente -- una regla scoped a una
    subcategoria especifica (ej. "Elite") nunca podia matchear ninguna
    linea, porque el total que se comparaba contra el umbral era el de
    TODA la categoria raiz, no el de la subcategoria. Ahora cada regla
    suma solo sus propias lineas coincidentes."""
    from cxc.models import DescuentoVolumen

    orden = b.orden(primera=False)
    linea_elite = b.linea(
        producto="P1", marca="Global Oil", categoria="Comercial",
        subcategoria="Elite", cantidad="5", precio="100",
    )
    linea_clasico = b.linea(
        "L2", producto="P2", marca="Global Oil", categoria="Comercial",
        subcategoria="Clasico", cantidad="5", precio="100",
    )
    regla_elite = DescuentoVolumen(
        regla_id="VOL_ELITE",
        marca="Global Oil",
        categoria="Elite",
        min_cantidad=Decimal("5"),
        max_cantidad=Decimal("999999"),
        unidad_medida="CAJAS",
        porcentaje=Decimal("0.10"),
        activo=True,
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea_elite, linea_clasico],
        abonos=[],
        descuentos_volumen=[regla_elite],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "100", "P2@USD": "100", "P2@BCV": "100"}),
    )
    res = calcular_factura(inp)
    # Solo la linea Elite (5 * 100 = 500) recibe el 10% -- la Clasico no
    # matchea la regla scoped a Elite. 500 * 0.10 = 50.
    assert res.total_descuentos == Decimal("50.00")


def test_descuento_volumen_regla_especifica_no_duplica_con_regla_amplia() -> None:
    """Una regla amplia ("Comercial") y una scoped ("Elite") pueden

    coexistir vigentes -- la mas especifica se queda con sus lineas
    primero, la amplia solo cobra sobre lo que queda libre (sin
    doble-conteo de las mismas unidades)."""
    from cxc.models import DescuentoVolumen

    orden = b.orden(primera=False)
    linea_elite = b.linea(
        producto="P1", marca="Global Oil", categoria="Comercial",
        subcategoria="Elite", cantidad="5", precio="100",
    )
    linea_clasico = b.linea(
        "L2", producto="P2", marca="Global Oil", categoria="Comercial",
        subcategoria="Clasico", cantidad="5", precio="100",
    )
    regla_amplia = DescuentoVolumen(
        regla_id="VOL_COMERCIAL",
        marca="Global Oil",
        categoria="Comercial",
        min_cantidad=Decimal("5"),
        max_cantidad=Decimal("999999"),
        unidad_medida="CAJAS",
        porcentaje=Decimal("0.05"),
        activo=True,
    )
    regla_elite = DescuentoVolumen(
        regla_id="VOL_ELITE",
        marca="Global Oil",
        categoria="Elite",
        min_cantidad=Decimal("5"),
        max_cantidad=Decimal("999999"),
        unidad_medida="CAJAS",
        porcentaje=Decimal("0.10"),
        activo=True,
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea_elite, linea_clasico],
        abonos=[],
        descuentos_volumen=[regla_amplia, regla_elite],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "100", "P2@USD": "100", "P2@BCV": "100"}),
    )
    res = calcular_factura(inp)
    # Elite (500) al 10% = 50; Clasico (500, libre, no reclamado por
    # Elite) al 5% = 25. Total = 75, no 100 (que seria doble-conteo de
    # Elite bajo ambas reglas).
    assert res.total_descuentos == Decimal("75.00")


def test_descuento_volumen_por_presentacion() -> None:
    """Regla scoped por presentacion (ej. "GARRAFA") en vez de subcategoria.

    Nota: "TAMBOR"/"PAILA"/"CAJA" tienen alias legacy en _match_categoria
    que los tratan como sinonimos de la categoria raiz Industrial/Comercial
    completa (no solo esa presentacion puntual) -- se usa "GARRAFA" aqui
    para probar el matching real por presentacion sin chocar con esos
    alias preexistentes."""
    from cxc.models import DescuentoVolumen

    orden = b.orden(primera=False)
    linea_garrafa = b.linea(
        producto="P1", marca="Sinoco", categoria="Industrial",
        presentacion_odoo="GARRAFA", cantidad="3", precio="200",
    )
    linea_bidon = b.linea(
        "L2", producto="P2", marca="Sinoco", categoria="Industrial",
        presentacion_odoo="BIDON", cantidad="3", precio="200",
    )
    regla_garrafa = DescuentoVolumen(
        regla_id="VOL_GARRAFA",
        marca="Sinoco",
        categoria="GARRAFA",
        min_cantidad=Decimal("2"),
        max_cantidad=Decimal("999999"),
        unidad_medida="CAJAS",
        porcentaje=Decimal("0.07"),
        activo=True,
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea_garrafa, linea_bidon],
        abonos=[],
        descuentos_volumen=[regla_garrafa],
        resolver=_resolver(**{"P1@USD": "200", "P1@BCV": "200", "P2@USD": "200", "P2@BCV": "200"}),
    )
    res = calcular_factura(inp)
    # Solo la linea GARRAFA (3 * 200 = 600) al 7% = 42.
    assert res.total_descuentos == Decimal("42.00")
