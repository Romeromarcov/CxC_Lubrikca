"""Pruebas Fase 3: Ventas unificadas (CxC + Odoo), auto-envío inteligente, límites y stock."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.capturas.servicio import ServicioCapturas
from cxc.documentos.catalogo import ItemCatalogo, cargar_datos_precios_odoo
from cxc.documentos.modelos import (
    COTIZACION,
    ENVIADO_ODOO,
    PEDIDO,
    PENDIENTE_APROBACION,
)
from cxc.documentos.politica_ventas import (
    KEY_AUTOSEND_ACTIVO,
    KEY_DIAS_MORA_MAX,
    KEY_LIMITE_CREDITO_USD,
    KEY_LIMITE_FACTURAS,
    KEY_RETENER_VENCIDAS,
)
from cxc.documentos.servicio import ServicioDocumentos
from cxc.models import (
    OrdenVenta,
)
from cxc.odoo.directorio import LectorDirectorioOdoo
from cxc.odoo.escritura import EscritorOdoo
from cxc.repositories import InMemoryRepository
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from cxc.web.directorio_api import get_lector_odoo, limpiar_cache_directorio
from cxc.web.documentos_api import get_servicio_documentos
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria
from tests.test_documentos_comerciales import (
    CATALOGO_PRUEBA,
    GERENTE_USER,
    VENTAS_USER,
    CatalogoMemoria,
    RepoDocumentosMemoria,
    _armar_repo_motor_prueba,
)

client = TestClient(app)
KEY = "clave-bot-fase3"
BOT = {"X-Bot-Key": KEY}


def _armar_entorno_memoria() -> (
    tuple[InMemoryRepository, RepoDocumentosMemoria, OdooFalso, ServicioDocumentos]
):
    repo_cxc = _armar_repo_motor_prueba()
    repo_docs = RepoDocumentosMemoria()
    odoo = OdooFalso(
        res_partner__read=[
            {"id": 88, "name": "Ronald Socorro", "vat": "V-13069226", "customer_rank": 1}
        ]
    )
    svc = ServicioDocumentos(
        repo_docs, CatalogoMemoria(CATALOGO_PRUEBA), lambda: EscritorOdoo(odoo), repo_cxc=repo_cxc
    )
    return repo_cxc, repo_docs, odoo, svc


# ── Auto-envío Inteligente y Retención Configurable ───────────────────────────


def test_autosend_exitoso_cuando_cumple_todos_los_parametros() -> None:
    repo_cxc, _, odoo, svc = _armar_entorno_memoria()
    repo_cxc.set_config(KEY_AUTOSEND_ACTIVO, "1")
    repo_cxc.set_config(KEY_LIMITE_CREDITO_USD, "500.00")
    repo_cxc.set_config(KEY_LIMITE_FACTURAS, "3")

    ped = svc.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "direccion": "Guatire",
            "descuentos_codigos": ["REC_GLOBAL_TRAMO1"],
            "lineas": [{"codigo": "0761", "cantidad": 3}],  # 3 * $24 = $72 (neto + IVA ~$81)
        },
        actor="vendedor@lubrikca.com",
    )

    assert ped.estado == ENVIADO_ODOO
    assert ped.auto_enviado is True
    assert ped.motivos_retencion == ()
    assert ped.odoo_order_name == "S00700"
    assert "auto_politica" in ped.aprobado_por

    so = odoo.creados_de("sale.order")[0]
    ln = so["order_line"][0][2]
    assert ln["price_unit"] == 24.0
    assert "discount" not in ln
    chatter_so = [txt for mod, _, txt in odoo.chatter if mod == "sale.order"]
    assert chatter_so and "Auto-aprobado por política CxC" in chatter_so[0]
    assert "REC_GLOBAL_TRAMO1" in chatter_so[0]


def test_retiene_pedido_si_excede_limite_credito_usd() -> None:
    repo_cxc, _, odoo, svc = _armar_entorno_memoria()
    hoy = date.today()
    # Deuda vigente sin pagar: S00501 por $450.00
    repo_cxc.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S00501",
                cliente_id="88",
                fecha=hoy,
                fecha_entrega=hoy,
                monto_total=Decimal("450.00"),
                lista_precios="21",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                dias_credito=30,
            )
        ]
    )
    repo_cxc.set_config(KEY_AUTOSEND_ACTIVO, "1")
    repo_cxc.set_config(KEY_LIMITE_CREDITO_USD, "500.00")

    ped = svc.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "lineas": [{"codigo": "0761", "cantidad": 4}],  # $96 + IVA = $111.36 -> total $561.36
        }
    )

    assert ped.estado == PENDIENTE_APROBACION
    assert ped.auto_enviado is False
    assert odoo.creados == []
    assert any("límite de crédito" in m.lower() for m in ped.motivos_retencion)


def test_retiene_pedido_si_excede_limite_facturas_sin_pagar() -> None:
    repo_cxc, _, odoo, svc = _armar_entorno_memoria()
    hoy = date.today()
    repo_cxc.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S00502",
                cliente_id="88",
                fecha=hoy,
                fecha_entrega=hoy,
                monto_total=Decimal("50.00"),
                lista_precios="21",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                dias_credito=15,
            ),
            OrdenVenta(
                so_id="S00503",
                cliente_id="88",
                fecha=hoy,
                fecha_entrega=hoy,
                monto_total=Decimal("60.00"),
                lista_precios="21",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                dias_credito=15,
            ),
        ]
    )
    repo_cxc.set_config(KEY_AUTOSEND_ACTIVO, "1")
    repo_cxc.set_config(KEY_LIMITE_FACTURAS, "2")

    ped = svc.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        }
    )
    assert ped.estado == PENDIENTE_APROBACION
    assert ped.auto_enviado is False
    assert odoo.creados == []
    assert any("sin pagar" in m.lower() for m in ped.motivos_retencion)


def test_mora_con_dias_de_gracia_configurables() -> None:
    repo_cxc, _, odoo, svc = _armar_entorno_memoria()
    hace_10_dias = date.today() - timedelta(days=10)
    # Crédito de 5 días -> vencida hace 5 días
    repo_cxc.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S00504",
                cliente_id="88",
                fecha=hace_10_dias,
                fecha_entrega=hace_10_dias,
                monto_total=Decimal("40.00"),
                lista_precios="21",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                dias_credito=5,
            )
        ]
    )
    repo_cxc.set_config(KEY_AUTOSEND_ACTIVO, "1")
    repo_cxc.set_config(KEY_RETENER_VENCIDAS, "1")
    repo_cxc.set_config(KEY_DIAS_MORA_MAX, "0")

    ped_ret = svc.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        }
    )
    assert ped_ret.estado == PENDIENTE_APROBACION
    assert any("cartera vencida" in m.lower() for m in ped_ret.motivos_retencion)
    assert odoo.creados == []

    # Con gracia de 10 días (mora real = 5d <= 10d), sí permite auto-envío
    repo_cxc.set_config(KEY_DIAS_MORA_MAX, "10")
    ped_ok = svc.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        }
    )
    assert ped_ok.estado == ENVIADO_ODOO
    assert ped_ok.auto_enviado is True
    assert len(odoo.creados_de("sale.order")) == 1


def test_retiene_por_alerta_motor_descuento_manual_y_ficha_incompleta() -> None:
    repo_cxc, _, odoo, svc = _armar_entorno_memoria()
    repo_cxc.set_config(KEY_AUTOSEND_ACTIVO, "1")

    # 1. Alerta del motor (1ra compra a cliente con S00500) + descuento manual en línea
    ped_alerta = svc.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "descuentos_codigos": ["PRIMERA_COMPRA_COMERCIAL_2PCT"],
            "lineas": [{"codigo": "0761", "cantidad": 2, "descuento_pct": 5}],
        }
    )
    assert ped_alerta.estado == PENDIENTE_APROBACION
    assert any("descuento(s) manual(es)" in m.lower() for m in ped_alerta.motivos_retencion)
    assert any("PRIMERA_COMPRA_COMERCIAL_2PCT" in m for m in ped_alerta.motivos_retencion)

    # 2. Ficha incompleta (sin RIF)
    ped_sin_rif = svc.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        }
    )
    assert ped_sin_rif.estado == PENDIENTE_APROBACION
    assert any("ficha del cliente incompleta" in m.lower() for m in ped_sin_rif.motivos_retencion)
    assert odoo.creados == []


def test_desactivacion_global_y_override_por_cliente() -> None:
    repo_cxc, _, odoo, svc = _armar_entorno_memoria()
    repo_cxc.set_config(KEY_AUTOSEND_ACTIVO, "0")

    ped_off = svc.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        }
    )
    assert ped_off.estado == PENDIENTE_APROBACION
    assert any("desactivado" in m.lower() for m in ped_off.motivos_retencion)

    # Reactiva global y simula override de cliente con bloquear_auto_envio=True
    repo_cxc.set_config(KEY_AUTOSEND_ACTIVO, "1")
    with patch(
        "cxc.documentos.politica_ventas.obtener_override_cliente",
        return_value={
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "limite_credito_usd": 5000.0,
            "limite_facturas_pendientes": 10,
            "dias_mora_max": 30,
            "bloquear_auto_envio": True,
        },
    ):
        ped_bloq = svc.crear(
            {
                "tipo": PEDIDO,
                "partner_odoo_id": 88,
                "cliente_nombre": "Ronald Socorro",
                "rif": "V-13069226",
                "lineas": [{"codigo": "0761", "cantidad": 2}],
            }
        )
    assert ped_bloq.estado == PENDIENTE_APROBACION
    assert any("revisión manual obligatoria" in m.lower() for m in ped_bloq.motivos_retencion)
    assert odoo.creados == []


# ── Integración HTTP: Unificadas, Conversión Auto-envío y Política Config ─────


@pytest.fixture
def entorno_fase3(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[ServicioDocumentos, InMemoryRepository, OdooFalso]]:
    monkeypatch.setenv("BOT_API_KEY", KEY)
    limpiar_cache_directorio()
    odoo = OdooFalso(
        res_partner__read=[
            {"id": 88, "name": "Ronald Socorro", "vat": "V-13069226", "customer_rank": 1}
        ]
    )
    repo_cap = RepoMemoria()
    espejo = EspejoFalso(productos={"0761": ["1033"]})
    svc_cap = ServicioCapturas(repo_cap, espejo, lambda: EscritorOdoo(odoo))
    repo_docs = RepoDocumentosMemoria()
    repo_cxc = _armar_repo_motor_prueba()
    # Orden nativa directa en Odoo (no vinculada a CxC) para probar deduplicación en /unificadas
    repo_cxc.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S00599",
                cliente_id="88",
                fecha=date.today(),
                fecha_entrega=date.today(),
                monto_total=Decimal("320.00"),
                lista_precios="21",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                facturada=True,
                entregada_completa=True,
                estado_orden="sale",
                dias_credito=15,
            )
        ]
    )
    svc_docs = ServicioDocumentos(
        repo_docs,
        CatalogoMemoria(CATALOGO_PRUEBA),
        lambda: EscritorOdoo(odoo),
        repo_cxc=repo_cxc,
    )
    app.dependency_overrides[get_servicio] = lambda: svc_cap
    app.dependency_overrides[get_servicio_documentos] = lambda: svc_docs
    app.dependency_overrides[get_lector_odoo] = lambda: LectorDirectorioOdoo(odoo)
    yield svc_docs, repo_cxc, odoo
    app.dependency_overrides.pop(get_servicio, None)
    app.dependency_overrides.pop(get_servicio_documentos, None)
    app.dependency_overrides.pop(get_lector_odoo, None)
    limpiar_cache_directorio()


def test_conversion_preview_y_bot_disparan_autosend_y_retencion(
    entorno_fase3: tuple[ServicioDocumentos, InMemoryRepository, OdooFalso],
) -> None:
    svc_docs, repo_cxc, odoo = entorno_fase3
    repo_cxc.set_config(KEY_AUTOSEND_ACTIVO, "1")
    repo_cxc.set_config(KEY_LIMITE_CREDITO_USD, "1000.00")

    # Cotización con RIF -> enlace público solo lectura (403);
    # conversión por ruta autenticada/bot AUTO-ENVÍA a Odoo
    cot_ok = svc_docs.crear(
        {
            "tipo": COTIZACION,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        }
    )
    assert (
        client.post(f"/cotizacion/{cot_ok.token_preview}/convertir-pedido", json={}).status_code
        == 403
    )
    r_prev = client.post(
        f"/api/bots/documentos/{cot_ok.documento_id}/convertir-pedido",
        headers=BOT,
        json={"vendedor_email": "vendedor@lubrikca.com"},
    )
    assert r_prev.status_code == 200
    d_prev = r_prev.json()
    assert d_prev["estado"] == ENVIADO_ODOO
    assert d_prev["auto_enviado"] is True
    assert d_prev["odoo_order_name"] == "S00700"

    # Ahora bajamos el límite global a $100 y convertimos por Bot -> queda RETENIDO
    repo_cxc.set_config(KEY_LIMITE_CREDITO_USD, "100.00")
    cot_ret = svc_docs.crear(
        {
            "tipo": COTIZACION,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "lineas": [{"codigo": "0761", "cantidad": 5}],  # > $100
        }
    )
    r_bot = client.post(
        f"/api/bots/documentos/{cot_ret.documento_id}/convertir-pedido",
        headers=BOT,
        json={"vendedor_email": "vendedor@lubrikca.com"},
    )
    assert r_bot.status_code == 200
    d_bot = r_bot.json()
    assert d_bot["estado"] == PENDIENTE_APROBACION
    assert d_bot["auto_enviado"] is False
    assert any("límite de crédito" in m.lower() for m in d_bot["motivos_retencion"])
    assert len(odoo.creados_de("sale.order")) == 1


def test_ventas_unificadas_deduplica_y_filtra_por_origen(
    entorno_fase3: tuple[ServicioDocumentos, InMemoryRepository, OdooFalso],
) -> None:
    svc_docs, _, _ = entorno_fase3
    # Pedido CxC enlazado a S00500 (la misma que está en repo_cxc) -> NO debe duplicarse
    doc_enlazado = svc_docs.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        }
    )
    svc_docs.registrar_envio_captura(
        doc_enlazado.documento_id,
        partner_odoo_id=88,
        odoo_order_id=500,
        odoo_order_name="S00500",
    )

    assert client.get("/api/ventas/unificadas").status_code == 401

    with patch("cxc.web.app.get_current_user_from_cookie", return_value=VENTAS_USER):
        r_all = client.get("/api/ventas/unificadas?origen=todos")
        assert r_all.status_code == 200
        body = r_all.json()
        # S00500 (enlazada en CxC) + S00599 (nativa Odoo) = 2 filas totales, sin duplicar S00500
        nums = [x["numero"] for x in body["items"]]
        assert body["resumen"]["origen_cxc"] == 1
        assert body["resumen"]["origen_odoo"] == 1
        assert len(nums) == 2
        assert "S00599" in nums and "S00500" not in nums

        r_odoo = client.get("/api/ventas/unificadas?origen=odoo")
        assert r_odoo.status_code == 200
        assert len(r_odoo.json()["items"]) == 1
        assert r_odoo.json()["items"][0]["origen_sistema"] == "odoo"

        r_cxc = client.get("/api/ventas/unificadas?origen=cxc")
        assert r_cxc.status_code == 200
        assert len(r_cxc.json()["items"]) == 1
        assert r_cxc.json()["items"][0]["origen_sistema"] == "cxc"


def test_config_politica_ventas_permisos_y_persistencia(
    entorno_fase3: tuple[ServicioDocumentos, InMemoryRepository, OdooFalso],
) -> None:
    assert client.get("/api/config/politica-ventas").status_code == 401
    assert client.post("/api/config/politica-ventas", json={}).status_code == 401

    with patch("cxc.web.app.get_current_user_from_cookie", return_value=VENTAS_USER):
        r_get_v = client.get("/api/config/politica-ventas")
        assert r_get_v.status_code == 200 and r_get_v.json()["puede_editar"] is False
        assert (
            client.post("/api/config/politica-ventas", json={"autosend_activo": False}).status_code
            == 403
        )

    with patch("cxc.web.app.get_current_user_from_cookie", return_value=GERENTE_USER):
        r_post = client.post(
            "/api/config/politica-ventas",
            json={
                "autosend_activo": True,
                "limite_credito_usd": 2500.5,
                "limite_facturas_pendientes": 4,
                "retener_si_vencidas": False,
                "dias_mora_max": 7,
            },
        )
        assert r_post.status_code == 200
        pol = r_post.json()["politica"]
        assert pol["limite_credito_usd"] == 2500.5
        assert pol["limite_facturas_pendientes"] == 4
        assert pol["retener_si_vencidas"] is False
        assert pol["dias_mora_max"] == 7


# ── Stock en Catálogo Real ────────────────────────────────────────────────────


def test_badge_stock_catalogo_y_fallback_sin_permiso_qty() -> None:
    it_ok = ItemCatalogo("1", "0761", "LIGA", stock_disponible=Decimal("24"))
    it_bajo = ItemCatalogo("2", "0762", "LIGA 2", stock_disponible=Decimal("3"))
    it_cero = ItemCatalogo("3", "0763", "LIGA 3", stock_disponible=Decimal("0"))
    it_none = ItemCatalogo("4", "0764", "LIGA 4")

    assert (
        it_ok.a_dict()["estado_stock"] == "disponible"
        and it_ok.a_dict()["stock_disponible"] == 24.0
    )
    assert it_bajo.a_dict()["estado_stock"] == "bajo"
    assert it_cero.a_dict()["estado_stock"] == "agotado"
    assert it_none.a_dict()["estado_stock"] is None and it_none.a_dict()["stock_disponible"] is None

    def _exec_ok(model: str, method: str, args: list[Any], kw: dict[str, Any]) -> Any:
        if model == "product.pricelist":
            return [{"id": 21, "name": "Pago VES Nov 2026", "active": True}]
        if model == "product.product":
            return [{"id": 1033, "product_tmpl_id": [55, "LIGA"], "qty_available": 14.0}]
        if model == "product.template":
            return [{"id": 55, "list_price_usd": 20.0}]
        if model == "product.pricelist.item":
            return [
                {
                    "pricelist_id": [21, "Pago VES Nov 2026"],
                    "product_tmpl_id": [55, "LIGA"],
                    "fixed_price": 24.0,
                }
            ]
        return []

    datos_ok = cargar_datos_precios_odoo(_exec_ok, [], [21])
    assert datos_ok.stock["1033"] == Decimal("14.0")

    def _exec_sin_stock(model: str, method: str, args: list[Any], kw: dict[str, Any]) -> Any:
        if model == "product.product" and "qty_available" in kw.get("fields", []):
            raise RuntimeError("Access Denied qty_available")
        if model == "product.product":
            return [{"id": 1033, "product_tmpl_id": [55, "LIGA"]}]
        return _exec_ok(model, method, args, kw)

    datos_fb = cargar_datos_precios_odoo(_exec_sin_stock, [], [21])
    assert datos_fb.stock == {} and datos_fb.precios["1033"][1] == Decimal("24.0")


def test_politica_override_cliente_sql_si_hay_postgres() -> None:
    from sqlalchemy import delete

    from cxc.db import schema
    from cxc.db.engine import make_engine
    from cxc.documentos.politica_ventas import (
        eliminar_override_cliente,
        guardar_override_cliente,
        obtener_override_cliente,
    )

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("Sin DATABASE_URL para probar overrides SQL.")
    eng = make_engine(url)
    schema.metadata.create_all(eng, tables=[schema.politica_credito_cliente], checkfirst=True)
    with eng.begin() as conn:
        conn.execute(
            delete(schema.politica_credito_cliente).where(
                schema.politica_credito_cliente.c.partner_odoo_id == 998877
            )
        )
    try:
        ov = guardar_override_cliente(
            eng,
            998877,
            {
                "cliente_nombre": "Taller Override SQL",
                "limite_credito_usd": 1500,
                "limite_facturas_pendientes": 5,
                "dias_mora_max": 3,
                "bloquear_auto_envio": False,
                "notas": "Prueba Fase 3",
            },
            actor="admin@lubrikca.com",
        )
        assert ov["limite_credito_usd"] == 1500.0
        leido = obtener_override_cliente(eng, 998877)
        assert leido is not None and leido["limite_facturas_pendientes"] == 5
        assert eliminar_override_cliente(eng, 998877) is True
    finally:
        with eng.begin() as conn:
            conn.execute(
                delete(schema.politica_credito_cliente).where(
                    schema.politica_credito_cliente.c.partner_odoo_id == 998877
                )
            )
