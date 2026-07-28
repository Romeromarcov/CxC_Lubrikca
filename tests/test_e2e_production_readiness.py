from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.engine.runner import EngineRunner
from cxc.models import (
    BandejaFacturacion,
    DescuentoAplicado,
    DescuentoMarcaCategoria,
    DescuentoVolumen,
    EstadoVinculacion,
    LineaOrden,
    Moneda,
    OrdenVenta,
    Pago,
    Vinculacion,
)
from cxc.web.app import SECRET_KEY, app, crear_session_token

client = TestClient(app)


@patch(
    "cxc.web.app._connect",
    return_value=lambda model, method, args, kwargs={}: (
        [{"id": 1, "name": "Chevron"}, {"id": 2, "name": "Lubrikca"}]
        if model == "product.brand"
        else []
    ),
)
@patch("cxc.web.app.AppConfig.from_env")
def test_e2e_01_catalog_and_odoo_ingestion(mock_env, mock_conn):
    """Test 1: Ingestión de catálogo de Odoo (productos, marcas y categorías)."""
    res = client.get("/api/odoo/marcas")
    assert res.status_code == 200
    data = res.json()
    assert "Chevron" in data
    assert "Lubrikca" in data


def test_e2e_02_payment_loading_and_manual_allocation():
    """Test 2 & 3: Carga de pagos y asignación manual a órdenes de venta."""
    mock_repo = MagicMock()
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO100",
            cliente_id="CLI_001",
            vendedor_email="vendedor1@lubrikca.com",
            fecha=date(2026, 7, 10),
            fecha_entrega=date(2026, 7, 10),
            monto_total=Decimal("500.00"),
            lista_precios="4",
            es_primera_compra=False,
        )
    ]
    mock_pago = Pago(
        pago_id="PAGO_100",
        cliente_id="CLI_001",
        monto=Decimal("300.00"),
        moneda=Moneda.USD,
        metodo_pago="Zelle",
        fecha_pago=datetime(2026, 7, 11, 10, 0),
        vendedor_email="vendedor1@lubrikca.com",
    )
    mock_repo.get_pago.return_value = mock_pago
    mock_repo.all_pagos.return_value = [mock_pago]
    mock_repo.all_vinculaciones.return_value = []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect"),
        patch("cxc.web.app.recalculate_all"),
    ):
        payload = {"pago_id": "PAGO_100", "so_id": "SO100", "monto_aplicado": 300.00}

        res = client.post("/api/vincular", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        mock_repo.update_vinculacion.assert_called_once()


def test_e2e_04_discount_engine_evaluation():
    """Test 4: Evaluación completa del motor de 5 reglas de descuento."""
    orden = OrdenVenta(
        so_id="SO200",
        cliente_id="CLI_002",
        vendedor_email="vendedor2@lubrikca.com",
        fecha=date(2026, 7, 1),
        fecha_entrega=date(2026, 7, 1),
        monto_total=Decimal("1000.00"),
        lista_precios="4",
        es_primera_compra=False,
    )
    lineas = [
        LineaOrden(
            linea_id="L1",
            so_id="SO200",
            producto="Aceite Motor Chevron 20W50",
            marca="Chevron",
            categoria="Comercial",
            cantidad=Decimal("100.0"),
            precio_unitario=Decimal("10.00"),
        )
    ]
    descuentos_mc = [
        DescuentoMarcaCategoria(
            regla_id="R_MC_1",
            marca="Chevron",
            categoria="Comercial",
            tipo_descuento="marca",
            porcentaje=Decimal("0.05"),
            vigencia_desde=date(2026, 1, 1),
            activo=True,
        )
    ]
    descuentos_vol = [
        DescuentoVolumen(
            regla_id="R_VOL_1",
            marca="Chevron",
            categoria="Comercial",
            litros_minimo=Decimal("50.0"),
            porcentaje=Decimal("0.03"),
            vigencia_desde=date(2026, 1, 1),
            activo=True,
        )
    ]

    mock_repo = MagicMock()
    mock_repo.get_orden.return_value = orden
    mock_repo.lineas_de_orden.return_value = lineas
    mock_repo.vinculaciones_de_orden.return_value = []
    mock_repo.descuentos_marca_categoria.return_value = descuentos_mc
    mock_repo.descuentos_volumen.return_value = descuentos_vol
    mock_repo.descuentos_pronto_pago.return_value = []
    mock_repo.descuento_recompra.return_value = None
    mock_repo.descuento_diferencial_cambiario.return_value = None
    mock_repo.exclusiones.return_value = []
    mock_repo.feriados.return_value = []
    mock_repo.descuentos_producto.return_value = []
    mock_repo.promocion_primera_compra.return_value = None
    mock_repo.es_primera_compra_cliente.return_value = False

    from cxc.config import EngineConfig

    cfg = EngineConfig(
        cash_window_business_days=3, bcv_complete_formula="full", lista_usd="4", lista_bcv="5"
    )
    resolver = MagicMock()
    resolver.volumen.return_value = Decimal("100.0")
    resolver.precio.return_value = Decimal("10.00")
    runner = EngineRunner(mock_repo, resolver, cfg)
    res = runner.run_orden("SO200", date(2026, 7, 5))
    assert res is not None
    assert res.so_id == "SO200"


def test_e2e_05_reconciliation_trays():
    """Test 5: Clasificación automática de conciliaciones en las 3 Bandejas."""
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P1",
                "fecha": "2026-07-15",
                "monto": "100.0",
                "moneda": "USD",
                "vendedor": "vendedor1@lubrikca.com",
                "cliente_nombre": "Cliente A",
            }
        ]
        if sheet == "Pagos"
        else []
    )

    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO300",
            cliente_id="C3",
            vendedor_email="vendedor1@lubrikca.com",
            fecha=date(2026, 7, 10),
            fecha_entrega=date(2026, 7, 10),
            monto_total=Decimal("100.00"),
            lista_precios="4",
            es_primera_compra=False,
        )
    ]
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="V1",
            pago_id="P1",
            so_id="SO300",
            monto_aplicado=Decimal("100.00"),
            hora_pago_confirmada=datetime.now(),
            tasa_bcv_aplicada=Decimal("60.0"),
            tasa_binance_aplicada=Decimal("63.0"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.CONCILIADO,
        )
    ]
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_conciliaciones.return_value = []

    with patch("cxc.web.app.get_repo", return_value=mock_repo), patch("cxc.web.app._connect"):
        res = client.get("/api/bandeja")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, dict)
        assert "ordenes_por_facturar" in data
        assert "notas_credito_pendientes" in data
        assert "iva_pendiente_agentes" in data


def test_e2e_06_vendor_scoping_and_roles():
    """Test 6: Scoping por vendedor y permisos de rol en reportes/cobranza."""
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P_VEND_1",
                "fecha": "2026-07-15",
                "monto": "200.0",
                "moneda": "USD",
                "vendedor": "vendedor_juan@lubrikca.com",
                "cliente_nombre": "Cliente Juan",
            },
            {
                "pago_id": "P_VEND_2",
                "fecha": "2026-07-15",
                "monto": "500.0",
                "moneda": "USD",
                "vendedor": "vendedor_pedro@lubrikca.com",
                "cliente_nombre": "Cliente Pedro",
            },
        ]
        if sheet == "Pagos"
        else []
    )

    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []

    token = crear_session_token("vendedor_juan@lubrikca.com", SECRET_KEY)

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch(
            "cxc.web.app.get_current_user_from_cookie",
            return_value={
                "email": "vendedor_juan@lubrikca.com",
                "rol": "ventas",
                "nombre": "Juan Vendedor",
            },
        ),
    ):
        client.cookies.set("cxc_session", token)
        res = client.get("/api/cobranza")
        assert res.status_code == 200
        data = res.json()
        for item in data:
            assert item["vendedor"] == "vendedor_juan@lubrikca.com"


def test_e2e_07_receipt_generation():
    """Test 7: Generación de Recibo de Entrega (2 Copias PDF)."""
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P_REC_1",
                "fecha": "2026-07-18",
                "monto": "150.0",
                "moneda": "USD",
                "vendedor": "juan",
                "cliente_nombre": "Cliente Test",
                "recibido": "FALSE",
            }
        ]
        if sheet == "Pagos"
        else []
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []

    payload = {"pago_ids": ["P_REC_1"], "recibido_por": "Caja Principal"}

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.post("/api/cobranza/marcar-recibido", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert "numero_recibido" in data
        assert data["recibido_por"] == "Caja Principal"
        assert len(data["pagos"]) == 1


def test_e2e_08_executive_daily_report():
    """Test 8: Consolidados de Reporte Diario Ejecutivo (USD, Litros y Cobranza)."""
    mock_repo = MagicMock()
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_D1",
            cliente_id="C1",
            vendedor_email="v1",
            fecha=date(2026, 7, 18),
            fecha_entrega=date(2026, 7, 18),
            monto_total=Decimal("1200.00"),
            lista_precios="4",
            es_primera_compra=False,
        )
    ]
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [{"so_id": "SO_D1", "product_id": "101", "cantidad_entregada": "50"}]
        if sheet == "LineasOrden"
        else (
            [
                {
                    "pago_id": "P_D1",
                    "fecha_pago": "2026-07-18",
                    "monto": "600.0",
                    "moneda": "USD",
                    "metodo_pago": "Efectivo",
                    "vendedor_email": "v1",
                }
            ]
            if sheet == "Pagos"
            else []
        )
    )

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/reporte/diario")
        assert res.status_code == 200
        data = res.json()
        assert "ventas_diarias" in data
        assert "cobranza_diaria" in data
        assert "resumen" in data
        assert len(data["ventas_diarias"]) >= 1
        assert len(data["cobranza_diaria"]) >= 1
        # La cobranza debe quedar en el día real del pago (fecha_pago), no en "hoy".
        assert data["cobranza_diaria"][0]["fecha"] == "2026-07-18"
        assert data["cobranza_diaria"][0]["total_eq_bcv"] == 600.0

        # Filtro por vendedor: v1 sí tiene datos, v2 no debe traer nada.
        res_v1 = client.get("/api/reporte/diario?vendedor=v1")
        assert len(res_v1.json()["ventas_diarias"]) == 1
        res_v2 = client.get("/api/reporte/diario?vendedor=v2")
        assert len(res_v2.json()["ventas_diarias"]) == 0
        assert len(res_v2.json()["cobranza_diaria"]) == 0


def test_e2e_09_listas_precio_mapeo():
    """Test 9: Configuración y lectura de mapeo de Listas de Precios por vigencia."""
    import cxc.web.app as app_module

    # Clear in-process pricelist cache so mock data is actually read from Sheets
    app_module._PRICELIST_MAPEO_CACHE.clear()

    mock_repo = MagicMock()
    mock_repo._g.get_meta.side_effect = lambda key: (
        "4,6"
        if key == "valid_pricelists_usd"
        else ("5,7" if key == "valid_pricelists_ves" else None)
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._load_mapeo_from_json", return_value=None),
    ):
        res_get = client.get("/api/config/listas-precio-mapeo")
        assert res_get.status_code == 200
        data_get = res_get.json()
        assert data_get["valid_pricelists_usd"] == ["4", "6"]
        assert data_get["valid_pricelists_ves"] == ["5", "7"]

        # Clear cache again before POST test
        app_module._PRICELIST_MAPEO_CACHE.clear()

        payload = {"valid_pricelists_usd": ["4", "8"], "valid_pricelists_ves": ["5", "9"]}
        res_post = client.post("/api/config/listas-precio-mapeo", json=payload)
        assert res_post.status_code == 200
        resp_data = res_post.json()
        assert resp_data["status"] == "success"
        assert resp_data["valid_pricelists_usd"] == ["4", "8"]
        assert resp_data["valid_pricelists_ves"] == ["5", "9"]
        mock_repo._g.set_meta.assert_called()


def test_e2e_10_conciliaciones_sugerencias_and_bulk_approval():
    """Test 10: Sugerencias Inteligentes de Conciliación (FIFO por cliente) y Aprobación Masiva."""
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P_SUG_1",
                "cliente_id": "CLI_10",
                "monto": "500.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-15",
                "vendedor": "juan@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else ([{"cliente_id": "CLI_10", "nombre": "Cliente Diez"}] if sheet == "Clientes" else [])
    )

    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_FIFO_1",
            cliente_id="CLI_10",
            vendedor_email="juan@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=date(2026, 7, 1),
            monto_total=Decimal("300.00"),
            lista_precios="4",
            es_primera_compra=False,
        ),
        OrdenVenta(
            so_id="SO_FIFO_2",
            cliente_id="CLI_10",
            vendedor_email="juan@lubrikca.com",
            fecha=date(2026, 7, 10),
            fecha_entrega=date(2026, 7, 10),
            monto_total=Decimal("400.00"),
            lista_precios="4",
            es_primera_compra=False,
        ),
    ]
    mock_repo.get_pago.return_value = Pago(
        pago_id="P_SUG_1",
        cliente_id="CLI_10",
        monto=Decimal("500.00"),
        moneda=Moneda.USD,
        metodo_pago="Zelle",
        fecha_pago=datetime(2026, 7, 15, 10, 0),
        vendedor_email="juan@lubrikca.com",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.recalculate_all"),
    ):
        res_sug = client.get("/api/conciliaciones/sugerencias")
        assert res_sug.status_code == 200
        sug_data = res_sug.json()
        assert len(sug_data) == 2
        # First suggestion matches oldest order SO_FIFO_1
        assert sug_data[0]["so_id"] == "SO_FIFO_1"
        assert sug_data[0]["monto_sugerido"] == 300.0
        # Second suggestion matches remaining $200 of payment to SO_FIFO_2
        assert sug_data[1]["so_id"] == "SO_FIFO_2"
        assert sug_data[1]["monto_sugerido"] == 200.0

        bulk_payload = {
            "items": [
                {"pago_id": "P_SUG_1", "so_id": "SO_FIFO_1", "monto_aplicado": 300.0},
                {"pago_id": "P_SUG_1", "so_id": "SO_FIFO_2", "monto_aplicado": 200.0},
            ]
        }
        res_bulk = client.post("/api/vincular-masivo", json=bulk_payload)
        assert res_bulk.status_code == 200
        assert res_bulk.json()["procesados"] == 2
        assert mock_repo.update_vinculacion.call_count == 2


def test_e2e_11_regla_global_excluye_ordenes_no_confirmadas():
    """Regla global: /api/resumen no debe contar cotizaciones (draft/sent) ni

    órdenes canceladas como "Total por Cobrar" — solo órdenes confirmadas.
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_conciliaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_CONFIRMADA",
            cliente_id="CLI_11",
            vendedor_email="juan@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("1000.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
        ),
        OrdenVenta(
            so_id="SO_COTIZACION",
            cliente_id="CLI_11",
            vendedor_email="juan@lubrikca.com",
            fecha=date(2026, 7, 2),
            fecha_entrega=None,
            monto_total=Decimal("3_800_000.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="draft",
        ),
        OrdenVenta(
            so_id="SO_CANCELADA",
            cliente_id="CLI_11",
            vendedor_email="juan@lubrikca.com",
            fecha=date(2026, 7, 3),
            fecha_entrega=None,
            monto_total=Decimal("500.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="cancel",
        ),
    ]

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/resumen")
        assert res.status_code == 200
        # Solo SO_CONFIRMADA (sale) debe contarse; draft y cancel quedan fuera.
        assert res.json()["total_por_cobrar_usd"] == 1000.0


def test_e2e_12_bandeja1_agente_retencion_subtotal_pagado():
    """Bandeja 1: un agente de retención de IVA entra al pagar el Subtotal

    (falta solo el IVA retenido); un cliente normal con el mismo saldo
    pendiente NO debe entrar -- para él aplica la regla estándar (100%).
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "cliente_id": "C_AGENTE",
                "nombre": "Cliente Agente",
                "wh_iva_agent": "True",
                "wh_iva_rate": "100",
            },
            {"cliente_id": "C_NORMAL", "nombre": "Cliente Normal"},
        ]
        if sheet == "Clientes"
        else []
    )
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_AGENTE",
            cliente_id="C_AGENTE",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),
            lista_precios="4",
            es_primera_compra=False,
            facturada=False,
        ),
        OrdenVenta(
            so_id="SO_NORMAL",
            cliente_id="C_NORMAL",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),
            lista_precios="4",
            es_primera_compra=False,
            facturada=False,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    # Ambas órdenes recibieron $100 de abono (el subtotal sin IVA de $116 a
    # 16%); a ninguna le falta más que la porción de IVA ($16).
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="V_AGENTE",
            pago_id="P_AGENTE",
            so_id="SO_AGENTE",
            monto_aplicado=Decimal("100.00"),
            hora_pago_confirmada=datetime.now(),
            tasa_bcv_aplicada=Decimal("60.0"),
            tasa_binance_aplicada=Decimal("63.0"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.CONCILIADO,
        ),
        Vinculacion(
            vinc_id="V_NORMAL",
            pago_id="P_NORMAL",
            so_id="SO_NORMAL",
            monto_aplicado=Decimal("100.00"),
            hora_pago_confirmada=datetime.now(),
            tasa_bcv_aplicada=Decimal("60.0"),
            tasa_binance_aplicada=Decimal("63.0"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.CONCILIADO,
        ),
    ]

    with patch("cxc.web.app.get_repo", return_value=mock_repo), patch("cxc.web.app._connect"):
        res = client.get("/api/bandeja")
        assert res.status_code == 200
        data = res.json()
        so_ids = {item["so_id"] for item in data["ordenes_por_facturar"]}
        assert "SO_AGENTE" in so_ids
        assert "SO_NORMAL" not in so_ids


def test_e2e_13_bandeja2_concepto_real_de_nc():
    """Bandeja 2: el "concepto" de la N/C debe venir del detalle real del

    motor (obsequio de producto o % de primera compra), no de un texto
    genérico fijo.
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [{"cliente_id": "C_NC", "nombre": "Cliente NC"}] if sheet == "Clientes" else []
    )
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_NC",
            cliente_id="C_NC",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("500.00"),
            lista_precios="4",
            es_primera_compra=True,
            facturada=True,
            factura_id="FAC-001",
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_NC",
            lista_aplicada="4",
            precio_base_calculado=Decimal("500.00"),
            descuentos_detalle=[
                DescuentoAplicado(
                    origen="primera_compra",
                    descripcion="NC obsequio (Aceite 20W50 Caja)",
                    monto=Decimal("45.00"),
                )
            ],
            total_descuentos=Decimal("0"),
            ncs_calculadas=Decimal("45.00"),
            total_motor=Decimal("455.00"),
        ),
    ]
    mock_repo.all_vinculaciones.return_value = []

    with patch("cxc.web.app.get_repo", return_value=mock_repo), patch("cxc.web.app._connect"):
        res = client.get("/api/bandeja")
        assert res.status_code == 200
        nc_items = res.json()["notas_credito_pendientes"]
        assert len(nc_items) == 1
        assert nc_items[0]["so_id"] == "SO_NC"
        assert nc_items[0]["concepto"] == "NC obsequio (Aceite 20W50 Caja)"
        assert nc_items[0]["nc_monto"] == 45.0


def test_e2e_14_bandeja3_iva_estimado_sobre_total_motor_no_bruto():
    """Bandeja 3: el 16% de IVA se estima sobre el total del MOTOR

    (ya con descuentos aplicados), no sobre el monto bruto original de la
    orden -- de lo contrario, órdenes con descuentos grandes sobreestiman
    cuánto IVA se retuvo y esconden un saldo real pendiente como si solo
    faltara el comprobante.
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "cliente_id": "C_AGENTE2",
                "nombre": "Cliente Agente 2",
                "wh_iva_agent": "True",
                "wh_iva_rate": "100",
            }
        ]
        if sheet == "Clientes"
        else []
    )
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_IVA2",
            cliente_id="C_AGENTE2",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("300.00"),  # monto bruto -- con descuento grande aplicado
            lista_precios="4",
            es_primera_compra=False,
            facturada=True,
            factura_id="FAC-002",
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_IVA2",
            lista_aplicada="4",
            precio_base_calculado=Decimal("300.00"),
            total_descuentos=Decimal("184.00"),
            ncs_calculadas=Decimal("0"),
            total_motor=Decimal("116.00"),  # subtotal $100 + IVA 16% ($16)
        ),
    ]
    # Pagó $90 de los $116 reales -- le faltan $26, pero la retención de IVA
    # (100% de $16) es solo $16: hay un saldo real de $10 más allá del IVA.
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="V_IVA2",
            pago_id="P_IVA2",
            so_id="SO_IVA2",
            monto_aplicado=Decimal("90.00"),
            hora_pago_confirmada=datetime.now(),
            tasa_bcv_aplicada=Decimal("60.0"),
            tasa_binance_aplicada=Decimal("63.0"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.CONCILIADO,
        ),
    ]

    with patch("cxc.web.app.get_repo", return_value=mock_repo), patch("cxc.web.app._connect"):
        res = client.get("/api/bandeja")
        assert res.status_code == 200
        so_ids = {item["so_id"] for item in res.json()["iva_pendiente_agentes"]}
        # No debe entrar: el saldo pendiente ($26) excede la retención real
        # de IVA sobre el total del motor ($16), aunque calculado sobre el
        # monto bruto ($300) sí hubiera "cabido" (bug que se corrige aquí).
        assert "SO_IVA2" not in so_ids
