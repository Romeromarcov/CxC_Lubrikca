"""Pruebas integrales Fase 3 — Portal de Clientes Web PWA + Mobile (/api/v1/portal/* y /portal)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from cxc import auth
from cxc.api_v1.jwt_tokens import secret_v1_desde_entorno
from cxc.api_v1.repositorio import RepoApiV1SQL
from cxc.api_v1.storage import AdjuntosStorage
from cxc.contactos.repositorio import RepoContactosSQL
from cxc.contactos.servicio import ServicioContactos
from cxc.db.schema import metadata
from cxc.documentos.catalogo import ItemCatalogo, ListaComercialActiva
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.documentos.servicio import ServicioDocumentos
from cxc.engine.equivalents import congelar_en_vinculacion
from cxc.models import (
    Cliente,
    Entrega,
    EstadoVinculacion,
    Factura,
    LineaOrden,
    Moneda,
    OrdenVenta,
    Pago,
    SerieTasa,
    Vinculacion,
)
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.cuentas_receptoras import RepoCuentasReceptorasSQL
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import RepoCuentasTercerosSQL
from cxc.repositories import InMemoryRepository
from cxc.web import app as web_app
from cxc.web.api_v1 import _RATE_LIMITER_V1, get_adjuntos_storage
from cxc.web.contactos_api import get_servicio_contactos
from cxc.web.documentos_api import get_servicio_documentos
from cxc.web.operativos_api import get_servicio_operativos
from tests.capturas_dobles import OdooFalso

client = TestClient(web_app.app)


class _CatalogoFase3:
    def __init__(self) -> None:
        self._items = [
            ItemCatalogo(
                producto_id="1033",
                codigo="0761",
                nombre="LIGA PARA FRENOS DOT3 (1x12)",
                marca="GLOBAL OIL",
                presentacion="1X12",
                litros=Decimal("3.5"),
                precio_usd=Decimal("18.00"),
                precio_ves=Decimal("24.00"),
                stock_disponible=Decimal("12.0"),
            )
        ]
        self._listas = [
            ListaComercialActiva(
                lista_id="21", nombre="Pago VES Nov 2026", moneda="VES", ruta_pago="bcv"
            ),
            ListaComercialActiva(
                lista_id="22", nombre="Pago USD Nov 2026", moneda="USD", ruta_pago="binance"
            ),
        ]

    def listar(self, *, refresh: bool = False) -> list[ItemCatalogo]:
        return list(self._items)

    def lista_comercial_defecto(self, *, refresh: bool = False) -> ListaComercialActiva:
        return self._listas[0]

    def listas_activas(self, *, refresh: bool = False) -> list[ListaComercialActiva]:
        return list(self._listas)


class _RepoFase3(InMemoryRepository):
    def __init__(self, engine: Any) -> None:
        super().__init__()
        self._engine = engine


@pytest.fixture(autouse=True)
def _limpiar() -> Iterator[None]:
    _RATE_LIMITER_V1.clear_all()
    yield
    _RATE_LIMITER_V1.clear_all()
    web_app.app.dependency_overrides.clear()


def _setup_fase3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(eng)
    repo = _RepoFase3(eng)

    vh, vs = auth.hash_password("Vend123!")
    repo.upsert_usuario_plataforma(
        {
            "email": "vendedor@lubrikca.com",
            "nombre_odoo": "Vendedor",
            "rol": "ventas",
            "activo": "TRUE",
            "password_hash": vh,
            "salt": vs,
        }
    )
    ah, as_ = auth.hash_password("Admin123!")
    repo.upsert_usuario_plataforma(
        {
            "email": "admin@lubrikca.com",
            "nombre_odoo": "Admin",
            "rol": "admin",
            "activo": "TRUE",
            "password_hash": ah,
            "salt": as_,
        }
    )
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 22, 8, 0, 0),
            tasa_bcv=Decimal("920.00"),
            tasa_binance=Decimal("1090.00"),
            fuente="test",
        )
    )
    repo.set_config(
        "pricelist_mapeo_unificado",
        json.dumps(
            {
                "21": {"nombre": "Pago VES Nov 2026", "moneda": "ves", "ruta": "bcv"},
                "22": {"nombre": "Pago USD Nov 2026", "moneda": "usd", "ruta": "binance"},
            }
        ),
    )
    repo.upsert_clientes(
        [
            Cliente("197", "1080 ZIP MARKET, C.A", vendedor_email="vendedor@lubrikca.com"),
            Cliente("198", "OTRA EMPRESA SA", vendedor_email="vendedor@lubrikca.com"),
        ]
    )
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S01313",
                cliente_id="197",
                fecha=date(2026, 11, 10),
                fecha_entrega=date(2026, 11, 11),
                monto_total=Decimal("180.00"),
                lista_precios="21",
                vendedor_email="vendedor@lubrikca.com",
                es_primera_compra=False,
                facturada=True,
                entregada_completa=True,
            ),
            OrdenVenta(
                so_id="S01399",
                cliente_id="198",
                fecha=date(2026, 11, 12),
                fecha_entrega=None,
                monto_total=Decimal("300.00"),
                lista_precios="22",
                vendedor_email="vendedor@lubrikca.com",
                es_primera_compra=False,
            ),
        ]
    )
    repo.upsert_lineas(
        [
            LineaOrden(
                linea_id="L-1",
                so_id="S01313",
                producto="LIGA PARA FRENOS DOT3 (1x12)",
                marca="GLOBAL OIL",
                categoria="Ligas",
                cantidad=Decimal("7.5"),
                precio_unitario=Decimal("24.00"),
            )
        ]
    )
    repo.upsert_facturas(
        [
            Factura(
                factura_id="F-1",
                numero="FAC/2026/001",
                so_id="S01313",
                move_type="out_invoice",
                es_nota_debito=False,
                fecha=date(2026, 11, 11),
                moneda="VES",
                monto_total=Decimal("165600.00"),
                monto_sin_impuestos=Decimal("165600.00"),
                estado="posted",
            )
        ]
    )
    repo.upsert_entregas(
        [
            Entrega(
                entrega_id="E-1",
                so_id="S01313",
                tipo="outgoing",
                fecha=date(2026, 11, 11),
                estado="done",
                es_devolucion=False,
            )
        ]
    )
    repo.upsert_pagos(
        [
            Pago(
                pago_id="P-1",
                cliente_id="197",
                monto=Decimal("92000.00"),
                moneda=Moneda.VES,
                metodo_pago="Banco Bancamiga",
                fecha_pago=datetime(2026, 11, 12, 10, 0),
                vendedor_email="vendedor@lubrikca.com",
                numero_odoo="PG-1",
                referencia="REF-100",
            )
        ]
    )
    repo.add_vinculacion(
        congelar_en_vinculacion(
            Vinculacion(
                vinc_id="V-1",
                pago_id="P-1",
                so_id="S01313",
                monto_aplicado=Decimal("92000.00"),
                hora_pago_confirmada=datetime(2026, 11, 12, 10, 0),
                tasa_bcv_aplicada=Decimal("920.00"),
                tasa_binance_aplicada=Decimal("1090.00"),
                es_tasa_heredada=False,
                estado=EstadoVinculacion.CONCILIADO,
                moneda_abono=Moneda.VES,
            )
        )
    )

    repo_cr = RepoCuentasReceptorasSQL(eng)
    repo_cr.sembrar_desde_diarios(actor="test")
    odoo_falso = OdooFalso()
    svc_docs = ServicioDocumentos(
        RepoDocumentosSQL(eng),
        _CatalogoFase3(),
        lambda: EscritorOdoo(odoo_falso),
        repo_cxc=repo,
    )
    svc_ops = ServicioOperativos(
        RepoOperativosSQL(eng),
        lambda: EscritorOdoo(odoo_falso),
        repo_cxc=repo,
        obtener_contactos=lambda: [],
        obtener_cuentas=lambda: [],
        repo_terceros=RepoCuentasTercerosSQL(eng),
        repo_cuentas_receptoras=repo_cr,
    )
    svc_contactos = ServicioContactos(
        RepoContactosSQL(eng),
        escritor_factory=lambda: EscritorOdoo(odoo_falso),
        engine=eng,
    )
    storage = AdjuntosStorage(
        repo=RepoApiV1SQL(eng),
        secret_key=secret_v1_desde_entorno(str(web_app.SECRET_KEY)),
        local_dir=tmp_path / "adj",
    )

    monkeypatch.setattr(web_app, "get_repo", lambda: repo)
    monkeypatch.setattr(web_app.accesos, "ajustes_de", lambda _e, _em: {})
    web_app.app.dependency_overrides[get_servicio_documentos] = lambda: svc_docs
    web_app.app.dependency_overrides[get_servicio_operativos] = lambda: svc_ops
    web_app.app.dependency_overrides[get_servicio_contactos] = lambda: svc_contactos
    web_app.app.dependency_overrides[get_adjuntos_storage] = lambda: storage
    return {"eng": eng, "repo": repo}


def _invitar_y_login_portal(
    *,
    cliente_id: str,
    partner_odoo_id: int,
    email: str,
    rol_portal: str = "titular",
) -> dict[str, Any]:
    tok_admin = auth.crear_session_token("admin@lubrikca.com", web_app.SECRET_KEY)
    r_inv = client.post(
        "/api/v1/portal/invitaciones",
        cookies={"cxc_session": tok_admin},
        json={
            "cliente_id": cliente_id,
            "partner_odoo_id": partner_odoo_id,
            "email": email,
            "rol_portal": rol_portal,
        },
    )
    assert r_inv.status_code == 201
    r_act = client.post(
        "/api/v1/portal/auth/activar-invitacion",
        json={"token": r_inv.json()["token"], "password": "ClaveSegura123!"},
    )
    assert r_act.status_code == 200
    r_log = client.post(
        "/api/v1/portal/auth/login",
        json={"email": email, "password": "ClaveSegura123!", "device_id": f"web-{email}"},
    )
    assert r_log.status_code == 200
    return dict(r_log.json())


def test_fase3_portal_pedidos_trazabilidad_recompra_y_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_fase3(monkeypatch, tmp_path)

    assert client.get("/api/v1/portal/pedidos").status_code == 401
    assert client.get("/api/v1/portal/estado-cuenta/export").status_code == 401

    r_vend = client.post(
        "/api/v1/auth/login",
        json={"email": "vendedor@lubrikca.com", "password": "Vend123!", "device_id": "v1"},
    )
    tok_vend = r_vend.json()["access_token"]
    assert (
        client.get(
            "/api/v1/portal/pedidos", headers={"Authorization": f"Bearer {tok_vend}"}
        ).status_code
        == 403
    )

    portal = _invitar_y_login_portal(
        cliente_id="197", partner_odoo_id=197, email="titular@1080zip.com", rol_portal="titular"
    )
    tok = portal["access_token"]
    hdrs = {"Authorization": f"Bearer {tok}"}

    # Trazabilidad + aislamiento: solo pedidos de cliente 197 (nunca S01399 de cliente 198)
    r_ped = client.get("/api/v1/portal/pedidos", headers=hdrs)
    assert r_ped.status_code == 200
    body_ped = r_ped.json()
    assert body_ped["total"] == 1
    assert body_ped["pedidos"][0]["numero"] == "S01313"
    assert [p["codigo"] for p in body_ped["pedidos"][0]["pasos"]] == [
        "cotizacion_recibida",
        "aprobado_ventas",
        "despachado",
        "facturado",
        "pagado_conciliado",
    ]
    assert all(p["completado"] for p in body_ped["pedidos"][0]["pasos"])
    assert body_ped["ultimo_pedido_recompra"]["referencia"] == "S01313"
    assert body_ped["ultimo_pedido_recompra"]["lineas"][0]["producto_id"] == "1033"

    # Export CSV y HTML dual BCV vs Binance
    r_csv = client.get("/api/v1/portal/estado-cuenta/export?formato=csv", headers=hdrs)
    assert r_csv.status_code == 200
    assert "text/csv" in r_csv.headers["content-type"]
    assert "S01313" in r_csv.text

    r_html = client.get("/api/v1/portal/estado-cuenta/export?formato=html", headers=hdrs)
    assert r_html.status_code == 200
    assert "Saldo Ruta BCV" in r_html.text
    assert "Saldo Ruta Binance" in r_html.text


def test_fase3_portal_gestion_usuarios_titular_y_aislamiento_cross_tenant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_fase3(monkeypatch, tmp_path)

    titular197 = _invitar_y_login_portal(
        cliente_id="197", partner_odoo_id=197, email="titular197@emp.com", rol_portal="titular"
    )
    otra198 = _invitar_y_login_portal(
        cliente_id="198", partner_odoo_id=198, email="titular198@otra.com", rol_portal="titular"
    )
    tok197 = titular197["access_token"]
    cta197_id = titular197["cuenta"]["cuenta_id"]
    cta198_id = otra198["cuenta"]["cuenta_id"]

    # Titular invita a miembro compras de SU empresa
    r_inv_m = client.post(
        "/api/v1/portal/usuarios",
        headers={"Authorization": f"Bearer {tok197}"},
        json={"email": "compras197@emp.com", "rol_portal": "compras"},
    )
    assert r_inv_m.status_code == 201
    r_act_m = client.post(
        "/api/v1/portal/auth/activar-invitacion",
        json={"token": r_inv_m.json()["token"], "password": "Compras123!"},
    )
    assert r_act_m.status_code == 200
    r_log_m = client.post(
        "/api/v1/portal/auth/login",
        json={"email": "compras197@emp.com", "password": "Compras123!", "device_id": "web-c"},
    )
    tok_compras = r_log_m.json()["access_token"]

    # Miembro compras no puede administrar equipo -> 403
    assert (
        client.get(
            "/api/v1/portal/usuarios", headers={"Authorization": f"Bearer {tok_compras}"}
        ).status_code
        == 403
    )

    # Titular 197 no puede desactivar su propia cuenta titular -> 422
    r_self = client.put(
        f"/api/v1/portal/usuarios/{cta197_id}",
        headers={"Authorization": f"Bearer {tok197}"},
        json={"activo": False},
    )
    assert r_self.status_code == 422

    # Titular 197 no puede modificar cuenta de cliente 198 -> 403
    r_cross = client.put(
        f"/api/v1/portal/usuarios/{cta198_id}",
        headers={"Authorization": f"Bearer {tok197}"},
        json={"activo": False},
    )
    assert r_cross.status_code == 403

    # Titular 197 sí lista y revoca a compras197@emp.com
    r_list = client.get("/api/v1/portal/usuarios", headers={"Authorization": f"Bearer {tok197}"})
    assert r_list.status_code == 200
    assert r_list.json()["total"] == 2
    id_compras = next(
        c["cuenta_id"] for c in r_list.json()["items"] if c["email"] == "compras197@emp.com"
    )
    r_rev = client.put(
        f"/api/v1/portal/usuarios/{id_compras}",
        headers={"Authorization": f"Bearer {tok197}"},
        json={"activo": False},
    )
    assert r_rev.status_code == 200
    assert r_rev.json()["activo"] is False


def test_fase3_rutas_pwa_portal_seguras(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    portal_dir = tmp_path / "portal"
    portal_dir.mkdir(parents=True, exist_ok=True)
    (portal_dir / "index.html").write_text(
        "<html><title>Portal PWA</title></html>", encoding="utf-8"
    )
    (portal_dir / "manifest.webmanifest").write_text(
        json.dumps({"name": "Portal de Clientes · Lubrikca"}), encoding="utf-8"
    )
    (portal_dir / "sw.js").write_text("self.addEventListener('fetch', () => {});", encoding="utf-8")
    monkeypatch.setattr(web_app, "_portal_static_root", lambda: portal_dir.resolve())

    r_root = client.get("/portal")
    assert r_root.status_code == 200
    assert "Portal PWA" in r_root.text

    r_spa = client.get("/portal/activar?token=demo")
    assert r_spa.status_code == 200
    assert "Portal PWA" in r_spa.text

    r_man = client.get("/portal/manifest.webmanifest")
    assert r_man.status_code == 200
    assert "Lubrikca" in r_man.text

    r_sw = client.get("/portal/sw.js")
    assert r_sw.status_code == 200
    assert r_sw.headers.get("service-worker-allowed") == "/portal"

    # Path traversal no filtra archivos fuera de portal_dir (404 o SPA fallback sin .env)
    r_trav_norm = client.get("/portal/../../.env")
    assert r_trav_norm.status_code in (200, 404)
    assert "DATABASE_URL" not in r_trav_norm.text

    r_trav_enc = client.get("/portal/%2e%2e/%2e%2e/.env")
    assert r_trav_enc.status_code == 200
    assert "Portal PWA" in r_trav_enc.text
    assert "DATABASE_URL" not in r_trav_enc.text
