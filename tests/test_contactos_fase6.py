"""Pruebas Fase 6: Maestro Único de Contactos (Odoo + Bots + CxC) y Motor Anti-Duplicados."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete

from cxc.capturas.servicio import ServicioCapturas
from cxc.contactos.modelos import (
    ContactoDuplicadoError,
    ContactoInvalido,
    ContactoMaestro,
)
from cxc.contactos.motor import (
    buscar_posibles_duplicados,
    detectar_grupos_duplicados,
    evaluar_completitud,
    normalizar_email,
    normalizar_nombre,
    normalizar_rif_canonico,
    normalizar_telefono,
)
from cxc.contactos.repositorio import RepoContactosMemoria, RepoContactosSQL
from cxc.contactos.servicio import ServicioContactos
from cxc.db import schema
from cxc.odoo.directorio import ContactoOdoo, LectorDirectorioOdoo
from cxc.odoo.escritura import EscritorOdoo
from cxc.permisos import efectivos
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from cxc.web.contactos_api import get_servicio_contactos
from cxc.web.directorio_api import get_lector_odoo, limpiar_cache_directorio
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria

client = TestClient(app)
KEY = "clave-bot-contactos-f6"
BOT = {"X-Bot-Key": KEY}
VENTAS_USER = {"email": "ventas@lubrikca.com", "rol": "ventas", "permisos": efectivos("ventas")}
GERENTE_USER = {
    "email": "gerencia@lubrikca.com",
    "rol": "gerente_ventas",
    "permisos": efectivos("gerente_ventas"),
}


# ── Pruebas Unitarias del Motor ───────────────────────────────────────────────


def test_normalizaciones_contacto() -> None:
    assert normalizar_rif_canonico(" j-12345678-9 ") == "J123456789"
    assert normalizar_rif_canonico("123") == ""
    assert normalizar_telefono("+58 (414) 555-1234") == "4145551234"
    assert normalizar_telefono("0414-555-1234") == "4145551234"
    assert normalizar_telefono("00584145551234") == "4145551234"
    assert normalizar_telefono("123") == ""
    assert normalizar_nombre("Inversiones Los Pinos, C.A.") == "inversiones los pinos"
    assert normalizar_nombre("INVERSIONES LOS PINOS S.R.L.") == "inversiones los pinos"
    assert normalizar_nombre("Lubricantes Ávila S.A.") == "lubricantes avila"
    assert normalizar_email("  VENTAS@Pinos.com ") == "ventas@pinos.com"
    assert normalizar_email("sin-arroba") == ""


def test_evaluar_completitud_prospecto_vs_cliente() -> None:
    # Prospecto con nombre + teléfono ya está listo para borrador Odoo, aunque falten datos
    pct_p, falt_p, listo_p = evaluar_completitud(
        {"rol": "prospecto", "nombre": "Taller Norte", "telefono": "04141112233"}
    )
    assert pct_p == 50
    assert listo_p is True
    assert "RIF / Cédula" in falt_p

    # Cliente sin RIF NO está listo para Odoo
    pct_c, falt_c, listo_c = evaluar_completitud(
        {"rol": "cliente", "nombre": "Taller Norte", "telefono": "04141112233"}
    )
    assert pct_c == 40
    assert listo_c is False
    assert "RIF / Cédula" in falt_c

    # Completo con GPS llega al 100%
    pct_ok, falt_ok, listo_ok = evaluar_completitud(
        {
            "rol": "cliente",
            "nombre": "Taller Norte C.A.",
            "rif": "J-30112233-4",
            "telefono": "04141112233",
            "direccion": "Av. Lara",
            "email": "info@taller.com",
            "ubicacion": {"latitud": 10.162, "longitud": -68.007},
        }
    )
    assert pct_ok == 100
    assert falt_ok == []
    assert listo_ok is True


def test_buscar_duplicados_y_detectar_grupos() -> None:
    c1 = ContactoMaestro(
        contacto_id=1,
        odoo_partner_id=101,
        origen="odoo",
        rol="cliente",
        nombre="Inversiones Los Pinos C.A.",
        nombre_normalizado=normalizar_nombre("Inversiones Los Pinos C.A."),
        rif="J-30998877-1",
        rif_normalizado=normalizar_rif_canonico("J-30998877-1"),
        telefono="0414-2223344",
        telefono_normalizado=normalizar_telefono("0414-2223344"),
        email="compras@lospinos.com",
        completitud_pct=90,
    )
    c2 = ContactoMaestro(
        contacto_id=2,
        odoo_partner_id=None,
        origen="bot",
        rol="prospecto",
        nombre="Inversiones Los Pinos",
        nombre_normalizado=normalizar_nombre("Inversiones Los Pinos"),
        rif="J309988771",
        rif_normalizado=normalizar_rif_canonico("J309988771"),
        telefono="584142223344",
        telefono_normalizado=normalizar_telefono("584142223344"),
        completitud_pct=65,
    )
    c3 = ContactoMaestro(
        contacto_id=3,
        odoo_partner_id=None,
        origen="cxc",
        rol="prospecto",
        nombre="Autorepuestos El Sol C.A.",
        nombre_normalizado=normalizar_nombre("Autorepuestos El Sol C.A."),
        telefono="0412-9990001",
        telefono_normalizado=normalizar_telefono("0412-9990001"),
        completitud_pct=50,
    )
    # Fuzzy nombre similar sin RIF
    dups_nombre = buscar_posibles_duplicados(
        {"nombre": "Inversiones Los Pino SRL", "telefono": "04160000000"},
        [c1, c3],
    )
    assert len(dups_nombre) == 1
    assert dups_nombre[0]["contacto_id"] == 1
    assert dups_nombre[0]["bloqueante"] is False
    assert any("similar" in m for m in dups_nombre[0]["motivos"])

    # Exacto RIF -> score 100 y bloqueante
    dups_rif = buscar_posibles_duplicados({"rif": "J 30998877-1"}, [c1, c3])
    assert len(dups_rif) == 1
    assert dups_rif[0]["bloqueante"] is True
    assert dups_rif[0]["score"] == 100

    # Cola de duplicados elige el de Odoo como maestro sugerido
    pares = detectar_grupos_duplicados([c1, c2, c3])
    assert len(pares) == 1
    assert pares[0]["maestro_sugerido_id"] == 1
    assert pares[0]["duplicado_sugerido_id"] == 2
    assert pares[0]["bloqueante"] is True


# ── Pruebas de Servicio: Sync Odoo, Anti-Duplicados, Envío Odoo, Fusión ───────


def test_servicio_sync_odoo_preserva_gps_y_fusiona_duplicados() -> None:
    repo = RepoContactosMemoria()
    odoo = OdooFalso()
    svc = ServicioContactos(repo, escritor_factory=lambda: EscritorOdoo(odoo))

    # Contacto existente en CxC con GPS capturado antes del sync de Odoo
    local_gps = svc.crear(
        {
            "nombre": "Distribuidora Caracas C.A.",
            "rol": "cliente",
            "rif": "J-40123456-7",
            "telefono": "0414-7778899",
            "ubicacion": {"latitud": 10.4806, "longitud": -66.9036, "plus_code": "772M+5M"},
            "odoo_partner_id": 77,
        },
        origen="cxc",
    )
    assert local_gps.latitud is not None

    # Sync desde Odoo sin GPS no borra el GPS local
    co = ContactoOdoo(
        id=77,
        nombre="Distribuidora Caracas C.A.",
        rif="J-40123456-7",
        email="pagos@caracas.com",
        telefono="0414-7778899",
        ciudad="Caracas",
        direccion="Av. Libertador",
        es_empresa=True,
        customer_rank=1,
        supplier_rank=0,
        clasificacion="cliente",
        clasificacion_etiqueta="Cliente",
        es_borrador_telegram=False,
        referencia="",
        vendedor="ana@lubrikca.com",
        notas="",
    )
    stats = svc.sincronizar_desde_odoo([co])
    assert stats["actualizados"] == 1
    tras_sync = repo.obtener_por_odoo_id(77)
    assert tras_sync is not None
    assert tras_sync.latitud == Decimal("10.4806")
    assert tras_sync.email == "pagos@caracas.com"

    # Crear contacto nuevo con mismo RIF queda bloqueado por defecto
    with pytest.raises(ContactoDuplicadoError) as exc_info:
        svc.crear({"nombre": "Otra Distribuidora", "rif": "J401234567", "telefono": "04120001122"})
    assert len(exc_info.value.duplicados) == 1

    # Crear contacto nuevo desde bot con teléfono similar (alerta no bloqueante) y fusionar
    dup_bot = svc.crear(
        {
            "nombre": "Distribuidora Caracas Sucursal",
            "rol": "prospecto",
            "telefono": "+58 414 777 8899",
            "notas": "Capturado en ruta",
        },
        origen="bot",
        actor="vendedor@lubrikca.com",
    )
    assert dup_bot.posibles_duplicados
    assert dup_bot.contacto_id is not None and tras_sync.contacto_id is not None

    res_fus = svc.fusionar(
        dup_bot.contacto_id, tras_sync.contacto_id, actor="gerencia@lubrikca.com"
    )
    assert res_fus["duplicado"]["estado_validacion"] == "DUPLICADO"
    assert res_fus["duplicado"]["activo"] is False
    assert "[Fusionado #" in res_fus["maestro"]["notas"]


def test_servicio_validar_y_enviar_a_odoo_y_bloqueo_rif_en_odoo() -> None:
    repo = RepoContactosMemoria()
    odoo = OdooFalso()
    svc = ServicioContactos(repo, escritor_factory=lambda: EscritorOdoo(odoo))

    # Incompleto no puede enviarse a Odoo
    incompleto = svc.crear({"nombre": "Solo Nombre", "rol": "cliente"}, origen="bot")
    with pytest.raises(ContactoInvalido):
        svc.validar_y_enviar_a_odoo(incompleto.contacto_id or 0, actor="gerencia@lubrikca.com")

    # Prospecto con nombre + teléfono + GPS -> se crea en Odoo con EscritorOdoo y promueve cliente
    completo = svc.crear(
        {
            "nombre": "Lubricantes Valencia C.A.",
            "rol": "cliente",
            "rif": "J-50111222-3",
            "telefono": "0414-3334455",
            "direccion": "Zona Industrial Sur",
            "latitud": 10.162,
            "longitud": -68.007,
        },
        origen="bot",
    )
    enviado = svc.validar_y_enviar_a_odoo(completo.contacto_id or 0, actor="gerencia@lubrikca.com")
    assert enviado.estado_validacion == "ENVIADO_ODOO"
    assert enviado.odoo_partner_id is not None

    # Si alguien intenta enviar otro contacto cuyo RIF ya existe en Odoo (partner #999), bloquea
    odoo.r["res.partner:search"] = (
        lambda args, kw: [999]
        if any(isinstance(d, list) and d and d[0] == "vat" for d in args[0])
        else []
    )
    odoo.r["res.partner:read"] = [{"id": 999, "name": "Ya En Odoo", "vat": "J-99887766-5"}]
    otro = svc.crear(
        {
            "nombre": "Repetido En Odoo",
            "rol": "cliente",
            "rif": "J-99887766-5",
            "telefono": "0412-1112233",
        },
        origen="cxc",
    )
    with pytest.raises(ContactoDuplicadoError):
        svc.validar_y_enviar_a_odoo(otro.contacto_id or 0, actor="gerencia@lubrikca.com")


# ── Pruebas REST API + Auth + Capturas + Bots ─────────────────────────────────


@pytest.fixture()
def entorno_contactos(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[RepoContactosMemoria, OdooFalso]]:
    monkeypatch.setenv("BOT_API_KEY", KEY)
    limpiar_cache_directorio()
    repo = RepoContactosMemoria()
    odoo = OdooFalso()
    partners_dir = [
        {
            "id": 10,
            "name": "Taller Central Odoo C.A.",
            "vat": "J-29998887-6",
            "email": "central@taller.com",
            "phone": "0414-1234567",
            "mobile": False,
            "city": "Valencia",
            "street": "Av. Bolívar",
            "street2": "",
            "partner_latitude": 0.0,
            "partner_longitude": 0.0,
            "is_company": True,
            "company_type": "company",
            "customer_rank": 2,
            "supplier_rank": 0,
            "ref": "",
            "comment": "",
            "user_id": [5, "Ana Vendedora"],
            "parent_id": False,
            "type": "contact",
        }
    ]
    svc = ServicioContactos(repo, escritor_factory=lambda: EscritorOdoo(odoo))
    repo_cap = RepoMemoria()
    svc_cap = ServicioCapturas(repo_cap, EspejoFalso(), lambda: EscritorOdoo(odoo), max_intentos=1)

    def _exec_dir(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if model == "res.partner" and method == "search_read":
            return list(partners_dir)
        return []

    app.dependency_overrides[get_servicio_contactos] = lambda: svc
    app.dependency_overrides[get_lector_odoo] = lambda: LectorDirectorioOdoo(_exec_dir)
    app.dependency_overrides[get_servicio] = lambda: svc_cap
    yield repo, odoo
    app.dependency_overrides.pop(get_servicio_contactos, None)
    app.dependency_overrides.pop(get_lector_odoo, None)
    app.dependency_overrides.pop(get_servicio, None)
    limpiar_cache_directorio()


def test_api_auth_y_permisos_contactos(
    entorno_contactos: tuple[RepoContactosMemoria, OdooFalso],
) -> None:
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=None):
        assert client.get("/api/contactos/maestro").status_code == 401
        assert client.post("/api/contactos/maestro", json={"nombre": "X"}).status_code == 401

    sin_perm = {"email": "inv@x.com", "rol": "ventas", "permisos": ["inicio"]}
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=sin_perm):
        assert client.get("/api/contactos/maestro").status_code == 403

    # Vendedor puede consultar, pero NO gestionar/crear sin contactos.gestionar
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=VENTAS_USER):
        r_get = client.get("/api/contactos/maestro")
        assert r_get.status_code == 200
        assert r_get.json()["puede_gestionar"] is False
        assert client.post("/api/contactos/maestro", json={"nombre": "No Perm"}).status_code == 403

    # Bot sin clave -> 401
    assert (
        client.post("/api/bots/contactos/verificar", json={"rif": "J-29998887-6"}).status_code
        == 401
    )


def test_api_flujo_completo_maestro_y_bots(
    entorno_contactos: tuple[RepoContactosMemoria, OdooFalso],
) -> None:
    _, odoo = entorno_contactos

    with patch("cxc.web.app.get_current_user_from_cookie", return_value=GERENTE_USER):
        # 1. Primera carga auto-hidrata desde Odoo
        r_list = client.get("/api/contactos/maestro")
        assert r_list.status_code == 200
        d_list = r_list.json()
        assert d_list["resumen"]["en_odoo"] == 1
        assert d_list["items"][0]["nombre"] == "Taller Central Odoo C.A."

        # 2. Bot verifica duplicado por RIF -> bloqueante=True
        r_bot_chk = client.post(
            "/api/bots/contactos/verificar",
            headers=BOT,
            json={"nombre": "Taller Central", "rif": "J299988876", "rol": "prospecto"},
        )
        assert r_bot_chk.status_code == 200
        assert r_bot_chk.json()["duplicado_bloqueante"] is True
        assert r_bot_chk.json()["candidatos"][0]["odoo_partner_id"] == 10

        # 3. Bot intenta crear duplicado por RIF -> 409
        r_bot_dup = client.post(
            "/api/bots/contactos",
            headers=BOT,
            json={"nombre": "Taller Central Dup", "rif": "J-29998887-6", "telefono": "04141234567"},
        )
        assert r_bot_dup.status_code == 409
        assert len(r_bot_dup.json()["duplicados"]) == 1

        # 4. Bot envía captura kind="contacto" nuevo con GPS -> crea captura y registra en maestro
        r_cap = client.post(
            "/api/bots/capturas",
            headers=BOT,
            json={
                "kind": "contacto",
                "draft": {
                    "nombre": "Lubricantes Express Bot",
                    "telefono": "0412-5556677",
                    "direccion": "Av. Intercomunal",
                    "ubicacion": {"latitud": 10.22, "longitud": -67.98, "plus_code": "773P+XX"},
                },
                "source": {"vendedor_email": "vendedor1@lubrikca.com"},
                "idempotency_key": "bot-cap-contacto-f6-1",
            },
        )
        assert r_cap.status_code in (200, 201)
        maestro_id_bot = r_cap.json()["resultado"].get("contacto_maestro_id")
        assert maestro_id_bot is not None

        # 5. GET /api/bots/contactos incluye tanto Odoo como contactos del Maestro
        r_bdir = client.get("/api/bots/contactos?q=Taller", headers=BOT)
        assert r_bdir.status_code == 200
        assert r_bdir.json()["total_filtrado"] >= 1

        # 6. Panel Web crea contacto nuevo, edita para completar y valida/envía a Odoo
        r_crea = client.post(
            "/api/contactos/maestro",
            json={
                "nombre": "Frenos Aragua C.A.",
                "rol": "cliente",
                "rif": "J-40990011-2",
                "telefono": "0424-8889900",
                "latitud": 10.25,
                "longitud": -67.60,
            },
        )
        assert r_crea.status_code == 201
        cid = r_crea.json()["contacto"]["contacto_id"]

        r_edit = client.put(
            f"/api/contactos/maestro/{cid}",
            json={"ciudad": "Maracay", "direccion": "Av. Las Delicias"},
        )
        assert r_edit.status_code == 200
        assert r_edit.json()["contacto"]["ciudad"] == "Maracay"

        r_env = client.post(f"/api/contactos/maestro/{cid}/validar-odoo")
        assert r_env.status_code == 200
        assert r_env.json()["contacto"]["estado_validacion"] == "ENVIADO_ODOO"
        assert r_env.json()["contacto"]["odoo_partner_id"] is not None

        # 7. Crear otro con teléfono igual (forzado) y fusionar desde cola duplicados
        r_sim = client.post(
            "/api/contactos/maestro",
            json={
                "nombre": "Frenos Aragua Sucursal",
                "rol": "prospecto",
                "telefono": "04248889900",
                "forzar_duplicado": True,
            },
        )
        assert r_sim.status_code == 201
        dup_id = r_sim.json()["contacto"]["contacto_id"]

        r_cola = client.get("/api/contactos/maestro/duplicados")
        assert r_cola.status_code == 200
        assert r_cola.json()["total"] >= 1

        r_fus = client.post(
            f"/api/contactos/maestro/{dup_id}/fusionar",
            json={"maestro_id": cid},
        )
        assert r_fus.status_code == 200
        assert r_fus.json()["duplicado"]["estado_validacion"] == "DUPLICADO"


def test_pagina_contactos_html_contiene_maestro_y_subtabs() -> None:
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=GERENTE_USER):
        r = client.get("/contactos")
        assert r.status_code == 200
        html = r.text
        assert 'id="tab-contactos"' in html
        assert 'id="contactos-maestro-body"' in html
        assert 'id="contactos-duplicados-lista"' in html
        assert 'id="dir-contactos-body"' in html
        assert "js/contactos_maestro.js?v=20261119_8" in html


# ── Pruebas RepoContactosSQL (SQLite en memoria + Postgres CI si disponible) ──


def test_repo_contactos_sql_sqlite_y_postgres_ci() -> None:
    # 1. Prueba hermética sobre SQLite en memoria
    eng_sqlite = create_engine("sqlite:///:memory:")
    schema.contactos_maestro.create(eng_sqlite)
    repo_sql = RepoContactosSQL(eng_sqlite)
    svc = ServicioContactos(repo_sql)

    c1 = svc.crear(
        {
            "nombre": "Repuestos SQLite C.A.",
            "rol": "cliente",
            "rif": "J-30123123-0",
            "telefono": "0414-0001122",
            "latitud": 10.15,
            "longitud": -68.0,
        },
        origen="cxc",
        actor="qa@lubrikca.com",
    )
    assert c1.contacto_id is not None
    items, total, resumen = svc.listar(q="SQLite")
    assert total == 1
    assert resumen["clientes"] == 1
    assert items[0].ubicacion_dict() is not None

    # 2. Prueba en Postgres real (CI con DATABASE_URL)
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return
    eng_pg = create_engine(db_url)
    repo_pg = RepoContactosSQL(eng_pg)
    svc_pg = ServicioContactos(repo_pg)
    rif_test = "J-99900066-1"
    with eng_pg.begin() as conn:
        conn.execute(
            delete(schema.contactos_maestro).where(
                schema.contactos_maestro.c.rif_normalizado == "J999000661"
            )
        )
    try:
        creado_pg = svc_pg.crear(
            {
                "nombre": "QA Postgres Contactos Fase6 C.A.",
                "rol": "proveedor",
                "rif": rif_test,
                "telefono": "0412-9996611",
                "actualizado_en": datetime.now(),
            },
            origen="cxc",
            actor="pytest_f6",
        )
        assert creado_pg.contacto_id is not None
        leido = repo_pg.obtener(creado_pg.contacto_id)
        assert leido is not None
        assert leido.rif_normalizado == "J999000661"
    finally:
        with eng_pg.begin() as conn:
            conn.execute(
                delete(schema.contactos_maestro).where(
                    schema.contactos_maestro.c.rif_normalizado == "J999000661"
                )
            )
