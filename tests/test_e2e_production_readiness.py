import contextlib
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cxc.engine.runner import EngineRunner
from cxc.models import (
    BandejaFacturacion,
    Cliente,
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
from cxc.sheets import serde
from cxc.web import app as _app_module
from cxc.web.app import SECRET_KEY, app, crear_session_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_module_level_caches():
    """``app.py`` tiene varios caches a nivel de módulo (TTL en memoria,

    compartidos entre requests reales) -- sin resetearlos entre tests, el
    resultado cacheado de un test (con SUS mocks) se filtra al siguiente
    (con mocks distintos) y produce fallos que dependen del orden de
    ejecución. Se resetean antes Y después de cada test.
    """
    _app_module._REPORTE_SALDOS_CACHE["data"] = None
    _app_module._REPORTE_SALDOS_CACHE["timestamp"] = 0.0
    _app_module._SALDOS_REALES_CACHE["data"] = None
    _app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0
    _app_module._PRICELIST_MAPEO_CACHE.clear()
    yield
    _app_module._REPORTE_SALDOS_CACHE["data"] = None
    _app_module._REPORTE_SALDOS_CACHE["timestamp"] = 0.0
    _app_module._SALDOS_REALES_CACHE["data"] = None
    _app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0
    _app_module._PRICELIST_MAPEO_CACHE.clear()
    with contextlib.suppress(Exception):
        client.cookies.delete("cxc_session")


def _mock_repo_with_gateway_bridge() -> MagicMock:
    """``MagicMock`` de ``Repository`` con los métodos "wholesale" (que
    reemplazaron a ``repo._g.read_rows(...)`` en ``cxc.web.app``) derivados
    en vivo de ``repo._g.read_rows`` -- así los tests que ya mockean por
    pestaña de Sheets (``side_effect = lambda sheet: ...``) siguen
    funcionando sin duplicar los datos de prueba en dos formatos."""
    repo = MagicMock()
    repo.all_pagos.side_effect = lambda: [
        serde.pago_from_row(r) for r in repo._g.read_rows("Pagos")
    ]
    repo.all_pagos_full.side_effect = lambda: repo._g.read_rows("Pagos")
    repo.all_lineas.side_effect = lambda: [
        serde.linea_from_row(r) for r in repo._g.read_rows("LineasOrden")
    ]
    repo.all_clientes.side_effect = lambda: [
        serde.cliente_from_row(r) for r in repo._g.read_rows("Clientes")
    ]
    repo.all_serie_tasas.side_effect = lambda: [
        serde.serie_from_row(r) for r in repo._g.read_rows("SerieTasas")
    ]
    repo.all_tasas_historicas_auditoria.side_effect = lambda: repo._g.read_rows(
        "TasasHistoricasAuditoria"
    )
    repo.all_pagos_huerfanos_cerrados.side_effect = lambda: repo._g.read_rows(
        "PagosHuerfanosCerrados"
    )

    def _marcar_recibido(pago_ids, numero_recibido, fecha_recibido, recibido_por):
        target = set(pago_ids)
        actualizados = []
        for r in repo._g.read_rows("Pagos"):
            pid = str(r.get("pago_id", "")).strip()
            if pid in target:
                r = dict(r)
                r["recibido"] = "TRUE"
                r["numero_recibido"] = numero_recibido
                r["fecha_recibido"] = fecha_recibido.isoformat()[:19]
                r["recibido_por"] = recibido_por
                actualizados.append(r)
        return actualizados

    repo.marcar_pagos_recibido.side_effect = _marcar_recibido
    return repo


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

    cfg = EngineConfig(cash_window_business_days=3, bcv_complete_formula="full")
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
    """Test 6: Scoping por vendedor y permisos de rol en la tabla unificada

    de Cobranza (GET /api/cobranza/pagos, reemplazó a GET /api/cobranza).
    """
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P_VEND_1",
                "cliente_id": "C_JUAN",
                "fecha_pago": "2026-07-15",
                "monto": "200.0",
                "moneda": "USD",
                "vendedor_email": "vendedor_juan@lubrikca.com",
            },
            {
                "pago_id": "P_VEND_2",
                "cliente_id": "C_PEDRO",
                "fecha_pago": "2026-07-15",
                "monto": "500.0",
                "moneda": "USD",
                "vendedor_email": "vendedor_pedro@lubrikca.com",
            },
        ]
        if sheet == "Pagos"
        else (
            [
                {
                    "cliente_id": "C_JUAN",
                    "nombre": "Cliente Juan",
                    "vendedor_email": "vendedor_juan@lubrikca.com",
                },
                {
                    "cliente_id": "C_PEDRO",
                    "nombre": "Cliente Pedro",
                    "vendedor_email": "vendedor_pedro@lubrikca.com",
                },
            ]
            if sheet == "Clientes"
            else []
        )
    )

    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_auditoria.return_value = []

    token = crear_session_token("vendedor_juan@lubrikca.com", SECRET_KEY)

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
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
        try:
            res = client.get("/api/cobranza/pagos")
            assert res.status_code == 200
            data = res.json()
            assert len(data) > 0
            for item in data:
                assert item["vendedor"] == "vendedor_juan@lubrikca.com"
        finally:
            # Evita que la cookie de sesión y el cache de saldos reales (TTL
            # 60s, módulo-level) "se filtren" al resto de los tests -- client
            # y _SALDOS_REALES_CACHE son compartidos a nivel de módulo. (El
            # fixture autouse _reset_module_level_caches ya hace este reset
            # entre todos los tests; se deja explícito acá también.)
            client.cookies.delete("cxc_session")
            _app_module._SALDOS_REALES_CACHE["data"] = None
            _app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0


def test_e2e_07_receipt_generation():
    """Test 7: Generación de Recibo de Entrega (2 Copias PDF)."""
    mock_repo = _mock_repo_with_gateway_bridge()
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
        else []
    )
    mock_repo.all_lineas.return_value = []
    mock_repo.all_pagos.return_value = [
        Pago(
            pago_id="P_D1",
            cliente_id="C1",
            monto=Decimal("600.0"),
            moneda=Moneda.USD,
            metodo_pago="Efectivo",
            fecha_pago=datetime(2026, 7, 18),
            vendedor_email="v1",
        )
    ]
    mock_repo.all_serie_tasas.return_value = []

    # _connect debe ir mockeada a None (sin Odoo real) -- si no, el
    # endpoint consulta account.payment EN VIVO y pisa por completo los
    # datos mockeados de arriba con lo que sea que Odoo tenga hoy,
    # volviendo el test no-determinista según el entorno donde corra.
    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=None),
    ):
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
    mock_repo.all_lineas.return_value = []
    hoy = date.today().isoformat()
    mock_repo._g.read_rows.side_effect = lambda sheet: []
    mock_repo.all_pagos.return_value = [
        Pago(
            pago_id="P_USD",
            cliente_id="C1",
            monto=Decimal("100.0"),
            moneda=Moneda.USD,
            metodo_pago="Efectivo",
            fecha_pago=datetime.strptime(hoy, "%Y-%m-%d"),
            vendedor_email="",
        ),
        Pago(
            pago_id="P_VES",
            cliente_id="C1",
            monto=Decimal("4000.0"),
            moneda=Moneda.VES,
            metodo_pago="Banco Bancamiga",
            fecha_pago=datetime.strptime(hoy, "%Y-%m-%d"),
            vendedor_email="",
        ),
    ]
    mock_repo.all_serie_tasas.return_value = [
        SerieTasa(
            timestamp=datetime.strptime(f"{hoy} 12:00:00", "%Y-%m-%d %H:%M:%S"),
            tasa_bcv=Decimal("40.0"),
            tasa_binance=Decimal("42.0"),
            fuente="test",
        )
    ]

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=None),
    ):
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
    mock_repo.get_config.side_effect = lambda key: (
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
        mock_repo.set_config.assert_called()


def test_e2e_10_conciliaciones_sugerencias_and_bulk_approval():
    """Test 10: Sugerencias Inteligentes de Conciliación (FIFO por cliente) y Aprobación Masiva."""
    mock_repo = _mock_repo_with_gateway_bridge()
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
    mock_repo = _mock_repo_with_gateway_bridge()
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
    mock_repo = _mock_repo_with_gateway_bridge()
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
            # Vinculado parcialmente: VES 6000 equivalen a $150 (tasa 40); ya
            # se aplicaron $50 (Vinculacion.monto_aplicado siempre esta en
            # USD, la moneda de las ordenes, nunca en la moneda original del
            # pago) -> quedan $100 pendientes (= 4000 VES a esa misma tasa).
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
            monto_aplicado=Decimal("50.00"),
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
    mock_repo = _mock_repo_with_gateway_bridge()
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
    mock_repo.all_serie_tasas.return_value = []  # sin tasa_bcv_euro capturada

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.recalculate_all"),
    ):
        res_no_eur = client.post(
            "/api/vinculacion/V_BCV/tasa-bcv-tipo", json={"variante": "EUR"}
        )
        assert res_no_eur.status_code == 400

    mock_repo.all_serie_tasas.return_value = [
        SerieTasa(
            timestamp=datetime(2026, 7, 10, 10, 0, 0),
            tasa_bcv=Decimal("36.0"),
            tasa_binance=Decimal("40.0"),
            fuente="test",
            tasa_bcv_euro=Decimal("39.8"),
        ),
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
    mock_repo = _mock_repo_with_gateway_bridge()
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
        [{"linea_id": "L1", "so_id": "SO_LIT", "producto": "555", "cantidad": "10"}]
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
    mock_repo = _mock_repo_with_gateway_bridge()
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

        # Tarea 2 (limpieza Ventas): "venta_bruta_teorica"/"venta_neta_teorica"
        # y sus variantes +impuestos ya no se exponen (redundantes con el
        # bloque VES/USD explícito, ver test_e2e_24b) -- pero siguen
        # calculándose internamente para diferencia/alerta, sin cambios.
        v1 = by_so["SO_V1"]
        assert v1["venta_bruta_real"] == 100.0
        assert v1["venta_neta_real"] == 116.0
        assert v1["total_facturado_con_impuestos"] == 116.0
        assert v1["total_facturado_neto"] == 116.0
        assert v1["diferencia"] == 0.0
        assert v1["alerta"] is False

        v2 = by_so["SO_V2"]
        assert v2["total_facturado_neto"] == 162.40
        assert v2["alerta"] is True

        # SO_V3: sin factura -- "diferencia" NO debe ser toda la venta bruta
        # teórica (facturado neto=0 sería una falsa alarma), sino la venta
        # neta teórica + impuestos contra el neto real de la propia orden en
        # Odoo (150.80 - 150.00 = 0.80). Sin factura, tampoco hay alerta.
        v3 = by_so["SO_V3"]
        assert v3["total_facturado_neto"] == 0.0
        assert v3["venta_neta_real"] == 150.0
        assert abs(v3["diferencia"] - 0.8) < 0.01
        assert v3["alerta"] is False

        assert data["kpis"]["total_alertas"] == 1
        assert data["kpis"]["iva_rate"] == 0.16
        assert abs(data["kpis"]["subtotal_real_total"] - 440.0) < 0.01


def test_e2e_24b_ventas_expone_lista_nacimiento_y_teoricos_por_lista():
    """/api/ventas debe exponer, por orden: la lista de precios con la que

    nació la venta (``lista_nacimiento``), la lista que terminó aplicando
    el motor (``lista_aplicada`` -- puede ganar la del método de pago sobre
    la de nacimiento), y la comparación teórica explícita VES vs USD
    (bruta/neta, con y sin impuestos -- Tarea 2 del rediseño de Ventas),
    calculados por el motor en ``BandejaFacturacion``: este endpoint solo
    los lee y aplica impuestos, no recalcula ningún descuento.
    """
    from cxc.config import EngineConfig
    from cxc.models import BandejaFacturacion

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_LISTA1",
            cliente_id="CLI_L",
            vendedor_email="ana@lubrikca.com",
            fecha=date(2026, 7, 1),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),
            lista_precios="5",  # nació en VES
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_LISTA1",
            lista_aplicada="4",  # el método de pago la reselecciona a USD
            precio_base_calculado=Decimal("100.00"),
            total_motor=Decimal("90.00"),
            equivalente_lista_usd=Decimal("102.00"),
            teorico_lista_ves=Decimal("3600.00"),
            teorico_lista_usd=Decimal("102.00"),
            descuentos_teorico_ves=Decimal("360.00"),
            descuentos_teorico_usd=Decimal("10.20"),
        ),
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=lambda *a, **k: []),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = res.json()["items"][0]

        assert item["lista_nacimiento"] == "5"
        assert item["lista_aplicada"] == "4"
        assert item["lista_nacimiento_label"] == "#5"
        assert item["lista_aplicada_label"] == "#4"
        # VES: bruta 3600, descuento teórico 360 -> neta 3240; +IVA 16%.
        assert item["ves_bruta_teorica"] == 3600.0
        assert item["ves_bruta_teorica_iva"] == 4176.0
        assert item["ves_neta_teorica"] == 3240.0
        assert item["ves_neta_teorica_iva"] == 3758.4
        # USD: bruta 102, descuento teórico 10.20 -> neta 91.8; +IVA 16%.
        assert item["usd_bruta_teorica"] == 102.0
        assert item["usd_bruta_teorica_iva"] == 118.32
        assert item["usd_neta_teorica"] == 91.8
        assert item["usd_neta_teorica_iva"] == 106.49
        # Campos genéricos/redundantes eliminados del payload (Tarea 2).
        for campo_eliminado in (
            "venta_bruta_teorica",
            "venta_bruta_teorica_iva",
            "venta_neta_teorica",
            "venta_neta_teorica_impuestos",
            "equivalente_lista_usd",
            "teorico_lista_ves",
            "teorico_lista_usd",
            "descuentos_teorico_ves",
            "descuentos_teorico_usd",
            "teorico_neto_ves",
            "teorico_neto_usd",
        ):
            assert campo_eliminado not in item


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
    mock_repo = _mock_repo_with_gateway_bridge()
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
                "linea_id": "L1",
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


def test_e2e_29_sugerencias_convierte_ves_a_usd_antes_de_sugerir():
    """Un pago en VES no debe tratarse como si su monto ya fuera USD -- el

    bug reportado: un pago de VES 5.181,27 aparecía en la tabla de
    sugerencias como "$5181.27", cuando a la tasa BCV del día ese pago vale
    ~$141.68 USD. La sugerencia de aplicación (monto_sugerido) debe basarse
    en el equivalente USD real, no en el número crudo en bolívares.
    """
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P_VES",
                "cliente_id": "C_VES",
                "monto": "5181.27",
                "moneda": "VES",
                "fecha_pago": "2026-07-23",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C_VES", "nombre": "Cliente VES", "vendedor_email": "v@lubrikca.com"}]
            if sheet == "Clientes"
            else (
                [{"timestamp": "2026-07-23 12:00:00", "tasa_bcv": "36.57", "tasa_binance": "38.0"}]
                if sheet == "SerieTasas"
                else []
            )
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_VES_1",
            cliente_id="C_VES",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 5, 7),
            fecha_entrega=date(2026, 5, 7),
            # Saldo pequeño a proposito: si el bug estuviera presente
            # (tratando 5181.27 como USD), este monto_sugerido seria
            # min(5181.27, 60.0) = 60.0 -- igual que con la conversion
            # correcta, por eso el saldo/residual es la aserción clave.
            monto_total=Decimal("60.00"),
            lista_precios="4",
            es_primera_compra=False,
        ),
    ]

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2  # cubre SO_VES_1 ($60) y queda un remanente sin orden
        primero = data[0]
        # VES 5181.27 / 36.57 ~= $141.71 -- NUNCA 5181.27 tratado como USD.
        assert abs(primero["monto_pago"] - 141.71) < 0.05
        assert abs(primero["saldo_pago"] - 141.71) < 0.05
        # El monto aplicado a la orden esta limitado por el saldo de la
        # orden ($60), no por el monto crudo en VES.
        assert primero["so_id"] == "SO_VES_1"
        assert primero["monto_sugerido"] == 60.0
        # Ambas referencias visibles para un pago VES -- BCV y Binance del
        # mismo monto original (5181.27 / 38.0 ~= $136.35), no solo una.
        assert abs(primero["monto_pago_binance"] - 136.35) < 0.05

        # El remanente del pago ($141.71 - $60 = $81.71) sigue apareciendo,
        # sin orden sugerida, para poder vincularse manualmente.
        segundo = data[1]
        assert segundo["so_id"] is None
        assert segundo["pago_id"] == "P_VES"
        # $141.71 originales - $60 ya sugeridos para SO_VES_1 = $81.71 aun sin asociar.
        assert abs(segundo["saldo_pago"] - 81.71) < 0.05
        # Ese mismo residual reexpresado a tasa Binance (~$78.61), no una
        # copia del valor BCV.
        assert abs(segundo["saldo_pago_binance"] - 78.61) < 0.05


def test_e2e_30_resumen_pagos_sin_asignar_excluye_reconciliados_en_odoo():
    """La tarjeta KPI "Pagos Sin Asignar" (/api/resumen) debe excluir pagos ya

    reconciliados directamente en Odoo, igual que /api/conciliaciones/sugerencias
    -- antes de este fix, este endpoint no consultaba Odoo en absoluto y
    sumaba pagos ya conciliados como si siguieran sin asignar.
    """
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "111",
                "cliente_id": "C1",
                "monto": "500.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-15",
            },
            {
                "pago_id": "222",
                "cliente_id": "C1",
                "monto": "300.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-16",
            },
        ]
        if sheet == "Pagos"
        else []
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_conciliaciones.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            # 111 ya reconciliado en Odoo -- no debe sumar en el KPI.
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
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env"),
    ):
        res = client.get("/api/resumen")
        assert res.status_code == 200
        data = res.json()
        # Solo el pago 222 ($300) debe contar -- el 111 esta reconciliado.
        assert data["pagos_sin_asignar_usd"] == 300.0


def test_e2e_31_pagos_historial_incluye_monto_pago_usd():
    """Cada fila de "Pagos Conciliados" debe exponer el monto original del

    pago (importe firmado, USD) además del monto aplicado -- para poder
    verificar a ojo que aplicado + residual cuadra contra el pago completo.
    """
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="V1",
            pago_id="P1",
            so_id="SO1",
            monto_aplicado=Decimal("60.00"),
            hora_pago_confirmada=datetime(2026, 7, 20),
            tasa_bcv_aplicada=Decimal("36.5"),
            tasa_binance_aplicada=Decimal("38.0"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.CONCILIADO,
        )
    ]
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P1",
                "cliente_id": "C1",
                "monto": "100.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-20",
            }
        ]
        if sheet == "Pagos"
        else ([{"cliente_id": "C1", "nombre": "Cliente Uno"}] if sheet == "Clientes" else [])
    )
    mock_repo.all_ordenes.return_value = []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
    ):
        res = client.get("/api/pagos-historial")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        # Monto total del pago ($100) visible además de lo aplicado ($60).
        assert data[0]["monto_pago_usd"] == 100.0
        assert data[0]["monto_aplicado"] == 60.0


def test_e2e_32_sugerencias_usa_tasa_odoo_del_pago_no_serietasas_cercana():
    """Bug real (pago Odoo 29, 2026-03-18): la tasa BCV usada para convertir

    un pago VES a USD venía de la fila de SerieTasas más CERCANA en el
    tiempo, sin respetar el día exacto del pago -- si SerieTasas tenía un
    hueco alrededor de esa fecha, terminaba usando la tasa de OTRO día.
    Ahora, cuando Odoo trae el campo propio `tax_today` (la tasa BCV EXACTA
    que Odoo aplicó a ESE pago) y `amount_ref` (su equivalente USD ya
    calculado por Odoo), esos valores deben prevalecer sobre cualquier
    adivinanza local -- ignorando a propósito la tasa "cercana pero
    incorrecta" de SerieTasas (36.5, muy distinta de la real 451.5072).
    """
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "29",
                "cliente_id": "C_ZIP",
                "monto": "36196.75",
                "moneda": "VES",
                "fecha_pago": "2026-03-18",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C_ZIP", "nombre": "ZIP MARKET, CA"}]
            if sheet == "Clientes"
            else (
                # SerieTasas NO tiene captura el 2026-03-18 -- la fila más
                # cercana es de otro día, con una tasa muy distinta (36.5
                # vs. la real 451.5072). Si el bug estuviera presente, el
                # sistema usaría esta tasa incorrecta.
                [{"timestamp": "2026-03-10 12:00:00", "tasa_bcv": "36.5", "tasa_binance": "38.0"}]
                if sheet == "SerieTasas"
                else (
                    # TasasHistoricasAuditoria SÍ tiene el día exacto para
                    # Binance (Odoo no tiene noción de esa tasa).
                    [
                        {
                            "fecha": "2026-03-18",
                            "tasa_binance_promedio_diario": "471.87",
                        }
                    ]
                    if sheet == "TasasHistoricasAuditoria"
                    else []
                )
            )
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            # El mock no distingue por "fields" -- la misma fila debe
            # satisfacer tanto a get_reconciled_pago_ids_odoo (is_reconciled/
            # state/reconciled_invoices_count) como al nuevo lookup de
            # tax_today/amount_ref/name.
            return [
                {
                    "id": 29,
                    "name": "PBAMI/2026/00009",
                    "tax_today": 451.5072,
                    "amount_ref": 80.17,
                    "is_reconciled": False,
                    "state": "in_process",
                    "reconciled_invoices_count": 0,
                }
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
        assert len(data) == 1
        item = data[0]
        # Tasa/monto de Odoo (exactos para ESE pago), no la de SerieTasas
        # más cercana (que hubiera dado 36196.75/36.5 ~= $991.69 -- muy
        # distinto del real $80.17).
        assert item["numero_pago_odoo"] == "PBAMI/2026/00009"
        assert abs(item["tasa_bcv"] - 451.5072) < 0.001
        assert abs(item["monto_pago"] - 80.17) < 0.01
        assert abs(item["saldo_pago"] - 80.17) < 0.01
        # Binance del día exacto (TasasHistoricasAuditoria), no una
        # adivinanza: 36196.75 / 471.87 ~= $76.71.
        assert abs(item["tasa_binance"] - 471.87) < 0.001
        assert abs(item["monto_pago_binance"] - 76.71) < 0.05


def test_e2e_33_sugerencias_binance_sin_historico_cae_a_serietasas():
    """Si TasasHistoricasAuditoria no tiene el día exacto del pago, Binance

    cae al comportamiento anterior (SerieTasas más cercana) en vez de
    fallar -- fallback, no default.
    """
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "500",
                "cliente_id": "C1",
                "monto": "1000.0",
                "moneda": "VES",
                "fecha_pago": "2026-07-20",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C1", "nombre": "Cliente Uno"}]
            if sheet == "Clientes"
            else (
                [{"timestamp": "2026-07-20 12:00:00", "tasa_bcv": "40.0", "tasa_binance": "45.0"}]
                if sheet == "SerieTasas"
                else []  # TasasHistoricasAuditoria vacío -- sin dato del día
            )
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        # Sin Odoo y sin histórico diario: BCV y Binance caen a SerieTasas.
        assert abs(data[0]["tasa_bcv"] - 40.0) < 0.001
        assert abs(data[0]["tasa_binance"] - 45.0) < 0.001


def test_e2e_34_sugerencias_excluye_orden_con_saldo_real_cero():
    """Bug real (orden S00170): el cálculo naive de sugerencias

    (``monto_total - Vinculaciones``) mostraba saldo $239.67 para una orden
    que en realidad ya estaba saldada según ``/api/reporte-saldos`` (que sí
    resta pagos directos de Odoo, NCs y descuentos del motor) -- el sistema
    sugería aplicarle un pago a una orden ya cerrada. Una orden ausente del
    reporte real (``saldo_con_descuento_bcv``) debe excluirse por completo
    como destino, sin importar lo que diga el cálculo naive local.
    """
    from cxc.web import app as web_app_module

    web_app_module._SALDOS_REALES_CACHE["data"] = None
    web_app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0

    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P1",
                "cliente_id": "C1",
                "monto": "100.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-20",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else ([{"cliente_id": "C1", "nombre": "Cliente Uno"}] if sheet == "Clientes" else [])
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO170",
            cliente_id="C1",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 3, 30),
            fecha_entrega=date(2026, 3, 30),
            monto_total=Decimal("239.67"),  # naive calc mostraría esto como saldo abierto
            lista_precios="4",
            es_primera_compra=False,
            facturada=True,
        ),
    ]

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
        patch(
            "cxc.web.app.get_reporte_saldos",
            new=AsyncMock(return_value={"items": [], "saldo_minimo_pendientes": []}),
        ),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        data = res.json()
        # SO170 no aparece ni en items ni en saldo_minimo_pendientes del
        # reporte real -> saldo real inexistente/cero -> nunca se sugiere.
        assert all(item["so_id"] != "SO170" for item in data)
        # El pago queda sin orden sugerida (huérfano), no aplicado a SO170.
        assert len(data) == 1
        assert data[0]["so_id"] is None


def test_e2e_35_sugerencias_usa_saldo_real_no_calculo_naive():
    """El monto sugerido y el "Saldo Orden" mostrado deben basarse en el

    saldo real de ``/api/reporte-saldos``, no en ``monto_total -
    Vinculaciones`` -- caso: la orden tiene $500 de monto_total sin
    Vinculaciones locales (naive daría $500 de saldo), pero el reporte real
    la reporta con solo $50 pendientes (ej. por un pago directo en Odoo sin
    Vinculación manual, o notas de crédito).
    """
    from cxc.web import app as web_app_module

    web_app_module._SALDOS_REALES_CACHE["data"] = None
    web_app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0

    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P2",
                "cliente_id": "C1",
                "monto": "200.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-20",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else ([{"cliente_id": "C1", "nombre": "Cliente Uno"}] if sheet == "Clientes" else [])
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO2",
            cliente_id="C1",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 6, 1),
            fecha_entrega=date(2026, 6, 1),
            monto_total=Decimal("500.00"),
            lista_precios="4",
            es_primera_compra=False,
            facturada=True,
        ),
    ]

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
        patch(
            "cxc.web.app.get_reporte_saldos",
            new=AsyncMock(
                return_value={
                    "items": [{"so_id": "SO2", "saldo_con_descuento_bcv": 50.0}],
                    "saldo_minimo_pendientes": [],
                }
            ),
        ),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2  # $50 aplicados a SO2 + $150 remanente sin orden
        primero = data[0]
        assert primero["so_id"] == "SO2"
        # Saldo real ($50), no naive ($500).
        assert primero["so_saldo_pendiente"] == 50.0
        assert primero["monto_sugerido"] == 50.0

        segundo = data[1]
        assert segundo["so_id"] is None
        assert abs(segundo["saldo_pago"] - 150.0) < 0.01


def test_e2e_36_sugerencias_cae_a_naive_si_reporte_saldos_falla():
    """Si get_reporte_saldos() falla (Odoo caído, error de datos), sugerencias

    no debe vaciarse por completo -- cae al cálculo naive anterior
    (``monto_total - Vinculaciones``) en vez de tratar "no pude calcular"
    como "saldo cero en todas las órdenes".
    """
    from cxc.web import app as web_app_module

    web_app_module._SALDOS_REALES_CACHE["data"] = None
    web_app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0

    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "P3",
                "cliente_id": "C1",
                "monto": "100.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-20",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else ([{"cliente_id": "C1", "nombre": "Cliente Uno"}] if sheet == "Clientes" else [])
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO3",
            cliente_id="C1",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 6, 1),
            fecha_entrega=date(2026, 6, 1),
            monto_total=Decimal("300.00"),
            lista_precios="4",
            es_primera_compra=False,
            facturada=False,
        ),
    ]

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
        patch(
            "cxc.web.app.get_reporte_saldos",
            new=AsyncMock(side_effect=Exception("Odoo no disponible")),
        ),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["so_id"] == "SO3"
        # Cálculo naive: saldo de la orden = monto_total ($300), sugerido = $100.
        assert data[0]["so_saldo_pendiente"] == 300.0
        assert data[0]["monto_sugerido"] == 100.0


def test_e2e_37_sugerencias_marca_pagos_posiblemente_duplicados():
    """Dos pagos con el mismo cliente, monto, moneda, método de pago y fecha

    -- posible pago cargado dos veces en Odoo (mismo caso real: pago id
    54). Ambos deben marcarse como posible_duplicado, cada uno apuntando al
    otro en duplicado_de, para que el frontend bloquee el auto-vincular y
    solo permita vinculación manual con advertencia.
    """
    from cxc.web import app as web_app_module

    web_app_module._SALDOS_REALES_CACHE["data"] = None
    web_app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0

    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "54",
                "cliente_id": "C1",
                "monto": "100.00",
                "moneda": "USD",
                "metodo_pago": "M1",
                "fecha_pago": "2026-07-20",
                "vendedor": "v@lubrikca.com",
            },
            {
                "pago_id": "55",
                "cliente_id": "C1",
                "monto": "100.00",
                "moneda": "USD",
                "metodo_pago": "M1",
                "fecha_pago": "2026-07-20",
                "vendedor": "v@lubrikca.com",
            },
        ]
        if sheet == "Pagos"
        else ([{"cliente_id": "C1", "nombre": "Cliente Uno"}] if sheet == "Clientes" else [])
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2
        by_id = {item["pago_id"]: item for item in data}
        assert by_id["54"]["posible_duplicado"] is True
        assert by_id["54"]["duplicado_de"] == ["55"]
        assert by_id["55"]["posible_duplicado"] is True
        assert by_id["55"]["duplicado_de"] == ["54"]


def test_e2e_38_sugerencias_no_marca_duplicado_si_algun_campo_difiere():
    """Si cliente, monto, moneda, método o fecha difieren, no se marca como

    posible duplicado -- dos pagos distintos y coincidentes por casualidad
    en algunos campos no deben generar falsos positivos.
    """
    from cxc.web import app as web_app_module

    web_app_module._SALDOS_REALES_CACHE["data"] = None
    web_app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0

    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "60",
                "cliente_id": "C1",
                "monto": "100.00",
                "moneda": "USD",
                "metodo_pago": "M1",
                "fecha_pago": "2026-07-20",
                "vendedor": "v@lubrikca.com",
            },
            {
                # Mismo cliente/monto/moneda/método, pero OTRA fecha -- no es
                # un duplicado, son dos pagos reales distintos.
                "pago_id": "61",
                "cliente_id": "C1",
                "monto": "100.00",
                "moneda": "USD",
                "metodo_pago": "M1",
                "fecha_pago": "2026-07-21",
                "vendedor": "v@lubrikca.com",
            },
        ]
        if sheet == "Pagos"
        else ([{"cliente_id": "C1", "nombre": "Cliente Uno"}] if sheet == "Clientes" else [])
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2
        assert all(item["posible_duplicado"] is False for item in data)
        assert all(item["duplicado_de"] == [] for item in data)


def test_e2e_39_sugerencias_excluye_pago_huerfano_ya_cerrado():
    """Un pago sin orden abierta del cliente (huérfano) que ya fue cerrado

    "a favor de la empresa" (POST /api/conciliaciones/cerrar-pago-huerfano)
    no debe seguir apareciendo en Pagos Pendientes por Asociar.
    """
    from cxc.web import app as web_app_module

    web_app_module._SALDOS_REALES_CACHE["data"] = None
    web_app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0

    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "1002",
                "cliente_id": "C1",
                "monto": "50.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-01",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C1", "nombre": "Cliente Uno"}]
            if sheet == "Clientes"
            else (
                [{"pago_id": "1002", "motivo": "sin orden", "cerrado_por": "admin"}]
                if sheet == "PagosHuerfanosCerrados"
                else []
            )
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        assert res.json() == []


def test_e2e_40_sugerencias_muestra_pago_huerfano_sin_cerrar():
    """El mismo pago huérfano, sin cerrar, debe seguir apareciendo (control

    negativo del test anterior -- confirma que la exclusión es por la
    presencia real en PagosHuerfanosCerrados, no por el pago en sí).
    """
    from cxc.web import app as web_app_module

    web_app_module._SALDOS_REALES_CACHE["data"] = None
    web_app_module._SALDOS_REALES_CACHE["timestamp"] = 0.0

    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "117",
                "cliente_id": "C1",
                "monto": "50.0",
                "moneda": "USD",
                "fecha_pago": "2026-07-01",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else ([{"cliente_id": "C1", "nombre": "Cliente Uno"}] if sheet == "Clientes" else [])
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
    ):
        res = client.get("/api/conciliaciones/sugerencias")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["pago_id"] == "117"
        assert data[0]["so_id"] is None


def test_e2e_41_post_cerrar_pago_huerfano_escribe_fila():
    """POST /api/conciliaciones/cerrar-pago-huerfano hace upsert en

    PagosHuerfanosCerrados con el motivo dado y quién lo cerró -- y NO
    llama a ningún endpoint/método de Odoo (solo marca local).
    """
    mock_repo = MagicMock()

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.post(
            "/api/conciliaciones/cerrar-pago-huerfano",
            json={"pago_id": "117", "motivo": "Cliente cerró operaciones"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "success"

        mock_repo.upsert_pago_huerfano_cerrado.assert_called_once()
        (row,), _ = mock_repo.upsert_pago_huerfano_cerrado.call_args
        assert row["pago_id"] == "117"
        assert row["motivo"] == "Cliente cerró operaciones"


def _fake_execute_pago_conciliado(so_ids: list[str], pago_id: int = 100):
    """Construye un ``execute`` falso donde ``pago_id`` aparece reconciliado

    en Odoo contra una factura por cada ``so_id`` en ``so_ids``.
    """
    invoice_ids = list(range(1, len(so_ids) + 1))

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "search_read":
            return [
                {
                    "id": pago_id,
                    "partner_id": False,
                    "amount": 500.0,
                    "amount_ref": 500.0,
                    "amount_available_for_refund": 0.0,
                    "currency_id": [1, "USD"],
                    "journal_id": False,
                    "date": "2026-07-01",
                    "reconciled_invoice_ids": invoice_ids,
                }
            ]
        if model == "account.move" and method == "read":
            return [
                {
                    "id": inv_id,
                    "name": f"INV/{inv_id:03d}",
                    "invoice_origin": so_id,
                    "move_type": "out_invoice",
                    "state": "posted",
                    "amount_total_signed_usd": 500.0,
                    "amount_residual_usd": 0.0,
                }
                for inv_id, so_id in zip(invoice_ids, so_ids, strict=True)
            ]
        return []

    return fake_execute


def test_e2e_42_odoo_prevalece_revincula_vinculacion_a_orden_correcta():
    """Regla general: si Odoo reconcilió un pago contra una orden distinta a

    la Vinculación local, la Vinculación debe seguir a Odoo automáticamente
    -- caso simple, sin ambigüedad (una Vinculación local, una orden en
    Odoo). Debe quedar rastro en BandejaAuditoria.
    """
    from cxc.web.app import _resincronizar_vinculaciones_con_odoo

    mock_repo = MagicMock()
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="V1",
            pago_id="100",
            so_id="SO_A",
            monto_aplicado=Decimal("500.00"),
            hora_pago_confirmada=datetime(2026, 7, 1),
            tasa_bcv_aplicada=Decimal("40.0"),
            tasa_binance_aplicada=Decimal("45.0"),
            es_tasa_heredada=False,
        )
    ]

    fake_execute = _fake_execute_pago_conciliado(["SO_B"])
    cambios = _resincronizar_vinculaciones_con_odoo(mock_repo, fake_execute)

    assert cambios == [
        {
            "pago_id": "100",
            "so_id_anterior": "SO_A",
            "so_id_nuevo": "SO_B",
            "requiere_revision_manual": False,
        }
    ]

    mock_repo.update_vinculaciones.assert_called_once()
    (vincs_actualizadas,), _ = mock_repo.update_vinculaciones.call_args
    assert len(vincs_actualizadas) == 1
    assert vincs_actualizadas[0].vinc_id == "V1"
    assert vincs_actualizadas[0].so_id == "SO_B"
    # monto_aplicado NO se toca -- solo se corrige a qué orden cuenta el pago.
    assert vincs_actualizadas[0].monto_aplicado == Decimal("500.00")

    mock_repo.append_auditoria_rows.assert_called_once()
    (audit_rows,), _ = mock_repo.append_auditoria_rows.call_args
    assert len(audit_rows) == 1
    assert audit_rows[0]["so_id"] == "SO_B"
    assert audit_rows[0]["tipo_auditoria"] == "vinculacion_revinculada_por_odoo"
    assert audit_rows[0]["estado"] == "aplicado"


def test_e2e_43_odoo_prevalece_no_toca_vinculacion_ya_correcta():
    """Si la Vinculación local ya coincide con lo que Odoo reconcilió, no se

    debe tocar nada -- ni update_vinculaciones ni auditoría.
    """
    from cxc.web.app import _resincronizar_vinculaciones_con_odoo

    mock_repo = MagicMock()
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="V1",
            pago_id="100",
            so_id="SO_B",
            monto_aplicado=Decimal("500.00"),
            hora_pago_confirmada=datetime(2026, 7, 1),
            tasa_bcv_aplicada=Decimal("40.0"),
            tasa_binance_aplicada=Decimal("45.0"),
            es_tasa_heredada=False,
        )
    ]

    fake_execute = _fake_execute_pago_conciliado(["SO_B"])
    cambios = _resincronizar_vinculaciones_con_odoo(mock_repo, fake_execute)

    assert cambios == []
    mock_repo.update_vinculaciones.assert_not_called()
    mock_repo.append_auditoria_rows.assert_not_called()


def test_e2e_44_odoo_prevalece_caso_ambiguo_no_autocorrige():
    """Si Odoo reconcilió el pago contra VARIAS órdenes a la vez (multi-

    factura), no hay forma inequívoca de reasignar la Vinculación local --
    se registra la discrepancia en auditoría para revisión manual, pero no
    se toca la Vinculación.
    """
    from cxc.web.app import _resincronizar_vinculaciones_con_odoo

    mock_repo = MagicMock()
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="V1",
            pago_id="100",
            so_id="SO_A",
            monto_aplicado=Decimal("500.00"),
            hora_pago_confirmada=datetime(2026, 7, 1),
            tasa_bcv_aplicada=Decimal("40.0"),
            tasa_binance_aplicada=Decimal("45.0"),
            es_tasa_heredada=False,
        )
    ]

    fake_execute = _fake_execute_pago_conciliado(["SO_C", "SO_D"])
    cambios = _resincronizar_vinculaciones_con_odoo(mock_repo, fake_execute)

    assert len(cambios) == 1
    assert cambios[0]["requiere_revision_manual"] is True

    mock_repo.update_vinculaciones.assert_not_called()
    mock_repo.append_auditoria_rows.assert_called_once()
    (audit_rows,), _ = mock_repo.append_auditoria_rows.call_args
    assert audit_rows[0]["tipo_auditoria"] == "vinculacion_discrepancia_multi_orden"
    assert audit_rows[0]["estado"] == "pendiente_revision"


def test_e2e_45_sugerencias_ignora_binance_implausible_del_historico():
    """Bug real (pago Odoo 29, tras el fix de tax_today): el spreadsheet usa

    locale es_ES (coma decimal); un valor Binance sembrado como NUMBER
    nativo se leía formateado con coma ("451,5072") y gspread lo
    "numericizaba" quitando la coma como si fuera separador de miles,
    guardando 4515072 en vez de 451.5072 -- un valor ~10000x mayor que la
    tasa BCV del mismo día, que producía un monto_pago_binance ~$0.01.
    La guardia de plausibilidad debe descartar ese dato corrupto y caer al
    fallback de SerieTasas en vez de aplicar una tasa disparatada.
    """
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "29",
                "cliente_id": "C_ZIP",
                "monto": "36196.75",
                "moneda": "VES",
                "fecha_pago": "2026-03-18",
                "vendedor": "v@lubrikca.com",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C_ZIP", "nombre": "ZIP MARKET, CA"}]
            if sheet == "Clientes"
            else (
                [
                    {
                        "timestamp": "2026-03-18 12:00:00",
                        "tasa_bcv": "451.5072",
                        "tasa_binance": "521.66",
                    }
                ]
                if sheet == "SerieTasas"
                else (
                    # Fila corrupta: dato del día exacto, pero con el bug de
                    # locale/numericise ya aplicado (punto decimal borrado).
                    [
                        {
                            "fecha": "2026-03-18",
                            "tasa_binance_promedio_diario": "5216600",
                        }
                    ]
                    if sheet == "TasasHistoricasAuditoria"
                    else []
                )
            )
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return [
                {
                    "id": 29,
                    "name": "PBAMI/2026/00009",
                    "tax_today": 451.5072,
                    "amount_ref": 80.17,
                    "is_reconciled": False,
                    "state": "in_process",
                    "reconciled_invoices_count": 0,
                }
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
        assert len(data) == 1
        item = data[0]
        # BCV sigue viniendo de Odoo (tax_today), sin cambios.
        assert abs(item["tasa_bcv"] - 451.5072) < 0.001
        # Binance NO debe ser el valor corrupto (5216600) -- se descarta por
        # implausible (ratio > 3x contra BCV) y cae a SerieTasas (521.66).
        assert abs(item["tasa_binance"] - 521.66) < 0.001
        assert item["tasa_binance"] < 1000


def test_e2e_46_get_eur_rate_for_date_lookup_dia_exacto():
    """``get_eur_rate_for_date`` -- mismo criterio que ``get_binance_rate_for_date``:

    lookup por día EXACTO en TasasHistoricasAuditoria, sin caer a otro día.
    """
    from cxc.web.app import get_eur_rate_for_date

    rows = [
        {"fecha": "2026-03-17", "tasa_bcv_euro": "500.0"},
        {"fecha": "2026-03-18", "tasa_bcv_euro": "520.642"},
    ]
    assert get_eur_rate_for_date(date(2026, 3, 18), rows) == Decimal("520.642")
    assert get_eur_rate_for_date(date(2026, 3, 19), rows) is None


def test_e2e_47_resolve_metodo_pago_nombre_batch_journals():
    """``resolve_metodo_pago_nombre`` trae el catálogo completo de

    ``account.journal`` y arma un mapa id -> nombre; sin ``execute`` (Odoo
    no disponible) devuelve un dict vacío en vez de fallar.
    """
    from cxc.web.app import resolve_metodo_pago_nombre

    def fake_execute(model, method, args, kwargs=None):
        assert model == "account.journal"
        return [{"id": 7, "name": "Banco Mercantil USD"}, {"id": 9, "name": "Efectivo VES"}]

    assert resolve_metodo_pago_nombre(fake_execute) == {7: "Banco Mercantil USD", 9: "Efectivo VES"}
    assert resolve_metodo_pago_nombre(None) == {}


def test_e2e_48_resolve_vendedor_validado_detecta_cambio_de_vendedor():
    """``resolve_vendedor_validado`` -- el cliente cambió de vendedor desde que

    se creó la orden: se prefiere el vendedor VIGENTE del cliente, y se
    marca ``mismatch=True`` para revisión humana (no se autocorrige nada).
    """
    from cxc.web.app import resolve_vendedor_validado

    clientes_map = {
        "C1": Cliente(cliente_id="C1", nombre="Cliente Uno", vendedor_email="nuevo@lubrikca.com")
    }
    ordenes_map = {
        "S00001": OrdenVenta(
            so_id="S00001",
            cliente_id="C1",
            fecha=date(2026, 1, 1),
            fecha_entrega=None,
            monto_total=Decimal("100"),
            lista_precios="USD",
            vendedor_email="viejo@lubrikca.com",
            es_primera_compra=False,
        )
    }

    vendedor, mismatch = resolve_vendedor_validado("C1", "S00001", clientes_map, ordenes_map)
    assert vendedor == "nuevo@lubrikca.com"
    assert mismatch is True

    # Mismo vendedor en ambos lados -- sin discrepancia.
    ordenes_map["S00001"].vendedor_email = "nuevo@lubrikca.com"
    _, mismatch_ok = resolve_vendedor_validado("C1", "S00001", clientes_map, ordenes_map)
    assert mismatch_ok is False

    # Sin orden sugerida -- se usa el vendedor del cliente, sin discrepancia
    # posible (no hay con qué comparar).
    vendedor_sin_so, mismatch_sin_so = resolve_vendedor_validado(
        "C1", None, clientes_map, ordenes_map
    )
    assert vendedor_sin_so == "nuevo@lubrikca.com"
    assert mismatch_sin_so is False


def test_e2e_49_leer_pagos_huerfanos_cerrados_expone_detalle():
    """``leer_pagos_huerfanos_cerrados`` -- lector compartido que expone el

    detalle completo (motivo, cerrado_por, timestamp), no solo el set de
    ids que se usaba antes para excluir de "pendientes".
    """
    from cxc.web.app import leer_pagos_huerfanos_cerrados

    mock_repo = MagicMock()
    mock_repo.all_pagos_huerfanos_cerrados.return_value = [
        {
            "pago_id": "1002",
            "motivo": "Sin orden abierta del cliente",
            "cerrado_por": "admin@lubrikca.com",
            "timestamp_cierre": "2026-07-01T10:00:00",
        }
    ]

    detalle = leer_pagos_huerfanos_cerrados(mock_repo)
    mock_repo.all_pagos_huerfanos_cerrados.assert_called_once_with()
    assert set(detalle.keys()) == {"1002"}
    assert detalle["1002"]["motivo"] == "Sin orden abierta del cliente"
    assert detalle["1002"]["cerrado_por"] == "admin@lubrikca.com"


def test_e2e_50_cobranza_pagos_unificado_pendiente_con_3_tasas():
    """``GET /api/cobranza/pagos`` -- endpoint unificado (PR 2). Un pago VES

    huérfano (sin orden abierta del cliente) debe quedar como fila
    "pendiente" con las 3 tasas (BCV vía Odoo tax_today, Binance vía
    TasasHistoricasAuditoria, y la NUEVA tasa EUR), el método de pago
    resuelto a nombre real (no id), el vendedor tomado del Cliente, y
    marcado como candidato a "cerrar a favor de la empresa" (sin orden).
    """
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "777",
                "cliente_id": "C1",
                "monto": "50000.00",
                "moneda": "VES",
                "fecha_pago": "2026-03-18",
                "vendedor_email": "actual@lubrikca.com",
                "metodo_pago": "5",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C1", "nombre": "Cliente Uno", "vendedor_email": "actual@lubrikca.com"}]
            if sheet == "Clientes"
            else (
                [
                    {
                        "fecha": "2026-03-18",
                        "tasa_binance_promedio_diario": "550.0",
                        "tasa_bcv_euro": "600.0",
                    }
                ]
                if sheet == "TasasHistoricasAuditoria"
                else []
            )
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_auditoria.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            domain = args[0] if args else []
            if ["is_reconciled", "=", True] in domain:
                return []  # get_live_pagos_conciliados -- este pago no está reconciliado
            return [
                {
                    "id": 777,
                    "name": "PBAMI/2026/00099",
                    "tax_today": 500.0,
                    "amount_ref": 100.0,
                    "is_reconciled": False,
                    "state": "in_process",
                    "reconciled_invoices_count": 0,
                }
            ]
        if model == "account.journal":
            return [{"id": 5, "name": "Banco Mercantil VES"}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=fake_execute),
    ):
        res = client.get("/api/cobranza/pagos")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        item = data[0]

        assert item["pago_id"] == "777"
        assert item["numero_pago_odoo"] == "PBAMI/2026/00099"
        assert item["estado"] == "pendiente"
        assert item["metodo_pago"] == "Banco Mercantil VES"
        assert item["vendedor"] == "actual@lubrikca.com"
        assert item["vendedor_mismatch"] is False
        assert item["moneda_pago"] == "VES"
        assert item["monto_pago_original"] == 50000.0

        # Las 3 tasas del día, siempre presentes.
        assert abs(item["tasa_bcv"] - 500.0) < 0.01
        assert abs(item["tasa_binance"] - 550.0) < 0.01
        assert abs(item["tasa_bcv_eur"] - 600.0) < 0.01

        # Equivalentes: BCV usa amount_ref de Odoo (100.0), EUR se calcula
        # sobre el monto original VES (50000 / 600 = 83.33).
        assert abs(item["monto_pago_bcv_usd"] - 100.0) < 0.01
        assert abs(item["monto_pago_eur"] - 83.33) < 0.01

        assert item["so_id"] is None
        assert item["puede_cerrar_huerfano"] is True
        assert item["puede_vincular"] is True
        assert item["posible_duplicado"] is False


def test_e2e_51_cobranza_pagos_unificado_cerrado_empresa_y_usd_1a1():
    """Un pago cerrado "a favor de la empresa" (PagosHuerfanosCerrados) debe

    aparecer en el endpoint unificado con ``estado="cerrado_empresa"`` y su
    detalle (motivo/cerrado_por) -- antes estos pagos eran invisibles fuera
    del filtro de exclusión de "pendientes". Además, siendo un pago en USD,
    el equivalente EUR debe ser 1:1 (igual que BCV/Binance) sin depender de
    que exista una tasa EUR para ese día.
    """
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "999",
                "cliente_id": "C2",
                "monto": "250.00",
                "moneda": "USD",
                "fecha_pago": "2026-07-01",
                "vendedor_email": "v@lubrikca.com",
                "metodo_pago": "",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C2", "nombre": "Cliente Dos", "vendedor_email": "v@lubrikca.com"}]
            if sheet == "Clientes"
            else (
                [
                    {
                        "pago_id": "999",
                        "motivo": "Sin orden abierta del cliente",
                        "cerrado_por": "admin@lubrikca.com",
                        "timestamp_cierre": "2026-07-01T10:00:00",
                    }
                ]
                if sheet == "PagosHuerfanosCerrados"
                else []
            )
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_auditoria.return_value = []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=None),
    ):
        res = client.get("/api/cobranza/pagos")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        item = data[0]

        assert item["pago_id"] == "999"
        assert item["estado"] == "cerrado_empresa"
        assert item["cerrado_motivo"] == "Sin orden abierta del cliente"
        assert item["cerrado_por"] == "admin@lubrikca.com"
        assert item["moneda_pago"] == "USD"
        assert item["monto_pago_original"] == 250.0
        # USD es 1:1 -- no depende de conocer la tasa EUR del día.
        assert item["monto_pago_eur"] == 250.0
        assert item["puede_vincular"] is False
        assert item["puede_cerrar_huerfano"] is False
