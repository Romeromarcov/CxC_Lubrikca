"""Pruebas integrales Fase 4 — Push Multicanal Anti-Loop, Storage R2/S3, Auditoría y Piloto E2E."""

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
from scripts.migrar_adjuntos_r2 import migrar_adjuntos_locales_a_r2
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from cxc import auth
from cxc.api_v1.jwt_tokens import secret_v1_desde_entorno
from cxc.api_v1.notificaciones import (
    DespachadorPush,
    RepoNotificacionesSQL,
    notificar_cambio_tasa_significativo,
)
from cxc.api_v1.repositorio import RepoApiV1SQL
from cxc.api_v1.storage import AdjuntosStorage
from cxc.contactos.repositorio import RepoContactosSQL
from cxc.contactos.servicio import ServicioContactos
from cxc.db.schema import accesos_bitacora, metadata
from cxc.documentos.catalogo import ItemCatalogo, ListaComercialActiva
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.documentos.servicio import ServicioDocumentos
from cxc.models import Cliente, OrdenVenta, SerieTasa
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.cuentas_receptoras import RepoCuentasReceptorasSQL
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import RepoCuentasTercerosSQL
from cxc.repositories import InMemoryRepository
from cxc.web import app as web_app
from cxc.web.api_v1 import (
    _RATE_LIMITER_V1,
    get_adjuntos_storage,
    get_repo_notificaciones,
)
from cxc.web.contactos_api import get_servicio_contactos
from cxc.web.documentos_api import get_servicio_documentos
from cxc.web.operativos_api import get_servicio_operativos
from tests.capturas_dobles import OdooFalso

client = TestClient(web_app.app)


class _CatalogoFase4:
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
                stock_disponible=Decimal("20.0"),
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


class _RepoFase4(InMemoryRepository):
    def __init__(self, engine: Any) -> None:
        super().__init__()
        self._engine = engine


@pytest.fixture(autouse=True)
def _limpiar() -> Iterator[None]:
    _RATE_LIMITER_V1.clear_all()
    yield
    _RATE_LIMITER_V1.clear_all()
    web_app.app.dependency_overrides.clear()


def _setup_fase4(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(eng)
    repo = _RepoFase4(eng)

    ah, as_ = auth.hash_password("Admin123!")
    repo.upsert_usuario_plataforma(
        {
            "email": "admin@lubrikca.com",
            "nombre_odoo": "Admin Gerencia",
            "rol": "admin",
            "activo": "TRUE",
            "password_hash": ah,
            "salt": as_,
        }
    )
    for i in range(1, 4):
        vh, vs = auth.hash_password("Vend123!")
        repo.upsert_usuario_plataforma(
            {
                "email": f"vendedor{i}@lubrikca.com",
                "nombre_odoo": f"Vendedor {i}",
                "rol": "ventas",
                "activo": "TRUE",
                "password_hash": vh,
                "salt": vs,
            }
        )
        cid = str(200 + i)
        repo.upsert_clientes(
            [Cliente(cid, f"CLIENTE PILOTO {i} C.A.", vendedor_email=f"vendedor{i}@lubrikca.com")]
        )
        repo.upsert_ordenes(
            [
                OrdenVenta(
                    so_id=f"SO-PIL-{i}",
                    cliente_id=cid,
                    fecha=date(2026, 11, 15),
                    fecha_entrega=date(2026, 11, 15),
                    monto_total=Decimal("100.00"),
                    lista_precios="21",
                    vendedor_email=f"vendedor{i}@lubrikca.com",
                    es_primera_compra=False,
                )
            ]
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

    repo_cr = RepoCuentasReceptorasSQL(eng)
    repo_cr.sembrar_desde_diarios(actor="test")
    odoo_falso = OdooFalso()
    svc_docs = ServicioDocumentos(
        RepoDocumentosSQL(eng),
        _CatalogoFase4(),
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
    repo_v1 = RepoApiV1SQL(eng)
    storage = AdjuntosStorage(
        repo=repo_v1,
        secret_key=secret_v1_desde_entorno(str(web_app.SECRET_KEY)),
        local_dir=tmp_path / "adj",
    )
    repo_n = RepoNotificacionesSQL(eng)

    monkeypatch.setattr(web_app, "get_repo", lambda: repo)
    monkeypatch.setattr(web_app.accesos, "ajustes_de", lambda _e, _em: {})
    web_app.app.dependency_overrides[get_servicio_documentos] = lambda: svc_docs
    web_app.app.dependency_overrides[get_servicio_operativos] = lambda: svc_ops
    web_app.app.dependency_overrides[get_servicio_contactos] = lambda: svc_contactos
    web_app.app.dependency_overrides[get_adjuntos_storage] = lambda: storage
    web_app.app.dependency_overrides[get_repo_notificaciones] = lambda: repo_n
    return {"eng": eng, "repo": repo, "repo_v1": repo_v1, "storage": storage, "repo_n": repo_n}


def test_fase4_push_outbox_antilooop_device_not_registered_y_notificaciones_actor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _setup_fase4(monkeypatch, tmp_path)
    repo_v1: RepoApiV1SQL = env["repo_v1"]
    repo_n: RepoNotificacionesSQL = env["repo_n"]

    # Login vendedor1 y registro de Expo push_token
    r_log = client.post(
        "/api/v1/auth/login",
        json={
            "email": "vendedor1@lubrikca.com",
            "password": "Vend123!",
            "device_id": "pix-1",
            "plataforma": "android",
        },
    )
    assert r_log.status_code == 200
    tok_v1 = r_log.json()["access_token"]

    r_pt = client.post(
        "/api/v1/dispositivos/push-token",
        headers={"Authorization": f"Bearer {tok_v1}"},
        json={"device_id": "pix-1", "push_token": "ExponentPushToken[dead]"},
    )
    assert r_pt.status_code == 200

    # Gatillo por cambio de tasa >= 1.5% (deduplicado por dedupe_key)
    creadas1 = notificar_cambio_tasa_significativo(
        repo_n,
        repo_v1,
        tasa_anterior=900.0,
        tasa_nueva=925.0,
        tipo_tasa="BCV",
        fecha_clave="20261122-1",
    )
    creadas2 = notificar_cambio_tasa_significativo(
        repo_n,
        repo_v1,
        tasa_anterior=900.0,
        tasa_nueva=925.0,
        tipo_tasa="BCV",
        fecha_clave="20261122-1",
    )
    assert creadas1 == 1
    assert creadas2 == 0  # Dedupe anti-loop!

    # Vendedor ve su notificacion y la marca como leida
    r_not = client.get("/api/v1/notificaciones", headers={"Authorization": f"Bearer {tok_v1}"})
    assert r_not.status_code == 200
    assert r_not.json()["total"] == 1
    assert r_not.json()["no_leidas"] == 1
    nid = r_not.json()["items"][0]["notificacion_id"]

    r_leer = client.post(
        f"/api/v1/notificaciones/{nid}/leer", headers={"Authorization": f"Bearer {tok_v1}"}
    )
    assert r_leer.status_code == 200
    assert r_leer.json()["notificacion"]["leida_en"] is not None

    # Despachador recibe DeviceNotRegistered -> limpia push_token y marca FALLIDO_PERMANENTE
    def fake_expo(_url: str, _body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {
            "data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]
        }

    res_desp = DespachadorPush(repo_n, repo_v1, http_post=fake_expo).despachar_pendientes()
    assert res_desp["permanentes"] == 1
    disps = repo_v1.listar_dispositivos_activos(
        sujeto_tipo="vendedor", sujeto_id="vendedor1@lubrikca.com"
    )
    assert disps[0]["push_token"] == ""
    assert repo_n.listar_pendientes() == []  # Ya no reintenta en loop


def test_fase4_storage_r2_presign_get_migracion_y_permiso_adjunto(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _setup_fase4(monkeypatch, tmp_path)
    storage: AdjuntosStorage = env["storage"]
    eng = env["eng"]

    # Crea invitación + cuenta portal y sube un comprobante local primero
    tok_admin = auth.crear_session_token("admin@lubrikca.com", web_app.SECRET_KEY)
    r_inv = client.post(
        "/api/v1/portal/invitaciones",
        cookies={"cxc_session": tok_admin},
        json={"cliente_id": "201", "partner_odoo_id": 201, "email": "cli1@emp.com"},
    )
    assert r_inv.status_code == 201
    assert (
        client.post(
            "/api/v1/portal/auth/activar-invitacion",
            json={"token": r_inv.json()["token"], "password": "Clave123!"},
        ).status_code
        == 200
    )
    r_plog = client.post(
        "/api/v1/portal/auth/login",
        json={"email": "cli1@emp.com", "password": "Clave123!", "device_id": "wp-1"},
    )
    tok_p1 = r_plog.json()["access_token"]

    # Suscripción Web Push desde portal
    r_sub = client.post(
        "/api/v1/portal/push/suscribir",
        headers={"Authorization": f"Bearer {tok_p1}"},
        json={"endpoint": "https://push.test/abc", "p256dh": "k1", "auth": "a1"},
    )
    assert r_sub.status_code == 201

    raw = b"\xff\xd8\xff\xe0comprobante-test"
    sha = hashlib.sha256(raw).hexdigest()
    r_pre = client.post(
        "/api/v1/adjuntos/presign",
        headers={"Authorization": f"Bearer {tok_p1}"},
        json={"mime": "image/jpeg", "bytes": len(raw), "sha256": sha, "nombre_archivo": "pago.jpg"},
    )
    assert r_pre.status_code == 200
    up_url = r_pre.json()["upload_url"]
    r_put = client.put(up_url, content=raw, headers={"Content-Type": "image/jpeg"})
    assert r_put.status_code in (200, 201)
    adj_id = r_put.json()["adjunto_id"]

    # Descarga segura local antes de migrar
    r_url_loc = client.get(
        f"/api/v1/adjuntos/{adj_id}/url", headers={"Authorization": f"Bearer {tok_p1}"}
    )
    assert r_url_loc.status_code == 200
    assert r_url_loc.json()["descarga_local"] is True

    # Otro cliente del portal recibe 403 sobre adjunto ajeno
    r_inv2 = client.post(
        "/api/v1/portal/invitaciones",
        cookies={"cxc_session": tok_admin},
        json={"cliente_id": "202", "partner_odoo_id": 202, "email": "cli2@emp.com"},
    )
    client.post(
        "/api/v1/portal/auth/activar-invitacion",
        json={"token": r_inv2.json()["token"], "password": "Clave123!"},
    )
    tok_p2 = client.post(
        "/api/v1/portal/auth/login",
        json={"email": "cli2@emp.com", "password": "Clave123!", "device_id": "wp-2"},
    ).json()["access_token"]
    assert (
        client.get(
            f"/api/v1/adjuntos/{adj_id}/url", headers={"Authorization": f"Bearer {tok_p2}"}
        ).status_code
        == 403
    )

    # Configuramos R2/S3 simulado -> dry-run y apply migran el adjunto y emiten GET SigV4 prefirmado
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://r2.ejemplo.test")
    monkeypatch.setenv("S3_BUCKET", "lubrikca-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "SECRET_TEST")

    res_dry = migrar_adjuntos_locales_a_r2(eng, storage, dry_run=True)
    assert res_dry["migrados"] == 1
    assert res_dry["dry_run"] is True

    subidos: list[str] = []
    res_app = migrar_adjuntos_locales_a_r2(
        eng, storage, dry_run=False, http_put=lambda u, _b, _m: (subidos.append(u) or 200)
    )
    assert res_app["migrados"] == 1
    assert len(subidos) == 1

    r_url_r2 = client.get(
        f"/api/v1/adjuntos/{adj_id}/url", headers={"Authorization": f"Bearer {tok_p1}"}
    )
    assert r_url_r2.status_code == 200
    assert r_url_r2.json()["storage_backend"] == "s3_r2"
    assert "X-Amz-Signature=" in r_url_r2.json()["download_url"]
    assert "X-Amz-Expires=300" in r_url_r2.json()["download_url"]


def test_fase4_simulacion_piloto_e2e_y_auditoria_unificada(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _setup_fase4(monkeypatch, tmp_path)
    eng = env["eng"]

    # Sin cookie admin -> /api/v1/observabilidad/metricas-piloto cerrado (401)
    assert client.get("/api/v1/observabilidad/metricas-piloto").status_code == 401

    tok_admin = auth.crear_session_token("admin@lubrikca.com", web_app.SECRET_KEY)

    # Simulamos 3 vendedores sincronizando offline en campo (incluyendo rechazo 0108->0104)
    for i in range(1, 4):
        r_log = client.post(
            "/api/v1/auth/login",
            json={
                "email": f"vendedor{i}@lubrikca.com",
                "password": "Vend123!",
                "device_id": f"piloto-dev-{i}",
                "plataforma": "android",
            },
        )
        assert r_log.status_code == 200
        tok = r_log.json()["access_token"]
        rt = r_log.json()["refresh_token"]

        if i == 1:
            # Simular reuso malicioso de refresh token -> debe quedar en accesos_bitacora
            r_r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
            assert r_r1.status_code == 200
            r_r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
            assert r_r2.status_code == 401

        r_push = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {tok}"},
            json={
                "device_id": f"piloto-dev-{i}",
                "items": [
                    {
                        "idempotency_key": f"pil-cot-{i}",
                        "tipo": "cotizacion",
                        "payload": {
                            "partner_odoo_id": 200 + i,
                            "cliente": f"CLIENTE PILOTO {i} C.A.",
                            "lista_precios_id": "21",
                            "ruta_pago": "bcv",
                            "lineas": [
                                {"producto_id": "1033", "cantidad": 2, "precio_unitario": 24.0}
                            ],
                        },
                    },
                    {
                        "idempotency_key": f"pil-pago-mal-{i}",
                        "tipo": "pago",
                        "payload": {
                            "partner_odoo_id": 200 + i,
                            "contraparte_nombre": f"CLIENTE PILOTO {i} C.A.",
                            "monto": 1000,
                            "moneda": "VES",
                            "fecha": "2026-11-22",
                            "referencia": f"REF-AJENA-{i}",
                            "banco_origen": "Banco Provincial",
                            "banco_destino": "Venezolano de Crédito",
                            "cuenta_destino": "01040011223344556677",
                        },
                    },
                ],
            },
        )
        assert r_push.status_code == 200
        res_items = r_push.json()["resultados"]
        assert res_items[0]["estado"] == "OK"
        assert res_items[1]["http_status"] == 422
        assert res_items[1]["codigo_negocio"] == "DESTINO_NO_PERTENECE_LUBRIKCA"
        assert res_items[1]["reintentable"] is False

    # Simulamos 3 clientes piloto activados en portal
    for i in range(1, 4):
        r_inv = client.post(
            "/api/v1/portal/invitaciones",
            cookies={"cxc_session": tok_admin},
            json={
                "cliente_id": str(200 + i),
                "partner_odoo_id": 200 + i,
                "email": f"piloto{i}@cliente.com",
            },
        )
        assert r_inv.status_code == 201
        client.post(
            "/api/v1/portal/auth/activar-invitacion",
            json={"token": r_inv.json()["token"], "password": "Portal123!"},
        )

    # Verificar accesos_bitacora contiene eventos de login + alerta de reuso + activación
    with eng.connect() as conn:
        bit_rows = conn.execute(select(accesos_bitacora)).all()
    eventos = [str(r.antes) for r in bit_rows]
    assert any("v1_vendedor_login_ok" in e for e in eventos)
    assert any("v1_refresh_reuse_alert" in e for e in eventos)
    assert any("v1_portal_activacion_ok" in e for e in eventos)

    # Consultar endpoint de Observabilidad Piloto y verificar criterios Go-Live
    r_met = client.get(
        "/api/v1/observabilidad/metricas-piloto",
        cookies={"cxc_session": tok_admin},
    )
    assert r_met.status_code == 200
    m = r_met.json()
    assert m["total_operaciones_sync"] == 6
    assert m["tasa_exito_sync_pct"] >= 99.0
    assert m["pagos_aprobados_destino_ajeno"] == 0
    assert m["pagos_bloqueados_destino_ajeno"] == 3
    assert m["dispositivos_moviles_activos"] == 3
    assert m["cuentas_portal_activas"] == 3
