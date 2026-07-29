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
    SerieTasa,
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


def test_e2e_08b_reporte_diario_resumen_cobranza_desglose_metodo_y_ves():
    """El resumen de cobranza (tarjetas Hoy/Mes/Trimestre/Año) debe

    desglosar por método de pago y separar lo cobrado en VES con su
    equivalente en USD a la tasa BCV -- no solo un total agregado.
    """
    mock_repo = MagicMock()
    mock_repo.all_ordenes.return_value = []
    hoy = date.today().isoformat()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P_USD",
                "fecha_pago": hoy,
                "monto": "100.0",
                "moneda": "USD",
                "metodo_pago": "Efectivo",
            },
            {
                "pago_id": "P_VES",
                "fecha_pago": hoy,
                "monto": "4000.0",
                "moneda": "VES",
                "metodo_pago": "Banco Bancamiga",
            },
        ]
        if sheet == "Pagos"
        else (
            [{"timestamp": f"{hoy} 12:00:00", "tasa_bcv": "40.0", "tasa_binance": "42.0"}]
            if sheet == "SerieTasas"
            else []
        )
    )

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/reporte/diario")
        assert res.status_code == 200
        cobranza_hoy = res.json()["resumen"]["cobranza"]["hoy"]
        # VES: 4000 Bs -> $100 equivalente a tasa 40.
        assert cobranza_hoy["ves_monto"] == 4000.0
        assert cobranza_hoy["ves_eq_usd"] == 100.0
        assert cobranza_hoy["por_metodo"]["Efectivo"] == 100.0
        assert cobranza_hoy["por_metodo"]["Banco Bancamiga"] == 100.0
        assert cobranza_hoy["total_eq_bcv"] == 200.0


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


def test_e2e_15_pagos_sin_asignar_usd_y_ves_saldo_parcial():
    """Tarjeta "Pagos Sin Asignar": debe reportar USD y VES, y usar el

    SALDO real (no todo-o-nada) -- un pago parcialmente vinculado sigue
    contando por lo que le queda pendiente, y uno vinculado al 100% no
    cuenta nada.
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            # Sin vincular en absoluto: USD $200 completos pendientes.
            {
                "pago_id": "P_LIBRE",
                "cliente_id": "C1",
                "monto": "200.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-01",
            },
            # Vinculado parcialmente: de VES 6000, ya se asignaron 2000 ->
            # quedan 4000 VES pendientes.
            {
                "pago_id": "P_PARCIAL",
                "cliente_id": "C1",
                "monto": "6000.0",
                "moneda": "VES",
                "fecha_pago": "2026-07-01",
            },
            # Vinculado al 100%: no debe sumar nada.
            {
                "pago_id": "P_COMPLETO",
                "cliente_id": "C1",
                "monto": "50.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-01",
            },
        ]
        if sheet == "Pagos"
        else (
            [{"timestamp": "2026-07-01 12:00:00", "tasa_bcv": "40.0", "tasa_binance": "42.0"}]
            if sheet == "SerieTasas"
            else []
        )
    )
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="V_PARCIAL",
            pago_id="P_PARCIAL",
            so_id="SO1",
            monto_aplicado=Decimal("2000.00"),
            hora_pago_confirmada=datetime.now(),
            tasa_bcv_aplicada=Decimal("40.0"),
            tasa_binance_aplicada=Decimal("42.0"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.CONCILIADO,
        ),
        Vinculacion(
            vinc_id="V_COMPLETO",
            pago_id="P_COMPLETO",
            so_id="SO2",
            monto_aplicado=Decimal("50.00"),
            hora_pago_confirmada=datetime.now(),
            tasa_bcv_aplicada=Decimal("40.0"),
            tasa_binance_aplicada=Decimal("42.0"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.CONCILIADO,
        ),
    ]
    mock_repo.all_conciliaciones.return_value = []

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/resumen")
        assert res.status_code == 200
        data = res.json()
        # USD: $200 (libre) + 4000 VES / 40 = $100 equivalente = $300.
        assert data["pagos_sin_asignar_usd"] == 300.0
        # VES: 200 USD * 40 = 8000 Bs + 4000 Bs restantes = 12000 Bs.
        assert data["pagos_sin_asignar_ves"] == 12000.0


def test_e2e_16_sugerencias_orden_facturada_no_pagada_sigue_siendo_destino():
    """Conciliaciones: una orden ya facturada sigue siendo destino válido de

    sugerencia mientras su factura en Odoo tenga residual > 0
    (amount_residual_usd, el campo firmado en USD -- no se recalcula la
    conversión con una tasa propia). Una vez el residual llega a 0, ya no
    debe sugerirse (antes cualquier orden facturada quedaba excluida por
    completo, sin importar su estado de pago real en Odoo).
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P_FACT",
                "cliente_id": "C_FACT",
                "monto": "100.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-10",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C_FACT", "nombre": "Cliente Facturado"}]
            if sheet == "Clientes"
            else []
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_FACT_NOPAGA",
            cliente_id="C_FACT",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=date(2026, 7, 1),
            monto_total=Decimal("100.00"),
            lista_precios="4",
            es_primera_compra=False,
            facturada=True,
        ),
        OrdenVenta(
            so_id="SO_FACT_PAGADA",
            cliente_id="C_FACT",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 6, 1),
            fecha_entrega=date(2026, 6, 1),
            monto_total=Decimal("100.00"),
            lista_precios="4",
            es_primera_compra=False,
            facturada=True,
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return []
        if model == "sale.order":
            return [
                {"name": "SO_FACT_NOPAGA", "state": "sale"},
                {"name": "SO_FACT_PAGADA", "state": "sale"},
            ]
        if model == "account.move":
            return [
                {"invoice_origin": "SO_FACT_NOPAGA", "amount_residual_usd": 100.0},
                {"invoice_origin": "SO_FACT_PAGADA", "amount_residual_usd": 0.0},
            ]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=fake_execute),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        data = res.json()
        so_ids = {item["so_id"] for item in data}
        assert "SO_FACT_NOPAGA" in so_ids
        assert "SO_FACT_PAGADA" not in so_ids


def test_e2e_17_pagos_historial_incluye_conciliados_directo_en_odoo():
    """Tabla de pagos conciliados: debe incluir tanto los vinculados por

    este sistema como los reconciliados directamente en Odoo vía factura,
    con TODAS sus facturas asociadas (un pago puede conciliar varias),
    monto conciliado y residual -- una sola fila por pago, no una por
    orden. Bug corregido: se leía "invoice_ids" de account.payment (SIEMPRE
    vacío en pagos reconciliados por matching de banco/manual -- el caso
    normal); el campo correcto es "reconciled_invoice_ids". Verificado en
    vivo: de 673 pagos reconciliados en producción, 0 tenían invoice_ids
    poblado.
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [{"cliente_id": "10", "nombre": "Cliente Odoo"}] if sheet == "Clientes" else []
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_ODOO_REC",
            cliente_id="10",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=date(2026, 7, 1),
            monto_total=Decimal("250.00"),
            lista_precios="4",
            es_primera_compra=False,
            facturada=True,
            factura_id="FAC-900",
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "search_read":
            return [
                {
                    "id": 555,
                    "partner_id": [10, "Cliente Odoo"],
                    "amount": 53750.0,
                    "amount_ref": 250.0,
                    "amount_available_for_refund": 0.0,
                    "currency_id": [2, "VES"],
                    "journal_id": [1, "Banco"],
                    "date": "2026-07-05",
                    "reconciled_invoice_ids": [900, 901],
                }
            ]
        if model == "account.move" and method == "read":
            # Un pago reconciliando facturas de DOS órdenes distintas --
            # FAC-901 queda con residual (pago parcial de esa factura).
            return [
                {
                    "id": 900,
                    "name": "FAC-900",
                    "invoice_origin": "SO_ODOO_REC",
                    "move_type": "out_invoice",
                    "state": "posted",
                    "amount_total_signed_usd": 200.0,
                    "amount_residual_usd": 0.0,
                },
                {
                    "id": 901,
                    "name": "FAC-901",
                    "invoice_origin": "SO_ODOO_REC_2",
                    "move_type": "out_invoice",
                    "state": "posted",
                    "amount_total_signed_usd": 80.0,
                    "amount_residual_usd": 30.0,
                },
            ]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=fake_execute),
    ):
        res = client.get("/api/pagos-historial")
        assert res.status_code == 200
        data = res.json()
        # Una sola fila para el pago, no una por cada orden que reconcilia.
        assert len(data) == 1
        item = data[0]
        assert item["pago_id"] == "555"
        assert item["origen"] == "Odoo (automático vía factura)"
        assert "SO_ODOO_REC" in item["so_id"]
        assert "SO_ODOO_REC_2" in item["so_id"]
        assert "FAC-900" in item["factura_id"]
        assert "FAC-901" in item["factura_id"]
        assert len(item["facturas"]) == 2
        # monto conciliado = amount_ref (USD) - amount_available_for_refund.
        assert item["monto_aplicado"] == 250.0
        assert item["moneda"] == "VES"
        # FAC-901 quedó con $30 de residual -- debe quedar visible, no perderse.
        assert item["residual_facturas_usd"] == 30.0


def test_e2e_18_editar_tasa_binance_valida_min_max_del_dia():
    """Editar la tasa Binance de una Vinculación: debe rechazar valores

    fuera del rango [mínimo, máximo] capturado ese día en SerieTasas, y
    aceptar (recalculando equivalentes) un valor dentro del rango.
    """
    vinc = Vinculacion(
        vinc_id="V_EDIT",
        pago_id="P_EDIT",
        so_id="SO_EDIT",
        monto_aplicado=Decimal("1000.00"),
        hora_pago_confirmada=datetime(2026, 7, 10, 10, 0),
        tasa_bcv_aplicada=Decimal("36.0"),
        tasa_binance_aplicada=Decimal("40.0"),
        es_tasa_heredada=False,
        moneda_abono=Moneda.VES,
    )
    mock_repo = MagicMock()
    mock_repo.all_vinculaciones.return_value = [vinc]
    mock_repo.serie_tasas_del_dia.return_value = [
        SerieTasa(datetime(2026, 7, 10, 8, 0), Decimal("36"), Decimal("39.0"), "x"),
        SerieTasa(datetime(2026, 7, 10, 12, 0), Decimal("36"), Decimal("41.0"), "x"),
    ]

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.recalculate_all"),
    ):
        # Fuera de rango (máximo capturado es 41.0).
        res_bad = client.post(
            "/api/vinculacion/V_EDIT/tasa-binance", json={"tasa_binance": 45.0}
        )
        assert res_bad.status_code == 400
        assert mock_repo.update_vinculacion.call_count == 0

        # Dentro de rango.
        res_ok = client.post(
            "/api/vinculacion/V_EDIT/tasa-binance", json={"tasa_binance": 40.5}
        )
        assert res_ok.status_code == 200
        data = res_ok.json()
        assert data["tasa_binance_aplicada"] == 40.5
        assert abs(data["equiv_usd_binance"] - 1000.0 / 40.5) < 1e-6
        assert mock_repo.update_vinculacion.call_count == 1


def test_e2e_19_cambiar_tipo_tasa_bcv_usd_eur():
    """Selector de tasa BCV: alternar entre BCV-USD y BCV-EUR debe

    recalcular la tasa aplicada y los equivalentes; sin dato de tasa_bcv_euro
    capturado, seleccionar EUR debe rechazarse con un error claro.
    """
    vinc = Vinculacion(
        vinc_id="V_BCV",
        pago_id="P_BCV",
        so_id="SO_BCV",
        monto_aplicado=Decimal("500.00"),
        hora_pago_confirmada=datetime(2026, 7, 10, 10, 0),
        tasa_bcv_aplicada=Decimal("36.0"),
        tasa_binance_aplicada=Decimal("40.0"),
        es_tasa_heredada=False,
        moneda_abono=Moneda.VES,
        bcv_variante="USD",
    )
    mock_repo = MagicMock()
    mock_repo.all_vinculaciones.return_value = [vinc]
    mock_repo._g.read_rows.return_value = []  # sin tasa_bcv_euro capturada

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.recalculate_all"),
    ):
        res_no_eur = client.post(
            "/api/vinculacion/V_BCV/tasa-bcv-tipo", json={"variante": "EUR"}
        )
        assert res_no_eur.status_code == 400

    mock_repo._g.read_rows.return_value = [
        {"timestamp": "2026-07-10 10:00:00", "tasa_bcv": "36.0", "tasa_bcv_euro": "39.8"},
    ]
    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.recalculate_all"),
    ):
        res_eur = client.post(
            "/api/vinculacion/V_BCV/tasa-bcv-tipo", json={"variante": "EUR"}
        )
        assert res_eur.status_code == 200
        data = res_eur.json()
        assert data["bcv_variante"] == "EUR"
        assert data["tasa_bcv_aplicada"] == 39.8
        assert abs(data["equiv_usd_bcv"] - 500.0 / 39.8) < 1e-6


def test_e2e_20_reporte_diario_litros_usa_claves_correctas_de_lineasorden():
    """LineasOrden usa las columnas "producto" (product_id de Odoo) y

    "cantidad" -- NO "product_id" ni "cantidad_ordenada", que no existen.
    Con las claves equivocadas, el cálculo caía siempre al fallback de
    1.0 L/unidad; con las correctas debe usar el volumen real de Odoo.
    """
    mock_repo = MagicMock()
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_LIT",
            cliente_id="C1",
            vendedor_email="v1",
            fecha=date(2026, 7, 18),
            fecha_entrega=date(2026, 7, 18),
            monto_total=Decimal("1200.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
        )
    ]
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [{"so_id": "SO_LIT", "producto": "555", "cantidad": "10"}]
        if sheet == "LineasOrden"
        else []
    )

    def fake_execute(model, method, args, kwargs=None):
        if model == "product.product":
            return [{"id": 555, "default_code": "P555", "name": "Aceite", "volume": "20.0"}]
        if model == "sale.order":
            return [{"name": "SO_LIT", "state": "sale"}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env"),
    ):
        res = client.get("/api/reporte/diario")
        assert res.status_code == 200
        ventas = res.json()["ventas_diarias"]
        assert len(ventas) == 1
        # 10 unidades x 20.0 L/unidad (volumen real) = 200 L, no 10 L
        # (que es lo que daría el fallback de 1.0 L/unidad con las
        # claves viejas y equivocadas).
        assert ventas[0]["litros_totales"] == 200.0


def test_e2e_21_conciliaciones_sugerencias_filtra_pagos_ya_reconciliados_en_odoo():
    """El pago_id local es el ID numérico de Odoo (map_pago: pago_id=str(rec["id"])),

    no el campo "name" (referencia formateada tipo "PUSD1/2026/00552"). El
    filtro de pagos ya reconciliados en Odoo debe buscar por "id", si no
    nunca hace match y pagos ya reconciliados aparecen como "sin asignar".
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "111",
                "cliente_id": "CLI_R1",
                "monto": "500.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-15",
                "vendedor": "juan@lubrikca.com",
            },
            {
                "pago_id": "222",
                "cliente_id": "CLI_R1",
                "monto": "300.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-16",
                "vendedor": "juan@lubrikca.com",
            },
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "CLI_R1", "nombre": "Cliente Reconciliado"}]
            if sheet == "Clientes"
            else []
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_R1",
            cliente_id="CLI_R1",
            vendedor_email="juan@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=date(2026, 7, 1),
            monto_total=Decimal("1000.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            # Pago 111 ya reconciliado en Odoo; 222 sigue en process, sin conciliar.
            return [
                {
                    "id": 111,
                    "is_reconciled": True,
                    "state": "paid",
                    "reconciled_invoices_count": 1,
                },
                {
                    "id": 222,
                    "is_reconciled": False,
                    "state": "in_process",
                    "reconciled_invoices_count": 0,
                },
            ]
        if model == "sale.order":
            return [{"name": "SO_R1", "state": "sale"}]
        if model == "account.move":
            return []
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env"),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        sug_data = res.json()
        pagos_sugeridos = {s["pago_id"] for s in sug_data}
        # El pago 111 ya está reconciliado en Odoo -- no debe sugerirse.
        assert "111" not in pagos_sugeridos
        # El pago 222 sigue sin conciliar -- sí debe aparecer.
        assert "222" in pagos_sugeridos


def test_e2e_22_orden_excluida_excepcion_solo_aplica_a_cancel_con_entrega():
    """orden_excluida(): la excepción de negocio (entrega_valida) solo debe

    salvar órdenes CANCELADAS con mercancía despachada y no devuelta -- una
    cotización (draft/sent) nunca es una venta real, así que la excepción no
    le aplica aunque tenga un "picking" asociado (Odoo lo bloquearía, pero
    la función no debe asumirlo).
    """
    from cxc.web.app import orden_excluida

    o_cancel = OrdenVenta(
        so_id="X1",
        cliente_id="C",
        vendedor_email="v",
        fecha=date(2026, 7, 1),
        fecha_entrega=None,
        monto_total=Decimal("1"),
        lista_precios="4",
        es_primera_compra=False,
        estado_orden="cancel",
    )
    o_draft = OrdenVenta(
        so_id="X2",
        cliente_id="C",
        vendedor_email="v",
        fecha=date(2026, 7, 1),
        fecha_entrega=None,
        monto_total=Decimal("1"),
        lista_precios="4",
        es_primera_compra=False,
        estado_orden="draft",
    )
    # Cancelada + entrega válida (ALM/OUT sin devolución) -> excepción de negocio, NO se excluye.
    assert orden_excluida(o_cancel, entrega_valida=True) is False
    # Cancelada sin entrega -> comportamiento normal, se excluye.
    assert orden_excluida(o_cancel, entrega_valida=False) is True
    # Cotización (draft) nunca se salva por la excepción -- solo aplica a cancel.
    assert orden_excluida(o_draft, entrega_valida=True) is True


def test_e2e_23_regla_global_excepcion_cancelada_con_entrega_no_devuelta():
    """Excepción de negocio end-to-end: una orden CANCELADA en Odoo cuya

    mercancía ya salió de almacén (ALM/OUT, stock.picking saliente "done")
    y no fue devuelta sigue siendo una venta real -- Odoo permite cancelar
    una SO después del despacho sin deshacer la entrega -- y debe seguir
    contando en /api/resumen pese a estado_orden="cancel" en el espejo local.
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_conciliaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_CANCEL_ENTREGADA",
            cliente_id="CLI_23",
            vendedor_email="juan@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("777.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="cancel",
        ),
        OrdenVenta(
            so_id="SO_CANCEL_SIN_ENTREGA",
            cliente_id="CLI_23",
            vendedor_email="juan@lubrikca.com",
            fecha=date(2026, 7, 2),
            fecha_entrega=None,
            monto_total=Decimal("300.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="cancel",
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [
                {"name": "SO_CANCEL_ENTREGADA", "state": "cancel", "picking_ids": [501]},
                {"name": "SO_CANCEL_SIN_ENTREGA", "state": "cancel", "picking_ids": []},
            ]
        if model == "stock.picking":
            return [{"id": 501, "picking_type_code": "outgoing", "return_id": False}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env"),
    ):
        res = client.get("/api/resumen")
        assert res.status_code == 200
        # SO_CANCEL_ENTREGADA tiene un picking de salida "done" sin devolución -> cuenta.
        # SO_CANCEL_SIN_ENTREGA no tiene picking -> sigue excluida como antes.
        assert res.json()["total_por_cobrar_usd"] == 777.0


def test_e2e_24_ventas_reporte_teorico_vs_real_y_alerta():
    """/api/ventas: venta bruta/neta teórica (motor) vs real (Odoo) vs

    facturado, con IVA aplicado a las columnas teóricas -- y la alerta solo
    debe dispararse cuando una orden YA facturada quedó facturada, en neto,
    por debajo de lo que el motor dice que debió facturarse neto (con
    impuestos). Una orden facturada al monto lleno (sin descuento aplicado
    en la factura) no es una alerta, aunque no coincida exactamente con la
    venta neta teórica -- solo lo es cuando factura MENOS de lo debido.
    """
    from cxc.config import EngineConfig
    from cxc.models import BandejaFacturacion

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_V1",
            cliente_id="CLI_V",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            # amount_total de Odoo (CON IVA 16% sobre 100 de subtotal).
            monto_total=Decimal("116.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
        OrdenVenta(
            so_id="SO_V2",
            cliente_id="CLI_V",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 2),
            fecha_entrega=None,
            monto_total=Decimal("232.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
        OrdenVenta(
            so_id="SO_V3",
            cliente_id="CLI_V",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 3),
            fecha_entrega=None,
            monto_total=Decimal("150.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_V1",
            lista_aplicada="4",
            precio_base_calculado=Decimal("100.00"),
            total_motor=Decimal("90.00"),
        ),
        BandejaFacturacion(
            so_id="SO_V2",
            lista_aplicada="4",
            precio_base_calculado=Decimal("200.00"),
            total_motor=Decimal("180.00"),
        ),
        BandejaFacturacion(
            so_id="SO_V3",
            lista_aplicada="4",
            precio_base_calculado=Decimal("140.00"),
            total_motor=Decimal("130.00"),
        ),
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
        lista_usd="4",
        lista_bcv="5",
    )  # iva_rate=0.16, igtf_activo=False por defecto

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [
                {"name": "SO_V1", "state": "sale", "amount_untaxed": 100.0},
                {"name": "SO_V2", "state": "sale", "amount_untaxed": 200.0},
                {"name": "SO_V3", "state": "sale", "amount_untaxed": 140.0},
            ]
        if model == "account.move":
            return [
                # SO_V1: facturado al monto lleno, sin descuento -- coincide
                # exactamente con la bruta teórica + IVA. No es una alerta.
                {
                    "invoice_origin": "SO_V1",
                    "move_type": "out_invoice",
                    "amount_untaxed_signed_usd": 100.0,
                    "amount_total_signed_usd": 116.0,
                },
                # SO_V2: facturado muy por debajo -- ni siquiera cubre la
                # venta neta teórica + IVA ($208.80). Debe alertar.
                {
                    "invoice_origin": "SO_V2",
                    "move_type": "out_invoice",
                    "amount_untaxed_signed_usd": 140.0,
                    "amount_total_signed_usd": 162.40,
                },
            ]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        data = res.json()
        by_so = {it["so_id"]: it for it in data["items"]}

        v1 = by_so["SO_V1"]
        assert v1["venta_bruta_teorica"] == 100.0
        assert v1["venta_bruta_teorica_iva"] == 116.0
        assert v1["venta_neta_teorica"] == 90.0
        assert abs(v1["venta_neta_teorica_impuestos"] - 104.4) < 0.01
        assert v1["venta_bruta_real"] == 100.0
        assert v1["venta_neta_real"] == 116.0
        assert v1["total_facturado_con_impuestos"] == 116.0
        assert v1["total_facturado_neto"] == 116.0
        assert v1["diferencia"] == 0.0
        assert v1["alerta"] is False

        v2 = by_so["SO_V2"]
        assert v2["total_facturado_neto"] == 162.40
        assert abs(v2["venta_neta_teorica_impuestos"] - 208.8) < 0.01
        assert v2["alerta"] is True

        # SO_V3: sin factura -- "diferencia" NO debe ser toda la venta bruta
        # teórica (facturado neto=0 sería una falsa alarma), sino la venta
        # neta teórica + impuestos contra el neto real de la propia orden en
        # Odoo (150.80 - 150.00 = 0.80). Sin factura, tampoco hay alerta.
        v3 = by_so["SO_V3"]
        assert v3["total_facturado_neto"] == 0.0
        assert v3["venta_neta_real"] == 150.0
        assert abs(v3["venta_neta_teorica_impuestos"] - 150.8) < 0.01
        assert abs(v3["diferencia"] - 0.8) < 0.01
        assert v3["alerta"] is False

        assert data["kpis"]["total_alertas"] == 1
        assert data["kpis"]["iva_rate"] == 0.16
        assert abs(data["kpis"]["subtotal_real_total"] - 440.0) < 0.01
        assert abs(data["kpis"]["venta_bruta_teorica_iva_total"] - 510.4) < 0.01


def test_e2e_25_recalcular_todo_requiere_admin_o_gerente():
    """POST /api/admin/recalcular-todo debe rechazar roles sin permiso

    (p.ej. "ventas") con 403, sin disparar el recálculo.
    """
    with (
        patch(
            "cxc.web.app.get_current_user_from_cookie",
            return_value={"email": "juan@lubrikca.com", "rol": "ventas", "nombre": "Juan"},
        ),
        patch("cxc.web.app.recalculate_all_orders") as mock_recalc,
    ):
        client.cookies.set("cxc_session", "fake-token")
        res = client.post("/api/admin/recalcular-todo")
        assert res.status_code == 403
        mock_recalc.assert_not_called()


def test_e2e_26_recalcular_todo_admin_dispara_recalculo_en_segundo_plano():
    """Con rol admin (o gerente_ventas), el endpoint debe programar

    recalculate_all_orders como background task y responder success.
    """
    with (
        patch(
            "cxc.web.app.get_current_user_from_cookie",
            return_value={"email": "admin@lubrikca.com", "rol": "admin", "nombre": "Admin"},
        ),
        patch("cxc.web.app.recalculate_all_orders") as mock_recalc,
    ):
        client.cookies.set("cxc_session", "fake-token")
        res = client.post("/api/admin/recalcular-todo")
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        mock_recalc.assert_called_once()


def test_e2e_27_reporte_diario_cobranza_incluye_pagos_reconciliados_en_odoo():
    """La hoja local "Pagos" solo sincroniza pagos is_reconciled=False

    (ver changed_pagos() en odoo/client.py -- existe para sugerir
    vinculaciones manuales, no para totalizar cobranza). Verificado en vivo
    contra producción: de 882 pagos confirmados en Odoo, 673 (76%) ya
    estaban reconciliados y el total de cobranza del dashboard quedaba
    ~$16,562 por debajo del real. /api/reporte/diario debe consultar
    account.payment EN VIVO (sin filtrar por is_reconciled) y sumar
    "amount_ref" -- el mismo campo que Odoo usa para su propio total -- en
    vez de depender de la hoja local.
    """
    mock_repo = MagicMock()
    mock_repo.all_ordenes.return_value = []
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P_SHEET_STALE",
                "fecha_pago": "2026-07-01",
                "monto": "1.0",
                "moneda": "USD",
                "metodo_pago": "Efectivo",
                "vendedor_email": "otro@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else []
    )

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return [
                {
                    "id": 1,
                    "amount": 100.0,
                    "amount_ref": 100.0,
                    "currency_id": [1, "USD"],
                    "journal_id": [8, "Zelle"],
                    "partner_id": [50, "Cliente Uno"],
                    "date": "2026-07-15",
                },
                {
                    # Pago YA RECONCILIADO en Odoo -- el sync incremental
                    # nunca lo trae a la hoja local "Pagos", pero SÍ debe
                    # contarse en la cobranza real.
                    "id": 2,
                    "amount": 43000.0,
                    "amount_ref": 200.0,
                    "currency_id": [2, "VES"],
                    "journal_id": [3, "Banco Bancamiga"],
                    "partner_id": [51, "Cliente Dos"],
                    "date": "2026-07-15",
                },
            ]
        if model == "res.partner":
            return [
                {"id": 50, "user_id": [10, "Juan Vendedor"]},
                {"id": 51, "user_id": [10, "Juan Vendedor"]},
            ]
        if model == "res.users":
            return [{"id": 10, "login": "juan@lubrikca.com"}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env"),
    ):
        res = client.get("/api/reporte/diario")
        assert res.status_code == 200
        cobranza = res.json()["cobranza_diaria"]
        assert len(cobranza) == 1
        dia = cobranza[0]
        assert dia["fecha"] == "2026-07-15"
        # 100 (USD directo) + 200 (VES via amount_ref, YA reconciliado) = 300.
        # La fila de la hoja local ("P_SHEET_STALE") NO debe sumarse --
        # una vez que hay datos en vivo de Odoo, reemplazan a la hoja.
        assert dia["total_eq_bcv"] == 300.0
        assert dia["por_metodo"]["Zelle"] == 100.0
        assert dia["por_metodo"]["Banco Bancamiga"] == 200.0
        assert dia["ves_monto"] == 43000.0
        assert dia["ves_eq_usd"] == 200.0

        # Filtro por vendedor resuelto vía res.partner.user_id -> res.users.login.
        res_v = client.get("/api/reporte/diario?vendedor=juan@lubrikca.com")
        assert res_v.json()["cobranza_diaria"][0]["total_eq_bcv"] == 300.0
        res_v2 = client.get("/api/reporte/diario?vendedor=nadie@lubrikca.com")
        assert res_v2.json()["cobranza_diaria"] == []


def test_e2e_28_reporte_diario_litros_usa_sale_report_de_odoo():
    """Los litros deben venir de sale.report (mismo "Volumen (L)" del pivot

    "Análisis de Ventas" de Odoo) para las órdenes que Odoo reconoce ahí,
    con fallback local (cantidad_entregada si es > 0, si no cantidad
    pedida) para órdenes que sale.report no trae (p.ej. la excepción de
    negocio cancelada+entregada). Verificado en vivo: el fallback anterior
    ("cantidad_entregada or cantidad") nunca caía a "cantidad" porque la
    hoja guarda "0" como texto (truthy), subestimando litros en ~2.600 L.
    """
    mock_repo = MagicMock()
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_SR1",
            cliente_id="C1",
            vendedor_email="v1",
            fecha=date(2026, 7, 18),
            fecha_entrega=date(2026, 7, 18),
            monto_total=Decimal("500.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="sale",
        ),
        OrdenVenta(
            so_id="SO_FALLBACK",
            cliente_id="C1",
            vendedor_email="v1",
            fecha=date(2026, 7, 18),
            fecha_entrega=date(2026, 7, 18),
            monto_total=Decimal("90.00"),
            lista_precios="4",
            es_primera_compra=False,
            estado_orden="cancel",
        ),
    ]
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            # Línea NO despachada aún: cantidad_entregada llega como texto
            # "0" (nunca vacío) -- el fallback debe usar "cantidad" (10).
            {
                "so_id": "SO_FALLBACK",
                "producto": "77",
                "cantidad": "10",
                "cantidad_entregada": "0",
            }
        ]
        if sheet == "LineasOrden"
        else []
    )

    def fake_execute(model, method, args, kwargs=None):
        if model == "product.product":
            return [{"id": 77, "name": "Producto Fallback", "volume": "2.0", "weight": "0"}]
        if model == "sale.order":
            return [
                {"name": "SO_SR1", "state": "sale", "picking_ids": []},
                {"name": "SO_FALLBACK", "state": "cancel", "picking_ids": [501]},
            ]
        if model == "stock.picking":
            return [{"id": 501, "picking_type_code": "outgoing", "return_id": False}]
        if model == "sale.report":
            # SO_SR1 SÍ aparece en sale.report -- SO_FALLBACK (cancelada) no.
            return [{"name": "SO_SR1", "product_volume": 123.45}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env"),
    ):
        res = client.get("/api/reporte/diario")
        assert res.status_code == 200
        ventas = {v["fecha"]: v for v in res.json()["ventas_diarias"]}
        # 123.45 (sale.report, SO_SR1) + 10*2.0=20.0 (fallback, SO_FALLBACK) = 143.45
        assert abs(ventas["2026-07-18"]["litros_totales"] - 143.45) < 0.01
