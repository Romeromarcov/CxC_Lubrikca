"""Pruebas integrales Fase 1 — Backend API v1 (/api/v1/*) para App Móvil y Portal de Clientes.

Excluido del parche automático ``hay_sesion_valida=True`` en ``tests/conftest.py`` para
ejercer el middleware real de aislamiento cookie / Bearer JWT / rutas públicas.
"""

from __future__ import annotations

import hashlib
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
    EstadoVinculacion,
    Moneda,
    OrdenVenta,
    Pago,
    SerieTasa,
    Vinculacion,
)
from cxc.odoo.directorio import ContactoOdoo
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


class _CatalogoFalso:
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
                stock_disponible=Decimal("10.0"),
            ),
            ItemCatalogo(
                producto_id="1034",
                codigo="0762",
                nombre="LIGA PARA FRENOS DOT3 (1x6)",
                marca="GLOBAL OIL",
                presentacion="1X6",
                litros=Decimal("1.75"),
                precio_usd=Decimal("9.50"),
                precio_ves=Decimal("13.00"),
                stock_disponible=Decimal("2.0"),
            ),
            ItemCatalogo(
                producto_id="1077",
                codigo="0997",
                nombre="LIGA PARA FRENOS DOT4 (Tambor)",
                marca="GLOBAL OIL",
                presentacion="TAMBOR",
                litros=Decimal("208.0"),
                precio_usd=Decimal("480.00"),
                precio_ves=Decimal("650.00"),
                stock_disponible=Decimal("0.0"),
            ),
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


class _RepoFase1(InMemoryRepository):
    def __init__(self, engine: Any) -> None:
        super().__init__()
        self._engine = engine


@pytest.fixture(autouse=True)
def _limpiar_fase1() -> Iterator[None]:
    _RATE_LIMITER_V1.clear_all()
    yield
    _RATE_LIMITER_V1.clear_all()
    web_app.app.dependency_overrides.clear()


def _entorno_fase1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, Any]:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(eng)

    repo = _RepoFase1(eng)
    pwd_hash, salt = auth.hash_password("Vendedor123!")
    repo.upsert_usuario_plataforma(
        {
            "email": "vendedor@lubrikca.com",
            "nombre_odoo": "Carlos Vendedor",
            "rol": "ventas",
            "activo": "TRUE",
            "password_hash": pwd_hash,
            "salt": salt,
        }
    )
    admin_hash, admin_salt = auth.hash_password("Admin123!")
    repo.upsert_usuario_plataforma(
        {
            "email": "admin@lubrikca.com",
            "nombre_odoo": "Admin Gerencia",
            "rol": "admin",
            "activo": "TRUE",
            "password_hash": admin_hash,
            "salt": admin_salt,
        }
    )

    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 22, 8, 0, 0),
            tasa_bcv=Decimal("919.82"),
            tasa_binance=Decimal("1091.62"),
            fuente="test",
            tasa_bcv_euro=Decimal("995.10"),
        )
    )
    repo.upsert_clientes(
        [
            Cliente(
                cliente_id="197",
                nombre="1080 ZIP MARKET, C.A",
                vendedor_email="vendedor@lubrikca.com",
            ),
            Cliente(
                cliente_id="198",
                nombre="OTRO CLIENTE SA",
                vendedor_email="otro@lubrikca.com",
            ),
        ]
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
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S01313",
                cliente_id="197",
                fecha=date(2026, 11, 10),
                fecha_entrega=date(2026, 11, 10),
                monto_total=Decimal("200.00"),
                lista_precios="21",
                vendedor_email="vendedor@lubrikca.com",
                es_primera_compra=False,
                dias_credito=0,
            ),
            OrdenVenta(
                so_id="S01314",
                cliente_id="197",
                fecha=date(2026, 11, 15),
                fecha_entrega=date(2026, 11, 15),
                monto_total=Decimal("150.00"),
                lista_precios="22",
                vendedor_email="vendedor@lubrikca.com",
                es_primera_compra=False,
                dias_credito=0,
            ),
        ]
    )
    repo.upsert_pagos(
        [
            Pago(
                pago_id="P-1",
                cliente_id="197",
                monto=Decimal("45991.00"),
                moneda=Moneda.VES,
                metodo_pago="Banco Bancamiga",
                fecha_pago=datetime(2026, 11, 11, 10, 0),
                vendedor_email="vendedor@lubrikca.com",
                numero_odoo="PAGO-1",
                referencia="REF-1",
            )
        ]
    )
    repo.add_vinculacion(
        congelar_en_vinculacion(
            Vinculacion(
                vinc_id="V-1",
                pago_id="P-1",
                so_id="S01313",
                monto_aplicado=Decimal("45991.00"),
                hora_pago_confirmada=datetime(2026, 11, 11, 10, 0),
                tasa_bcv_aplicada=Decimal("919.82"),
                tasa_binance_aplicada=Decimal("1091.62"),
                es_tasa_heredada=False,
                estado=EstadoVinculacion.CONCILIADO,
                moneda_abono=Moneda.VES,
            )
        )
    )

    repo_cr = RepoCuentasReceptorasSQL(eng)
    repo_cr.sembrar_desde_diarios(actor="test")
    cr_bami = next(c for c in repo_cr.listar() if c.banco_codigo_sudeban == "0172")
    cr_usd = repo_cr.crear(
        {
            "nombre": "Binance Lubrikca",
            "tipo_canal": "binance",
            "moneda": "USD",
            "diario_odoo_id": 35,
            "diario_odoo_nombre": "Binance USDT",
        },
        actor="test",
    )

    contactos_odoo = [
        ContactoOdoo(
            id=197,
            nombre="1080 ZIP MARKET, C.A",
            rif="J-50332770-7",
            email="compras@1080zip.com",
            telefono="04141112233",
            ciudad="Valencia",
            direccion="Av Bolívar",
            es_empresa=True,
            customer_rank=1,
            supplier_rank=0,
            clasificacion="cliente",
            clasificacion_etiqueta="Cliente",
            es_borrador_telegram=False,
            referencia="",
            vendedor="vendedor@lubrikca.com",
            notas="",
        ),
        ContactoOdoo(
            id=198,
            nombre="OTRO CLIENTE SA",
            rif="J-40998877-1",
            email="otro@cliente.com",
            telefono="04149998877",
            ciudad="Caracas",
            direccion="Altamira",
            es_empresa=True,
            customer_rank=1,
            supplier_rank=0,
            clasificacion="cliente",
            clasificacion_etiqueta="Cliente",
            es_borrador_telegram=False,
            referencia="",
            vendedor="otro@lubrikca.com",
            notas="",
        ),
    ]

    odoo_falso = OdooFalso()
    catalogo = _CatalogoFalso()
    svc_docs = ServicioDocumentos(
        RepoDocumentosSQL(eng),
        catalogo,
        lambda: EscritorOdoo(odoo_falso),
        repo_cxc=repo,
    )
    svc_ops = ServicioOperativos(
        RepoOperativosSQL(eng),
        lambda: EscritorOdoo(odoo_falso),
        repo_cxc=repo,
        obtener_contactos=lambda: contactos_odoo,
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

    return {
        "eng": eng,
        "repo": repo,
        "repo_cr": repo_cr,
        "cr_bami": cr_bami,
        "cr_usd": cr_usd,
        "svc_docs": svc_docs,
        "svc_ops": svc_ops,
        "odoo": odoo_falso,
    }


def _cookie_admin() -> dict[str, str]:
    tok = auth.crear_session_token("admin@lubrikca.com", web_app.SECRET_KEY)
    return {"cxc_session": tok}


def test_v1_auth_vendedor_rotacion_reuso_pwd_y_rate_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _entorno_fase1(monkeypatch, tmp_path)

    # Sin Bearer -> 401 desde middleware real
    assert client.get("/api/v1/sync/bootstrap").status_code == 401
    assert client.get("/api/v1/portal/estado-cuenta").status_code == 401
    assert client.get("/api/v1/portal/invitaciones/cuentas").status_code == 401

    # Login vendedor
    r_log = client.post(
        "/api/v1/auth/login",
        json={
            "email": "vendedor@lubrikca.com",
            "password": "Vendedor123!",
            "device_id": "pixel-01",
            "plataforma": "android",
            "app_version": "1.0.0",
        },
    )
    assert r_log.status_code == 200
    toks = r_log.json()
    acc1 = toks["access_token"]
    rt1 = toks["refresh_token"]

    r_me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {acc1}"})
    assert r_me.status_code == 200
    assert r_me.json()["user"]["email"] == "vendedor@lubrikca.com"

    # Refresh rotatorio exitoso
    r_ref = client.post("/api/v1/auth/refresh", json={"refresh_token": rt1})
    assert r_ref.status_code == 200
    rt2 = r_ref.json()["refresh_token"]
    assert rt2 != rt1

    # Detección de reuso del refresh anterior -> revoca TODA la familia (incluyendo rt2)
    r_reuso = client.post("/api/v1/auth/refresh", json={"refresh_token": rt1})
    assert r_reuso.status_code == 401
    assert "Reuso" in r_reuso.json()["detail"]

    r_rt2_revocado = client.post("/api/v1/auth/refresh", json={"refresh_token": rt2})
    assert r_rt2_revocado.status_code == 401

    # Cambio de contraseña revoca access token anterior por pwd_v
    new_h, new_s = auth.hash_password("NuevaClave456!")
    env["repo"].upsert_usuario_plataforma(
        {
            "email": "vendedor@lubrikca.com",
            "nombre_odoo": "Carlos Vendedor",
            "rol": "ventas",
            "activo": "TRUE",
            "password_hash": new_h,
            "salt": new_s,
        }
    )
    r_pwd_old = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {acc1}"})
    assert r_pwd_old.status_code == 401

    # Logout limpio con nueva sesión
    r_log2 = client.post(
        "/api/v1/auth/login",
        json={"email": "vendedor@lubrikca.com", "password": "NuevaClave456!", "device_id": "px2"},
    )
    assert r_log2.status_code == 200
    rt3 = r_log2.json()["refresh_token"]
    r_out = client.post("/api/v1/auth/logout", json={"refresh_token": rt3})
    assert r_out.status_code == 200 and r_out.json()["revocados"] >= 1
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": rt3}).status_code == 401

    # Rate limit tras 10 intentos fallidos -> 429
    for _ in range(10):
        r_bad = client.post(
            "/api/v1/auth/login",
            json={"email": "atacante@lubrikca.com", "password": "mal"},
        )
        assert r_bad.status_code == 401
    r_429 = client.post(
        "/api/v1/auth/login",
        json={"email": "atacante@lubrikca.com", "password": "mal"},
    )
    assert r_429.status_code == 429


def test_v1_sync_bootstrap_delta_push_idempotente_y_adjuntos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _entorno_fase1(monkeypatch, tmp_path)
    r_log = client.post(
        "/api/v1/auth/login",
        json={"email": "vendedor@lubrikca.com", "password": "Vendedor123!", "device_id": "mob-1"},
    )
    hdr = {"Authorization": f"Bearer {r_log.json()['access_token']}"}

    # Bootstrap
    r_boot = client.get("/api/v1/sync/bootstrap", headers=hdr)
    assert r_boot.status_code == 200
    boot = r_boot.json()
    assert boot["modo"] == "bootstrap"
    assert len(boot["listas_precio"]) == 2
    assert len(boot["catalogo"]) == 3
    assert len(boot["cuentas_receptoras"]) >= 6
    # Solo clientes del vendedor (197), NO 198 de otro vendedor
    cids = {c["cliente_id"] for c in boot["clientes"]}
    assert "197" in cids and "198" not in cids

    cursor0 = boot["cursor"]

    # Desactivar una cuenta receptora y pedir delta -> debe traer tombstone
    env["repo_cr"].desactivar(env["cr_usd"].cuenta_id)
    r_delta = client.get(f"/api/v1/sync/delta?cursor={cursor0}", headers=hdr)
    assert r_delta.status_code == 200
    assert env["cr_usd"].cuenta_id in r_delta.json()["tumbstones"]["cuentas_receptoras_inactivas"]

    # Push batch: cotización OK, pago ajeno rechazado (reintentable=False), contacto nuevo OK
    item_cot = {
        "idempotency_key": "idem-cot-1",
        "tipo": "cotizacion",
        "payload": {
            "partner_odoo_id": 197,
            "cliente": "1080 ZIP MARKET, C.A",
            "lista_precios_id": "21",
            "lineas": [{"producto_id": "1033", "cantidad": 2}],
        },
    }
    item_pago_ajeno = {
        "idempotency_key": "idem-pago-bad-1",
        "tipo": "pago",
        "payload": {
            "partner_odoo_id": 197,
            "cliente": "1080 ZIP MARKET, C.A",
            "banco_origen": "Banco Provincial",
            "banco_destino": "Venezolano de Crédito",
            "cuenta_destino": "01040000112233445566",
            "referencia": "998877",
            "monto": "100",
            "moneda": "VES",
            "fecha_documento": "2026-11-22",
        },
    }
    item_contacto = {
        "idempotency_key": "idem-cto-1",
        "tipo": "contacto",
        "payload": {
            "nombre": "Lubricantes del Centro CA",
            "rif": "J-40123456-7",
            "telefono": "04141234567",
            "rol": "prospecto",
        },
    }
    r_push = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": "mob-1", "items": [item_cot, item_pago_ajeno, item_contacto]},
    )
    assert r_push.status_code == 200
    res = r_push.json()["resultados"]
    assert res[0]["estado"] == "OK", res[0]
    assert res[0]["documento"]["origen"] == "app_movil"
    assert res[1]["estado"] == "CONFLICTO_NEGOCIO", res[1]
    assert res[1]["codigo_negocio"] == "DESTINO_NO_PERTENECE_LUBRIKCA"
    assert res[1]["reintentable"] is False
    assert res[2]["estado"] == "OK", res[2]

    # Reenvío idempotente con misma clave y payload -> DUPLICADO_IDEMPOTENTE
    r_push_dup = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": "mob-1", "items": [item_cot]},
    )
    assert r_push_dup.status_code == 200
    assert r_push_dup.json()["resultados"][0]["estado"] == "DUPLICADO_IDEMPOTENTE"

    # Misma clave con payload distinto -> CONFLICTO_NEGOCIO 409
    r_push_diff = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={
            "device_id": "mob-1",
            "items": [{**item_cot, "payload": {"partner_odoo_id": 197, "notas": "alterado"}}],
        },
    )
    assert r_push_diff.status_code == 200
    assert r_push_diff.json()["resultados"][0]["http_status"] == 409

    # Adjuntos presign + PUT upload + dedupe SHA-256
    contenido = b"JPG-COMPROBANTE-TEST-12345"
    sha = hashlib.sha256(contenido).hexdigest()
    r_pre = client.post(
        "/api/v1/adjuntos/presign",
        headers=hdr,
        json={"mime": "image/jpeg", "bytes": len(contenido), "sha256": sha},
    )
    assert r_pre.status_code == 200
    pre = r_pre.json()
    assert pre["ya_existe"] is False
    r_put = client.put(pre["upload_url"], content=contenido, headers={"Content-Type": "image/jpeg"})
    assert r_put.status_code == 200 and r_put.json()["sha256"] == sha

    r_pre2 = client.post(
        "/api/v1/adjuntos/presign",
        headers=hdr,
        json={"mime": "image/jpeg", "bytes": len(contenido), "sha256": sha},
    )
    assert r_pre2.status_code == 200 and r_pre2.json()["ya_existe"] is True

    # MIME inválido -> 422
    assert (
        client.post(
            "/api/v1/adjuntos/presign",
            headers=hdr,
            json={"mime": "application/x-msdownload", "bytes": 100, "sha256": sha},
        ).status_code
        == 422
    )


def test_v1_portal_clientes_aislamiento_rutas_bcv_binance_y_revocacion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _entorno_fase1(monkeypatch, tmp_path)

    # Admin crea invitación con cookie interna
    cookies_adm = _cookie_admin()
    # Bearer de vendedor NO puede abrir administración de invitaciones (middleware exige cookie)
    r_log_v = client.post(
        "/api/v1/auth/login",
        json={"email": "vendedor@lubrikca.com", "password": "Vendedor123!"},
    )
    tok_vend = r_log_v.json()["access_token"]
    assert (
        client.post(
            "/api/v1/portal/invitaciones",
            headers={"Authorization": f"Bearer {tok_vend}"},
            json={"cliente_id": "197", "email": "cli@1080.com"},
        ).status_code
        == 401
    )

    client.cookies.update(cookies_adm)
    r_inv = client.post(
        "/api/v1/portal/invitaciones",
        json={
            "cliente_id": "197",
            "partner_odoo_id": 197,
            "cliente_nombre": "1080 ZIP MARKET, C.A",
            "email": "finanzas@1080zip.com",
            "rol_portal": "titular",
        },
    )
    assert r_inv.status_code == 201
    inv_tok = r_inv.json()["token"]
    client.cookies.clear()

    # Cliente activa invitación (1 vez OK, 2da vez 409)
    r_act = client.post(
        "/api/v1/portal/auth/activar-invitacion",
        json={"token": inv_tok, "password": "PortalPassword99!", "telefono": "04141112233"},
    )
    assert r_act.status_code == 200
    cuenta_id = r_act.json()["cuenta"]["cuenta_id"]
    assert (
        client.post(
            "/api/v1/portal/auth/activar-invitacion",
            json={"token": inv_tok, "password": "PortalPassword99!"},
        ).status_code
        == 409
    )

    # Login del cliente
    r_plog = client.post(
        "/api/v1/portal/auth/login",
        json={"email": "finanzas@1080zip.com", "password": "PortalPassword99!"},
    )
    assert r_plog.status_code == 200
    tok_cli = r_plog.json()["access_token"]
    rt_cli = r_plog.json()["refresh_token"]
    hdr_cli = {"Authorization": f"Bearer {tok_cli}"}

    # Aislamiento estricto de audiencias:
    # - Token de cliente RECHAZADO en vendedor y rutas internas legadas
    assert client.get("/api/v1/sync/bootstrap", headers=hdr_cli).status_code == 403
    assert (
        client.post("/api/v1/sync/push", headers=hdr_cli, json={"items": []}).status_code == 403
    )
    assert client.get("/api/bandeja", headers=hdr_cli).status_code == 401
    # - Token de vendedor RECHAZADO en portal de clientes
    assert (
        client.get(
            "/api/v1/portal/estado-cuenta", headers={"Authorization": f"Bearer {tok_vend}"}
        ).status_code
        == 403
    )

    # Estado de cuenta dual (BCV y Binance separados)
    r_ec = client.get("/api/v1/portal/estado-cuenta", headers=hdr_cli)
    assert r_ec.status_code == 200
    ec = r_ec.json()
    assert ec["ruta_bcv"]["ventas_usd"] == 200.0
    assert ec["ruta_bcv"]["pagos_usd"] == 50.0
    assert ec["ruta_bcv"]["saldo_usd"] == 150.0
    assert ec["ruta_binance"]["ventas_usd"] == 150.0
    assert ec["ruta_binance"]["saldo_usd"] == 150.0

    # Cuentas de pago filtradas por ruta
    r_cta_bcv = client.get("/api/v1/portal/cuentas-pago?ruta=bcv", headers=hdr_cli)
    assert r_cta_bcv.status_code == 200
    assert all(c["moneda"] == "VES" for c in r_cta_bcv.json()["items"])

    r_cta_bin = client.get("/api/v1/portal/cuentas-pago?ruta=binance", headers=hdr_cli)
    assert r_cta_bin.status_code == 200
    assert all(
        c["moneda"] == "USD" or c["tipo_canal"] in ("binance", "zelle")
        for c in r_cta_bin.json()["items"]
    )

    # Catálogo con semáforo
    r_cat = client.get("/api/v1/portal/catalogo", headers=hdr_cli)
    assert r_cat.status_code == 200
    disps = {it["producto_id"]: it["disponibilidad"] for it in r_cat.json()["items"]}
    assert disps == {"1033": "DISPONIBLE", "1034": "BAJO_STOCK", "1077": "BAJO_PEDIDO"}

    # Crear cotización desde portal (NO toca Odoo)
    r_cot = client.post(
        "/api/v1/portal/cotizaciones",
        headers=hdr_cli,
        json={"lista_precios_id": "21", "lineas": [{"producto_id": "1033", "cantidad": 3}]},
    )
    assert r_cot.status_code == 201
    cot_data = r_cot.json()
    assert cot_data["origen"] == "portal_cliente"
    assert cot_data["estado"] == "BORRADOR"
    assert not [c for c in env["odoo"].creados if c[0] == "sale.order"]

    # Pago portal hacia cuenta ajena -> 422 bloqueado
    r_pag_bad = client.post(
        "/api/v1/portal/pagos",
        headers=hdr_cli,
        json={
            "banco_origen": "Banco Provincial",
            "banco_destino": "Venezolano de Crédito",
            "cuenta_destino": "01040000112233445566",
            "referencia": "P-PORTAL-BAD",
            "monto": "100",
            "moneda": "VES",
            "fecha_documento": "2026-11-22",
        },
    )
    assert r_pag_bad.status_code == 422

    # Cruce de ruta prohibido (orden S01314 es USD/Binance, intentar pagar en VES/BCV -> 422)
    r_cruce = client.post(
        "/api/v1/portal/pagos",
        headers=hdr_cli,
        json={
            "pedido_ref": "S01314",
            "banco_origen": "Banco de Venezuela",
            "banco_destino": "Bancamiga",
            "referencia": "P-CRUCE-1",
            "monto": "100",
            "moneda": "VES",
            "fecha_documento": "2026-11-22",
        },
    )
    assert r_cruce.status_code == 422 and "Ruta Binance" in r_cruce.json()["error"]

    # Pago válido a cuenta propia Bancamiga -> 201
    r_pag_ok = client.post(
        "/api/v1/portal/pagos",
        headers=hdr_cli,
        json={
            "pedido_ref": "S01313",
            "banco_origen": "Banco de Venezuela",
            "banco_destino": "Bancamiga",
            "referencia": "P-PORTAL-OK-1",
            "monto": "5000",
            "moneda": "VES",
            "fecha_documento": "2026-11-22",
        },
    )
    assert r_pag_ok.status_code == 201
    op_id = r_pag_ok.json()["id"]

    # Recibo propio -> 200; recibo de OTRO cliente -> 403
    assert (
        client.get(f"/api/v1/portal/documentos/{op_id}/recibo", headers=hdr_cli).status_code == 200
    )
    otro_op = env["svc_ops"].crear(
        {
            "tipo": "pago",
            "partner_odoo_id": 198,
            "cliente": "OTRO CLIENTE SA",
            "referencia": "OTRO-1",
            "monto": "50",
            "moneda": "USD",
            "fecha_documento": "2026-11-22",
        },
        origen="web",
        actor="otro",
    )
    assert (
        client.get(
            f"/api/v1/portal/documentos/{otro_op.documento_id}/recibo", headers=hdr_cli
        ).status_code
        == 403
    )

    # Admin desactiva cuenta -> tokens access + refresh del cliente quedan revocados inmediatamente
    client.cookies.update(cookies_adm)
    r_des = client.put(
        f"/api/v1/portal/invitaciones/cuentas/{cuenta_id}",
        json={"activo": False},
    )
    assert r_des.status_code == 200 and r_des.json()["activo"] is False
    client.cookies.clear()

    assert client.get("/api/v1/portal/me", headers=hdr_cli).status_code == 401
    r_ref_off = client.post("/api/v1/portal/auth/refresh", json={"refresh_token": rt_cli})
    assert r_ref_off.status_code == 401
