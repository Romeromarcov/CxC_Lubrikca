"""Pruebas de Documentos Comerciales, Catálogo Real de Odoo y edición de contactos."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.capturas.modelos import CapturaInvalida, validar
from cxc.capturas.servicio import ServicioCapturas
from cxc.documentos.catalogo import ItemCatalogo, buscar_productos, resolver_producto_unico
from cxc.documentos.modelos import (
    BORRADOR,
    COTIZACION,
    ENVIADO_ODOO,
    PEDIDO,
    DocumentoComercial,
    DocumentoInvalido,
    LineaDocumento,
)
from cxc.documentos.servicio import DocumentoYaEnviado, ServicioDocumentos
from cxc.odoo.directorio import LectorDirectorioOdoo, mapear_contacto
from cxc.odoo.escritura import EscritorOdoo
from cxc.permisos import efectivos
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from cxc.web.directorio_api import get_lector_odoo, limpiar_cache_directorio
from cxc.web.documentos_api import get_servicio_documentos
from tests.capturas_dobles import UBICACION, EspejoFalso, OdooFalso, RepoMemoria

client = TestClient(app)
KEY = "clave-bot-docs"
BOT = {"X-Bot-Key": KEY}
VENTAS_USER = {"email": "vendedor@lubrikca.com", "rol": "ventas", "permisos": efectivos("ventas")}

CATALOGO_PRUEBA = [
    ItemCatalogo(
        "1033",
        "0761",
        "LIGA PARA FRENOS DOT3 (1x12)",
        "Global Oil",
        "1X12",
        Decimal("3.5"),
        Decimal("18.00"),
        Decimal("24.00"),
    ),
    ItemCatalogo(
        "1034",
        "0762",
        "LIGA PARA FRENOS DOT3 (1x6)",
        "Global Oil",
        "1X6",
        Decimal("1.75"),
        Decimal("9.50"),
        Decimal("13.00"),
    ),
    ItemCatalogo(
        "1077",
        "0997",
        "LIGA PARA FRENOS DOT4 (Tambor)",
        "Global Oil",
        "TAMBOR",
        Decimal("208.0"),
        Decimal("480.00"),
        Decimal("650.00"),
    ),
    ItemCatalogo(
        "1200",
        "0801",
        "ELITE API SP SAE 20W-50 (1x12)",
        "Global Oil",
        "1X12",
        Decimal("11.35"),
        Decimal("42.00"),
        Decimal("58.00"),
    ),
]


class CatalogoMemoria:
    def __init__(self, items: Sequence[ItemCatalogo]) -> None:
        self.items = list(items)

    def listar(self, *, refresh: bool = False) -> list[ItemCatalogo]:
        return list(self.items)


class RepoDocumentosMemoria:
    def __init__(self) -> None:
        self.docs: dict[int, DocumentoComercial] = {}
        self._sig = 1
        self._sig_ln = 1

    def _construir_lineas(self, lineas: Sequence[dict[str, Any]]) -> tuple[LineaDocumento, ...]:
        out: list[LineaDocumento] = []
        for idx, ln in enumerate(lineas, start=1):
            out.append(
                LineaDocumento(
                    linea_id=self._sig_ln,
                    orden=int(ln.get("orden") or idx),
                    producto_id=str(ln.get("producto_id") or ""),
                    codigo=str(ln.get("codigo") or ""),
                    descripcion=str(ln["descripcion"]),
                    presentacion=str(ln.get("presentacion") or ""),
                    litros_unidad=Decimal(str(ln.get("litros_unidad") or "0")),
                    cantidad=Decimal(str(ln["cantidad"])),
                    precio_unitario=Decimal(str(ln.get("precio_unitario") or "0")),
                    descuento_pct=Decimal(str(ln.get("descuento_pct") or "0")),
                    subtotal=Decimal(str(ln.get("subtotal") or "0")),
                )
            )
            self._sig_ln += 1
        return tuple(out)

    def crear(
        self, cabecera: dict[str, Any], lineas: Sequence[dict[str, Any]]
    ) -> DocumentoComercial:
        did = self._sig
        self._sig += 1
        ahora = datetime.now()
        tipo = str(cabecera["tipo"])
        pref = "COT" if tipo == COTIZACION else "PED"
        doc = DocumentoComercial(
            documento_id=did,
            numero=f"{pref}-{did:05d}",
            tipo=tipo,
            estado=str(cabecera.get("estado") or BORRADOR),
            origen=str(cabecera.get("origen") or "web"),
            captura_id=cabecera.get("captura_id"),
            partner_odoo_id=cabecera.get("partner_odoo_id"),
            tipo_contacto=str(cabecera.get("tipo_contacto") or "prospecto"),
            cliente_nombre=str(cabecera["cliente_nombre"]),
            rif=str(cabecera.get("rif") or ""),
            email=str(cabecera.get("email") or ""),
            telefono=str(cabecera.get("telefono") or ""),
            direccion=str(cabecera.get("direccion") or ""),
            plus_code=str(cabecera.get("plus_code") or ""),
            latitud=cabecera.get("latitud"),
            longitud=cabecera.get("longitud"),
            es_primera_compra=bool(cabecera.get("es_primera_compra", True)),
            moneda_lista=str(cabecera.get("moneda_lista") or "USD"),
            vendedor_email=str(cabecera.get("vendedor_email") or ""),
            fecha_entrega=cabecera.get("fecha_entrega"),
            condiciones_negociacion=str(cabecera.get("condiciones_negociacion") or ""),
            observaciones=str(cabecera.get("observaciones") or ""),
            subtotal=Decimal(str(cabecera.get("subtotal") or "0")),
            total_litros=Decimal(str(cabecera.get("total_litros") or "0")),
            odoo_order_id=cabecera.get("odoo_order_id"),
            odoo_order_name=str(cabecera.get("odoo_order_name") or ""),
            error_odoo=str(cabecera.get("error_odoo") or ""),
            creado_por=str(cabecera.get("creado_por") or "sistema"),
            creado_en=ahora,
            actualizado_en=ahora,
            lineas=self._construir_lineas(lineas),
        )
        self.docs[did] = doc
        return doc

    def obtener(self, documento_id: int) -> DocumentoComercial | None:
        return self.docs.get(documento_id)

    def obtener_por_captura(self, captura_id: int) -> DocumentoComercial | None:
        for d in sorted(self.docs.values(), key=lambda x: x.documento_id, reverse=True):
            if d.captura_id == captura_id:
                return d
        return None

    def listar(
        self,
        *,
        tipo: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentoComercial], int, dict[str, int]]:
        vals = sorted(self.docs.values(), key=lambda x: x.documento_id, reverse=True)
        resumen = {
            "total": len(vals),
            "cotizaciones_borrador": sum(
                1 for d in vals if d.tipo == "cotizacion" and d.estado == "BORRADOR"
            ),
            "pedidos_borrador": sum(
                1 for d in vals if d.tipo == "pedido" and d.estado == "BORRADOR"
            ),
            "enviados_odoo": sum(1 for d in vals if d.estado == "ENVIADO_ODOO"),
            "con_error": sum(1 for d in vals if d.estado == "ERROR"),
        }
        filtrados = [
            d
            for d in vals
            if (not tipo or d.tipo == tipo)
            and (not estado or d.estado == estado)
            and (
                not q
                or q.lower() in f"{d.numero} {d.cliente_nombre} {d.rif} {d.odoo_order_name}".lower()
            )
        ]
        return filtrados[offset : offset + limit], len(filtrados), resumen

    def actualizar(
        self,
        documento_id: int,
        cabecera: dict[str, Any],
        lineas: Sequence[dict[str, Any]] | None = None,
    ) -> DocumentoComercial:
        actual = self.docs[documento_id]
        tipo = str(cabecera.get("tipo", actual.tipo))
        pref = "COT" if tipo == COTIZACION else "PED"
        nuevo = DocumentoComercial(
            documento_id=documento_id,
            numero=f"{pref}-{documento_id:05d}",
            tipo=tipo,
            estado=str(cabecera.get("estado", actual.estado)),
            origen=str(cabecera.get("origen", actual.origen)),
            captura_id=cabecera.get("captura_id", actual.captura_id),
            partner_odoo_id=cabecera.get("partner_odoo_id", actual.partner_odoo_id),
            tipo_contacto=str(cabecera.get("tipo_contacto", actual.tipo_contacto)),
            cliente_nombre=str(cabecera.get("cliente_nombre", actual.cliente_nombre)),
            rif=str(cabecera.get("rif", actual.rif)),
            email=str(cabecera.get("email", actual.email)),
            telefono=str(cabecera.get("telefono", actual.telefono)),
            direccion=str(cabecera.get("direccion", actual.direccion)),
            plus_code=str(cabecera.get("plus_code", actual.plus_code)),
            latitud=cabecera.get("latitud", actual.latitud),
            longitud=cabecera.get("longitud", actual.longitud),
            es_primera_compra=bool(cabecera.get("es_primera_compra", actual.es_primera_compra)),
            moneda_lista=str(cabecera.get("moneda_lista", actual.moneda_lista)),
            vendedor_email=str(cabecera.get("vendedor_email", actual.vendedor_email)),
            fecha_entrega=cabecera.get("fecha_entrega", actual.fecha_entrega),
            condiciones_negociacion=str(
                cabecera.get("condiciones_negociacion", actual.condiciones_negociacion)
            ),
            observaciones=str(cabecera.get("observaciones", actual.observaciones)),
            subtotal=Decimal(str(cabecera.get("subtotal", actual.subtotal))),
            total_litros=Decimal(str(cabecera.get("total_litros", actual.total_litros))),
            odoo_order_id=cabecera.get("odoo_order_id", actual.odoo_order_id),
            odoo_order_name=str(cabecera.get("odoo_order_name", actual.odoo_order_name)),
            error_odoo=str(cabecera.get("error_odoo", actual.error_odoo)),
            creado_por=actual.creado_por,
            creado_en=actual.creado_en,
            actualizado_en=datetime.now(),
            lineas=self._construir_lineas(lineas) if lineas is not None else actual.lineas,
        )
        self.docs[documento_id] = nuevo
        return nuevo


# ── Búsqueda en catálogo real ────────────────────────────────────────────────


def test_busqueda_catalogo_distingue_ambiguo_de_unico() -> None:
    unico_amb, cands = resolver_producto_unico(CATALOGO_PRUEBA, texto="liga para frenos")
    assert unico_amb is None and len(cands) == 3

    unico_ok, _ = resolver_producto_unico(CATALOGO_PRUEBA, texto="liga dot4 tambor")
    assert unico_ok is not None and unico_ok.producto_id == "1077"

    por_cod, _ = resolver_producto_unico(CATALOGO_PRUEBA, texto="0761")
    assert por_cod is not None and por_cod.producto_id == "1033"

    res = buscar_productos(CATALOGO_PRUEBA, "20w-50")
    assert len(res) == 1 and res[0].codigo == "0801"


def test_gps_cero_cero_se_rechaza_como_ubicacion_invalida() -> None:
    with pytest.raises(CapturaInvalida, match="0, 0"):
        validar(
            "contacto",
            {
                "nombre": "Ronald",
                "telefono": "04123720251",
                "ubicacion": {"latitud": 0.0, "longitud": 0.0},
            },
        )


def test_directorio_ignora_gps_cero_cero_pero_conserva_coordenadas_reales() -> None:
    c_vacio = mapear_contacto(
        {"id": 10, "name": "Prospecto", "partner_latitude": 0.0, "partner_longitude": 0.0}
    )
    assert c_vacio.latitud is None and c_vacio.longitud is None
    c_real = mapear_contacto(
        {
            "id": 11,
            "name": "Taller Guatire",
            "street2": "772J62VW+M4",
            "partner_latitude": 10.4712,
            "partner_longitude": -66.5411,
        }
    )
    assert c_real.plus_code == "772J62VW+M4" and c_real.latitud == 10.4712


# ── Servicio de Documentos Comerciales ───────────────────────────────────────


def test_cotizacion_a_prospecto_calcula_precios_y_se_envia_a_odoo_desde_cxc() -> None:
    odoo = OdooFalso()
    repo = RepoDocumentosMemoria()
    svc = ServicioDocumentos(repo, CatalogoMemoria(CATALOGO_PRUEBA), lambda: EscritorOdoo(odoo))

    cot = svc.crear(
        {
            "tipo": COTIZACION,
            "cliente_nombre": "Prospecto Repuestos C.A.",
            "telefono": "04141234567",
            "moneda_lista": "VES",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
            "observaciones": "Solicitó cotización inicial",
        }
    )
    assert cot.numero == "COT-00001" and cot.estado == BORRADOR
    assert cot.subtotal == Decimal("48.00") and cot.total_litros == Decimal("7.00")
    assert odoo.creados == []  # La cotización no se manda sola a Odoo hasta pulsar Enviar

    enviado = svc.enviar_a_odoo(cot.documento_id, actor="gerencia@lubrikca.com")
    assert enviado.estado == ENVIADO_ODOO and enviado.odoo_order_name == "S00700"
    assert odoo.creados_de("res.partner")[0]["customer_rank"] == 0  # sigue como prospecto
    assert odoo.creados_de("sale.order")[0]["order_line"][0][2]["product_id"] == 1033

    with pytest.raises(DocumentoYaEnviado):
        svc.editar(cot.documento_id, {"observaciones": "cambio tardío"})


def test_pedido_promueve_prospecto_existente_o_exige_ficha_si_es_nuevo() -> None:
    odoo = OdooFalso(
        res_partner__read=[
            {"id": 88, "name": "Ronald Socorro", "vat": "V-13069226", "customer_rank": 0}
        ]
    )
    repo = RepoDocumentosMemoria()
    svc = ServicioDocumentos(repo, CatalogoMemoria(CATALOGO_PRUEBA), lambda: EscritorOdoo(odoo))

    # Cliente nuevo sin RIF/GPS: guarda borrador en CxC pero no deja enviar a Odoo sin completar
    inc = svc.crear(
        {
            "tipo": PEDIDO,
            "cliente_nombre": "Cliente Sin Datos",
            "lineas": [{"producto_id": "1077", "cantidad": 1, "descuento_pct": 5}],
        }
    )
    assert inc.subtotal == Decimal("456.00")
    with pytest.raises(DocumentoInvalido) as exc:
        svc.enviar_a_odoo(inc.documento_id)
    assert set(exc.value.faltan) >= {"rif", "email", "direccion", "ubicacion"}

    # Prospecto existente (id=88): al enviar pedido se actualiza y promueve a customer_rank=1
    ped = svc.crear(
        {
            "tipo": PEDIDO,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "direccion": "Guatire",
            "ubicacion": UBICACION,
            "condiciones_negociacion": "Contado USD, 5% descuento",
            "lineas": [{"producto_id": "1077", "cantidad": 1, "descuento_pct": 5}],
        }
    )
    env = svc.enviar_a_odoo(ped.documento_id)
    assert env.estado == ENVIADO_ODOO and env.partner_odoo_id == 88
    assert odoo.escritos[0][2]["customer_rank"] == 1
    linea_odoo = odoo.creados_de("sale.order")[0]["order_line"][0][2]
    assert linea_odoo["product_id"] == 1077 and linea_odoo["discount"] == 5.0


# ── Integración API Bot + Web CxC ────────────────────────────────────────────


@pytest.fixture
def entorno_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[ServicioDocumentos, RepoDocumentosMemoria, OdooFalso]]:
    monkeypatch.setenv("BOT_API_KEY", KEY)
    limpiar_cache_directorio()
    odoo = OdooFalso(
        res_partner__search_read=[
            {
                "id": 88,
                "name": "Ronald Socorro",
                "vat": "V-13069226",
                "email": "ronald@test.com",
                "phone": "04123720251",
                "street": "Guatire",
                "street2": "772J62VW+M4",
                "partner_latitude": 10.4712,
                "partner_longitude": -66.5411,
                "customer_rank": 0,
                "supplier_rank": 0,
                "parent_id": False,
                "type": "contact",
                "ref": "BOT-1",
                "comment": "[Borrador Telegram]",
            }
        ]
    )
    repo_cap = RepoMemoria()
    espejo = EspejoFalso(productos={"0761": ["1033"], "LIGA PARA FRENOS DOT3 (1x12)": ["1033"]})
    svc_cap = ServicioCapturas(repo_cap, espejo, lambda: EscritorOdoo(odoo))
    repo_docs = RepoDocumentosMemoria()
    svc_docs = ServicioDocumentos(
        repo_docs, CatalogoMemoria(CATALOGO_PRUEBA), lambda: EscritorOdoo(odoo)
    )
    app.dependency_overrides[get_servicio] = lambda: svc_cap
    app.dependency_overrides[get_servicio_documentos] = lambda: svc_docs
    app.dependency_overrides[get_lector_odoo] = lambda: LectorDirectorioOdoo(odoo)
    yield svc_docs, repo_docs, odoo
    app.dependency_overrides.pop(get_servicio, None)
    app.dependency_overrides.pop(get_servicio_documentos, None)
    app.dependency_overrides.pop(get_lector_odoo, None)
    limpiar_cache_directorio()


def test_endpoints_bot_catalogo_contactos_edicion_y_flujo_cotizacion_pedido(
    entorno_docs: tuple[ServicioDocumentos, RepoDocumentosMemoria, OdooFalso],
) -> None:
    _, repo_docs, odoo = entorno_docs

    # 1. Bot consulta catálogo real (ambiguo vs único)
    r_cat = client.get("/api/bots/catalogo?q=liga+para+frenos", headers=BOT)
    assert r_cat.status_code == 200 and r_cat.json()["unico"] is None
    assert len(r_cat.json()["items"]) == 3

    r_unico = client.get("/api/bots/catalogo?q=0761", headers=BOT)
    assert r_unico.status_code == 200 and r_unico.json()["unico"]["producto_id"] == "1033"

    # 2. Bot consulta contactos en Odoo (detecta prospecto Ronald Socorro)
    r_cont = client.get("/api/bots/contactos?q=Ronald", headers=BOT)
    assert r_cont.status_code == 200
    items_c = r_cont.json()["items"]
    assert len(items_c) == 1 and items_c[0]["clasificacion"] == "prospecto"

    # 3. Bot edita un contacto existente en Odoo
    r_ed = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "contacto_editar",
            "draft": {
                "partner_odoo_id": 88,
                "telefono": "04149998877",
                "direccion": "Guatire Centro",
            },
            "source": {"vendedor_email": "vendedor@lubrikca.com"},
            "idempotency_key": "ed-88",
        },
    )
    assert r_ed.status_code == 201 and r_ed.json()["estado"] == "ENVIADO"
    assert any(w[1] == [88] and w[2]["phone"] == "04149998877" for w in odoo.escritos)

    # 4. Bot envía Cotización -> queda como BORRADOR en CxC (COT-00001) sin escribir sale.order aún
    r_cot = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "cotizacion",
            "draft": {
                "cliente": "Ronald Socorro",
                "partner_odoo_id": 88,
                "lineas": [
                    {
                        "producto_id": "1033",
                        "producto": "LIGA PARA FRENOS DOT3 (1x12)",
                        "cantidad": "4",
                    }
                ],
            },
            "source": {"vendedor_email": "vendedor@lubrikca.com"},
            "idempotency_key": "cot-1",
        },
    )
    assert r_cot.status_code == 201
    assert r_cot.json()["resultado"]["documento_numero"] == "COT-00001"
    assert len(odoo.creados_de("sale.order")) == 0

    # 5. Desde Ventas en CxC el usuario revisa la cotización y pulsa Enviar a Odoo
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=VENTAS_USER):
        r_list = client.get("/api/documentos")
        assert r_list.status_code == 200 and r_list.json()["total"] == 1
        r_push = client.post("/api/documentos/1/enviar-odoo")
        assert r_push.status_code == 200 and r_push.json()["estado"] == "ENVIADO_ODOO"
    assert len(odoo.creados_de("sale.order")) == 1

    # 6. Bot confirma Pedido -> se envía automático a Odoo Y crea PED-00002 en CxC enlazado a S00700
    r_ped = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "pedido",
            "draft": {
                "cliente": "Ronald Socorro",
                "partner_odoo_id": 88,
                "es_primera_compra": False,
                "condiciones_negociacion": "Contado BCV sin descuento",
                "lineas": [
                    {
                        "producto_id": "1033",
                        "producto": "LIGA PARA FRENOS DOT3 (1x12)",
                        "cantidad": "2",
                    }
                ],
            },
            "source": {"vendedor_email": "vendedor@lubrikca.com"},
            "idempotency_key": "ped-1",
        },
    )
    assert r_ped.status_code == 201 and r_ped.json()["estado"] == "ENVIADO"
    assert r_ped.json()["resultado"]["documento_numero"] == "PED-00002"
    assert r_ped.json()["resultado"]["odoo_name"] == "S00700"
    assert (
        repo_docs.docs[2].estado == ENVIADO_ODOO and repo_docs.docs[2].odoo_order_name == "S00700"
    )


def test_repositorio_sql_documentos_comerciales_en_postgres() -> None:
    import os

    from sqlalchemy import delete, insert, text

    from cxc.db import schema
    from cxc.db.engine import make_engine
    from cxc.documentos.catalogo import CatalogoSQLOdoo
    from cxc.documentos.repositorio import RepoDocumentosSQL

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("Sin DATABASE_URL para probar RepoDocumentosSQL.")
    eng = make_engine(url)
    schema.metadata.create_all(
        eng,
        tables=[
            schema.catalogo,
            schema.documentos_comerciales,
            schema.documentos_comerciales_lineas,
        ],
        checkfirst=True,
    )
    with eng.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE documentos_comerciales_lineas, documentos_comerciales "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(delete(schema.catalogo).where(schema.catalogo.c.producto_id == "999901"))
        conn.execute(
            insert(schema.catalogo).values(
                producto_id="999901",
                codigo="TEST-0761",
                nombre="LIGA FRENOS PRUEBA SQL (1x12)",
                marca="Global Oil",
                volumen=Decimal("3.5"),
                peso=Decimal("4.0"),
                unidades_por_paleta=Decimal("50"),
            )
        )
    try:
        CatalogoSQLOdoo.limpiar_cache()
        cat = CatalogoSQLOdoo(eng, lambda: {"999901": (Decimal("19.50"), Decimal("25.00"))})
        repo = RepoDocumentosSQL(eng)
        svc = ServicioDocumentos(repo, cat, lambda: None)

        creado = svc.crear(
            {
                "tipo": "cotizacion",
                "cliente_nombre": "Prospecto SQL C.A.",
                "moneda_lista": "USD",
                "lineas": [{"codigo": "TEST-0761", "cantidad": "3"}],
            },
            origen="bot",
            captura_id=777,
            actor="bot",
        )
        assert creado.numero == "COT-00001" and creado.subtotal == Decimal("58.50")
        por_cap = repo.obtener_por_captura(777)
        assert por_cap is not None and por_cap.documento_id == creado.documento_id

        editado = svc.editar(
            creado.documento_id,
            {"moneda_lista": "VES", "lineas": [{"codigo": "TEST-0761", "cantidad": "2"}]},
        )
        assert editado.subtotal == Decimal("50.00")

        lista, total, res = svc.listar(q="Prospecto SQL")
        assert total == 1 and res["cotizaciones_borrador"] == 1 and len(lista) == 1
    finally:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE documentos_comerciales_lineas, documentos_comerciales "
                    "RESTART IDENTITY CASCADE"
                )
            )
            conn.execute(delete(schema.catalogo).where(schema.catalogo.c.producto_id == "999901"))
