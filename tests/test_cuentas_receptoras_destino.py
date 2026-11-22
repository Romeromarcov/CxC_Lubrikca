"""Pruebas Fase 0 (Sprint 0C): Cuentas receptoras propias de Lubrikca y validador de destino."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from cxc.db.schema import metadata
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.cuentas_receptoras import (
    BANCOS_SUDEBAN,
    RepoCuentasReceptorasSQL,
    codigo_sudeban_de_banco,
    codigo_sudeban_de_cuenta,
    normalizar_cuenta_bancaria,
    normalizar_rif_cedula,
    normalizar_telefono_ve,
    validar_destino_pago,
)
from cxc.operativos.modelos import ENVIADO_ODOO, OperativoInvalido
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.validador import validar_documento_operativo
from cxc.web import app as web_app_mod
from cxc.web.app import app
from cxc.web.operativos_api import (
    PERMISO_APROBAR_PAGOS,
    get_repo_cuentas_receptoras,
    get_servicio_operativos,
)
from tests.capturas_dobles import OdooFalso, RepoMemoria


def _sqlite_engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    metadata.create_all(eng)
    return eng


def _sembrar_catalogo(repo: RepoCuentasReceptorasSQL) -> None:
    repo.crear(
        {
            "nombre": "Banesco Principal",
            "tipo_canal": "banco",
            "banco_codigo_sudeban": "0134",
            "banco_nombre": "Banesco",
            "numero_cuenta": "0134-0000-11-2222333344",
            "titular_nombre": "Lubrikca C.A.",
            "titular_rif": "J-40001122-3",
            "telefono": "0414-1234567",
            "moneda": "VES",
            "diario_odoo_id": 32,
            "diario_odoo_codigo": "BBAN",
            "diario_odoo_nombre": "Banco Banesco",
        }
    )
    repo.crear(
        {
            "nombre": "Provincial Propia",
            "tipo_canal": "banco",
            "banco_codigo_sudeban": "0108",
            "banco_nombre": "Banco Provincial",
            "numero_cuenta": "0108-0000-55-6666777788",
            "titular_nombre": "Lubrikca C.A.",
            "titular_rif": "J-40001122-3",
            "moneda": "VES",
            "diario_odoo_id": 33,
            "diario_odoo_codigo": "BPRO",
            "diario_odoo_nombre": "Banco Provincial",
        }
    )
    repo.crear(
        {
            "nombre": "Zelle Fluasa",
            "tipo_canal": "zelle",
            "email_zelle": "zelle@lubrikca.com",
            "titular_nombre": "Lubrikca LLC",
            "moneda": "USD",
            "diario_odoo_id": 34,
            "diario_odoo_codigo": "ZEFLU",
            "diario_odoo_nombre": "Zelle Fluasa",
        }
    )


def test_normalizadores_y_catalogo_sudeban():
    assert BANCOS_SUDEBAN["0104"] == "Venezolano de Crédito"
    assert BANCOS_SUDEBAN["0108"] == "Banco Provincial"
    assert codigo_sudeban_de_banco("Venezolano de Crédito") == "0104"
    assert codigo_sudeban_de_banco("BBVA Provincial") == "0108"
    assert codigo_sudeban_de_cuenta("0104-0000-11-2233445566") == "0104"
    assert normalizar_cuenta_bancaria("0134 0000-11") == "0134000011"
    assert normalizar_telefono_ve("+58 414-123.4567") == "04141234567"
    assert normalizar_rif_cedula("j-40001122-3") == "J400011223"


def test_provincial_a_venezolano_de_credito_bloqueado_y_nunca_asigna_diario():
    eng = _sqlite_engine()
    repo_cr = RepoCuentasReceptorasSQL(eng)
    _sembrar_catalogo(repo_cr)
    activas = repo_cr.listar(solo_activas=True, solo_propias=True)

    datos, validacion = validar_documento_operativo(
        "pago",
        {
            "cliente": "Taller Norte",
            "rif": "J-111111111",
            "monto": "2500.00",
            "moneda": "VES",
            "metodo_pago": "transferencia",
            "referencia": "REF-VVC-01",
            "banco": "Provincial",
            "banco_origen": "Banco Provincial",
            "banco_destino": "Venezolano de Crédito",
            "cuenta_destino": "01040000112233445566",
            "diario_id": 33,  # incluso si vino prefijado por error, debe limpiarse
        },
        cuentas_receptoras=activas,
    )
    assert validacion["ok"] is False
    assert validacion["diario_sugerido"] is None
    assert datos["diario_id"] is None
    dv = validacion["destino_validacion"]
    assert dv["estado"] == "DESTINO_NO_PERTENECE_LUBRIKCA"
    assert dv["bloqueante"] is True
    assert dv["diario_odoo_id"] is None
    assert "0104" in dv["mensaje"] or "Venezolano" in dv["mensaje"]


def test_validacion_destino_cuenta_errada_rif_errado_y_pago_movil_valido():
    eng = _sqlite_engine()
    repo_cr = RepoCuentasReceptorasSQL(eng)
    _sembrar_catalogo(repo_cr)
    activas = repo_cr.listar(solo_activas=True, solo_propias=True)

    # Mismo banco (Banesco 0134) pero número de cuenta ajeno -> bloqueante
    r_cta = validar_destino_pago(
        {"banco_destino": "Banesco", "cuenta_destino": "01340000999999999999", "moneda": "VES"},
        activas,
    )
    assert r_cta["estado"] == "DESTINO_NO_PERTENECE_LUBRIKCA" and r_cta["bloqueante"] is True

    # RIF destino ajeno -> bloqueante
    r_rif = validar_destino_pago(
        {"banco_destino": "Banesco", "rif_cedula_destino": "J-99999999-9", "moneda": "VES"},
        activas,
    )
    assert r_rif["estado"] == "DESTINO_NO_PERTENECE_LUBRIKCA" and r_rif["bloqueante"] is True

    # Teléfono Pago Móvil ajeno -> bloqueante
    r_tel_bad = validar_destino_pago(
        {"banco_destino": "Banesco", "telefono_destino": "0414-9999999", "moneda": "VES"},
        activas,
    )
    assert (
        r_tel_bad["estado"] == "DESTINO_NO_PERTENECE_LUBRIKCA" and r_tel_bad["bloqueante"] is True
    )

    # Pago Móvil válido con teléfono propio -> resuelve Banesco (#32)
    r_tel_ok = validar_destino_pago(
        {
            "banco_origen": "Banco Provincial",
            "banco_destino": "Banesco",
            "telefono_destino": "+58 414-1234567",
            "rif_destino": "400011223",
            "moneda": "VES",
        },
        activas,
    )
    assert r_tel_ok["estado"] == "VALIDADO_LUBRIKCA"
    assert r_tel_ok["bloqueante"] is False
    assert r_tel_ok["diario_odoo_id"] == 32

    # Zelle propio por email -> resuelve Zelle Fluasa (#34)
    r_zelle = validar_destino_pago(
        {"banco_destino": "Zelle", "email_destino": "ZELLE@LUBRIKCA.COM", "moneda": "USD"},
        activas,
    )
    assert r_zelle["estado"] == "VALIDADO_LUBRIKCA"
    assert r_zelle["diario_odoo_id"] == 34

    # Exenciones e inferencia segura cuando legacy ``banco`` == ``banco_origen``
    assert validar_destino_pago({}, activas, es_efectivo_flag=True)["estado"] == "EXENTO_EFECTIVO"
    assert (
        validar_destino_pago({}, activas, es_zelle_tercero=True)["estado"] == "EXENTO_ZELLE_TERCERO"
    )
    r_solo_orig = validar_destino_pago(
        {"banco_origen": "Banco Provincial", "banco": "Provincial"},
        activas,
    )
    assert r_solo_orig["estado"] == "SIN_DATOS_DESTINO"
    assert r_solo_orig["diario_odoo_id"] is None


def test_aprobacion_bloquea_destino_ajeno_y_permite_override_admin_auditado(monkeypatch):
    eng = _sqlite_engine()
    repo_cr = RepoCuentasReceptorasSQL(eng)
    _sembrar_catalogo(repo_cr)
    odoo = OdooFalso(
        res_partner__search=[501],
        account_move__search=[],
    )
    serv = ServicioOperativos(
        RepoOperativosSQL(eng),
        lambda: EscritorOdoo(odoo),
        repo_capturas=RepoMemoria(),
        repo_cuentas_receptoras=repo_cr,
    )
    doc = serv.crear(
        {
            "tipo": "pago",
            "partner_odoo_id": 501,
            "contraparte_nombre": "Taller Norte",
            "monto": "100.00",
            "moneda": "VES",
            "metodo_pago": "transferencia",
            "referencia": "TRF-0104",
            "banco_origen": "Banco Provincial",
            "banco_destino": "Venezolano de Crédito",
            "cuenta_destino": "01040000112233445566",
            "fecha_documento": date(2026, 11, 20).isoformat(),
        },
        actor="vendedor@lubrikca.com",
    )

    with pytest.raises(OperativoInvalido) as exc_blk:
        serv.aprobar_y_enviar_a_odoo(doc.documento_id, actor="admin@lubrikca.com")
    assert "Venezolano" in str(exc_blk.value) or "0104" in str(exc_blk.value)

    # API: usuario no admin -> 403; admin sin motivo -> 422; admin con motivo + diario -> 200
    usuario_actual: dict[str, object] = {
        "email": "cobros@lubrikca.com",
        "rol": "cobranza",
        "permisos": {PERMISO_APROBAR_PAGOS: True, "pagos": True},
    }
    monkeypatch.setattr(web_app_mod, "get_current_user_from_cookie", lambda _c: usuario_actual)
    app.dependency_overrides[get_servicio_operativos] = lambda: serv
    app.dependency_overrides[get_repo_cuentas_receptoras] = lambda: repo_cr
    try:
        client = TestClient(app)

        # Sin body: bloqueado 422
        r_422 = client.post(f"/api/operativos/{doc.documento_id}/aprobar")
        assert r_422.status_code == 422

        # No-admin intentando override -> 403
        r_403 = client.post(
            f"/api/operativos/{doc.documento_id}/aprobar",
            json={"forzar_destino": True, "motivo_override_destino": "test"},
        )
        assert r_403.status_code == 403

        # Admin sin motivo -> 422
        usuario_actual["rol"] = "admin"
        usuario_actual["email"] = "admin@lubrikca.com"
        r_no_mot = client.post(
            f"/api/operativos/{doc.documento_id}/aprobar",
            json={"forzar_destino": True, "motivo_override_destino": "   "},
        )
        assert r_no_mot.status_code == 422

        # Override admin auditado con diario_id explícito -> 200 y deja rastro en chatter/validacion
        r_ok = client.post(
            f"/api/operativos/{doc.documento_id}/aprobar",
            json={
                "forzar_destino": True,
                "motivo_override_destino": "Cuenta transitoria autorizada por gerencia",
                "diario_id": 32,
            },
        )
        assert r_ok.status_code == 200
        data_ok = r_ok.json()
        assert data_ok["estado"] == ENVIADO_ODOO
        assert data_ok["validacion"]["override_destino"]["por"] == "admin@lubrikca.com"
        assert any("[OVERRIDE DESTINO AUDITADO]" in body for _, _, body in odoo.chatter)
    finally:
        app.dependency_overrides.clear()


def test_crud_tesoreria_cuentas_receptoras_y_endpoint_bot(monkeypatch):
    eng = _sqlite_engine()
    repo_cr = RepoCuentasReceptorasSQL(eng)
    repo_cr.sembrar_desde_diarios(actor="test")
    assert len(repo_cr.listar()) >= 6

    odoo = OdooFalso(
        account_journal__fields_get={
            "id": {},
            "name": {},
            "code": {},
            "type": {},
            "currency_id": {},
            "bank_account_id": {},
        },
        account_journal__search_read=[
            {
                "id": 91,
                "name": "Banco Mercantil Test",
                "code": "BMER",
                "type": "bank",
                "currency_id": [2, "VES"],
                "bank_account_id": [401, "01050000112233445566"],
            }
        ],
        res_partner_bank__read=[{"id": 401, "acc_number": "01050000112233445566"}],
    )
    serv = ServicioOperativos(
        RepoOperativosSQL(eng),
        lambda: EscritorOdoo(odoo),
        repo_capturas=RepoMemoria(),
        repo_cuentas_receptoras=repo_cr,
    )
    usuario_admin = {
        "email": "admin@lubrikca.com",
        "rol": "admin",
        "permisos": {PERMISO_APROBAR_PAGOS: True, "pagos": True},
    }
    monkeypatch.setattr(web_app_mod, "get_current_user_from_cookie", lambda _c: usuario_admin)
    monkeypatch.setenv("BOT_API_KEY", "bot-secret-0c")
    app.dependency_overrides[get_servicio_operativos] = lambda: serv
    app.dependency_overrides[get_repo_cuentas_receptoras] = lambda: repo_cr
    try:
        client = TestClient(app)

        r_list = client.get("/api/tesoreria/cuentas-receptoras")
        assert r_list.status_code == 200
        assert r_list.json()["total"] >= 6

        r_create = client.post(
            "/api/tesoreria/cuentas-receptoras",
            json={
                "nombre": "Binance USDT Principal",
                "tipo_canal": "binance",
                "email_zelle": "binance@lubrikca.com",
                "moneda": "USD",
                "diario_odoo_id": 35,
            },
        )
        assert r_create.status_code == 201
        cid = r_create.json()["cuenta_id"]

        r_put = client.put(
            f"/api/tesoreria/cuentas-receptoras/{cid}",
            json={"titular_nombre": "Lubrikca USDT"},
        )
        assert r_put.status_code == 200 and r_put.json()["titular_nombre"] == "Lubrikca USDT"

        r_sync = client.post("/api/tesoreria/cuentas-receptoras/sincronizar-odoo")
        assert r_sync.status_code == 200
        assert r_sync.json()["sync_odoo"]["creadas"] == 1

        r_del = client.delete(f"/api/tesoreria/cuentas-receptoras/{cid}")
        assert r_del.status_code == 200 and r_del.json()["activa"] is False

        r_bot = client.get(
            "/api/bots/operativos/cuentas-receptoras",
            headers={"X-Bot-Key": "bot-secret-0c"},
        )
        assert r_bot.status_code == 200
        nombres_activos = {c["nombre"] for c in r_bot.json()["items"]}
        assert "Binance USDT Principal" not in nombres_activos
        assert "Banco Mercantil Test" in nombres_activos
    finally:
        app.dependency_overrides.clear()
