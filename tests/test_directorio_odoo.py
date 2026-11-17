"""Pruebas del directorio de contactos en Odoo y plan de cuentas / clasificación de gastos."""

from __future__ import annotations

import xmlrpc.client
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc import permisos as acc
from cxc.odoo.directorio import (
    LectorDirectorioOdoo,
    OdooDirectorioError,
    clasificar_contacto,
    es_subdireccion_entrega,
    filtrar_contactos,
    filtrar_plan_cuentas,
    mapear_contacto,
    mapear_cuenta,
    proponer_clasificacion_gastos,
    resumir_contactos,
    resumir_plan_cuentas,
    sugerir_para_concepto,
)
from cxc.web.app import app
from cxc.web.directorio_api import get_lector_odoo, limpiar_cache_directorio

client = TestClient(app)

TESORERIA = {
    "email": "teso@lubrikca.test",
    "rol": "tesoreria",
    "permisos": acc.efectivos("tesoreria"),
}
SIN_ACCESO = {"email": "x@lubrikca.test", "rol": "invitado", "permisos": ["dashboard"]}


PARTNERS_FALSOS: list[dict[str, Any]] = [
    {
        "id": 10,
        "name": "Taller El Rayo C.A.",
        "vat": "J-40111222-3",
        "email": "contacto@rayo.ve",
        "phone": "0414-1234567",
        "mobile": False,
        "street": "Av. Bolívar",
        "city": "Valencia",
        "is_company": True,
        "company_type": "company",
        "customer_rank": 0,
        "supplier_rank": 0,
        "parent_id": False,
        "type": "contact",
        "ref": "BOT-42",
        "comment": "[Borrador Telegram] pendiente de completar",
        "user_id": [5, "Marco Vendedor"],
    },
    {
        "id": 20,
        "name": "Lubricantes del Centro C.A.",
        "vat": "J301234567",
        "email": "compras@lubcentro.ve",
        "phone": False,
        "mobile": "0412-9998877",
        "street": "Zona Industrial",
        "city": "Maracay",
        "is_company": True,
        "company_type": "company",
        "customer_rank": 3,
        "supplier_rank": 0,
        "parent_id": False,
        "type": "contact",
        "ref": "",
        "comment": False,
        "user_id": [5, "Marco Vendedor"],
    },
    {
        "id": 30,
        "name": "Importadora de Bases y Aditivos S.A.",
        "vat": "J-00998877-1",
        "email": "ventas@importadora.ve",
        "phone": "0212-5551122",
        "mobile": False,
        "street": "Calle 3",
        "city": "Caracas",
        "is_company": True,
        "company_type": "company",
        "customer_rank": 0,
        "supplier_rank": 2,
        "parent_id": False,
        "type": "contact",
        "ref": "PROV-01",
        "comment": "Proveedor principal",
        "user_id": False,
    },
    {
        "id": 40,
        "name": "Transporte y Suministros Global C.A.",
        "vat": "J-50001111-9",
        "email": "info@transglobal.ve",
        "phone": "0424-1112233",
        "mobile": False,
        "street": "Autopista Regional",
        "city": "Guacara",
        "is_company": True,
        "company_type": "company",
        "customer_rank": 1,
        "supplier_rank": 1,
        "parent_id": False,
        "type": "contact",
        "ref": "",
        "comment": "",
        "user_id": [6, "Ana Comercial"],
    },
    {
        "id": 21,
        "name": "Galpón Entrega Lubricantes del Centro",
        "vat": False,
        "email": False,
        "phone": False,
        "mobile": False,
        "street": "Galpón 4",
        "city": "Maracay",
        "is_company": False,
        "company_type": "person",
        "customer_rank": 0,
        "supplier_rank": 0,
        "parent_id": [20, "Lubricantes del Centro C.A."],
        "type": "delivery",
        "ref": False,
        "comment": False,
        "user_id": False,
    },
]

CUENTAS_FALSAS: list[dict[str, Any]] = [
    {
        "id": 101,
        "code": "110201",
        "name": "Inventario de Mercancías y Lubricantes",
        "account_type": "asset_current",
        "reconcile": False,
    },
    {
        "id": 201,
        "code": "210101",
        "name": "Cuentas por Pagar Proveedores Nacionales",
        "account_type": "liability_payable",
        "reconcile": True,
    },
    {
        "id": 501,
        "code": "510101",
        "name": "Costo de Venta y Compras de Mercancía",
        "account_type": "expense_direct_cost",
        "reconcile": False,
    },
    {
        "id": 601,
        "code": "610201",
        "name": "Atención al Personal, Refrigerios y Alimentación",
        "account_type": "expense",
        "reconcile": False,
    },
    {
        "id": 602,
        "code": "610301",
        "name": "Combustible y Lubricantes de Flota",
        "account_type": "expense",
        "reconcile": False,
    },
    {
        "id": 603,
        "code": "610401",
        "name": "Papelería y Útiles de Oficina",
        "account_type": "expense",
        "reconcile": False,
    },
    {
        "id": 604,
        "code": "610501",
        "name": "Mantenimiento de Galpón, Ferretería y Limpieza",
        "account_type": "expense",
        "reconcile": False,
    },
    {
        "id": 605,
        "code": "610601",
        "name": "Transporte, Fletes, Peajes y Traslados",
        "account_type": "expense",
        "reconcile": False,
    },
    {
        "id": 606,
        "code": "610701",
        "name": "Servicios Públicos, Electricidad, Internet y Honorarios",
        "account_type": "expense",
        "reconcile": False,
    },
    {
        "id": 607,
        "code": "610999",
        "name": "Otros Gastos Generales, Eventos e Impuestos Municipales",
        "account_type": "expense",
        "reconcile": False,
    },
]


def _execute_falso(
    model: str, method: str, args: list[Any], kwargs: dict[str, Any]
) -> Any:
    if method == "fields_get":
        if model == "res.partner":
            return {k: {"string": k} for k in (*PARTNERS_FALSOS[0].keys(), "active")}
        if model == "account.account":
            return {k: {"string": k} for k in (*CUENTAS_FALSAS[0].keys(), "deprecated", "active")}
        return {}
    if method == "search_read":
        if model == "res.partner":
            return list(PARTNERS_FALSOS)
        if model == "account.account":
            return list(CUENTAS_FALSAS)
    return []


# ── Pruebas unitarias de contactos ───────────────────────────────────────────


def test_clasificar_contacto_en_cuatro_categorias() -> None:
    assert clasificar_contacto(0, 0) == "prospecto"
    assert clasificar_contacto(2, 0) == "cliente"
    assert clasificar_contacto(0, 1) == "proveedor"
    assert clasificar_contacto(1, 3) == "ambos"


def test_lector_descarta_subdirecciones_y_resume_contactos() -> None:
    lector = LectorDirectorioOdoo(_execute_falso)
    contactos = lector.leer_contactos()
    assert len(contactos) == 4  # El id 21 (type=delivery con parent_id) se excluye
    assert es_subdireccion_entrega(PARTNERS_FALSOS[4])

    resumen = resumir_contactos(contactos)
    assert resumen == {
        "total": 4,
        "prospectos": 1,
        "clientes": 1,
        "proveedores": 1,
        "ambos": 1,
        "clientes_total": 2,
        "proveedores_total": 2,
        "borradores_telegram": 1,
    }


def test_filtrar_contactos_por_clasificacion_y_busqueda() -> None:
    contactos = [mapear_contacto(r) for r in PARTNERS_FALSOS[:4]]
    assert [c.id for c in filtrar_contactos(contactos, clasificacion="prospecto")] == [10]
    assert [c.id for c in filtrar_contactos(contactos, clasificacion="cliente")] == [20, 40]
    assert [c.id for c in filtrar_contactos(contactos, clasificacion="proveedor")] == [30, 40]
    assert [c.id for c in filtrar_contactos(contactos, clasificacion="ambos")] == [40]
    assert [c.id for c in filtrar_contactos(contactos, clasificacion="borrador_telegram")] == [10]
    assert [c.id for c in filtrar_contactos(contactos, q="maracay")] == [20]
    assert [c.id for c in filtrar_contactos(contactos, q="J-00998877-1")] == [30]


# ── Pruebas unitarias de plan de cuentas y clasificación de gastos ───────────


def test_proponer_clasificacion_gastos_asigna_cuentas_correctas() -> None:
    cuentas = [mapear_cuenta(r) for r in CUENTAS_FALSAS]
    resumen = resumir_plan_cuentas(cuentas)
    assert resumen["total"] == 10
    assert resumen["gastos"] == 7
    assert resumen["costo_directo"] == 1
    assert resumen["pasivo"] == 1
    assert resumen["activo"] == 1

    propuestas = {p["categoria"]: p for p in proponer_clasificacion_gastos(cuentas)}
    assert set(propuestas) == {
        "alimentación",
        "combustible",
        "papelería",
        "mantenimiento",
        "transporte",
        "servicios",
        "otros",
        "factura_proveedor",
    }
    assert propuestas["alimentación"]["cuenta_sugerida"]["code"] == "610201"
    assert propuestas["combustible"]["cuenta_sugerida"]["code"] == "610301"
    assert propuestas["papelería"]["cuenta_sugerida"]["code"] == "610401"
    assert propuestas["mantenimiento"]["cuenta_sugerida"]["code"] == "610501"
    assert propuestas["transporte"]["cuenta_sugerida"]["code"] == "610601"
    assert propuestas["servicios"]["cuenta_sugerida"]["code"] == "610701"
    assert propuestas["factura_proveedor"]["cuenta_sugerida"]["code"] == "510101"
    assert propuestas["factura_proveedor"]["cuenta_cxp_sugerida"]["code"] == "210101"


def test_sugerir_para_concepto_clasifica_ejemplos_reales_lubrikca() -> None:
    cuentas = [mapear_cuenta(r) for r in CUENTAS_FALSAS]

    s_diesel = sugerir_para_concepto("Recibo de gasoil diesel para camión", cuentas)
    assert s_diesel["categoria_sugerida"] == "combustible"
    assert s_diesel["cuenta_sugerida"]["code"] == "610301"

    s_refri = sugerir_para_concepto("Refrigerios reunión gerencial y café", cuentas)
    assert s_refri["categoria_sugerida"] == "alimentación"
    assert s_refri["cuenta_sugerida"]["code"] == "610201"

    s_serv = sugerir_para_concepto("Pago de electricidad y aseo urbano", cuentas)
    assert s_serv["categoria_sugerida"] == "servicios"
    assert s_serv["cuenta_sugerida"]["code"] == "610701"

    s_compra = sugerir_para_concepto("Compra de inventario de lubricantes", cuentas)
    assert s_compra["categoria_sugerida"] == "factura_proveedor"
    assert s_compra["kind_bot"] == "factura_proveedor"
    assert s_compra["cuenta_sugerida"]["code"] in ("510101", "110201")


def test_filtrar_plan_cuentas() -> None:
    cuentas = [mapear_cuenta(r) for r in CUENTAS_FALSAS]
    solo_pasivo = filtrar_plan_cuentas(cuentas, grupo="pasivo")
    assert len(solo_pasivo) == 1 and solo_pasivo[0].code == "210101"

    por_codigo = filtrar_plan_cuentas(cuentas, q="6103")
    assert len(por_codigo) == 1 and por_codigo[0].code == "610301"


def test_lector_maneja_errores_xmlrpc() -> None:
    def _falla_fault(m: str, fn: str, a: list[Any], kw: dict[str, Any]) -> Any:
        raise xmlrpc.client.Fault(1, "Access Denied")

    lector = LectorDirectorioOdoo(_falla_fault)
    with pytest.raises(OdooDirectorioError, match="Odoo rechazó la lectura"):
        lector.leer_contactos()


# ── Pruebas de los endpoints FastAPI ─────────────────────────────────────────


@pytest.fixture(autouse=True)
def _limpiar_cache() -> Any:
    limpiar_cache_directorio()
    app.dependency_overrides[get_lector_odoo] = lambda: LectorDirectorioOdoo(_execute_falso)
    yield
    app.dependency_overrides.pop(get_lector_odoo, None)
    limpiar_cache_directorio()


def test_endpoints_exigen_sesion_y_permiso() -> None:
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=None):
        assert client.get("/api/odoo/contactos").status_code == 401
        assert client.get("/api/odoo/plan-cuentas").status_code == 401

    with patch("cxc.web.app.get_current_user_from_cookie", return_value=SIN_ACCESO):
        assert client.get("/api/odoo/contactos").status_code == 403
        assert client.get("/api/odoo/plan-cuentas").status_code == 403


def test_api_listar_contactos_odoo() -> None:
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=TESORERIA):
        r = client.get("/api/odoo/contactos")
        assert r.status_code == 200
        data = r.json()
        assert data["resumen"]["total"] == 4
        assert data["resumen"]["prospectos"] == 1
        assert data["resumen"]["clientes_total"] == 2
        assert data["resumen"]["proveedores_total"] == 2

        r_prosp = client.get("/api/odoo/contactos?clasificacion=prospecto")
        assert r_prosp.status_code == 200
        items = r_prosp.json()["items"]
        assert len(items) == 1
        assert items[0]["nombre"] == "Taller El Rayo C.A."
        assert items[0]["es_borrador_telegram"] is True

        r_invalido = client.get("/api/odoo/contactos?clasificacion=inexistente")
        assert r_invalido.status_code == 422


def test_api_plan_cuentas_y_simulador_concepto() -> None:
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=TESORERIA):
        r = client.get("/api/odoo/plan-cuentas?concepto=Ferreteria+brochas+galpon")
        assert r.status_code == 200
        data = r.json()
        assert data["resumen"]["total"] == 10
        assert len(data["propuestas"]) == 8
        sug = data["sugerencia_concepto"]
        assert sug is not None
        assert sug["categoria_sugerida"] == "mantenimiento"
        assert sug["cuenta_sugerida"]["code"] == "610501"


def test_index_html_incluye_secciones_contactos_y_plan_cuentas() -> None:
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=TESORERIA):
        r = client.get("/capturas")
        assert r.status_code == 200
        html = r.text
        assert 'data-capturas-subpage="contactos"' in html
        assert 'data-capturas-subpage="cuentas"' in html
        assert 'id="dir-contactos-body"' in html
        assert 'id="cta-propuestas-body"' in html
        assert 'id="cta-catalogo-body"' in html


def test_api_refresh_y_errores_502_503(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=TESORERIA):
        r1 = client.get("/api/odoo/contactos?refresh=true&q=Valencia")
        assert r1.status_code == 200
        assert r1.json()["total_filtrado"] == 1

        r2 = client.get("/api/odoo/plan-cuentas?refresh=true&grupo=gastos&account_type=expense")
        assert r2.status_code == 200
        assert r2.json()["total_filtrado"] == 7

    def _falla(m: str, fn: str, a: list[Any], kw: dict[str, Any]) -> Any:
        raise TimeoutError("timeout")

    app.dependency_overrides[get_lector_odoo] = lambda: LectorDirectorioOdoo(_falla)
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=TESORERIA):
        assert client.get("/api/odoo/contactos?refresh=true").status_code == 502
        assert client.get("/api/odoo/plan-cuentas?refresh=true").status_code == 502

    monkeypatch.delenv("ODOO_URL", raising=False)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        get_lector_odoo()
    assert exc.value.status_code == 503

