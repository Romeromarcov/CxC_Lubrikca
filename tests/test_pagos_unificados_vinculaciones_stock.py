"""Pruebas integrales Fase 4/5: Pagos Unificados, Vinculaciones y Stock Libre."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from cxc.db import schema
from cxc.documentos.catalogo import ItemCatalogo, verificar_stock_lineas_catalogo
from cxc.documentos.modelos import ENVIADO_ODOO
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.documentos.servicio import ServicioDocumentos
from cxc.models import Cliente, MetodoPago, Moneda, OrdenVenta, Pago, Producto, SerieTasa, TipoTasa
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import RepoCuentasTercerosSQL
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web.app import app
from cxc.web.documentos_api import get_servicio_documentos
from cxc.web.operativos_api import get_repo_terceros, get_servicio_operativos

client = TestClient(app)

ADMIN_USER = {
    "email": "admin@lubrikca.com",
    "nombre": "Admin Prueba",
    "rol": "admin",
    "permisos": efectivos("admin"),
}
VENTAS_USER = {
    "email": "vend1@lubrikca.com",
    "nombre": "Carlos Vendedor",
    "rol": "ventas",
    "permisos": efectivos("ventas"),
}


def _sqlite_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    schema.metadata.create_all(
        eng,
        tables=[
            schema.clientes,
            schema.ordenes_venta,
            schema.lineas_orden,
            schema.pagos,
            schema.metodos_pago,
            schema.serie_tasas,
            schema.tasas_historicas_auditoria,
            schema.bandeja_facturacion,
            schema.vinculaciones,
            schema.facturas,
            schema.bandeja_auditoria,
            schema.descuentos_sistema_aprobados,
            schema.descuentos_no_otorgados,
            schema.app_settings,
            schema.usuarios_plataforma,
            schema.lotes_caja_chica,
            schema.cuentas_terceros,
            schema.documentos_operativos,
            schema.documentos_comerciales,
            schema.documentos_comerciales_lineas,
            schema.lotes_recibo_efectivo,
            schema.orden_descuentos_ajustes,
            schema.pagos_huerfanos_cerrados,
            schema.vinculaciones_ventas_op,
            schema.vinculaciones_compras,
            schema.bancos_archivos,
            schema.bancos_movimientos,
            schema.bancos_propuestas,
            schema.bancos_decisiones,
        ],
    )
    return eng


class _RepoPruebaSQL(InMemoryRepository):
    def __init__(self, engine: Any) -> None:
        super().__init__()
        self._engine = engine
        self._pagos_prov: list[dict[str, Any]] = []
        self._compras_odoo: list[dict[str, Any]] = []
        self._cerrados: list[dict[str, Any]] = []
        self._auditoria_list: list[Any] = []
        self._usuarios: list[dict[str, str]] = [
            {"email": "vend1@lubrikca.com", "nombre": "Carlos Vendedor", "user_id": "v1"},
            {"email": "vend2@lubrikca.com", "nombre": "Otro Vendedor", "user_id": "v2"},
            {"email": "admin@lubrikca.com", "nombre": "Admin Prueba", "user_id": "adm"},
        ]

    def all_usuarios_plataforma(self) -> list[dict[str, str]]:
        return list(self._usuarios)

    def all_pagos_proveedores_odoo(self) -> list[dict[str, Any]]:
        return list(self._pagos_prov)

    def all_compras_gastos_odoo(self) -> list[dict[str, Any]]:
        return list(self._compras_odoo)

    def all_pagos_huerfanos_cerrados(self) -> list[dict[str, Any]]:
        return list(self._cerrados)

    def all_auditoria(self) -> list[Any]:
        return list(self._auditoria_list)


def _setup_base(eng: Any) -> _RepoPruebaSQL:
    repo = _RepoPruebaSQL(eng)
    repo.upsert_clientes(
        [
            Cliente("1001", "Cliente Alpha C.A.", "vend1@lubrikca.com"),
            Cliente("1002", "Cliente Beta C.A.", "vend2@lubrikca.com"),
            Cliente("601", "Proveedor Lubricantes S.A.", ""),
        ]
    )
    repo.add_metodo_pago(
        MetodoPago("11", "Efectivo USD", Moneda.USD, TipoTasa.BINANCE, es_contado=True)
    )
    repo.add_metodo_pago(MetodoPago("12", "Banesco VES", Moneda.VES, TipoTasa.BCV, es_contado=True))
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 20, 9, 0),
            tasa_bcv=Decimal("50.00"),
            tasa_binance=Decimal("55.00"),
            tasa_bcv_euro=Decimal("54.00"),
            fuente="api",
            es_heredada=False,
            capturada_ok=True,
        )
    )
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S00501",
                cliente_id="1001",
                fecha=date(2026, 11, 10),
                fecha_entrega=date(2026, 11, 10),
                monto_total=Decimal("100.00"),
                lista_precios="1",
                vendedor_email="vend1@lubrikca.com",
                es_primera_compra=False,
                estado_orden="sale",
                facturada=True,
                factura_id="8801",
                odoo_order_id=9501,
            ),
            OrdenVenta(
                so_id="S00502",
                cliente_id="1001",
                fecha=date(2026, 11, 18),
                fecha_entrega=date(2026, 11, 18),
                monto_total=Decimal("200.00"),
                lista_precios="1",
                vendedor_email="vend1@lubrikca.com",
                es_primera_compra=False,
                estado_orden="sale",
                facturada=False,
                odoo_order_id=9502,
            ),
        ]
    )
    with eng.begin() as conn:
        conn.execute(
            insert(schema.clientes).values(
                [
                    {
                        "cliente_id": "1001",
                        "nombre": "Cliente Alpha C.A.",
                        "vendedor_email": "vend1@lubrikca.com",
                    },
                    {
                        "cliente_id": "1002",
                        "nombre": "Cliente Beta C.A.",
                        "vendedor_email": "vend2@lubrikca.com",
                    },
                ]
            )
        )
        conn.execute(
            insert(schema.ordenes_venta).values(
                [
                    {
                        "so_id": "S00501",
                        "odoo_order_id": 9501,
                        "cliente_id": "1001",
                        "fecha": date(2026, 11, 10),
                        "fecha_entrega": date(2026, 11, 10),
                        "monto_total": Decimal("100.00"),
                        "lista_precios": "1",
                        "vendedor_email": "vend1@lubrikca.com",
                        "es_primera_compra": False,
                        "facturada": True,
                        "factura_id": "8801",
                        "estado_orden": "sale",
                    },
                    {
                        "so_id": "S00502",
                        "odoo_order_id": 9502,
                        "cliente_id": "1001",
                        "fecha": date(2026, 11, 18),
                        "fecha_entrega": date(2026, 11, 18),
                        "monto_total": Decimal("200.00"),
                        "lista_precios": "1",
                        "vendedor_email": "vend1@lubrikca.com",
                        "es_primera_compra": False,
                        "facturada": False,
                        "factura_id": None,
                        "estado_orden": "sale",
                    },
                ]
            )
        )
        conn.execute(
            insert(schema.documentos_comerciales).values(
                numero="PED-00502",
                token_preview="tok-ped-502",
                tipo="pedido",
                estado=ENVIADO_ODOO,
                origen="bot",
                partner_odoo_id=1001,
                cliente_nombre="Cliente Alpha C.A.",
                lista_precios_id=1,
                lista_precios_nombre="Pago USD",
                moneda_lista="USD",
                vendedor_email="vend1@lubrikca.com",
                es_primera_compra=False,
                auto_enviado=False,
                total_con_iva=Decimal("200.00"),
                subtotal=Decimal("172.41"),
                odoo_order_id=9502,
                odoo_order_name="S00502",
                creado_por="vend1@lubrikca.com",
                creado_en=datetime(2026, 11, 18, 10, 0),
                actualizado_en=datetime(2026, 11, 18, 10, 0),
            )
        )
    return repo


def test_pagos_unificados_filtros_cerrados_y_alcance_vendedor() -> None:
    eng = _sqlite_engine()
    repo = _setup_base(eng)
    repo.upsert_pagos(
        [
            Pago(
                pago_id="7701",
                cliente_id="1001",
                monto=Decimal("150.00"),
                moneda=Moneda.USD,
                metodo_pago="11",
                fecha_pago=datetime(2026, 11, 19, 10, 0),
                vendedor_email="vend1@lubrikca.com",
                numero_odoo="PUSD/2026/0100",
            ),
            Pago(
                pago_id="7702",
                cliente_id="1002",
                monto=Decimal("5000.00"),
                moneda=Moneda.VES,
                metodo_pago="12",
                fecha_pago=datetime(2026, 11, 19, 11, 0),
                vendedor_email="vend2@lubrikca.com",
                numero_odoo="PVES/2026/0101",
            ),
        ]
    )
    repo._cerrados = [
        {
            "pago_id": "7702",
            "motivo": "Diferencia menor a favor de la empresa",
            "cerrado_por": "admin@lubrikca.com",
            "timestamp_cierre": "2026-11-20T08:30:00",
        }
    ]
    with eng.begin() as conn:
        # Pago CxC pendiente por vincular (duplicate alert with 7701 same client/amount/date)
        conn.execute(
            insert(schema.documentos_operativos).values(
                numero="PAG-00100",
                token_preview="tok-pag-100",
                tipo="pago",
                estado="PENDIENTE_APROBACION",
                origen="bot",
                partner_odoo_id=1001,
                contraparte_nombre="Cliente Alpha C.A.",
                monto=Decimal("150.00"),
                moneda="USD",
                metodo_pago="11",
                diario_nombre="Efectivo USD",
                fecha_documento=date(2026, 11, 19),
                es_zelle_tercero=False,
                confirmado_cxc=False,
                sin_factura=False,
                creado_por="vend1@lubrikca.com",
                creado_en=datetime(2026, 11, 19, 12, 0),
                actualizado_en=datetime(2026, 11, 19, 12, 0),
            )
        )

    srv_op = ServicioOperativos(RepoOperativosSQL(eng), lambda: None, repo_cxc=repo)
    repo_ter = RepoCuentasTercerosSQL(eng)
    app.dependency_overrides[get_servicio_operativos] = lambda: srv_op
    app.dependency_overrides[get_repo_terceros] = lambda: repo_ter
    try:
        with (
            patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN_USER),
            patch("cxc.web.app.get_repo", return_value=repo),
        ):
            r = client.get("/api/pagos/unificados?direccion=recibidos&limit=50")
            assert r.status_code == 200, r.text
            data = r.json()
            # 7702 está cerrado a favor empresa: sale en cerrados_empresa, NO en tabla principal
            ids_activos = {x.get("odoo_pago_id") or x.get("numero") for x in data["items"]}
            assert "7702" not in ids_activos
            assert len(data["cerrados_empresa"]) == 1
            assert data["cerrados_empresa"][0]["pago_id"] == "7702"
            assert "Diferencia" in data["cerrados_empresa"][0]["cerrado_motivo"]

            # 7701 tiene sugerencia FIFO hacia S00501 (más antigua: 2026-11-10)
            p7701 = next(x for x in data["items"] if x.get("odoo_pago_id") == "7701")
            assert p7701["sugerencia_fifo"]["so_id"] == "S00501"
            assert p7701["posible_duplicado"] is True

            # Filtrar explícitamente cerrados
            r_c = client.get("/api/pagos/unificados?direccion=recibidos&aplicacion=cerrado_empresa")
            assert r_c.status_code == 200
            assert len(r_c.json()["items"]) == 1

            # Filtrar solo duplicados
            r_dup = client.get("/api/pagos/unificados?direccion=recibidos&solo_duplicados=true")
            assert r_dup.status_code == 200
            assert len(r_dup.json()["items"]) >= 2

        # Alcance vendedor: vend1 solo ve sus propios pagos
        with (
            patch("cxc.web.app.get_current_user_from_cookie", return_value=VENTAS_USER),
            patch("cxc.web.app.get_repo", return_value=repo),
        ):
            r_v = client.get("/api/pagos/unificados?direccion=recibidos&aplicacion=cerrado_empresa")
            assert r_v.status_code == 200
            assert r_v.json()["items"] == []
    finally:
        app.dependency_overrides.clear()


def test_vinculaciones_ventas_fifo_y_reconciliacion_odoo() -> None:
    eng = _sqlite_engine()
    repo = _setup_base(eng)
    repo.upsert_pagos(
        [
            Pago(
                pago_id="7701",
                cliente_id="1001",
                monto=Decimal("120.00"),
                moneda=Moneda.USD,
                metodo_pago="11",
                fecha_pago=datetime(2026, 11, 19, 10, 0),
                vendedor_email="vend1@lubrikca.com",
                numero_odoo="PUSD/2026/0100",
            )
        ]
    )

    class _CatVacio:
        def listar(self, refresh: bool = False) -> list:
            return []

    srv_op = ServicioOperativos(RepoOperativosSQL(eng), lambda: None, repo_cxc=repo)
    srv_docs = ServicioDocumentos(RepoDocumentosSQL(eng), _CatVacio(), lambda: None, repo_cxc=repo)
    app.dependency_overrides[get_servicio_operativos] = lambda: srv_op
    app.dependency_overrides[get_servicio_documentos] = lambda: srv_docs
    app.dependency_overrides[get_repo_terceros] = lambda: RepoCuentasTercerosSQL(eng)

    escritor_ok = MagicMock(spec=EscritorOdoo)
    escritor_ok.reconciliar_pago_con_documento.return_value = {
        "reconciliado": True,
        "factura_move_id": 8801,
        "motivo": "Conciliado automáticamente en Odoo (move 8801).",
    }

    try:
        with (
            patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN_USER),
            patch("cxc.web.app.get_repo", return_value=repo),
            patch("cxc.web.documentos_api._escritor_vinc_ventas", return_value=escritor_ok),
        ):
            # 1) Órdenes candidatas para el pago 7701 -> sugiere S00501 primero (FIFO)
            r_cand = client.get("/api/pagos/vinculaciones/ordenes-candidatas?pago_id=7701")
            assert r_cand.status_code == 200, r_cand.text
            cands = r_cand.json()["candidatos"]
            assert [c["so_id"] for c in cands] == ["S00501", "S00502"]
            assert cands[0]["sugerido_fifo"] is True
            assert cands[0]["monto_sugerido_usd"] == 100.0
            assert cands[1]["monto_sugerido_usd"] == 20.0

            # 2) Vincular $100 a S00501 -> reconcilia automáticamente en Odoo
            r_link1 = client.post(
                "/api/ventas/vinculaciones",
                json={
                    "so_id": "S00501",
                    "pago_id": "7701",
                    "monto_aplicado": 100.0,
                    "origen_vinculacion": "fifo",
                },
            )
            assert r_link1.status_code == 200, r_link1.text
            res1 = r_link1.json()
            assert res1["odoo_reconciliado"] is True
            escritor_ok.reconciliar_pago_con_documento.assert_called_once()

            # 3) Vincular remanente ($20) a S00502 (sin factura -> CxC no bloqueante)
            escritor_ok.reconciliar_pago_con_documento.reset_mock()
            escritor_ok.buscar_factura_abierta_de_orden.return_value = None
            r_link2 = client.post(
                "/api/ventas/vinculaciones",
                json={
                    "so_id": "S00502",
                    "pago_id": "7701",
                    "monto_aplicado": 20.0,
                    "origen_vinculacion": "manual",
                },
            )
            assert r_link2.status_code == 200, r_link2.text
            res2 = r_link2.json()
            assert res2["odoo_reconciliado"] is False
            assert "factura" in res2["odoo_motivo"].lower()

            # 4) Intentar sobreaplicar ($5 más cuando disp = $0) -> 422
            r_over = client.post(
                "/api/ventas/vinculaciones",
                json={"so_id": "S00502", "pago_id": "7701", "monto_aplicado": 5.0},
            )
            assert r_over.status_code == 422

            # 5) Verificar en ventas/unificadas el resumen vinculado en S00502
            r_v = client.get("/api/ventas/unificadas?q=S00502")
            assert r_v.status_code == 200
            so502 = next(x for x in r_v.json()["items"] if x.get("odoo_order_name") == "S00502")
            assert so502["vinculaciones_count"] == 1
            assert so502["monto_vinculado_usd"] == 20.0
            assert so502["saldo_pendiente_vinc_usd"] == 180.0

            # 6) Probar Route 2 (cxc_op): PAG-* CxC a S00502 y desvincular -> 200
            with eng.begin() as conn:
                res_p = conn.execute(
                    insert(schema.documentos_operativos).values(
                        numero="PAG-00105",
                        token_preview="tok-pag-105",
                        tipo="pago",
                        estado="CONFIRMADO_CXC",
                        origen="bot",
                        partner_odoo_id=1001,
                        contraparte_nombre="Cliente Alpha C.A.",
                        monto=Decimal("50.00"),
                        moneda="USD",
                        metodo_pago="11",
                        fecha_documento=date(2026, 11, 19),
                        es_zelle_tercero=False,
                        confirmado_cxc=True,
                        sin_factura=False,
                        creado_por="vend1@lubrikca.com",
                        creado_en=datetime(2026, 11, 19, 13, 0),
                        actualizado_en=datetime(2026, 11, 19, 13, 0),
                    )
                )
                pago_op_id = int(res_p.inserted_primary_key[0])

            r_link3 = client.post(
                "/api/ventas/vinculaciones",
                json={
                    "so_id": "S00502",
                    "pago_operativo_id": pago_op_id,
                    "monto_aplicado": 50.0,
                    "origen_vinculacion": "manual",
                },
            )
            assert r_link3.status_code == 200, r_link3.text
            res3 = r_link3.json()
            assert res3["vinculacion"]["origen_registro"] == "cxc_op"
            vinc_op_id = res3["vinculacion"]["vinc_id"]

            r_del = client.delete(f"/api/ventas/vinculaciones/cxc_op/{vinc_op_id}")
            assert r_del.status_code == 200, r_del.text

            # Confirmar bloqueo 409 cuando ya fue conciliada en Odoo (Route 2 con odoo_res_id)
            with eng.begin() as conn:
                res_p2 = conn.execute(
                    insert(schema.documentos_operativos).values(
                        numero="PAG-00106",
                        token_preview="tok-pag-106",
                        tipo="pago",
                        estado=ENVIADO_ODOO,
                        origen="bot",
                        partner_odoo_id=1001,
                        contraparte_nombre="Cliente Alpha C.A.",
                        monto=Decimal("30.00"),
                        moneda="USD",
                        metodo_pago="11",
                        fecha_documento=date(2026, 11, 19),
                        odoo_model="account.payment",
                        odoo_res_id=7799,
                        odoo_ref="PUSD/2026/0199",
                        es_zelle_tercero=False,
                        confirmado_cxc=True,
                        sin_factura=False,
                        creado_por="vend1@lubrikca.com",
                        creado_en=datetime(2026, 11, 19, 13, 30),
                        actualizado_en=datetime(2026, 11, 19, 13, 30),
                    )
                )
                pago_op_id2 = int(res_p2.inserted_primary_key[0])

            escritor_ok.buscar_factura_abierta_de_orden.return_value = 8802
            r_link_rec = client.post(
                "/api/ventas/vinculaciones",
                json={
                    "so_id": "S00502",
                    "pago_operativo_id": pago_op_id2,
                    "monto_aplicado": 10.0,
                },
            )
            assert r_link_rec.status_code == 200, r_link_rec.text
            assert r_link_rec.json()["odoo_reconciliado"] is True
            vid_rec = r_link_rec.json()["vinculacion"]["vinc_id"]
            r_del_rec = client.delete(f"/api/ventas/vinculaciones/cxc_op/{vid_rec}")
            assert r_del_rec.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_vinculaciones_compras_pagos_y_estado_cuenta_cxp() -> None:
    eng = _sqlite_engine()
    repo = _setup_base(eng)
    repo._compras_odoo = [
        {
            "move_id": "4401",
            "numero_odoo": "BILL/2026/001",
            "move_type": "in_invoice",
            "tipo_cxc": "factura_proveedor",
            "partner_odoo_id": 601,
            "proveedor_nombre": "Proveedor Lubricantes S.A.",
            "rif": "J-30111222-3",
            "monto_total": 300.0,
            "moneda": "USD",
            "fecha_documento": "2026-11-12",
            "estado_odoo": "posted",
        }
    ]
    repo._pagos_prov = [
        {
            "pago_id": "8805",
            "numero_odoo": "PPR/2026/005",
            "partner_odoo_id": 601,
            "proveedor_nombre": "Proveedor Lubricantes S.A.",
            "monto": 180.0,
            "moneda": "USD",
            "fecha_pago": "2026-11-18",
            "diario_nombre": "Bank USD",
            "referencia": "TRF-8805",
        }
    ]
    with eng.begin() as conn:
        # También una cuenta tercero + Zelle 3ro hacia el mismo proveedor 601
        res_ct = conn.execute(
            insert(schema.cuentas_terceros).values(
                alias="Zelle Proveedor 601",
                titular="Proveedor Lubricantes S.A.",
                proveedor_odoo_id=601,
                proveedor_nombre="Proveedor Lubricantes S.A.",
                activo=True,
                creado_por="admin@lubrikca.com",
                creado_en=datetime(2026, 11, 10, 9, 0),
                actualizado_en=datetime(2026, 11, 10, 9, 0),
            )
        )
        ct_id = int(res_ct.inserted_primary_key[0])
        conn.execute(
            insert(schema.documentos_operativos).values(
                numero="PAG-00201",
                token_preview="tok-zt-201",
                tipo="pago",
                estado="CONFIRMADO_CXC",
                origen="bot",
                partner_odoo_id=1001,
                contraparte_nombre="Cliente Alpha C.A.",
                monto=Decimal("100.00"),
                moneda="USD",
                metodo_pago="zelle",
                referencia="ZL-999",
                fecha_documento=date(2026, 11, 19),
                es_zelle_tercero=True,
                cuenta_tercero_id=ct_id,
                beneficiario_tercero="Proveedor Lubricantes S.A.",
                confirmado_cxc=True,
                sin_factura=False,
                creado_por="vend1@lubrikca.com",
                creado_en=datetime(2026, 11, 19, 10, 0),
                actualizado_en=datetime(2026, 11, 19, 10, 0),
            )
        )

    srv_op = ServicioOperativos(RepoOperativosSQL(eng), lambda: None, repo_cxc=repo)
    app.dependency_overrides[get_servicio_operativos] = lambda: srv_op
    app.dependency_overrides[get_repo_terceros] = lambda: RepoCuentasTercerosSQL(eng)
    try:
        with (
            patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN_USER),
            patch("cxc.web.app.get_repo", return_value=repo),
            patch("cxc.web.operativos_api._escritor_vinc_op", return_value=None),
        ):
            r_cand = client.get("/api/compras/vinculaciones/candidatos?compra_odoo_move_id=4401")
            assert r_cand.status_code == 200, r_cand.text
            cands = r_cand.json()["candidatos"]
            # Incluye el pago Odoo 8805 y el Zelle 3ro hacia proveedor 601
            assert any(c.get("pago_odoo_id") == "8805" for c in cands)
            assert any(c.get("es_zelle_tercero") is True for c in cands)

            r_link = client.post(
                "/api/compras/vinculaciones",
                json={
                    "compra_odoo_move_id": "4401",
                    "pago_odoo_id": "8805",
                    "monto_aplicado": 180.0,
                    "origen_vinculacion": "fifo",
                },
            )
            assert r_link.status_code == 200, r_link.text
            vid = r_link.json()["vinculacion"]["vinc_id"]

            r_cmp = client.get("/api/compras/unificadas?q=BILL/2026/001")
            assert r_cmp.status_code == 200
            bill = next(x for x in r_cmp.json()["items"] if x.get("odoo_numero") == "BILL/2026/001")
            assert bill["vinculaciones_count"] == 1
            assert bill["monto_vinculado_usd"] == 180.0
            assert bill["saldo_pendiente_vinc_usd"] == 120.0

            r_ec = client.get("/api/reportes/cxp/estado-cuenta/601")
            assert r_ec.status_code == 200, r_ec.text
            assert len(r_ec.json().get("vinculaciones_compras") or []) == 1

            r_del = client.delete(f"/api/compras/vinculaciones/{vid}")
            assert r_del.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_stock_libre_bots_y_inventario_no_bloqueante(monkeypatch: Any) -> None:
    repo = InMemoryRepository()
    repo.upsert_catalogo(
        [
            Producto(
                "101",
                "0520",
                "ELITE 5W-20 (1x12)",
                "Global Oil",
                Decimal("12"),
                Decimal("10"),
                Decimal("48"),
            ),
            Producto(
                "102",
                "0997",
                "ELITE TAMBOR",
                "Global Oil",
                Decimal("208"),
                Decimal("190"),
                Decimal("4"),
            ),
        ]
    )

    class _CatStub:
        def lista_comercial_defecto(self, refresh: bool = False):
            class _L:
                def a_dict(self):
                    return {"id": "21", "nombre": "Pago VES Nov 2026", "moneda": "VES"}

            return _L()

        def catalogo_items(self, refresh: bool = False):
            return [
                ItemCatalogo(
                    producto_id="101",
                    codigo="0520",
                    nombre="ELITE 5W-20 (1x12)",
                    marca="Global Oil",
                    presentacion="1X12",
                    litros=Decimal("12"),
                    precio_usd=Decimal("58.00"),
                    precio_ves=Decimal("72.50"),
                    stock_disponible=Decimal("20"),
                ),
                ItemCatalogo(
                    producto_id="102",
                    codigo="0997",
                    nombre="ELITE TAMBOR",
                    marca="Global Oil",
                    presentacion="TAMBOR",
                    litros=Decimal("208"),
                    precio_usd=Decimal("690.00"),
                    precio_ves=Decimal("850.00"),
                    stock_disponible=Decimal("1"),
                ),
            ]

    monkeypatch.setenv("BOT_API_KEY", "k-test")
    app.dependency_overrides[get_servicio_documentos] = lambda: _CatStub()
    try:
        with patch(
            "cxc.web.documentos_api._leer_stock_vivo_seguro",
            return_value={"101": Decimal("14"), "102": Decimal("0")},
        ):
            r_st = client.get("/api/bots/stock?q=5W-20", headers={"X-Bot-Key": "k-test"})
            assert r_st.status_code == 200, r_st.text
            j_st = r_st.json()
            assert j_st["fuente_stock"] == "free_qty"
            assert j_st["vivo_consultado"] is True
            assert j_st["items"][0]["stock_disponible"] == 14.0

            r_ver = client.post(
                "/api/bots/stock/verificar",
                headers={"X-Bot-Key": "k-test"},
                json={"lineas": [{"producto_id": "102", "cantidad": 5}]},
            )
            assert r_ver.status_code == 200, r_ver.text
            j_ver = r_ver.json()
            assert j_ver["todo_disponible"] is False
            assert "agotado en Odoo (pides 5)" in j_ver["alertas"][0]

        # Test directo fallback free_qty -> qty_available en verificar_stock_lineas_catalogo
        chk = verificar_stock_lineas_catalogo(
            _CatStub().catalogo_items(),
            [{"producto_id": "102", "cantidad": 3}],
            stock_vivo={"102": Decimal("2")},
        )
        assert chk["todo_disponible"] is False
        assert chk["lineas"][0]["estado_stock"] == "bajo"
        assert chk["lineas"][0]["stock_disponible"] == 2.0

        # Inventario catalogo expone stock_disponible y estado_stock
        with (
            patch("cxc.web.app.get_repo", return_value=repo),
            patch(
                "cxc.web.app._stock_mapa_inventario_seguro",
                return_value={"101": 14.0, "102": 0.0},
            ),
        ):
            r_inv = client.get("/api/inventario/catalogo")
            assert r_inv.status_code == 200
            prods = {x["producto_id"]: x for x in r_inv.json()}
            assert prods["101"]["stock_disponible"] == 14.0
            assert prods["101"]["estado_stock"] == "disponible"
            assert prods["102"]["stock_disponible"] == 0.0
            assert prods["102"]["estado_stock"] == "agotado"
    finally:
        app.dependency_overrides.clear()


def test_alembic_schema_head_contiene_tablas_vinculaciones() -> None:
    eng = _sqlite_engine()
    assert eng.dialect.has_table(eng.connect(), "vinculaciones_ventas_op")
    assert eng.dialect.has_table(eng.connect(), "vinculaciones_compras")
