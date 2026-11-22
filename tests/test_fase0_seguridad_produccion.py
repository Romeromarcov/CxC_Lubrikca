"""Pruebas de regresión de seguridad para Sprint 0A (B1–B5).

Excluido del parche automático ``hay_sesion_valida=True`` en ``tests/conftest.py``
para ejercer el middleware real de autenticación, cookies HMAC, rate limiting,
RBAC por endpoint y alcance de reportes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from cxc import auth
from cxc.capturas.servicio import ServicioCapturas
from cxc.documentos.modelos import COTIZACION
from cxc.documentos.politica_ventas import evaluar_politica_auto_envio
from cxc.documentos.servicio import ServicioDocumentos
from cxc.models import Cliente, Moneda, OrdenVenta, TipoTasa, Vinculacion
from cxc.odoo.directorio import LectorDirectorioOdoo
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.modelos import CONFIRMADO_CXC, INCOMPLETO, PENDIENTE_APROBACION
from cxc.operativos.servicio import ServicioOperativos
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web import app as web
from cxc.web.ajustes_descuentos_api import get_servicio_ajustes
from cxc.web.capturas_api import get_servicio
from cxc.web.directorio_api import get_lector_odoo, limpiar_cache_directorio
from cxc.web.documentos_api import get_servicio_documentos
from cxc.web.operativos_api import get_servicio_operativos
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria
from tests.test_documentos_comerciales import (
    CATALOGO_PRUEBA,
    CatalogoMemoria,
    RepoDocumentosMemoria,
)
from tests.test_documentos_operativos import RepoOperativosMemoria

client = TestClient(web.app)
BOT_KEY = "clave-seguridad-bot-0a"
BOT_HDR = {"X-Bot-Key": BOT_KEY}


@pytest.fixture(autouse=True)
def _limpiar_estado_seguridad() -> Iterator[None]:
    web._LOGIN_RATE_LIMIT.clear()
    yield
    web._LOGIN_RATE_LIMIT.clear()
    web.app.dependency_overrides.clear()
    limpiar_cache_directorio()


def _cookie_para(
    usuario: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    repo: Any = None,
) -> dict[str, str]:
    pwd_hash, salt = auth.hash_password("ClaveSegura123!")
    if usuario.get("password_hash"):
        pwd_hash = str(usuario["password_hash"])
    email = str(usuario["email"]).strip().lower()
    rol = str(usuario.get("rol") or "ventas")
    permisos = list(usuario.get("permisos") or efectivos(rol))
    if repo is None:
        repo = MagicMock()
    repo.get_usuario_plataforma.return_value = {
        "email": email,
        "nombre_odoo": usuario.get("nombre") or email,
        "rol": rol,
        "activo": "TRUE",
        "password_hash": pwd_hash,
        "salt": salt,
    }
    monkeypatch.setattr(web, "get_repo", lambda: repo)
    monkeypatch.setattr(web.accesos, "ajustes_de", lambda _eng, _em: {})
    monkeypatch.setattr(
        web,
        "get_current_user_from_cookie",
        lambda token=None: {
            "email": email,
            "nombre": usuario.get("nombre") or email,
            "rol": rol,
            "permisos": permisos,
            "ajustes": {},
        }
        if token
        else None,
    )
    tok = auth.crear_session_token(
        email,
        web.SECRET_KEY,
        pwd_version=auth.password_token_version(pwd_hash),
    )
    return {"cxc_session": tok}


def _armar_docs() -> tuple[ServicioDocumentos, RepoDocumentosMemoria]:
    repo_cxc = InMemoryRepository()
    repo_cxc.upsert_clientes([Cliente("501", "CLIENTE DEMO CA", "vend1@lubrikca.com")])
    repo_docs = RepoDocumentosMemoria()
    svc_docs = ServicioDocumentos(
        repo_docs,
        CatalogoMemoria(CATALOGO_PRUEBA),
        lambda: EscritorOdoo(OdooFalso()),
        repo_cxc=repo_cxc,
    )
    return svc_docs, repo_docs


# ── B1: Preview público solo lectura y piso de precio ────────────────────────


def test_b1_preview_cotizacion_solo_lectura_y_post_convertir_403() -> None:
    svc_docs, repo_docs = _armar_docs()
    cot = svc_docs.crear(
        {
            "tipo": COTIZACION,
            "cliente": "CLIENTE DEMO CA",
            "partner_odoo_id": 501,
            "rif": "J-12345678-9",
            "vendedor_email": "vend1@lubrikca.com",
            "descuento_manual_pct": "15",
            "lineas": [
                {"producto_id": "1033", "cantidad": "5", "precio_unitario": "10.00"},
            ],
        },
        actor="bot",
    )
    web.app.dependency_overrides[get_servicio_documentos] = lambda: svc_docs

    r_get = client.get(f"/cotizacion/{cot.token_preview}")
    assert r_get.status_code == 200
    assert "solo lectura" in r_get.text.lower()
    assert 'id="convert-card"' not in r_get.text
    assert "alertas-banner" not in r_get.text
    assert "retencion-banner" not in r_get.text

    r_post = client.post(
        f"/cotizacion/{cot.token_preview}/convertir-pedido",
        json={"rif": "J-12345678-9", "dias_credito": 30},
    )
    assert r_post.status_code == 403
    doc_actual = repo_docs.obtener(cot.documento_id)
    assert doc_actual is not None and doc_actual.tipo == COTIZACION


def test_b1_politica_retiene_precio_inferior_a_lista() -> None:
    svc_docs, _ = _armar_docs()
    cot = svc_docs.crear(
        {
            "tipo": COTIZACION,
            "cliente": "CLIENTE DEMO CA",
            "partner_odoo_id": 501,
            "rif": "J-12345678-9",
            "vendedor_email": "vend1@lubrikca.com",
            "lineas": [
                {"producto_id": "1033", "cantidad": "2", "precio_unitario": "12.00"},
            ],
        },
        actor="bot",
    )
    eval_res = evaluar_politica_auto_envio(
        cot,
        InMemoryRepository(),
        precios_catalogo={"1033": Decimal("24.00")},
    )
    assert not eval_res["puede_auto_enviar"]
    assert any("precio unitario inferior" in m for m in eval_res["motivos_retencion"])


# ── B2: Comprobante público no muta estados avanzados ni campos contables ────


def test_b2_comprobante_confirmar_filtra_campos_contables_y_bloquea_avanzados() -> None:
    repo_op = RepoOperativosMemoria()
    repo_cxc = InMemoryRepository()
    svc_op = ServicioOperativos(
        repo_op,
        lambda: EscritorOdoo(OdooFalso()),
        repo_cxc=repo_cxc,
        obtener_contactos=lambda: [],
        obtener_cuentas=lambda: [],
        diarios_extra={"efectivo_usd": 19, "efectivo_ves": 20},
    )
    doc = svc_op.crear(
        {
            "tipo": "pago",
            "cliente": "CLIENTE DEMO CA",
            "monto": "100.00",
            "moneda": "USD",
            "banco": "BDV",
            "vendedor_email": "vend1@lubrikca.com",
        },
        origen="bot",
        actor="bot",
    )
    assert doc.estado == INCOMPLETO
    web.app.dependency_overrides[get_servicio_operativos] = lambda: svc_op

    # Inyección maliciosa de campos contables sobre INCOMPLETO: debe ignorarlos
    r_patch = client.post(
        f"/comprobante/{doc.token_preview}/confirmar",
        json={
            "referencia": "998877",
            "monto": "999999.00",
            "moneda": "VES",
            "diario_id": 999,
            "partner_odoo_id": 777,
            "cuenta_contable_id": 888,
            "estado": CONFIRMADO_CXC,
        },
    )
    assert r_patch.status_code == 200
    actual = repo_op.obtener(doc.documento_id)
    assert actual is not None
    assert actual.referencia == "998877"
    assert actual.monto == Decimal("100.00")
    assert actual.moneda == "USD"
    assert actual.diario_id is None
    assert actual.estado in (INCOMPLETO, PENDIENTE_APROBACION)

    # Una vez fuera de INCOMPLETO, reenviar POST público devuelve 409
    repo_op.actualizar(doc.documento_id, {"estado": CONFIRMADO_CXC})
    r_bloqueado = client.post(
        f"/comprobante/{doc.token_preview}/confirmar",
        json={"referencia": "000111"},
    )
    assert r_bloqueado.status_code == 409


# ── B3: Rutas /api/bots/* autorizadas con X-Bot-Key sin sesión web ───────────


def test_b3_rutas_bots_stock_listas_y_verificar_con_y_sin_clave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_API_KEY", BOT_KEY)
    svc_docs, _ = _armar_docs()
    svc_cap = ServicioCapturas(RepoMemoria(), EspejoFalso(), lambda: EscritorOdoo(OdooFalso()))
    web.app.dependency_overrides[get_servicio] = lambda: svc_cap
    web.app.dependency_overrides[get_servicio_documentos] = lambda: svc_docs
    web.app.dependency_overrides[get_lector_odoo] = lambda: LectorDirectorioOdoo(OdooFalso())

    for method, path, payload in [
        ("GET", "/api/bots/stock?q=0761&vivo=false", None),
        ("GET", "/api/bots/listas-precio", None),
        (
            "POST",
            "/api/bots/stock/verificar",
            {"lineas": [{"producto_id": "1033", "cantidad": "1"}]},
        ),
    ]:
        sin_clave = client.request(method, path, json=payload)
        assert sin_clave.status_code == 401, f"Se esperaba 401 sin clave en {path}"
        con_clave = client.request(method, path, json=payload, headers=BOT_HDR)
        assert con_clave.status_code == 200, f"Fallo {path} con X-Bot-Key: {con_clave.text}"


# ── B4: Métodos POST-only, RBAC financiero/configuración y alcance vendedor ─


def test_b4_sync_y_backfill_rechazan_get_con_405(monkeypatch: pytest.MonkeyPatch) -> None:
    cookies = _cookie_para(
        {"email": "admin@lubrikca.com", "rol": "admin", "permisos": efectivos("admin")},
        monkeypatch,
    )
    for ruta in ("/api/sync/manual", "/api/backfill/ventas-teoricos", "/api/backfill/entregas"):
        r = client.get(ruta, cookies=cookies)
        assert r.status_code == 405, f"{ruta} debe ser POST-only"


def test_b4_rbac_bloquea_ventas_en_finanzas_config_cxp_y_otros_clientes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = InMemoryRepository()
    repo.upsert_clientes(
        [
            Cliente("10", "MI CLIENTE CA", "vendedor1@lubrikca.com"),
            Cliente("20", "OTRO CLIENTE CA", "vendedor2@lubrikca.com"),
        ]
    )
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S00010",
                cliente_id="10",
                fecha=date(2026, 11, 1),
                fecha_entrega=None,
                monto_total=Decimal("100.00"),
                lista_precios="Pago VES",
                vendedor_email="vendedor1@lubrikca.com",
                es_primera_compra=False,
            ),
            OrdenVenta(
                so_id="S00020",
                cliente_id="20",
                fecha=date(2026, 11, 1),
                fecha_entrega=None,
                monto_total=Decimal("200.00"),
                lista_precios="Pago VES",
                vendedor_email="vendedor2@lubrikca.com",
                es_primera_compra=False,
            ),
        ]
    )
    repo.update_vinculacion(
        Vinculacion(
            vinc_id="V_1",
            pago_id="P_1",
            so_id="S00010",
            monto_aplicado=Decimal("10"),
            hora_pago_confirmada=datetime(2026, 11, 2, 10, 0),
            tasa_bcv_aplicada=Decimal("45"),
            tasa_binance_aplicada=Decimal("50"),
            es_tasa_heredada=False,
            equiv_usd_bcv=Decimal("10"),
            equiv_usd_binance=Decimal("10"),
            equiv_ves_bcv=Decimal("450"),
            equiv_ves_binance=Decimal("500"),
            confirmado_por="test",
            timestamp_registro=datetime(2026, 11, 2, 10, 0),
            moneda_abono=Moneda.USD,
            tipo_tasa_abono=TipoTasa.BCV,
        )
    )
    mrepo = MagicMock(wraps=repo)
    web.app.dependency_overrides[get_servicio_ajustes] = lambda: MagicMock()
    cookies = _cookie_para(
        {"email": "vendedor1@lubrikca.com", "rol": "ventas", "permisos": efectivos("ventas")},
        monkeypatch,
        repo=mrepo,
    )

    rutas_prohibidas = [
        ("POST", "/api/config/tasas", {"tasa_bcv": 50.0, "tasa_binance": 60.0}),
        ("POST", "/api/vincular", {"pago_id": "P_1", "so_id": "S00010", "monto_aplicado": 10}),
        (
            "POST",
            "/api/auditoria/aceptar-discrepancia",
            {"discrepancia_id": "D_1", "so_id": "S00010", "tipo_discrepancia": "OTRA"},
        ),
        ("GET", "/api/reportes/cxp-propios", None),
        ("GET", "/api/reportes/estado-cuenta/20", None),
        (
            "POST",
            "/api/ordenes/S00010/ajustes-descuentos/especial",
            {"porcentaje": 5, "motivo": "intento no autorizado"},
        ),
        ("POST", "/api/sync/manual", None),
    ]
    for metodo, url, payload in rutas_prohibidas:
        r = client.request(metodo, url, json=payload, cookies=cookies)
        assert r.status_code == 403, (
            f"Ventas no debe acceder a {metodo} {url} (dio {r.status_code})"
        )

    # Su propio estado de cuenta sí responde 200 y acotado
    r_propio = client.get("/api/reportes/estado-cuenta/10", cookies=cookies)
    assert r_propio.status_code == 200


# ── B5: scrypt, revocación, resiliencia Odoo, rate-limit, registro y healthz ─


def test_b5_scrypt_upgrade_y_revocacion_token_por_cambio_de_clave() -> None:
    clave = "MiClaveFuerte#2026"
    h_scrypt, salt = auth.hash_password(clave)
    assert h_scrypt.startswith("scrypt$")
    assert auth.verificar_password(clave, h_scrypt, salt)
    assert not auth.verificar_password("otra", h_scrypt, salt)

    # Legacy SHA-256 se verifica y autenticar_usuario lo actualiza a scrypt$
    legacy_salt = "legacy-salt-0a"
    legacy = hashlib.sha256((clave + legacy_salt).encode("utf-8")).hexdigest()
    repo = MagicMock()
    repo.get_usuario_plataforma.return_value = {
        "email": "u@lubrikca.com",
        "rol": "ventas",
        "activo": "TRUE",
        "nombre_odoo": "Usuario",
        "password_hash": legacy,
        "salt": legacy_salt,
    }
    user_info, err = auth.autenticar_usuario(
        lambda *_a: [{"id": 1, "name": "Usuario", "login": "u@lubrikca.com"}],
        repo,
        "u@lubrikca.com",
        clave,
    )
    assert user_info is not None and not err
    repo.upsert_usuario_plataforma.assert_called()
    row_guardado = repo.upsert_usuario_plataforma.call_args.args[0]
    assert str(row_guardado["password_hash"]).startswith("scrypt$")

    # Cambio de contraseña revoca el token firmado anteriormente
    tok_v1 = auth.crear_session_token(
        "u@lubrikca.com", "sec-test", pwd_version=auth.password_token_version(h_scrypt)
    )
    h_nuevo, _salt_nuevo = auth.hash_password("NuevaClave#2027")
    assert auth.verificar_session_token(
        tok_v1, "sec-test", expected_pwd_version=auth.password_token_version(h_scrypt)
    ) == "u@lubrikca.com"
    assert (
        auth.verificar_session_token(
            tok_v1, "sec-test", expected_pwd_version=auth.password_token_version(h_nuevo)
        )
        is None
    )


def test_b5_login_resiste_caida_odoo_y_bloquea_usuario_desactivado_en_odoo() -> None:
    h, salt = auth.hash_password("ClaveOk#1")
    repo = MagicMock()
    repo.get_usuario_plataforma.return_value = {
        "email": "ana@lubrikca.com",
        "rol": "ventas",
        "activo": "TRUE",
        "nombre_odoo": "Ana",
        "password_hash": h,
        "salt": salt,
    }
    # Caída de red en Odoo -> permite acceso local si usuario sigue activo localmente
    user_ok, err_ok = auth.autenticar_usuario(
        lambda *_a: (_ for _ in ()).throw(ConnectionError("Odoo timeout")),
        repo,
        "ana@lubrikca.com",
        "ClaveOk#1",
    )
    assert user_ok is not None and not err_ok

    # Odoo sí responde vacío (inactivo) -> deniega aunque clave local sea correcta
    user_deny, err_deny = auth.autenticar_usuario(
        lambda *_a: [], repo, "ana@lubrikca.com", "ClaveOk#1"
    )
    assert user_deny is None and "activo en odoo" in (err_deny or "").lower()


def test_b5_login_rate_limit_registro_cerrado_y_healthz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = MagicMock()
    repo.get_usuario_plataforma.return_value = None
    repo.all_usuarios_plataforma.return_value = [{"email": "admin@lubrikca.com", "activo": "TRUE"}]
    monkeypatch.setattr(web, "get_repo", lambda: repo)
    monkeypatch.setattr(web, "_connect", lambda *_a: (lambda *_x: []))

    # 11 intentos fallidos -> el 11° devuelve 429
    statuses = [
        client.post(
            "/api/auth/login",
            json={"email": "bruteforce@lubrikca.com", "password": "mal"},
        ).status_code
        for _ in range(11)
    ]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429

    # Auto-registro sin invitación ni sesión admin -> 403
    r_reg = client.post(
        "/api/auth/register-password",
        json={"email": "intruso@lubrikca.com", "password": "ClaveNueva#123"},
    )
    assert r_reg.status_code == 403

    # /healthz accesible sin cookie de sesión
    r_hz = client.get("/healthz")
    assert r_hz.status_code == 200
    assert r_hz.json()["status"] == "ok"
