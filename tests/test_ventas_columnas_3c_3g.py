"""Tarea 3c-3g: nuevas columnas de ``/api/ventas``.

Cubre la matriz pedida por el /goal: lista USD/VES x con/sin descuento en
Odoo x con/sin N/C x con/sin N/D. Ninguno de estos tests recalcula
descuentos -- solo verifica que ``/api/ventas`` lea correctamente lo que
Odoo y el motor (``BandejaFacturacion``) ya tienen, y que la comparación
(Tarea 3c/3d) use las mismas funciones puras de ``discount_audit`` que
``/api/auditoria``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.config import EngineConfig
from cxc.models import (
    BandejaFacturacion,
    Entrega,
    EstadoVinculacion,
    Factura,
    LineaFactura,
    LineaOrden,
    OrdenVenta,
    Producto,
    SerieTasa,
    VentasTeorico,
    Vinculacion,
)
from cxc.web.app import app

client = TestClient(app)


def _orden(so_id: str, *, lista: str, monto_total: str, facturada: bool = True) -> OrdenVenta:
    return OrdenVenta(
        so_id=so_id,
        cliente_id="CLI_3C",
        vendedor_email="ana@lubrikca.com",
        fecha=date(2026, 7, 1),
        fecha_entrega=None,
        monto_total=Decimal(monto_total),
        lista_precios=lista,
        es_primera_compra=False,
        estado_orden="sale",
        facturada=facturada,
    )


def _fake_execute(model, method, args, kwargs=None):
    domain = args[0] if args else []

    if model == "sale.order":
        return [
            {"name": "SO_OK", "state": "sale", "amount_untaxed": 95.0},
            {"name": "SO_PENDIENTE", "state": "sale", "amount_untaxed": 97.0},
            {"name": "SO_NC", "state": "sale", "amount_untaxed": 200.0},
            {"name": "SO_ND", "state": "sale", "amount_untaxed": 90.0},
        ]

    if model == "account.move":
        # Dos llamadas distintas al mismo modelo: la principal (facturas +
        # NC por invoice_origin) y la de notas de débito (por
        # debit_origin_id). Se distinguen por el campo del dominio.
        is_debit_notes_query = any(
            isinstance(clause, list | tuple) and clause and clause[0] == "debit_origin_id"
            for clause in domain
        )
        if is_debit_notes_query:
            # SO_ND: la factura original es id=901 (ver abajo).
            return [
                {
                    "debit_origin_id": [901, "FAC/901"],
                    "amount_total_signed_usd": 20.0,
                }
            ]
        return [
            {
                "id": 900,
                "invoice_origin": "SO_OK",
                "move_type": "out_invoice",
                "amount_untaxed_signed_usd": 95.0,
                "amount_total_signed_usd": 95.0,
            },
            {
                "id": 902,
                "invoice_origin": "SO_PENDIENTE",
                "move_type": "out_invoice",
                "amount_untaxed_signed_usd": 97.0,
                "amount_total_signed_usd": 97.0,
            },
            {
                "id": 903,
                "invoice_origin": "SO_NC",
                "move_type": "out_invoice",
                "amount_untaxed_signed_usd": 200.0,
                "amount_total_signed_usd": 200.0,
            },
            {
                "id": 904,
                "invoice_origin": "SO_NC",
                "move_type": "out_refund",
                "amount_untaxed_signed_usd": 30.0,
                "amount_total_signed_usd": 30.0,
            },
            {
                "id": 901,
                "invoice_origin": "SO_ND",
                "move_type": "out_invoice",
                "amount_untaxed_signed_usd": 90.0,
                "amount_total_signed_usd": 90.0,
            },
        ]

    if model == "sale.order.line":
        return [
            # SO_OK: Odoo ya tiene exactamente el 5% que el motor calculó.
            {
                "order_id": [1, "SO_OK"],
                "product_uom_qty": 1.0,
                "price_unit": 100.0,
                "discount": 5.0,
                "price_subtotal": 95.0,
            },
            # SO_PENDIENTE: Odoo solo tiene 3, el motor exige 10 -> pendiente.
            {
                "order_id": [2, "SO_PENDIENTE"],
                "product_uom_qty": 1.0,
                "price_unit": 100.0,
                "discount": 3.0,
                "price_subtotal": 97.0,
            },
        ]

    if model == "account.move.line":
        # SO_OK/SO_PENDIENTE ya están facturadas (ver account.move arriba) --
        # la factura es el documento de referencia para "pendiente" una vez
        # facturada (Fase 6), así que copia el mismo % que sale.order.line
        # para que estos fixtures representen el flujo normal de Odoo
        # (factura generada desde la SO hereda su descuento).
        return [
            {
                "move_id": 900,
                "product_uom_qty": 1.0,
                "quantity": 1.0,
                "price_unit": 100.0,
                "discount": 5.0,
                "price_subtotal": 95.0,
            },
            {
                "move_id": 902,
                "product_uom_qty": 1.0,
                "quantity": 1.0,
                "price_unit": 100.0,
                "discount": 3.0,
                "price_subtotal": 97.0,
            },
        ]

    return []


def _facturas_estandar() -> list[Factura]:
    """Espejo Factura equivalente al fixture account.move de _fake_execute

    -- Fase 2: facturado/NC/ND ahora se leen del espejo, no de Odoo en
    vivo (ver _facturacion_por_so_desde_espejo)."""
    return [
        Factura(
            factura_id="900",
            numero="FAC/900",
            so_id="SO_OK",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 1),
            moneda="USD",
            monto_total=Decimal("95.00"),
            monto_sin_impuestos=Decimal("95.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("95.00"),
            monto_sin_impuestos_signed_usd=Decimal("95.00"),
        ),
        Factura(
            factura_id="902",
            numero="FAC/902",
            so_id="SO_PENDIENTE",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 1),
            moneda="USD",
            monto_total=Decimal("97.00"),
            monto_sin_impuestos=Decimal("97.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("97.00"),
            monto_sin_impuestos_signed_usd=Decimal("97.00"),
        ),
        Factura(
            factura_id="903",
            numero="FAC/903",
            so_id="SO_NC",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 1),
            moneda="USD",
            monto_total=Decimal("200.00"),
            monto_sin_impuestos=Decimal("200.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("200.00"),
            monto_sin_impuestos_signed_usd=Decimal("200.00"),
        ),
        Factura(
            factura_id="904",
            numero="RFAC/904",
            so_id="SO_NC",
            move_type="out_refund",
            es_nota_debito=False,
            fecha=date(2026, 7, 1),
            moneda="USD",
            monto_total=Decimal("30.00"),
            monto_sin_impuestos=Decimal("30.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("30.00"),
            monto_sin_impuestos_signed_usd=Decimal("30.00"),
        ),
        Factura(
            factura_id="901",
            numero="FAC/901",
            so_id="SO_ND",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 1),
            moneda="USD",
            monto_total=Decimal("90.00"),
            monto_sin_impuestos=Decimal("90.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("90.00"),
            monto_sin_impuestos_signed_usd=Decimal("90.00"),
        ),
        # N/D: SO_ND, atada a la factura 901 vía debit_origin_id
        # (factura_origen_id) -- Odoo no la marca con invoice_origin.
        Factura(
            factura_id="905",
            numero="ND/905",
            so_id=None,
            move_type="out_debit",
            es_nota_debito=True,
            fecha=date(2026, 7, 2),
            moneda="USD",
            monto_total=Decimal("20.00"),
            monto_sin_impuestos=Decimal("20.00"),
            estado="posted",
            factura_origen_id="901",
            monto_total_signed_usd=Decimal("20.00"),
            monto_sin_impuestos_signed_usd=Decimal("20.00"),
        ),
    ]


def _lineas_orden_estandar() -> list[LineaOrden]:
    """Espejo LineaOrden equivalente al fixture sale.order.line de

    _fake_execute -- Fase 4: descuentos de línea de orden ahora se leen
    del espejo (ver _descuentos_lineas_desde_espejo)."""
    return [
        LineaOrden(
            linea_id="1",
            so_id="SO_OK",
            producto="1",
            marca="Sinoco",
            categoria="*",
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100"),
            descuento=Decimal("5.0"),
        ),
        LineaOrden(
            linea_id="2",
            so_id="SO_PENDIENTE",
            producto="1",
            marca="Sinoco",
            categoria="*",
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100"),
            descuento=Decimal("3.0"),
        ),
    ]


def _lineas_factura_estandar() -> list[LineaFactura]:
    """Espejo LineaFactura equivalente al fixture account.move.line de

    _fake_execute -- Fase 4/5."""
    return [
        LineaFactura(
            linea_id="1",
            factura_id="900",
            nombre="",
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100"),
            descuento=Decimal("5.0"),
            subtotal=Decimal("95"),
        ),
        LineaFactura(
            linea_id="2",
            factura_id="902",
            nombre="",
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100"),
            descuento=Decimal("3.0"),
            subtotal=Decimal("97"),
        ),
    ]


def _run_get_ventas(
    descuentos_sistema: list[dict] | None = None,
    vinculaciones: list | None = None,
    facturas: list[Factura] | None = None,
    lineas: list[LineaOrden] | None = None,
    lineas_factura: list[LineaFactura] | None = None,
):
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = descuentos_sistema or []
    mock_repo.all_vinculaciones.return_value = vinculaciones or []
    mock_repo.all_entregas.return_value = []
    mock_repo.all_catalogo.return_value = []
    mock_repo.all_facturas.return_value = (
        facturas if facturas is not None else _facturas_estandar()
    )
    mock_repo.all_lineas.return_value = (
        lineas if lineas is not None else _lineas_orden_estandar()
    )
    mock_repo.all_lineas_factura.return_value = (
        lineas_factura if lineas_factura is not None else _lineas_factura_estandar()
    )
    mock_repo.all_ordenes.return_value = [
        _orden("SO_OK", lista="4", monto_total="95.00"),
        _orden("SO_PENDIENTE", lista="4", monto_total="97.00"),
        _orden("SO_NC", lista="4", monto_total="200.00"),
        _orden("SO_ND", lista="5", monto_total="90.00"),
    ]
    # Fase 10: los teóricos VES/USD ya no viven en BandejaFacturacion.
    mock_repo.all_ventas_teoricos.return_value = [
        VentasTeorico(
            so_id="SO_OK",
            # Fase 6: descuentos_teorico_ves/_usd son DISTINTOS entre sí
            # (reglas con listas_aplicables distintas por lista) -- reemplaza
            # la columna única "Desc. Motor".
            teorico_ves=Decimal("120.00"),
            teorico_usd=Decimal("100.00"),
            descuentos_teorico_ves=Decimal("8.00"),
            descuentos_teorico_usd=Decimal("3.00"),
            lista_ves_id="5",
            lista_usd_id="4",
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_OK",
            lista_aplicada="4",
            precio_base_calculado=Decimal("100.00"),
            total_descuentos=Decimal("5.00"),
            total_motor=Decimal("95.00"),
        ),
        BandejaFacturacion(
            so_id="SO_PENDIENTE",
            lista_aplicada="4",
            precio_base_calculado=Decimal("100.00"),
            total_descuentos=Decimal("10.00"),
            total_motor=Decimal("90.00"),
        ),
        BandejaFacturacion(
            so_id="SO_NC",
            lista_aplicada="4",
            precio_base_calculado=Decimal("200.00"),
            total_motor=Decimal("200.00"),
            # Fase 6: motor exige 30 de descuento -- la N/C ($30, ver
            # account.move arriba) es la que lo materializa, sin tocar la
            # línea de la orden/factura (descuento_aplicado_orden/_factura
            # quedan en 0 para esta SO).
            total_descuentos=Decimal("30.00"),
        ),
        BandejaFacturacion(
            so_id="SO_ND",
            lista_aplicada="5",
            precio_base_calculado=Decimal("90.00"),
            total_motor=Decimal("90.00"),
        ),
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        return {it["so_id"]: it for it in res.json()["items"]}


def test_descuento_aplicado_orden_coincide_con_motor_es_ok() -> None:
    """Lista USD, sin NC, sin ND: Odoo ya tiene el mismo % que dictó el motor."""
    by_so = _run_get_ventas()
    ok = by_so["SO_OK"]
    assert ok["descuento_aplicado_orden"] == 5.0
    assert ok["descuento_motor_total"] == 5.0
    assert ok["descuento_validacion_orden"] == "ok"
    assert ok["descuento_pendiente_aplicar"] == 0.0
    assert ok["descuento_aplicado_sistema"] == 0.0


def test_descuento_teorico_ves_y_usd_son_columnas_independientes() -> None:
    """Fase 6: reemplaza "Desc. Motor" -- descuentos_teorico_ves/_usd son

    valores distintos (reglas por lista distintas), monto y %."""
    by_so = _run_get_ventas()
    ok = by_so["SO_OK"]
    assert ok["descuento_teorico_ves"] == 8.0
    assert ok["descuento_teorico_ves_pct"] == round(8.0 / 120.0, 4)
    assert ok["descuento_teorico_usd"] == 3.0
    assert ok["descuento_teorico_usd_pct"] == round(3.0 / 100.0, 4)


def test_descuento_pendiente_aplicar_cuando_motor_exige_mas_que_odoo() -> None:
    """El motor calculó 10 de descuento; Odoo solo tiene 3 -> pendiente = 7."""
    by_so = _run_get_ventas()
    pend = by_so["SO_PENDIENTE"]
    assert pend["descuento_aplicado_orden"] == 3.0
    assert pend["descuento_motor_total"] == 10.0
    assert pend["descuento_pendiente_aplicar"] == 7.0
    assert pend["descuento_validacion_orden"] == "discrepancia"


def test_orden_real_subtotal_teoricos_resta_el_total_del_motor_de_venta_bruta_real_con_imp() -> (
    None
):
    """Puntos 5-6 (agosto 2026): nueva columna de totales de orden real --

    (venta_bruta_real menos TODO el descuento que el motor exige, esté ya
    materializado en Odoo o no) + impuestos, sin piso negativo, y sin
    bloqueo (SO_PENDIENTE no tiene sobre-descuento -- Odoo tiene MENOS
    aplicado que el motor)."""
    from cxc.config import AppConfig

    config = AppConfig.from_env()
    multiplicador = 1 + float(config.engine.iva_rate) + (
        float(config.engine.igtf_rate) if config.engine.igtf_activo else 0.0
    )

    by_so = _run_get_ventas()
    pend = by_so["SO_PENDIENTE"]
    assert pend["orden_real_subtotal_teoricos_bloqueado"] is False
    base_esperada = max(0.0, pend["venta_bruta_real"] - pend["descuento_motor_total"])
    esperado = round(base_esperada * multiplicador, 2)
    assert pend["orden_real_subtotal_teoricos"] == esperado
    assert pend["orden_real_subtotal_teoricos"] < pend["venta_bruta_real"] * multiplicador


def test_orden_real_subtotal_teoricos_no_resta_teorico_si_hay_sobre_descuento() -> None:
    """Si Odoo ya tiene aplicado MÁS descuento del que el motor calcula

    (sobre-descuento, Tarea 2), el subtotal no debe restar el teórico
    encima -- solo se le suman impuestos a venta_bruta_real, igual que
    venta_neta_real."""
    from cxc.config import AppConfig

    config = AppConfig.from_env()
    multiplicador = 1 + float(config.engine.iva_rate) + (
        float(config.engine.igtf_rate) if config.engine.igtf_activo else 0.0
    )

    # SO_OK: motor exige $5.00, Odoo ya tiene 20% ($20 sobre $100) -- sobre-descuento.
    lineas_sobre_descuento = [
        LineaOrden(
            linea_id="1",
            so_id="SO_OK",
            producto="1",
            marca="Sinoco",
            categoria="*",
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100"),
            descuento=Decimal("20.0"),
        ),
        *[ln for ln in _lineas_orden_estandar() if ln.so_id != "SO_OK"],
    ]
    by_so = _run_get_ventas(lineas=lineas_sobre_descuento)
    ok = by_so["SO_OK"]
    assert ok["orden_real_subtotal_teoricos_bloqueado"] is True
    esperado = round(ok["venta_bruta_real"] * multiplicador, 2)
    assert ok["orden_real_subtotal_teoricos"] == esperado


def test_nota_credito_reduce_facturado_neto_logica_reutilizada() -> None:
    """N/C (lógica ya existente): factura $200 - NC $30 = neto $170, sin N/D."""
    by_so = _run_get_ventas()
    nc = by_so["SO_NC"]
    assert nc["total_facturado_con_impuestos"] == 200.0
    assert nc["total_nc_aplicada"] == 30.0
    assert nc["total_nd_aplicada"] == 0.0
    assert nc["total_facturado_neto"] == 170.0


def test_descuento_pendiente_aplicar_dinamico_lo_reduce_una_nota_de_credito() -> None:
    """Fase 6: el pendiente es dinámico -- una N/C que materializa el

    descuento (sin tocar la línea de orden/factura) también lo resta,
    igual que si se hubiera aplicado en la línea."""
    by_so = _run_get_ventas()
    nc = by_so["SO_NC"]
    # motor exige 30; SO_NC no tiene descuento en la línea de orden/factura
    # (0), pero la N/C ($30) cubre exactamente ese monto -> pendiente = 0,
    # NO 30 (que es lo que hubiera dado la lógica vieja, que ignoraba NC).
    assert nc["descuento_motor_total"] == 30.0
    assert nc["descuento_aplicado_orden"] == 0.0
    assert nc["descuento_aplicado_factura"] == 0.0
    assert nc["descuento_pendiente_aplicar"] == 0.0


def test_descuento_pendiente_aplicar_dinamico_lo_reduce_descuento_de_sistema() -> None:
    """Fase 6: aprobar un descuento de sistema también resta del pendiente,

    sin tocar Odoo -- dinámico igual que la N/C."""
    by_so = _run_get_ventas(
        descuentos_sistema=[
            {
                "so_id": "SO_PENDIENTE",
                "monto": "4.00",
                "motivo": "Ajuste manual",
                "aprobado_por": "Tester",
                "timestamp_aprobacion": "2026-07-01T00:00:00",
                "activo": "true",
            }
        ]
    )
    pend = by_so["SO_PENDIENTE"]
    # motor=10; SO_PENDIENTE ya está facturada -> referencia = factura (3,
    # copiado de la orden) -> 10-3=7 antes del ajuste; menos 4 aprobados = 3.
    assert pend["descuento_pendiente_aplicar"] == 3.0


def test_nota_debito_atada_a_factura_incrementa_facturado_neto() -> None:
    """Lista VES: N/D $20 atada a la factura de SO_ND -> neto = 90 - 0 + 20 = 110."""
    by_so = _run_get_ventas()
    nd = by_so["SO_ND"]
    assert nd["total_facturado_con_impuestos"] == 90.0
    assert nd["total_nc_aplicada"] == 0.0
    assert nd["total_nd_aplicada"] == 20.0
    assert nd["total_facturado_neto"] == 110.0


def test_descuento_aplicado_sistema_cero_sin_aprobaciones() -> None:
    """Fase 3: sin aprobaciones en descuentos_sistema_aprobados, el campo es 0.0

    (ya no None -- la lógica de Facturación existe, simplemente no hay nada
    aprobado para estas órdenes de prueba)."""
    by_so = _run_get_ventas()
    for it in by_so.values():
        assert it["descuento_aplicado_sistema"] == 0.0
        assert it["saldo_pendiente_cxc"] >= 0.0


def test_descuento_aplicado_sistema_ajusta_saldo_pendiente_cxc() -> None:
    """Un descuento de sistema activo se refleja en el monto Y en

    saldo_pendiente_cxc (target real de CxC), sin tocar Odoo."""
    by_so = _run_get_ventas(
        descuentos_sistema=[
            {
                "so_id": "SO_OK",
                "monto": "10.00",
                "motivo": "Ajuste de prueba",
                "aprobado_por": "Tester",
                "timestamp_aprobacion": "2026-07-01T00:00:00",
                "activo": "true",
            },
            # Registro revocado -- NO debe afectar SO_PENDIENTE.
            {
                "so_id": "SO_PENDIENTE",
                "monto": "50.00",
                "motivo": "Revocado",
                "aprobado_por": "Tester",
                "timestamp_aprobacion": "2026-07-01T00:00:00",
                "activo": "false",
            },
        ]
    )
    ok = by_so["SO_OK"]
    assert ok["descuento_aplicado_sistema"] == 10.0
    assert ok["descuento_aplicado_sistema_motivo"] == "Ajuste de prueba"
    # SO_OK está facturada (tiene_factura=True) -> saldo = total_facturado_neto - 10
    assert ok["saldo_pendiente_cxc"] == round(ok["total_facturado_neto"] - 10.0, 2)

    pend = by_so["SO_PENDIENTE"]
    assert pend["descuento_aplicado_sistema"] == 0.0


def test_post_aprobar_descuento_sistema_nunca_toca_odoo() -> None:
    """El endpoint de aprobación solo persiste en el repo -- no debe

    invocar Odoo/_connect en absoluto (invariante: Odoo es solo lectura)."""
    mock_repo = MagicMock()
    # Tarea 2 (agosto 2026): el endpoint ahora chequea sobre-descuento antes
    # de aprobar -- get_orden=None hace que ese chequeo (pura lectura del
    # espejo, sin Odoo) salga temprano sin necesitar mockear facturas/líneas.
    mock_repo.get_orden.return_value = None

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.post(
            "/api/facturacion/aprobar-descuento-sistema",
            json={
                "so_id": "SO_APROBAR",
                "monto": 15.5,
                "motivo": "Ajuste por reclamo del cliente",
                "aprobado_por": "gerencia@lubrikca.com",
            },
        )

    assert res.status_code == 200
    assert res.json()["status"] == "success"
    mock_repo.upsert_descuento_sistema_aprobado.assert_called_once()
    saved_row = mock_repo.upsert_descuento_sistema_aprobado.call_args[0][0]
    assert saved_row["so_id"] == "SO_APROBAR"
    assert saved_row["monto"] == "15.5"
    assert saved_row["motivo"] == "Ajuste por reclamo del cliente"
    assert saved_row["activo"] == "true"


def _vinc(so_id: str, *, equiv_bcv=None, equiv_binance=None, monto="0") -> Vinculacion:
    return Vinculacion(
        vinc_id=f"VC_{so_id}",
        pago_id=f"PG_{so_id}",
        so_id=so_id,
        monto_aplicado=Decimal(monto),
        hora_pago_confirmada=datetime(2026, 7, 1, 10, 0),
        tasa_bcv_aplicada=Decimal("40.0"),
        tasa_binance_aplicada=Decimal("42.0"),
        es_tasa_heredada=False,
        equiv_usd_bcv=Decimal(str(equiv_bcv)) if equiv_bcv is not None else None,
        equiv_usd_binance=Decimal(str(equiv_binance)) if equiv_binance is not None else None,
        # Fase 0 (arquitectura de pagos): estos tests construyen un pago ya
        # confirmado -- CONCILIADO explícito, si no el default del dataclass
        # (PENDIENTE) haría que Ventas ya no lo cuente como pagado real.
        estado=EstadoVinculacion.CONCILIADO,
    )


def test_estatus_pago_real_orden_lista_usd_usa_binance_pagada() -> None:
    """SO_OK nace en lista "4" (USD, ver default de get_ui_pricelist_ids) --

    el estatus real debe comparar contra el equivalente Binance, no BCV."""
    by_so = _run_get_ventas(
        vinculaciones=[_vinc("SO_OK", equiv_binance=95.0, equiv_bcv=40.0)]
    )
    ok = by_so["SO_OK"]
    assert ok["estatus_pago_real_orden"] == "pagada"
    assert ok["estatus_pago_real_factura"] == "pagada"


def test_estatus_pago_real_orden_lista_ves_usa_bcv_parcial() -> None:
    """SO_ND nace en lista "5" (VES) -- el estatus real debe comparar

    contra el equivalente BCV, no Binance. Con solo la mitad pagado en BCV
    queda "parcial"."""
    by_so = _run_get_ventas(
        vinculaciones=[_vinc("SO_ND", equiv_bcv=55.0, equiv_binance=999.0)]
    )
    nd = by_so["SO_ND"]
    # venta_neta_real de SO_ND = 90.0 (monto_total) -> 55 < 90 - eps.
    assert nd["estatus_pago_real_orden"] == "parcial"


def test_estatus_pago_sin_pago_y_sin_factura() -> None:
    """Sin ninguna vinculación: sin_pago en real orden; SO_PENDIENTE (no

    facturada por diseño del fixture -- amount_total_signed_usd solo cubre
    parte de las órdenes) usa "sin_factura" cuando total_facturado_con_impuestos
    es 0."""
    by_so = _run_get_ventas()
    ok = by_so["SO_OK"]
    assert ok["estatus_pago_real_orden"] == "sin_pago"
    # SO_OK tiene teorico_lista_ves/_usd explícitos y no nulos (120/100,
    # ver fixture) -- sin pagos, el estado real es "sin_pago", determinista.
    assert ok["estatus_pago_teorico_ves"] == "sin_pago"
    assert ok["estatus_pago_teorico_usd"] == "sin_pago"


def test_estatus_pago_teorico_sin_datos_si_bandeja_no_calculo_ese_teorico() -> None:
    """Fase 9 -- bug real (S00696): BandejaFacturacion puede existir con

    precio_base_calculado > 0 (el cálculo real SÍ corrió) pero su teórico
    VES/USD en 0 (hueco de datos, ej. KeyError silencioso por falta de
    precio de algún producto en esa lista específica) -- eso NO es "nada
    que pagar", es "no sabemos" -- debe ser "sin_datos", nunca "pagada"
    (antes el piso target<=EPS de _estado_pago lo marcaba "pagada" sin
    importar el pago real, aunque la orden tuviera saldo pendiente real)."""
    by_so = _run_get_ventas()
    nd = by_so["SO_ND"]
    # SO_ND: precio_base_calculado=90 (bandeja real), pero sin
    # teorico_lista_ves/_usd explícitos en el fixture -> quedan en 0.
    assert nd["estatus_pago_teorico_ves"] == "sin_datos"
    assert nd["estatus_pago_teorico_usd"] == "sin_datos"


def test_estatus_pago_real_factura_descuenta_descuento_de_sistema() -> None:
    """Un descuento de sistema aprobado reduce el target -- una orden que

    antes estaba "parcial" puede pasar a "pagada" si el descuento cubre la
    diferencia."""
    by_so = _run_get_ventas(
        descuentos_sistema=[
            {
                "so_id": "SO_ND",
                "monto": "35.00",
                "motivo": "Ajuste",
                "aprobado_por": "Tester",
                "timestamp_aprobacion": "2026-07-01T00:00:00",
                "activo": "true",
            }
        ],
        vinculaciones=[_vinc("SO_ND", equiv_bcv=55.0)],
    )
    nd = by_so["SO_ND"]
    # target_orden = 90 - 35 = 55; pagado = 55 -> pagada (antes era "parcial").
    assert nd["estatus_pago_real_orden"] == "pagada"


def test_estatus_pago_usa_pagos_odoo_directos_sin_vinculaciones() -> None:
    """Fase 7: bug real -- la tabla Vinculaciones está vacía en producción,

    así que TODAS las órdenes salían "sin pagar" en Ventas aunque el
    reporte de CxC (que lee directo de Odoo) sí las mostraba pagadas. Esta
    orden está 100% pagada vía account.payment reconciliado (sin ninguna
    Vinculacion) -- debe salir "pagada", no "sin_pago"."""

    def _fake_execute_pago_odoo(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_PAGADA_ODOO", "state": "sale", "amount_untaxed": 90.0}]
        if model == "account.move":
            return [
                {
                    "id": 950,
                    "invoice_origin": "SO_PAGADA_ODOO",
                    "move_type": "out_invoice",
                    "amount_untaxed_signed_usd": 90.0,
                    "amount_total_signed_usd": 100.0,
                    "amount_total": 100.0,
                    "amount_residual": 0.0,
                    "currency_id": [1, "USD"],
                    "invoice_date": "2026-07-05",
                }
            ]
        if model == "account.payment":
            return [
                {
                    "id": 1,
                    "amount": 100.0,
                    "currency_id": [1, "USD"],
                    "date": "2026-07-05",
                    "reconciled_invoice_ids": [950],
                }
            ]
        if model == "sale.order.line":
            return []
        return []

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_vinculaciones.return_value = []  # sin Vinculaciones (caso real)
    mock_repo.all_entregas.return_value = []
    mock_repo.all_catalogo.return_value = []
    # Fase 2: facturado/invoice_ids_all/inv_id_to_so ahora vienen del
    # espejo Factura, no de account.move en vivo.
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="950",
            numero="FAC/950",
            so_id="SO_PAGADA_ODOO",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 5),
            moneda="USD",
            monto_total=Decimal("100.00"),
            monto_sin_impuestos=Decimal("90.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("100.00"),
            monto_sin_impuestos_signed_usd=Decimal("90.00"),
        )
    ]
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_PAGADA_ODOO",
            cliente_id="CLI_ODOO",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        )
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_PAGADA_ODOO",
            lista_aplicada="4",
            precio_base_calculado=Decimal("100.00"),
            total_motor=Decimal("100.00"),
        ),
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute_pago_odoo),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = next(it for it in res.json()["items"] if it["so_id"] == "SO_PAGADA_ODOO")

    assert item["estatus_pago_real_orden"] == "pagada"
    assert item["estatus_pago_real_factura"] == "pagada"


def test_revisar_motivo_devolucion_entrega_de_mas_y_cancelada_sin_devolver() -> None:
    """Alerta "Revisar" -- 3 casos reales encontrados en producción (SO 465/485):

    devolución registrada, entrega de más (cantidad_entregada > cantidad
    pedida), y orden cancelada en Odoo cuya mercancía nunca fue devuelta.
    Una orden sin ninguno de los 3 casos debe salir con revisar_motivo=None.
    """
    from cxc.models import LineaOrden

    def _fake_execute_revisar(model, method, args, kwargs=None):
        if model == "sale.order":
            return [
                {"name": "SO_DEVUELTA", "state": "sale", "amount_untaxed": 100.0},
                {"name": "SO_ENTREGA_MAS", "state": "sale", "amount_untaxed": 100.0},
                {"name": "SO_CANCELADA_SIN_DEV", "state": "cancel", "amount_untaxed": 100.0},
                {"name": "SO_LIMPIA", "state": "sale", "amount_untaxed": 100.0},
            ]
        if model == "sale.order" and method == "search_read":
            pass
        if model == "sale.order.line":
            return []
        if model == "account.move":
            return []
        if model == "account.move.line":
            return []
        # get_live_delivered_not_returned: SO_CANCELADA_SIN_DEV tiene un
        # picking de salida "done" sin devolución -- es la excepción de
        # negocio que la deja pasar el filtro de orden_excluida.
        if model == "sale.order" and "picking_ids" in str(kwargs):
            return []
        return []

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_vinculaciones.return_value = []

    def _orden_revisar(so_id, *, tiene_devolucion=False, estado_orden="sale"):
        return OrdenVenta(
            so_id=so_id,
            cliente_id="CLI_REV",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden=estado_orden,
            facturada=False,
            tiene_devolucion=tiene_devolucion,
        )

    mock_repo.all_ordenes.return_value = [
        _orden_revisar("SO_DEVUELTA", tiene_devolucion=True),
        _orden_revisar("SO_ENTREGA_MAS"),
        _orden_revisar("SO_CANCELADA_SIN_DEV", estado_orden="cancel"),
        _orden_revisar("SO_LIMPIA"),
    ]
    mock_repo.all_ventas_teoricos.return_value = []
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_catalogo.return_value = []
    # Fase 2: entregas/devoluciones ahora se leen del espejo Entrega (repo),
    # no de get_live_entregas_info -- ver _entregas_desde_espejo.
    # SO_CANCELADA_SIN_DEV tiene un picking de salida "done" sin devolución
    # -- es la excepción de negocio que la deja pasar el filtro de
    # orden_excluida.
    mock_repo.all_entregas.return_value = [
        Entrega(
            entrega_id="900",
            so_id="SO_CANCELADA_SIN_DEV",
            tipo="outgoing",
            fecha=None,
            estado="done",
            es_devolucion=False,
        )
    ]
    mock_repo.all_lineas.return_value = [
        LineaOrden(
            linea_id="L_MAS",
            so_id="SO_ENTREGA_MAS",
            producto="101",
            marca="Sinoco",
            categoria="Comercial",
            cantidad=Decimal("10"),
            precio_unitario=Decimal("50"),
            cantidad_entregada=Decimal("12"),
        ),
        LineaOrden(
            linea_id="L_LIMPIA",
            so_id="SO_LIMPIA",
            producto="101",
            marca="Sinoco",
            categoria="Comercial",
            cantidad=Decimal("10"),
            precio_unitario=Decimal("50"),
            cantidad_entregada=Decimal("10"),
        ),
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute_revisar),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        by_so = {it["so_id"]: it for it in res.json()["items"]}

    assert "Devolución" in by_so["SO_DEVUELTA"]["revisar_motivo"]
    assert "Entrega de más" in by_so["SO_ENTREGA_MAS"]["revisar_motivo"]
    assert "cancelada" in by_so["SO_CANCELADA_SIN_DEV"]["revisar_motivo"].lower()
    assert by_so["SO_LIMPIA"]["revisar_motivo"] is None


def test_lista_aplicada_label_indica_lista_historica_para_ordenes_de_la_ventana() -> None:
    """Órdenes anteriores a S00092/13-3-2026 (o sin lista de precios

    asignada) usan la Lista Histórica de Auditoría (Euro) para el cálculo
    VES -- antes esto quedaba invisible en "lista aplicada" cuando la orden
    no tenía ``lista_precios`` asignada (label salía en blanco)."""

    def _fake_execute_hist(model, method, args, kwargs=None):
        if model == "sale.order":
            return [
                {"name": "SO_HIST_SIN_LISTA", "state": "sale", "amount_untaxed": 100.0},
                {"name": "SO_HIST_CON_LISTA", "state": "sale", "amount_untaxed": 100.0},
                {"name": "SO_NO_HIST", "state": "sale", "amount_untaxed": 100.0},
            ]
        if model == "product.pricelist":
            return [{"id": 3, "name": "USD"}]
        return []

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_lineas.return_value = []
    mock_repo.all_ventas_teoricos.return_value = []
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_ordenes.return_value = [
        # Sin lista asignada -- histórica INCONDICIONAL (cualquier fecha).
        OrdenVenta(
            so_id="SO_HIST_SIN_LISTA",
            cliente_id="CLI_HIST",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 3, 5),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
        # Con lista asignada pero fecha dentro de la ventana histórica.
        OrdenVenta(
            so_id="SO_HIST_CON_LISTA",
            cliente_id="CLI_HIST",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 3, 1),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="3",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
        # Fuera de la ventana -- como S00092 (13-3-2026 en adelante).
        OrdenVenta(
            so_id="SO_NO_HIST",
            cliente_id="CLI_HIST",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 3, 13),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="3",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute_hist),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        by_so = {it["so_id"]: it for it in res.json()["items"]}

    assert "Lista Histórica de Auditoría" in by_so["SO_HIST_SIN_LISTA"]["lista_aplicada_label"]
    assert "Lista Histórica de Auditoría" in by_so["SO_HIST_CON_LISTA"]["lista_aplicada_label"]
    # Con lista asignada, el label combina el id/nombre real + la nota histórica.
    assert "#3" in by_so["SO_HIST_CON_LISTA"]["lista_aplicada_label"]
    assert "Lista Histórica de Auditoría" not in (by_so["SO_NO_HIST"]["lista_aplicada_label"] or "")


def test_revisar_motivo_dias_credito_excede_maximo_por_volumen() -> None:
    """Regla 0-500L->21d: una orden de 600L (rango 501-2500L, max 30d) con

    45 días de crédito otorgados por Odoo debe salir marcada "Revisar"; una
    orden de 600L con 25 días (dentro del máximo) no debe marcarse."""
    from cxc.models import LineaOrden

    def _fake_execute_credito(model, method, args, kwargs=None):
        if model == "sale.order":
            return [
                {
                    "name": "SO_EXCEDE",
                    "state": "sale",
                    "amount_untaxed": 100.0,
                    "payment_term_id": [1, "45 días"],
                },
                {
                    "name": "SO_OK_CREDITO",
                    "state": "sale",
                    "amount_untaxed": 100.0,
                    "payment_term_id": [2, "25 días"],
                },
            ]
        if model in ("sale.order.line", "account.move", "account.move.line"):
            return []
        return []

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ventas_teoricos.return_value = []
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_entregas.return_value = []
    # Fase 2: litros ahora se leen del espejo Producto (repo), no de
    # product.template en vivo -- ver _litros_por_so_desde_espejo.
    mock_repo.all_catalogo.return_value = [
        Producto(
            producto_id="500",
            codigo="P500",
            nombre="Producto 500",
            marca="",
            volumen=Decimal("60.0"),
            peso=Decimal("0"),
        )
    ]
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_EXCEDE",
            cliente_id="CLI_CREDITO",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
        OrdenVenta(
            so_id="SO_OK_CREDITO",
            cliente_id="CLI_CREDITO",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
    ]
    # 10 cajas * 60L/caja = 600L -> rango 501-2500L, max 30 días.
    mock_repo.all_lineas.return_value = [
        LineaOrden(
            linea_id="L1",
            so_id="SO_EXCEDE",
            producto="500",
            marca="Sinoco",
            categoria="Comercial",
            cantidad=Decimal("10"),
            precio_unitario=Decimal("50"),
        ),
        LineaOrden(
            linea_id="L2",
            so_id="SO_OK_CREDITO",
            producto="500",
            marca="Sinoco",
            categoria="Comercial",
            cantidad=Decimal("10"),
            precio_unitario=Decimal("50"),
        ),
    ]
    mock_repo.all_reglas_dias_credito_volumen.return_value = [
        {
            "regla_id": "CREDITO_0_500L",
            "litros_minimo": "0",
            "litros_maximo": "500",
            "dias_credito_max": "21",
            "activo": "true",
        },
        {
            "regla_id": "CREDITO_501L_2500L",
            "litros_minimo": "501",
            "litros_maximo": "2500",
            "dias_credito_max": "30",
            "activo": "true",
        },
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute_credito),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        by_so = {it["so_id"]: it for it in res.json()["items"]}

    assert "Días de crédito excede el máximo" in by_so["SO_EXCEDE"]["revisar_motivo"]
    assert by_so["SO_OK_CREDITO"]["revisar_motivo"] is None


def test_descuento_aplicado_factura_convierte_ves_a_usd_con_ratio_de_la_factura() -> None:
    """Bug real (orden S00010): descuento_aplicado_factura leía

    account.move.line.price_unit/price_subtotal tal cual, en la moneda de
    la FACTURA (a veces VES) -- salía en miles/millones en la tabla de
    Ventas aunque el modal de detalle (que sí convierte) mostraba bien."""

    def _fake_execute_ves(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_VES_FACT", "state": "sale", "amount_untaxed": 100.0}]
        return []

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_lineas.return_value = []
    mock_repo.all_ventas_teoricos.return_value = []
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_entregas.return_value = []
    mock_repo.all_catalogo.return_value = []
    # Fase 4/5: descuento_aplicado_factura ahora viene del espejo
    # LineaFactura -- línea "Descuento" separada (price_subtotal negativo),
    # ratio USD lo calcula _facturacion_por_so_desde_espejo desde
    # monto_total_signed_usd/monto_total de la factura (arriba).
    mock_repo.all_lineas_factura.return_value = [
        LineaFactura(
            linea_id="1",
            factura_id="1359",
            nombre="Descuento",
            cantidad=Decimal("1"),
            precio_unitario=Decimal("27708.028582"),
            descuento=Decimal("0"),
            subtotal=Decimal("-5000.0"),
        )
    ]
    # Fase 2: invoice_ids_all/inv_id_to_so/inv_usd_ratio_map ahora vienen
    # del espejo Factura, no de account.move en vivo.
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="1359",
            numero="FAC/1359",
            so_id="SO_VES_FACT",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 1),
            moneda="VES",
            monto_total=Decimal("47455.08"),
            monto_sin_impuestos=Decimal("40000.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("99.99"),
            monto_sin_impuestos_signed_usd=Decimal("90.00"),
        )
    ]
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_VES_FACT",
            cliente_id="CLI_VES",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        )
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute_ves),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = next(it for it in res.json()["items"] if it["so_id"] == "SO_VES_FACT")

    ratio = 99.99 / 47455.08
    esperado = round(5000.0 * ratio, 2)
    assert item["descuento_aplicado_factura"] == esperado
    assert item["descuento_aplicado_factura"] < 100.0  # NUNCA en miles/bolívares crudos


def test_dias_credito_y_fecha_entrega_en_ventas() -> None:
    """Nuevas columnas pedidas: días de crédito reales (payment_term de

    Odoo) y fecha de entrega efectiva (ALM/OUT), mismo criterio que ya usa
    el reporte de Cuentas por Cobrar."""

    def _fake_execute_credito_entrega(model, method, args, kwargs=None):
        if model == "sale.order" and method == "search_read":
            return [
                {
                    "name": "SO_CREDITO_ENTREGA",
                    "state": "sale",
                    "amount_untaxed": 100.0,
                    "payment_term_id": [7, "45 días"],
                }
            ]
        if model in ("sale.order.line", "account.move", "account.move.line"):
            return []
        return []

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_lineas.return_value = []
    mock_repo.all_ventas_teoricos.return_value = []
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_reglas_dias_credito_volumen.return_value = []
    mock_repo.all_catalogo.return_value = []
    # Fase 2: fecha_entrega ahora se lee del espejo Entrega (repo), no de
    # stock.picking en vivo -- ver _entregas_desde_espejo.
    mock_repo.all_entregas.return_value = [
        Entrega(
            entrega_id="900",
            so_id="SO_CREDITO_ENTREGA",
            tipo="outgoing",
            fecha=date(2026, 7, 10),
            estado="done",
            es_devolucion=False,
        )
    ]
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_CREDITO_ENTREGA",
            cliente_id="CLI_CE",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
            dias_credito=0,
        )
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute_credito_entrega),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = next(it for it in res.json()["items"] if it["so_id"] == "SO_CREDITO_ENTREGA")

    assert item["dias_credito"] == 45
    assert item["fecha_entrega"] == "2026-07-10"


def test_notas_debito_y_credito_reales_sin_invoice_origin_ni_out_invoice() -> None:
    """Bug real (orden S00357 y otras del mismo cliente, agosto 2026):

    verificado en vivo contra Odoo -- las N/D reales tienen
    move_type="out_debit" (NUNCA "out_invoice") y las N/C reales tienen
    invoice_origin=False, enlazadas a la factura original vía
    reversed_entry_id (NUNCA invoice_origin). Antes de este fix, ambas
    quedaban invisibles en /api/ventas."""

    def _fake_execute_nd_nc_reales(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_ND_NC", "state": "sale", "amount_untaxed": 3000.0}]
        if model in ("sale.order.line", "account.move.line"):
            return []
        return []

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_lineas.return_value = []
    mock_repo.all_ventas_teoricos.return_value = []
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_reglas_dias_credito_volumen.return_value = []
    mock_repo.all_entregas.return_value = []
    mock_repo.all_catalogo.return_value = []
    # Fase 2: facturado/NC/ND ahora vienen del espejo Factura -- el propio
    # espejo ya resuelve ND/NC sin invoice_origin propio vía la cadena
    # factura_origen_id (ver _facturacion_por_so_desde_espejo), replicando
    # lo que hacían las 2 consultas en vivo removidas de este fixture.
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="900",
            numero="00000052",
            so_id="SO_ND_NC",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 5, 14),
            moneda="USD",
            monto_total=Decimal("3082.00"),
            monto_sin_impuestos=Decimal("3000.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("3082.00"),
            monto_sin_impuestos_signed_usd=Decimal("3000.00"),
        ),
        # N/D real: move_type="out_debit", sin so_id propio -- se resuelve
        # vía factura_origen_id -> factura 900.
        Factura(
            factura_id="901",
            numero="ND/901",
            so_id=None,
            move_type="out_debit",
            es_nota_debito=True,
            fecha=date(2026, 5, 15),
            moneda="USD",
            monto_total=Decimal("375.94"),
            monto_sin_impuestos=Decimal("375.94"),
            estado="posted",
            factura_origen_id="900",
            monto_total_signed_usd=Decimal("375.94"),
            monto_sin_impuestos_signed_usd=Decimal("375.94"),
        ),
        # N/C real: invoice_origin vacío, enlazada vía reversed_entry_id.
        Factura(
            factura_id="6283",
            numero="RFAC/6283",
            so_id=None,
            move_type="out_refund",
            es_nota_debito=False,
            fecha=date(2026, 5, 15),
            moneda="USD",
            monto_total=Decimal("516.89"),
            monto_sin_impuestos=Decimal("516.89"),
            estado="posted",
            factura_origen_id="900",
            monto_total_signed_usd=Decimal("-516.89"),
            monto_sin_impuestos_signed_usd=Decimal("-516.89"),
        ),
    ]
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_ND_NC",
            cliente_id="CLI_ND_NC",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 5, 14),
            fecha_entrega=None,
            monto_total=Decimal("3457.96"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        )
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute_nd_nc_reales),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = next(it for it in res.json()["items"] if it["so_id"] == "SO_ND_NC")

    assert item["total_nd_aplicada"] == 375.94
    assert item["total_nc_aplicada"] == 516.89


def test_pagado_teorico_bcv_y_binance_usan_rutas_distintas_y_eur_para_historica() -> None:
    """Bug real reportado por el usuario (agosto 2026, cliente Emprendimiento

    Tomas Marcano 5 / orden real S00020): un pago en VES se restaba IGUAL
    de ambos teoricos (BCV y Binance daban el mismo numero), y para una
    orden de la ventana historica no se aplicaba la tasa BCV-EUR. Causa
    raiz: sin Vinculaciones, ``val_bcv``/``val_binance`` (que alimentan
    ``pagado_teorico_bcv``/``_binance`` y los estatus de pago teoricos)
    venian de ``pagos_odoo_map`` (mismo numero duplicado, sin ajuste EUR)
    en vez de ``pagos_bcv_binance_map`` (``_pagos_bcv_binance_por_orden``,
    ya usado para "Monto Pagado BCV/USD" -- ahora tambien para los
    teoricos)."""

    def _fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_HIST_PAGO", "state": "sale", "amount_untaxed": 100.0}]
        if model == "account.payment":
            return [
                {
                    "id": 3,
                    "amount": 16606.59,
                    "currency_id": [166, "VES"],
                    "date": "2026-03-09",
                    "reconciled_invoice_ids": [950],
                }
            ]
        if model in ("sale.order.line", "account.move.line"):
            return []
        return []

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_lineas.return_value = []
    mock_repo.all_ventas_teoricos.return_value = []
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_entregas.return_value = []
    mock_repo.all_catalogo.return_value = []
    # Fase 2: invoice_ids_all/inv_id_to_so ahora vienen del espejo
    # Factura -- _pagos_bcv_binance_por_orden (payment reconciliation,
    # dominio de Cobranza) se queda en vivo, pero necesita esos ids.
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="950",
            numero="FAC/950",
            so_id="SO_HIST_PAGO",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 3, 9),
            moneda="VES",
            monto_total=Decimal("16606.59"),
            monto_sin_impuestos=Decimal("14000.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("32.53"),
            monto_sin_impuestos_signed_usd=Decimal("28.00"),
        )
    ]
    mock_repo.all_tasas_historicas_auditoria.return_value = []
    mock_repo.all_serie_tasas.return_value = [
        SerieTasa(
            timestamp=datetime(2026, 3, 9, 10, 0, 0),
            tasa_bcv=Decimal("440.9657"),
            tasa_binance=Decimal("516.8118"),
            fuente="test",
            tasa_bcv_euro=Decimal("510.4884"),
        )
    ]
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_HIST_PAGO",
            cliente_id="CLI_HIST_PAGO",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 3, 9),
            fecha_entrega=None,
            monto_total=Decimal("100.00"),
            lista_precios="",  # sin lista -- histórica incondicional
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        )
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = next(it for it in res.json()["items"] if it["so_id"] == "SO_HIST_PAGO")

    # 16606.59 / 510.4884 (tasa BCV-EUR, no la BCV normal) ~= 32.53
    assert item["pagado_teorico_bcv"] == 32.53
    # 16606.59 / 516.8118 (tasa Binance normal, el Euro NO la sustituye)
    assert round(item["pagado_teorico_binance"], 2) == round(16606.59 / 516.8118, 2)
    # Las dos rutas dan numeros DISTINTOS -- ya no se duplica el mismo valor.
    assert item["pagado_teorico_bcv"] != item["pagado_teorico_binance"]
