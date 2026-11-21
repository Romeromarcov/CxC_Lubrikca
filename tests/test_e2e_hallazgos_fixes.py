"""Pruebas de regresión para las correcciones derivadas de la suite E2E en staging_V1."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from cxc.bancos import repositorio as bancos_repo
from cxc.capturas.servicio import ServicioCapturas
from cxc.contactos.repositorio import RepoContactosSQL
from cxc.contactos.servicio import ServicioContactos
from cxc.db import schema
from cxc.documentos.catalogo import ItemCatalogo, ListaComercialActiva
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.documentos.servicio import ServicioDocumentos
from cxc.models import Cliente, LineaOrden, OrdenVenta
from cxc.odoo.directorio import ContactoOdoo, CuentaContable
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.modelos import CONFIRMADO_CXC, ENVIADO_ODOO, PENDIENTE_APROBACION
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import RepoCuentasTercerosSQL
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from cxc.web.contactos_api import get_servicio_contactos
from cxc.web.directorio_api import get_lector_odoo, limpiar_cache_directorio
from cxc.web.documentos_api import get_servicio_documentos
from cxc.web.operativos_api import get_repo_terceros, get_servicio_operativos
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria

client = TestClient(app)

ADMIN_USER = {
    "email": "admin@lubrikca.com",
    "nombre": "Admin Prueba",
    "rol": "admin",
    "permisos": efectivos("admin"),
}
CONFIG_USER = {
    "email": "config@lubrikca.com",
    "nombre": "Config User",
    "rol": "tesoreria",
    "permisos": ["configuracion", "pagos"],
}
VENTAS_USER = {
    "email": "ventas@lubrikca.com",
    "nombre": "Vendedor",
    "rol": "ventas",
    "permisos": efectivos("ventas"),
}


def _sqlite_engine() -> Any:
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
            schema.bancos_archivos,
            schema.bancos_movimientos,
            schema.bancos_propuestas,
            schema.bancos_decisiones,
            schema.contactos_maestro,
        ],
    )
    return eng


class _RepoConEngine(InMemoryRepository):
    def __init__(self, engine: Any) -> None:
        super().__init__()
        self._engine = engine


class _CatalogoFijo:
    def listar(self, *, refresh: bool = False) -> list[ItemCatalogo]:
        return [
            ItemCatalogo(
                producto_id="904",
                codigo="0007",
                nombre="ELITE API SN SAE 5W20 (1x6)",
                marca="Global Oil",
                presentacion="1X6",
                tipo_envase="CAJA",
                litros=Decimal("5.67"),
                precio_usd=Decimal("47.41"),
                precio_ves=Decimal("72.94"),
                stock_disponible=Decimal("100"),
            )
        ]

    def lista_comercial_defecto(self, *, refresh: bool = False) -> ListaComercialActiva:
        return ListaComercialActiva(lista_id="10", nombre="Pago VES Sept 2026", moneda="VES")


class _FakeOdooSalesDeltaReader:
    """Reader delta mínimo que simula cambios recientes de orden/líneas en Odoo."""

    def __init__(self) -> None:
        self.llamadas: int = 0

    def changed_clientes(self, since: datetime | None) -> list[Cliente]:
        return [Cliente(cliente_id="2156", nombre="Prospecto Prueba", vendedor_email="v@x.com")]

    def changed_ordenes(self, since: datetime | None) -> list[OrdenVenta]:
        self.llamadas += 1
        return [
            OrdenVenta(
                so_id="S01313",
                cliente_id="2156",
                fecha=date(2026, 11, 20),
                fecha_entrega=None,
                monto_total=Decimal("423.05"),
                lista_precios="10",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                estado_orden="sale",
                odoo_order_id=1313,
            )
        ]

    def changed_lineas(self, since: datetime | None) -> list[LineaOrden]:
        return [
            LineaOrden(
                linea_id="3409",
                so_id="S01313",
                producto="904",
                marca="ELITE",
                categoria="Comercial",
                cantidad=Decimal("5"),
                precio_unitario=Decimal("72.94"),
                descuento=Decimal("0"),
                nombre="ELITE API SN SAE 5W20 (1x6)",
                subtotal=Decimal("364.70"),
            )
        ]

    def lineas_vigentes_por_orden(self, so_ids: list[str]) -> dict[str, set[str]]:
        return {"S01313": {"3409"}}


def test_fix1_sincronizar_odoo_refresca_delta_en_vivo_antes_de_reconciliar() -> None:
    eng = _sqlite_engine()
    repo_cxc = _RepoConEngine(eng)
    repo_cxc.upsert_clientes(
        [Cliente(cliente_id="2156", nombre="Prospecto Prueba", vendedor_email="v@x.com")]
    )
    repo_cxc.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S01313",
                cliente_id="2156",
                fecha=date(2026, 11, 20),
                fecha_entrega=None,
                monto_total=Decimal("169.22"),
                lista_precios="10",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                estado_orden="draft",
                odoo_order_id=1313,
            )
        ]
    )
    repo_docs = RepoDocumentosSQL(eng)
    doc = repo_docs.crear(
        {
            "tipo": "pedido",
            "estado": "ENVIADO_ODOO",
            "origen": "bot",
            "partner_odoo_id": 2156,
            "cliente_nombre": "Prospecto Prueba",
            "moneda_lista": "VES",
            "lista_precios_id": "10",
            "lista_precios_nombre": "Pago VES Sept 2026",
            "subtotal": Decimal("145.88"),
            "subtotal_neto": Decimal("145.88"),
            "monto_iva": Decimal("23.34"),
            "total_con_iva": Decimal("169.22"),
            "odoo_order_id": 1313,
            "odoo_order_name": "S01313",
            "es_primera_compra": False,
            "auto_enviado": False,
        },
        [
            {
                "producto_id": "904",
                "codigo": "0007",
                "descripcion": "ELITE API SN SAE 5W20 (1x6)",
                "cantidad": Decimal("2"),
                "precio_unitario": Decimal("72.94"),
                "descuento_pct": Decimal("0"),
                "subtotal": Decimal("145.88"),
            }
        ],
    )
    svc_docs = ServicioDocumentos(repo_docs, _CatalogoFijo(), lambda: None, repo_cxc=repo_cxc)
    fake_reader = _FakeOdooSalesDeltaReader()

    app.dependency_overrides[get_servicio_documentos] = lambda: svc_docs
    try:
        with (
            patch("cxc.web.app.get_repo", return_value=repo_cxc),
            patch("cxc.web.app.get_current_user_from_cookie", return_value=VENTAS_USER),
            patch("cxc.web.documentos_api._crear_reader_ventas_odoo", return_value=fake_reader),
        ):
            r = client.post("/api/ventas/sincronizar-odoo")
            assert r.status_code == 200
            data = r.json()
            assert data["ok"] is True
            assert data["odoo_refrescado"] is True
            assert data["ordenes_odoo"] == 1
            assert data["ajustados"] == 1
            assert data["confirmados"] == 1
            assert fake_reader.llamadas == 1
    finally:
        app.dependency_overrides.clear()

    act = repo_docs.obtener(doc.documento_id)
    assert act is not None
    assert act.lineas[0].cantidad == Decimal("5.00")
    assert act.total_con_iva == Decimal("423.05")
    assert act.validacion_motor["sync_odoo"]["confirmado_odoo"] is True
    assert act.validacion_motor["sync_odoo"]["estado_odoo"] == "sale"


def test_fix2_decidir_r5_confirma_y_reabre_pago_electronico_pendiente() -> None:
    eng = _sqlite_engine()
    repo_op = RepoOperativosSQL(eng)
    doc = repo_op.crear(
        {
            "tipo": "pago",
            "partner_odoo_id": 197,
            "contraparte_nombre": "1080 ZIP MARKET, C.A",
            "monto": Decimal("1200.00"),
            "moneda": "VES",
            "metodo_pago": "Transferencia Bancamiga",
            "banco": "Bancamiga",
            "diario_id": 30,
            "diario_nombre": "Banco Bancamiga 7806",
            "referencia": "77154559",
            "fecha_documento": date(2026, 11, 20),
            "token_preview": "tok-r5-test",
            "estado": PENDIENTE_APROBACION,
            "es_zelle_tercero": False,
            "confirmado_cxc": False,
            "numero_recibo": "",
        }
    )
    assert doc.odoo_res_id is None
    assert doc.confirmado_cxc is False

    huella = "huella_r5_e2e_test"
    with eng.begin() as conn:
        conn.execute(
            insert(schema.bancos_archivos).values(
                archivo_id=1,
                sha256="h1",
                banco="BANCAMIGA",
                cuenta="BANCAMIGA",
                moneda="VES",
                nombre_archivo="bancamiga.xls",
                contenido=b"<table/>",
                cargado_en=datetime(2026, 11, 20, 10, 0, 0),
                cargado_por="admin@lubrikca.com",
                n_movimientos=1,
                n_nuevos=1,
                saldo_inicial_derivado=False,
                cuadra=True,
            )
        )
        conn.execute(
            insert(schema.bancos_movimientos).values(
                huella=huella,
                unique_import_id=huella,
                archivo_id=1,
                banco="BANCAMIGA",
                cuenta="BANCAMIGA",
                moneda="VES",
                fecha=date(2026, 11, 20),
                referencia="77154559",
                descripcion="TRANSF E2E",
                debito=Decimal("0"),
                credito=Decimal("1200.00"),
                saldo=Decimal("1200.00"),
                categoria="cobro_candidato",
                regla="r",
                orden=1,
                creado_en=datetime(2026, 11, 20, 10, 0, 0),
            )
        )
        conn.execute(
            insert(schema.bancos_propuestas).values(
                huella=huella,
                tipo="pago",
                regla="R5",
                motivo="ingreso CxC PAG-00001",
                pago_id=None,
                pago_nombre=doc.numero,
                cuenta="",
                puntaje=8,
                candidatos=f'[{{"id": {doc.documento_id}, "nombre": "{doc.numero}"}}]',
                estado="pendiente",
                generada_en=datetime(2026, 11, 20, 10, 0, 0),
            )
        )

    res_ap = bancos_repo.decidir(eng, huella, "aprobar", "admin@lubrikca.com")
    assert res_ap["estado"] == "aprobada"
    doc_conf = repo_op.obtener(doc.documento_id)
    assert doc_conf is not None
    assert doc_conf.confirmado_cxc is True
    assert doc_conf.estado == CONFIRMADO_CXC
    assert doc_conf.conciliado_banco_huella == huella
    assert doc_conf.confirmado_cxc_por == "admin@lubrikca.com"

    res_reab = bancos_repo.decidir(
        eng, huella, "reabrir", "admin@lubrikca.com", comentario="Reabrir prueba"
    )
    assert res_reab["estado"] == "pendiente"
    doc_rev = repo_op.obtener(doc.documento_id)
    assert doc_rev is not None
    assert doc_rev.confirmado_cxc is False
    assert doc_rev.estado == PENDIENTE_APROBACION
    assert doc_rev.conciliado_banco_huella == ""


def test_fix3_proveedor_background_sincroniza_maestro_y_vincula_por_rif(
    monkeypatch: Any,
) -> None:
    eng = _sqlite_engine()
    repo_contactos = RepoContactosSQL(eng)
    svc_contactos = ServicioContactos(repo_contactos, engine=eng)
    repo_cap = RepoMemoria()
    odoo = OdooFalso()
    svc_cap = ServicioCapturas(repo_cap, EspejoFalso(), lambda: EscritorOdoo(odoo))

    monkeypatch.setenv("BOT_API_KEY", "bot-key-fix3")
    app.dependency_overrides[get_servicio] = lambda: svc_cap
    app.dependency_overrides[get_servicio_contactos] = lambda: svc_contactos
    try:
        r = client.post(
            "/api/bots/capturas",
            headers={"X-Bot-Key": "bot-key-fix3"},
            json={
                "kind": "proveedor",
                "idempotency_key": "prov-fix3-1",
                "draft": {
                    "nombre": "[E2E-TEST] Proveedor Sync",
                    "rif": "J-88990011-2",
                    "telefono": "+584141112233",
                },
                "source": {"vendedor_email": "compras@lubrikca.com"},
            },
        )
        assert r.status_code == 201
        assert r.json()["estado"] == "PENDIENTE"
        cap_id = r.json()["id"]
    finally:
        app.dependency_overrides.clear()

    # BackgroundTasks corre al salir de client.post en TestClient: maestro queda ENVIADO_ODOO
    cap_final = repo_cap.filas[cap_id]
    assert cap_final.estado == "ENVIADO"
    pid_odoo = cap_final.resultado.get("partner_id")
    assert pid_odoo is not None

    en_maestro = repo_contactos.obtener_por_captura_id(cap_id)
    assert en_maestro is not None
    assert en_maestro.odoo_partner_id == int(pid_odoo)
    assert en_maestro.estado_validacion == "ENVIADO_ODOO"

    # Ahora probar sincronizar_desde_odoo cuando un contacto local previo quedó sin odoo_partner_id
    local_sin_odoo = svc_contactos.crear(
        {
            "nombre": "Proveedor Sin Vincular",
            "rol": "proveedor",
            "rif": "J-77665544-3",
            "telefono": "04125556677",
        },
        origen="bot",
        actor="compras@lubrikca.com",
    )
    assert local_sin_odoo.odoo_partner_id is None
    stats = svc_contactos.sincronizar_desde_odoo(
        [
            ContactoOdoo(
                id=2157,
                nombre="Proveedor Sin Vincular",
                rif="J-77665544-3",
                email="",
                telefono="04125556677",
                ciudad="",
                direccion="",
                es_empresa=True,
                customer_rank=0,
                supplier_rank=1,
                clasificacion="proveedor",
                clasificacion_etiqueta="Proveedor",
                es_borrador_telegram=False,
                referencia="",
                vendedor="",
                notas="",
            )
        ]
    )
    assert stats["actualizados"] == 1
    assert stats["creados"] == 0
    vinculado = repo_contactos.obtener(local_sin_odoo.contacto_id or 0)
    assert vinculado is not None
    assert vinculado.odoo_partner_id == 2157
    assert vinculado.estado_validacion == "ENVIADO_ODOO"


def test_fix4_rutas_plan_cuentas_paginacion_cobranza_y_etiqueta_asiento() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "cxc" / "web" / "static"
    compras_js = (root / "js" / "compras_unificadas.js").read_text(encoding="utf-8")
    tesoreria_js = (root / "js" / "config_tesoreria.js").read_text(encoding="utf-8")
    assert "/api/directorio/cuentas" not in compras_js
    assert "/api/directorio/cuentas" not in tesoreria_js
    assert "/api/odoo/plan-cuentas" in compras_js
    assert "/api/odoo/plan-cuentas" in tesoreria_js

    index_html = (root / "index.html").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    assert 'id="cobranza-pagination"' in index_html
    assert "js/contactos_maestro.js?v=20261119_8" in index_html
    assert "window.setCobranzaPage" in app_js
    assert "window.setCobranzaPageSize" in app_js

    # Usuario con permiso configuracion/pagos puede consultar /api/odoo/plan-cuentas
    class _LectorStub:
        def leer_plan_cuentas(self) -> list[CuentaContable]:
            return [
                CuentaContable(
                    id=430,
                    code="6.1.1.2.30.16",
                    name="Gasto Combustible",
                    account_type="expense",
                    tipo_nombre="Gasto operativo",
                    grupo="gastos",
                    reconcile=False,
                )
            ]

    limpiar_cache_directorio()
    app.dependency_overrides[get_lector_odoo] = lambda: _LectorStub()
    try:
        with patch("cxc.web.app.get_current_user_from_cookie", return_value=CONFIG_USER):
            r_ctas = client.get("/api/odoo/plan-cuentas")
            assert r_ctas.status_code == 200
            body_ctas = r_ctas.json()
            assert len(body_ctas["cuentas"]) == 1
            assert body_ctas["cuentas"][0]["id"] == 430
    finally:
        app.dependency_overrides.clear()
        limpiar_cache_directorio()

    # Zelle de Tercero con odoo_model="account.move" debe etiquetar ASIENTO-<id>
    eng = _sqlite_engine()
    repo_cxc = _RepoConEngine(eng)
    repo_op = RepoOperativosSQL(eng)
    repo_t = RepoCuentasTercerosSQL(eng)
    cta = repo_t.crear({"alias": "Z3 Test", "proveedor_odoo_id": 1684, "proveedor_nombre": "PROV"})
    repo_op.crear(
        {
            "tipo": "pago",
            "partner_odoo_id": 197,
            "contraparte_nombre": "1080 ZIP MARKET",
            "monto": Decimal("75.00"),
            "moneda": "USD",
            "metodo_pago": "zelle",
            "es_zelle_tercero": True,
            "cuenta_tercero_id": cta.cuenta_id,
            "referencia": "Z3-REF",
            "fecha_documento": date(2026, 11, 20),
            "token_preview": "tok-z3-label",
            "estado": ENVIADO_ODOO,
            "odoo_model": "account.move",
            "odoo_res_id": 21794,
            "odoo_ref": "account.move:21794",
        }
    )
    svc_op = ServicioOperativos(repo_op, lambda: None, repo_cxc=repo_cxc, repo_terceros=repo_t)
    app.dependency_overrides[get_servicio_operativos] = lambda: svc_op
    app.dependency_overrides[get_repo_terceros] = lambda: repo_t
    try:
        with (
            patch("cxc.web.app.get_repo", return_value=repo_cxc),
            patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN_USER),
        ):
            r_unif = client.get("/api/pagos/unificados?direccion=recibidos&origen=zelle_tercero")
            assert r_unif.status_code == 200
            items = r_unif.json()["items"]
            assert len(items) == 1
            assert items[0]["odoo_numero"] == "ASIENTO-21794"
    finally:
        app.dependency_overrides.clear()
