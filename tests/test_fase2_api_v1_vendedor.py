"""Pruebas integrales Fase 2 — Endpoints API v1 complementarios para App Móvil de Vendedores."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
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
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.cuentas_receptoras import RepoCuentasReceptorasSQL
from cxc.operativos.lotes_efectivo import RepoLotesEfectivo
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


class _CatalogoFase2:
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
                producto_id="1077",
                codigo="0997",
                nombre="ACEITE MINERAL 20W50",
                marca="LUBRIKCA",
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


class _RepoFase2(InMemoryRepository):
    def __init__(self, engine: Any) -> None:
        super().__init__()
        self._engine = engine


@pytest.fixture(autouse=True)
def _limpiar() -> Iterator[None]:
    _RATE_LIMITER_V1.clear_all()
    yield
    _RATE_LIMITER_V1.clear_all()
    web_app.app.dependency_overrides.clear()


def _setup_entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(eng)
    repo = _RepoFase2(eng)

    vh, vs = auth.hash_password("Vend123!")
    repo.upsert_usuario_plataforma(
        {
            "email": "vendedor@lubrikca.com",
            "nombre_odoo": "Vendedor Uno",
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
            "nombre_odoo": "Admin Gerencia",
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
            Cliente("198", "CLIENTE AJENO SA", vendedor_email="otro@lubrikca.com"),
        ]
    )
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="SO-BCV-1",
                cliente_id="197",
                fecha=date(2026, 11, 10),
                fecha_entrega=date(2026, 11, 10),
                monto_total=Decimal("200.00"),
                lista_precios="21",
                vendedor_email="vendedor@lubrikca.com",
                es_primera_compra=False,
                dias_credito=7,
            ),
            OrdenVenta(
                so_id="SO-BIN-1",
                cliente_id="197",
                fecha=date(2026, 11, 12),
                fecha_entrega=date(2026, 11, 12),
                monto_total=Decimal("150.00"),
                lista_precios="22",
                vendedor_email="vendedor@lubrikca.com",
                es_primera_compra=False,
                dias_credito=0,
            ),
            OrdenVenta(
                so_id="SO-OTRO-1",
                cliente_id="198",
                fecha=date(2026, 11, 14),
                fecha_entrega=date(2026, 11, 14),
                monto_total=Decimal("300.00"),
                lista_precios="21",
                vendedor_email="otro@lubrikca.com",
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
                monto=Decimal("46000.00"),
                moneda=Moneda.VES,
                metodo_pago="Banco Bancamiga",
                fecha_pago=datetime(2026, 11, 11, 9, 0),
                vendedor_email="vendedor@lubrikca.com",
                numero_odoo="PG-1",
                referencia="REF-PG-1",
            )
        ]
    )
    repo.add_vinculacion(
        congelar_en_vinculacion(
            Vinculacion(
                vinc_id="V-1",
                pago_id="P-1",
                so_id="SO-BCV-1",
                monto_aplicado=Decimal("46000.00"),
                hora_pago_confirmada=datetime(2026, 11, 11, 9, 0),
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
        _CatalogoFase2(),
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
    return {"eng": eng, "repo": repo, "svc_contactos": svc_contactos}


def _login_vendedor(email: str, password: str) -> str:
    r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "device_id": "dev-1", "plataforma": "android"},
    )
    assert r.status_code == 200
    return str(r.json()["access_token"])


def _crear_token_portal(eng: Any) -> str:
    tok_admin = auth.crear_session_token("admin@lubrikca.com", web_app.SECRET_KEY)
    r_inv = client.post(
        "/api/v1/portal/invitaciones",
        cookies={"cxc_session": tok_admin},
        json={"cliente_id": "197", "partner_odoo_id": 197, "email": "cli@1080zip.com"},
    )
    assert r_inv.status_code == 201
    r_act = client.post(
        "/api/v1/portal/auth/activar-invitacion",
        json={"token": r_inv.json()["token"], "password": "Portal123!"},
    )
    assert r_act.status_code == 200
    r_log = client.post(
        "/api/v1/portal/auth/login",
        json={"email": "cli@1080zip.com", "password": "Portal123!", "device_id": "web-1"},
    )
    assert r_log.status_code == 200
    assert eng is not None
    return str(r_log.json()["access_token"])


def test_fase2_bootstrap_delta_contactos_y_politica(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _setup_entorno(monkeypatch, tmp_path)
    env["svc_contactos"].crear(
        {
            "nombre": "DISTRIBUIDORA NORTE C.A.",
            "rif": "J-30112233-4",
            "telefono": "04141234567",
            "rol": "cliente",
            "vendedor_email": "vendedor@lubrikca.com",
        },
        origen="cxc",
        actor="vendedor@lubrikca.com",
    )
    tok = _login_vendedor("vendedor@lubrikca.com", "Vend123!")

    r_boot = client.get("/api/v1/sync/bootstrap", headers={"Authorization": f"Bearer {tok}"})
    assert r_boot.status_code == 200
    boot = r_boot.json()
    assert isinstance(boot["politica_ventas"], dict)
    assert boot["listas_precio"]
    assert all("lista_id" in lp for lp in boot["listas_precio"])
    assert all("nombre" in d for d in boot["descuentos_catalogo"])
    assert any(c["rif"] == "J-30112233-4" for c in boot["contactos_maestro"])

    from cxc.operativos.terceros import CuentaTercero

    ct_dict = CuentaTercero(
        cuenta_id=7,
        alias="Zelle Aliado",
        titular="Aliado CA",
        correo_telefono="aliado@example.com",
        proveedor_odoo_id=123,
        proveedor_nombre="Aliado Proveedor",
        diario_odoo_id=None,
        diario_odoo_nombre="",
        cuenta_cxp_override_id=None,
        cuenta_cxc_override_id=None,
        moneda="USD",
        notas="",
        activo=True,
        creado_por="admin",
        creado_en=datetime.now(UTC).replace(tzinfo=None),
        actualizado_en=datetime.now(UTC).replace(tzinfo=None),
    ).a_dict()
    assert ct_dict["cuenta_tercero_id"] == 7
    assert ct_dict["email"] == "aliado@example.com"
    assert ct_dict["activa"] is True

    cli_197 = next(c for c in boot["clientes"] if c["cliente_id"] == "197")
    assert len(cli_197["ordenes_pendientes"]) == 2
    assert {o["ruta_pago"] for o in cli_197["ordenes_pendientes"]} == {"bcv", "binance"}

    futuro = (datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)).isoformat(
        timespec="seconds"
    )
    r_delta = client.get(
        f"/api/v1/sync/delta?cursor={futuro}", headers={"Authorization": f"Bearer {tok}"}
    )
    assert r_delta.status_code == 200
    assert r_delta.json()["contactos_maestro"] == []


def test_fase2_stock_verificar_audiencia_y_filtros(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _setup_entorno(monkeypatch, tmp_path)
    assert client.get("/api/v1/stock/verificar").status_code == 401

    tok_portal = _crear_token_portal(env["eng"])
    assert (
        client.get(
            "/api/v1/stock/verificar", headers={"Authorization": f"Bearer {tok_portal}"}
        ).status_code
        == 403
    )

    tok_vend = _login_vendedor("vendedor@lubrikca.com", "Vend123!")
    r = client.get(
        "/api/v1/stock/verificar?q=0761",
        headers={"Authorization": f"Bearer {tok_vend}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["codigo"] == "0761"
    assert body["items"][0]["stock_disponible"] == 10.0

    r_id = client.get(
        "/api/v1/stock/verificar?producto_id=1077",
        headers={"Authorization": f"Bearer {tok_vend}"},
    )
    assert r_id.status_code == 200
    assert r_id.json()["total"] == 1
    assert r_id.json()["items"][0]["codigo"] == "0997"


def test_fase2_vendedor_lotes_y_cartera_por_ruta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _setup_entorno(monkeypatch, tmp_path)
    tok_portal = _crear_token_portal(env["eng"])
    tok_vend = _login_vendedor("vendedor@lubrikca.com", "Vend123!")
    tok_admin = _login_vendedor("admin@lubrikca.com", "Admin123!")

    # Audiencias
    assert client.get("/api/v1/vendedor/lotes-efectivo").status_code == 401
    assert client.get("/api/v1/vendedor/cartera").status_code == 401
    assert (
        client.get(
            "/api/v1/vendedor/lotes-efectivo", headers={"Authorization": f"Bearer {tok_portal}"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/vendedor/cartera", headers={"Authorization": f"Bearer {tok_portal}"}
        ).status_code
        == 403
    )

    # Lotes efectivo: uno del vendedor y uno de otro vendedor
    repo_lotes = RepoLotesEfectivo(env["eng"])
    repo_lotes.crear_lote(
        vendedor_email="vendedor@lubrikca.com",
        vendedor_nombre="Vendedor Uno",
        items=[{"metodo_pago": "Efectivo USD", "monto": 100, "moneda": "USD"}],
        recibido_por="caja@lubrikca.com",
    )
    repo_lotes.crear_lote(
        vendedor_email="otro@lubrikca.com",
        vendedor_nombre="Otro Vendedor",
        items=[{"metodo_pago": "Efectivo USD", "monto": 250, "moneda": "USD"}],
        recibido_por="caja@lubrikca.com",
    )

    r_lotes_v = client.get(
        "/api/v1/vendedor/lotes-efectivo", headers={"Authorization": f"Bearer {tok_vend}"}
    )
    assert r_lotes_v.status_code == 200
    assert r_lotes_v.json()["total"] == 1
    assert r_lotes_v.json()["alcance_global"] is False

    r_lotes_a = client.get(
        "/api/v1/vendedor/lotes-efectivo", headers={"Authorization": f"Bearer {tok_admin}"}
    )
    assert r_lotes_a.status_code == 200
    assert r_lotes_a.json()["total"] == 2
    assert r_lotes_a.json()["alcance_global"] is True

    # Cartera separada BCV vs Binance y filtrada por vendedor
    r_cart_v = client.get(
        "/api/v1/vendedor/cartera", headers={"Authorization": f"Bearer {tok_vend}"}
    )
    assert r_cart_v.status_code == 200
    cart_v = r_cart_v.json()
    assert cart_v["resumen"]["total_clientes"] == 1
    cli = cart_v["clientes"][0]
    assert cli["cliente_id"] == "197"
    assert cli["ruta_bcv"]["ventas_usd"] == 200.0
    assert cli["ruta_bcv"]["pagos_usd"] == 50.0
    assert cli["ruta_bcv"]["saldo_usd"] == 150.0
    assert cli["ruta_bcv"]["ordenes_pendientes"][0]["so_id"] == "SO-BCV-1"
    assert cli["ruta_binance"]["ventas_usd"] == 150.0
    assert cli["ruta_binance"]["saldo_usd"] == 150.0
    assert cli["ruta_binance"]["ordenes_pendientes"][0]["so_id"] == "SO-BIN-1"

    r_cart_a = client.get(
        "/api/v1/vendedor/cartera", headers={"Authorization": f"Bearer {tok_admin}"}
    )
    assert r_cart_a.status_code == 200
    assert r_cart_a.json()["resumen"]["total_clientes"] == 2
