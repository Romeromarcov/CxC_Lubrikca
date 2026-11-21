"""Pruebas integrales de listas de precio, días de crédito, ruta BCV/Binance y restricciones."""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import ExitStack
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert

import cxc.web.app as web_app
from cxc.db import schema
from cxc.db.postgres_repository import PostgresRepository
from cxc.documentos.catalogo import (
    CatalogoSQLOdoo,
    ItemCatalogo,
    ListaComercialActiva,
    cargar_datos_precios_odoo,
)
from cxc.documentos.evaluador_descuentos import evaluar_descuentos_cotizacion
from cxc.documentos.modelos import (
    BORRADOR,
    COTIZACION,
    ENVIADO_ODOO,
    PEDIDO,
    PENDIENTE_APROBACION,
    DocumentoComercial,
    LineaDocumento,
)
from cxc.documentos.politica_ventas import evaluar_politica_auto_envio
from cxc.documentos.servicio import ServicioDocumentos
from cxc.engine.cxc_routing import BandejaDestino, clasificar_estado_cxc
from cxc.engine.rutas_pago import (
    RUTA_BCV,
    RUTA_BINANCE,
    etiqueta_ruta,
    inferir_ruta_por_nombre,
    listas_por_ruta,
    normalizar_ruta,
    ruta_de_lista,
    validar_compatibilidad_ruta_pago,
)
from cxc.engine.saldos import saldo_pendiente_por_ruta, saldos_de_la_orden
from cxc.engine.saldos_propios import calcular_saldos_propios
from cxc.models import (
    Cliente,
    DescuentoProntoPago,
    EstadoVinculacion,
    MetodoPago,
    Moneda,
    OrdenVenta,
    Pago,
    SerieTasa,
    TipoTasa,
    Vinculacion,
)
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.vinculaciones_docs import (
    VinculacionInvalida,
    candidatos_pagos_para_venta,
    candidatos_ventas_para_pago,
    vincular_pago_venta,
)
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web.documentos_api import get_servicio_documentos
from tests.capturas_dobles import UBICACION, OdooFalso

KEY = "clave-bot-docs"
BOT = {"X-Bot-Key": KEY}
VENTAS_USER = {"email": "vend@lubrikca.com", "rol": "ventas", "permisos": efectivos("ventas")}
GERENTE_USER = {
    "email": "gerencia@lubrikca.com",
    "rol": "gerente_ventas",
    "permisos": efectivos("gerente_ventas"),
}


class _RepoDocsRutasMem:
    """Repositorio en memoria que persiste dias_credito, ruta_pago y descuentos_especiales."""

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
            moneda_lista=str(cabecera.get("moneda_lista") or "VES"),
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
            token_preview=str(cabecera.get("token_preview") or ""),
            lista_precios_id=str(cabecera.get("lista_precios_id") or "21"),
            lista_precios_nombre=str(cabecera.get("lista_precios_nombre") or "Pago VES Nov 2026"),
            dias_credito=int(cabecera.get("dias_credito") or 0),
            ruta_pago=str(cabecera.get("ruta_pago") or ""),
            descuentos_elegidos=tuple(cabecera.get("descuentos_elegidos") or ()),
            descuentos_especiales=tuple(cabecera.get("descuentos_especiales") or ()),
            validacion_motor=cabecera.get("validacion_motor"),
            total_descuentos=Decimal(str(cabecera.get("total_descuentos") or "0")),
            subtotal_neto=Decimal(str(cabecera.get("subtotal_neto") or "0")),
            monto_iva=Decimal(str(cabecera.get("monto_iva") or "0")),
            total_con_iva=Decimal(str(cabecera.get("total_con_iva") or "0")),
            aprobado_por=str(cabecera.get("aprobado_por") or ""),
            aprobado_en=cabecera.get("aprobado_en"),
            auto_enviado=bool(cabecera.get("auto_enviado", False)),
            motivos_retencion=tuple(str(x) for x in (cabecera.get("motivos_retencion") or ())),
            evaluacion_politica=cabecera.get("evaluacion_politica"),
        )
        self.docs[did] = doc
        return doc

    def obtener(self, documento_id: int) -> DocumentoComercial | None:
        return self.docs.get(documento_id)

    def obtener_por_token(self, token_preview: str) -> DocumentoComercial | None:
        return next(
            (d for d in self.docs.values() if d.token_preview and d.token_preview == token_preview),
            None,
        )

    def obtener_por_captura(self, captura_id: int) -> DocumentoComercial | None:
        for d in sorted(self.docs.values(), key=lambda x: x.documento_id, reverse=True):
            if d.captura_id == captura_id:
                return d
        return None

    def listar(self, **_kw: Any) -> tuple[list[DocumentoComercial], int, dict[str, int]]:
        vals = list(self.docs.values())
        return vals, len(vals), {"total": len(vals)}

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
            token_preview=str(cabecera.get("token_preview", actual.token_preview)),
            lista_precios_id=str(cabecera.get("lista_precios_id", actual.lista_precios_id)),
            lista_precios_nombre=str(
                cabecera.get("lista_precios_nombre", actual.lista_precios_nombre)
            ),
            dias_credito=int(cabecera.get("dias_credito", actual.dias_credito)),
            ruta_pago=str(cabecera.get("ruta_pago", actual.ruta_pago)),
            descuentos_elegidos=tuple(
                cabecera.get("descuentos_elegidos", actual.descuentos_elegidos)
            ),
            descuentos_especiales=tuple(
                cabecera.get("descuentos_especiales", actual.descuentos_especiales)
            ),
            validacion_motor=cabecera.get("validacion_motor", actual.validacion_motor),
            total_descuentos=Decimal(
                str(cabecera.get("total_descuentos", actual.total_descuentos))
            ),
            subtotal_neto=Decimal(str(cabecera.get("subtotal_neto", actual.subtotal_neto))),
            monto_iva=Decimal(str(cabecera.get("monto_iva", actual.monto_iva))),
            total_con_iva=Decimal(str(cabecera.get("total_con_iva", actual.total_con_iva))),
            aprobado_por=str(cabecera.get("aprobado_por", actual.aprobado_por)),
            aprobado_en=cabecera.get("aprobado_en", actual.aprobado_en),
            auto_enviado=bool(cabecera.get("auto_enviado", actual.auto_enviado)),
            motivos_retencion=tuple(
                str(x) for x in cabecera.get("motivos_retencion", actual.motivos_retencion)
            ),
            evaluacion_politica=cabecera.get("evaluacion_politica", actual.evaluacion_politica),
        )
        self.docs[documento_id] = nuevo
        return nuevo


class _CatalogoMultiListaMem:
    """Catálogo con múltiples listas activas y precios diferenciados por lista."""

    def __init__(self, items: Sequence[ItemCatalogo], listas: Sequence[ListaComercialActiva]):
        self.items = list(items)
        self._listas = list(listas)

    def listar(self, *, refresh: bool = False) -> list[ItemCatalogo]:
        return list(self.items)

    def listas_activas(self, *, refresh: bool = False) -> list[ListaComercialActiva]:
        return list(self._listas)

    def lista_comercial_defecto(self, *, refresh: bool = False) -> ListaComercialActiva:
        return self._listas[0]


ITEMS_MULTI = [
    ItemCatalogo(
        "1033",
        "0761",
        "LIGA PARA FRENOS DOT3 (1x12)",
        "GLOBAL OIL",
        "1X12",
        Decimal("3.5"),
        Decimal("18.00"),
        Decimal("24.00"),
        precios_por_lista={"21": Decimal("24.00"), "20": Decimal("18.00")},
    ),
    ItemCatalogo(
        "1077",
        "0997",
        "LIGA PARA FRENOS DOT4 (Tambor)",
        "GLOBAL OIL",
        "TAMBOR",
        Decimal("208.0"),
        Decimal("480.00"),
        Decimal("650.00"),
        precios_por_lista={"21": Decimal("650.00"), "20": Decimal("480.00")},
    ),
]

LISTAS_MULTI = [
    ListaComercialActiva("21", "Pago VES Nov 2026", "VES", RUTA_BCV),
    ListaComercialActiva("20", "Pago USD Nov 2026", "USD", RUTA_BINANCE),
]


def _client_con_repo(repo: Any) -> tuple[ExitStack, TestClient]:
    async def _nada() -> None:
        return None

    pila = ExitStack()
    for parche in (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.hay_sesion_valida", return_value=True),
        patch("cxc.web.app.get_current_user_from_cookie", return_value=GERENTE_USER),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app.run_sync_in_background", _nada),
        patch("cxc.web.app._aplicar_migraciones_pendientes"),
        patch("cxc.web.app._connect", return_value=None),
    ):
        pila.enter_context(parche)
    return pila, pila.enter_context(TestClient(web_app.app, raise_server_exceptions=False))


# ── 1. cxc.engine.rutas_pago ────────────────────────────────────────────────


def test_rutas_pago_inferencia_overrides_y_compatibilidad() -> None:
    assert normalizar_ruta("BCV") == RUTA_BCV
    assert normalizar_ruta("Ruta Binance") == RUTA_BINANCE
    assert inferir_ruta_por_nombre("Pago VES Nov 2026") == RUTA_BCV
    assert inferir_ruta_por_nombre("Industrial USD Especial") == RUTA_BINANCE
    assert inferir_ruta_por_nombre("Lista General", moneda="USD") == RUTA_BINANCE
    assert "BCV" in etiqueta_ruta(RUTA_BCV)
    assert "Binance" in etiqueta_ruta(RUTA_BINANCE)

    mapeo = {
        "21": {"moneda": "ves", "categoria": "comercial", "nombre": "Pago VES Nov 2026"},
        "20": {"moneda": "usd", "categoria": "comercial", "nombre": "Pago USD Nov 2026"},
        # Override explícito: lista 99 sin VES/USD en nombre, forzada a binance
        "99": {
            "moneda": "ves",
            "categoria": "comercial",
            "ruta": "binance",
            "nombre": "Promo Especial",
        },
    }
    assert ruta_de_lista("21", mapeo) == RUTA_BCV
    assert ruta_de_lista("20", mapeo) == RUTA_BINANCE
    assert ruta_de_lista("99", mapeo) == RUTA_BINANCE
    # Histórica fuerza BCV siempre
    assert ruta_de_lista("20", mapeo, es_historica=True) == RUTA_BCV

    ids_bcv, ids_bin = listas_por_ruta(mapeo)
    assert ids_bcv == {"21"}
    assert ids_bin == {"20", "99"}

    # Pagos genéricos (efectivo USD / Zelle / Bs transferencia) compatibles con ambas rutas
    assert validar_compatibilidad_ruta_pago(RUTA_BCV, metodo_pago_nombre="Zelle Terceros") == (
        True,
        "",
    )
    assert validar_compatibilidad_ruta_pago(RUTA_BINANCE, metodo_pago_nombre="Efectivo USD") == (
        True,
        "",
    )
    # Declaración explícita o diario de la ruta contraria se rechaza en ambos sentidos
    ok1, msg1 = validar_compatibilidad_ruta_pago(RUTA_BCV, ruta_pago_declarada="binance")
    assert ok1 is False and "BCV" in msg1
    ok2, msg2 = validar_compatibilidad_ruta_pago(RUTA_BINANCE, ruta_pago_declarada="bcv")
    assert ok2 is False and "Binance" in msg2
    ok3, _ = validar_compatibilidad_ruta_pago(RUTA_BCV, metodo_pago_nombre="Binance Pay USDT")
    assert ok3 is False
    ok4, _ = validar_compatibilidad_ruta_pago(RUTA_BINANCE, metodo_pago_nombre="BDV BCV 0102")
    assert ok4 is False


# ── 2. cxc_routing bidireccional + saldo_pendiente_por_ruta ────────────────


def test_cxc_routing_bidireccional_y_saldo_pendiente_por_ruta() -> None:
    # Orden nacida en USD / Binance: solo Teórico BS NO la saca de CxC
    r_bin_solo_bs = clasificar_estado_cxc(
        so_id="S_BIN_1",
        facturada=False,
        teorico_bs_pagado=True,
        teorico_usd_pagado=False,
        factura_real_pagada=False,
        ruta_orden=RUTA_BINANCE,
    )
    assert r_bin_solo_bs.sale_de_cxc is False
    assert r_bin_solo_bs.bandeja_destino is None

    r_bin_usd = clasificar_estado_cxc(
        so_id="S_BIN_1",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=True,
        factura_real_pagada=False,
        ruta_orden=RUTA_BINANCE,
    )
    assert r_bin_usd.sale_de_cxc is True
    assert r_bin_usd.bandeja_destino == BandejaDestino.FACTURACION_1

    # Orden nacida en VES / BCV: solo Teórico USD NO la saca de CxC
    r_bcv_solo_usd = clasificar_estado_cxc(
        so_id="S_BCV_1",
        facturada=False,
        teorico_bs_pagado=False,
        teorico_usd_pagado=True,
        factura_real_pagada=False,
        ruta_orden=RUTA_BCV,
    )
    assert r_bcv_solo_usd.sale_de_cxc is False

    # Órdenes históricas/saldadas en Odoo siguen saliendo en cualquier ruta
    for ruta in (RUTA_BCV, RUTA_BINANCE):
        r_odoo = clasificar_estado_cxc(
            so_id=f"S_{ruta}",
            facturada=True,
            teorico_bs_pagado=False,
            teorico_usd_pagado=False,
            factura_real_pagada=False,
            factura_pagada_confirmada_odoo=True,
            ruta_orden=ruta,
        )
        assert r_odoo.sale_de_cxc is True
        assert r_odoo.bandeja_destino == BandejaDestino.FACTURACION_2

    # saldo_pendiente_por_ruta elige el teórico de su ruta sin mutar saldos_de_la_orden
    item_bin = {
        "ruta_pago": "binance",
        "ves_neta_teorica_iva": 100.0,
        "usd_neta_teorica_iva": 65.0,
        "venta_neta_real": 100.0,
        "descuento_aplicado_sistema": 0.0,
        "pagado_teorico_bcv_incl_pendiente": 10.0,
        "pagado_teorico_binance_incl_pendiente": 20.0,
        "monto_pagado_factura_odoo_incl_pendiente": 0.0,
    }
    saldos_raw = saldos_de_la_orden(item_bin)
    assert saldos_raw["teorico_bs"] == 90.0
    assert saldos_raw["teorico_usd"] == 45.0
    assert saldo_pendiente_por_ruta(item_bin) == (RUTA_BINANCE, 45.0)
    assert saldo_pendiente_por_ruta({**item_bin, "ruta_pago": "bcv"}) == (RUTA_BCV, 90.0)


# ── 3. Endpoints /api/vincular, /api/config/pricelist-mapeo, saldos_propios ─


def test_post_put_vincular_rechaza_cruce_de_ruta_y_estampa_tipo_tasa() -> None:
    repo = InMemoryRepository()
    repo.set_config(
        "pricelist_mapeo_unificado",
        json.dumps(
            {
                "21": {"moneda": "ves", "categoria": "comercial", "ruta": "bcv", "vigente": True},
                "20": {
                    "moneda": "usd",
                    "categoria": "comercial",
                    "ruta": "binance",
                    "vigente": True,
                },
            }
        ),
    )
    repo.upsert_clientes([Cliente(cliente_id="C1", nombre="Cliente 1", vendedor_email="v@x.com")])
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S_VES_21",
                cliente_id="C1",
                fecha=date(2026, 11, 20),
                fecha_entrega=date(2026, 11, 20),
                monto_total=Decimal("100.00"),
                lista_precios="21",
                vendedor_email="v@x.com",
                es_primera_compra=False,
            ),
            OrdenVenta(
                so_id="S_USD_20",
                cliente_id="C1",
                fecha=date(2026, 11, 20),
                fecha_entrega=date(2026, 11, 20),
                monto_total=Decimal("100.00"),
                lista_precios="20",
                vendedor_email="v@x.com",
                es_primera_compra=False,
            ),
        ]
    )
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 20, 12, 0),
            tasa_bcv=Decimal("40.00"),
            tasa_binance=Decimal("55.00"),
            fuente="test",
        )
    )
    repo.add_metodo_pago(
        MetodoPago(
            metodo_id="M_BNB",
            nombre="Binance USDT",
            moneda=Moneda.USD,
            es_contado=True,
            tipo_tasa=TipoTasa.BINANCE,
        )
    )
    repo.add_metodo_pago(
        MetodoPago(
            metodo_id="M_GEN",
            nombre="Efectivo BS",
            moneda=Moneda.VES,
            es_contado=True,
            tipo_tasa=TipoTasa.BCV,
        )
    )
    repo.upsert_pagos(
        [
            Pago(
                pago_id="P_BNB",
                cliente_id="C1",
                monto=Decimal("50.00"),
                moneda=Moneda.USD,
                metodo_pago="M_BNB",
                fecha_pago=datetime(2026, 11, 20, 12, 0),
                vendedor_email="v@x.com",
            ),
            Pago(
                pago_id="P_VES",
                cliente_id="C1",
                monto=Decimal("2200.00"),
                moneda=Moneda.VES,
                metodo_pago="M_GEN",
                fecha_pago=datetime(2026, 11, 20, 12, 0),
                vendedor_email="v@x.com",
            ),
        ]
    )
    web_app.invalidar_tasas()
    pila, c = _client_con_repo(repo)
    with pila:
        # 1) Método Binance en orden BCV -> 400
        r_bad1 = c.post(
            "/api/vincular",
            json={"pago_id": "P_BNB", "so_id": "S_VES_21", "monto_aplicado": 50.0},
        )
        assert r_bad1.status_code == 400
        # 2) Pago genérico declarando ruta_pago="bcv" en orden Binance -> 400
        r_bad2 = c.post(
            "/api/vincular",
            json={
                "pago_id": "P_VES",
                "so_id": "S_USD_20",
                "monto_aplicado": 2200.0,
                "ruta_pago": "bcv",
            },
        )
        assert r_bad2.status_code == 400
        # 3) Pago genérico VES vinculado a orden Binance -> éxito y TipoTasa.BINANCE
        r_ok = c.post(
            "/api/vincular",
            json={"pago_id": "P_VES", "so_id": "S_USD_20", "monto_aplicado": 2200.0},
        )
        assert r_ok.status_code == 200, r_ok.text
        vinc = next(v for v in repo.all_vinculaciones() if v.pago_id == "P_VES")
        assert vinc.tipo_tasa_abono == TipoTasa.BINANCE

        # 4) PUT /editar intentando mover P_BNB (ya vinculado a S_USD_20) hacia S_VES_21 -> 400
        r_ok_bnb = c.post(
            "/api/vincular",
            json={"pago_id": "P_BNB", "so_id": "S_USD_20", "monto_aplicado": 50.0},
        )
        assert r_ok_bnb.status_code == 200, r_ok_bnb.text
        v_bnb = next(v for v in repo.all_vinculaciones() if v.pago_id == "P_BNB")
        r_put_bad = c.put(
            f"/api/vinculacion/{v_bnb.vinc_id}/editar",
            json={"so_id": "S_VES_21", "monto_aplicado": 25.0},
        )
        assert r_put_bad.status_code == 400


def test_config_pricelist_mapeo_persiste_y_devuelve_rutas_por_lista() -> None:
    repo = InMemoryRepository()
    pila, c = _client_con_repo(repo)
    with pila:
        r_post = c.post(
            "/api/config/pricelist-mapeo",
            json={
                "mapeo": {
                    "21": {
                        "moneda": "ves",
                        "categoria": "comercial",
                        "ruta": "bcv",
                        "nombre": "Pago VES Nov 2026",
                        "vigente": True,
                    },
                    "20": {
                        "moneda": "usd",
                        "categoria": "comercial",
                        "ruta": "binance",
                        "nombre": "Pago USD Nov 2026",
                        "vigente": True,
                    },
                },
                "historical_pricelist_enabled": True,
            },
        )
        assert r_post.status_code == 200, r_post.text
        r_get = c.get("/api/config/pricelist-mapeo")
        assert r_get.status_code == 200
        body = r_get.json()
        assert body["rutas_por_lista"]["21"] == RUTA_BCV
        assert body["rutas_por_lista"]["20"] == RUTA_BINANCE
        assert body["mapeo"]["21"]["ruta"] == RUTA_BCV
        assert body["mapeo"]["20"]["ruta"] == RUTA_BINANCE
        assert web_app.get_valid_pricelists_usd_and_ves(repo) == (["20"], ["21"])


def test_saldos_propios_aplica_binance_en_ruta_usd_y_separa_subtotales() -> None:
    repo = InMemoryRepository()
    repo.set_config(
        "pricelist_mapeo_unificado",
        json.dumps(
            {
                "21": {"moneda": "ves", "categoria": "comercial", "ruta": "bcv"},
                "20": {"moneda": "usd", "categoria": "comercial", "ruta": "binance"},
            }
        ),
    )
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 20, 12, 0),
            tasa_bcv=Decimal("40.00"),
            tasa_binance=Decimal("55.00"),
            fuente="test",
        )
    )
    repo.upsert_clientes([Cliente(cliente_id="101", nombre="ALFA", vendedor_email="v@x.com")])
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="SO_BCV",
                cliente_id="101",
                fecha=date(2026, 11, 20),
                fecha_entrega=date(2026, 11, 20),
                monto_total=Decimal("100.00"),
                lista_precios="21",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                estado_orden="sale",
            ),
            OrdenVenta(
                so_id="SO_BIN",
                cliente_id="101",
                fecha=date(2026, 11, 20),
                fecha_entrega=date(2026, 11, 20),
                monto_total=Decimal("100.00"),
                lista_precios="20",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                estado_orden="sale",
            ),
        ]
    )
    # 1 pago VES de 2200 Bs: vinculado con Binance a SO_BIN vale 2200/55 = $40 (NO $55 BCV)
    repo.upsert_pagos(
        [
            Pago(
                pago_id="P_VES_1",
                cliente_id="101",
                monto=Decimal("2200.00"),
                moneda=Moneda.VES,
                metodo_pago="M1",
                fecha_pago=datetime(2026, 11, 20, 12, 0),
                vendedor_email="v@x.com",
            )
        ]
    )
    repo.add_vinculacion(
        Vinculacion(
            vinc_id="V_BIN",
            pago_id="P_VES_1",
            so_id="SO_BIN",
            monto_aplicado=Decimal("2200.00"),
            hora_pago_confirmada=datetime(2026, 11, 20, 12, 0),
            tasa_bcv_aplicada=Decimal("40.00"),
            tasa_binance_aplicada=Decimal("55.00"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.CONCILIADO,
            moneda_abono=Moneda.VES,
            tipo_tasa_abono=TipoTasa.BINANCE,
            equiv_usd_bcv=Decimal("55.00"),
            equiv_usd_binance=Decimal("40.00"),
        )
    )
    rep = calcular_saldos_propios(repo, hoy=date(2026, 11, 21))
    alfa = next(c for c in rep["clientes"] if c["cliente_id"] == "101")
    assert alfa["ventas_bcv_usd"] == 100.0
    assert alfa["ventas_binance_usd"] == 100.0
    assert alfa["pagos_binance_usd"] == 40.0
    assert alfa["pagos_bcv_usd"] == 0.0
    assert rep["tasa_binance_referencia"] == 55.0


# ── 4. vinculaciones_docs: candidatos y vinculación con ruta + Binance ─────


def test_vinculaciones_docs_rechaza_cruce_de_ruta_y_calcula_tasa_binance(tmp_path: Path) -> None:
    db_file = tmp_path / "vinc_rutas.db"
    eng = create_engine(f"sqlite:///{db_file}", future=True)
    schema.metadata.create_all(eng)
    repo = PostgresRepository(eng)
    repo.set_config(
        "pricelist_mapeo_unificado",
        json.dumps(
            {
                "21": {"moneda": "ves", "categoria": "comercial", "ruta": "bcv"},
                "20": {"moneda": "usd", "categoria": "comercial", "ruta": "binance"},
            }
        ),
    )
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 20, 12, 0),
            tasa_bcv=Decimal("40.00"),
            tasa_binance=Decimal("55.00"),
            fuente="test",
        )
    )
    repo.upsert_clientes([Cliente(cliente_id="1001", nombre="Alfa C.A.", vendedor_email="v@x.com")])
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S00510",
                cliente_id="1001",
                fecha=date(2026, 11, 20),
                fecha_entrega=date(2026, 11, 20),
                monto_total=Decimal("100.00"),
                lista_precios="20",  # Binance
                vendedor_email="v@x.com",
                es_primera_compra=False,
                estado_orden="sale",
            )
        ]
    )
    repo.add_metodo_pago(
        MetodoPago(
            metodo_id="14",
            nombre="BDV BCV",
            moneda=Moneda.VES,
            es_contado=False,
            tipo_tasa=TipoTasa.BCV,
        )
    )
    repo.add_metodo_pago(
        MetodoPago(
            metodo_id="29",
            nombre="Efectivo BS",
            moneda=Moneda.VES,
            es_contado=True,
            tipo_tasa=TipoTasa.N_A,
        )
    )
    repo.upsert_pagos(
        [
            Pago(
                pago_id="9001",
                cliente_id="1001",
                monto=Decimal("4000.00"),
                moneda=Moneda.VES,
                metodo_pago="14",
                fecha_pago=datetime(2026, 11, 20, 12, 0),
                vendedor_email="v@x.com",
                numero_odoo="PBV/1",
            ),
            Pago(
                pago_id="9002",
                cliente_id="1001",
                monto=Decimal("2750.00"),
                moneda=Moneda.VES,
                metodo_pago="29",
                fecha_pago=datetime(2026, 11, 20, 12, 0),
                vendedor_email="v@x.com",
                numero_odoo="PEF/2",
            ),
        ]
    )
    with eng.begin() as conn:
        conn.execute(
            insert(schema.documentos_comerciales).values(
                numero="PED-00510",
                token_preview="tok-510",
                tipo="pedido",
                estado=ENVIADO_ODOO,
                origen="web",
                partner_odoo_id=1001,
                cliente_nombre="Alfa C.A.",
                lista_precios_id="20",
                lista_precios_nombre="Pago USD Nov 2026",
                moneda_lista="USD",
                ruta_pago=RUTA_BINANCE,
                vendedor_email="v@x.com",
                es_primera_compra=False,
                auto_enviado=True,
                total_con_iva=Decimal("100.00"),
                subtotal=Decimal("86.21"),
                odoo_order_name="S00510",
                creado_por="v@x.com",
                creado_en=datetime(2026, 11, 20, 10, 0),
                actualizado_en=datetime(2026, 11, 20, 10, 0),
            )
        )

    cands_p = candidatos_pagos_para_venta(eng, repo, so_id="S00510")
    assert cands_p["documento"]["ruta_pago"] == RUTA_BINANCE
    por_pid = {str(p.get("pago_odoo_id") or p.get("pago_id")): p for p in cands_p["candidatos"]}
    assert por_pid["9001"]["compatible_ruta"] is False
    assert por_pid["9002"]["compatible_ruta"] is True

    # Cruce de ruta rechazado
    with pytest.raises(VinculacionInvalida, match="Binance"):
        vincular_pago_venta(
            eng,
            repo,
            so_id="S00510",
            pago_id="9001",
            monto_aplicado=50.0,
            actor="v@x.com",
        )

    # Mismo pago genérico VES vinculado a orden Binance -> usa tasa Binance (55.0)
    res_ok = vincular_pago_venta(
        eng,
        repo,
        so_id="S00510",
        pago_id="9002",
        monto_aplicado=2750.0,
        actor="v@x.com",
    )
    assert res_ok["ok"] is True
    vinc_guardada = next(v for v in repo.all_vinculaciones() if v.pago_id == "9002")
    assert vinc_guardada.tipo_tasa_abono == TipoTasa.BINANCE
    assert vinc_guardada.monto_aplicado == Decimal("2750.0000")
    assert vinc_guardada.equiv_usd_binance == Decimal("50.0000")

    cands_v = candidatos_ventas_para_pago(eng, repo, pago_id="9001")
    assert cands_v["candidatos"][0]["compatible_ruta"] is False


# ── 5. Catálogo multi-lista, crédito, descuentos especiales y Servicio ───────


def test_cargar_datos_precios_odoo_multilista_y_precio_para_lista() -> None:
    def _execute(modelo: str, _metodo: str, _args: list[Any], _kwargs: Any = None) -> list[Any]:
        if modelo == "product.pricelist":
            return [
                {
                    "id": 21,
                    "name": "Pago VES Nov 2026",
                    "active": True,
                    "currency_id": [166, "VES"],
                },
                {
                    "id": 20,
                    "name": "Pago USD Nov 2026",
                    "active": True,
                    "currency_id": [2, "USD"],
                },
            ]
        if modelo == "product.pricelist.item":
            return [
                {
                    "id": 1,
                    "pricelist_id": [21, "Pago VES Nov 2026"],
                    "product_tmpl_id": [500, "P1"],
                    "product_id": False,
                    "fixed_price": 24.0,
                    "compute_price": "fixed",
                },
                {
                    "id": 2,
                    "pricelist_id": [20, "Pago USD Nov 2026"],
                    "product_tmpl_id": [500, "P1"],
                    "product_id": False,
                    "fixed_price": 18.0,
                    "compute_price": "fixed",
                },
            ]
        if modelo == "product.product":
            return [{"id": 1033, "product_tmpl_id": [500, "P1"]}]
        return []

    CatalogoSQLOdoo.limpiar_cache()
    datos = cargar_datos_precios_odoo(_execute, [20], [21])
    por_id = {x.lista_id: x for x in datos.listas_activas}
    assert por_id["21"].ruta_efectiva == RUTA_BCV
    assert por_id["20"].ruta_efectiva == RUTA_BINANCE
    assert datos.precios_por_lista["21"]["1033"] == Decimal("24.0")
    assert datos.precios_por_lista["20"]["1033"] == Decimal("18.0")
    assert ITEMS_MULTI[0].precio_para_lista("20", "USD") == Decimal("18.00")
    assert ITEMS_MULTI[0].precio_para_lista("21", "VES") == Decimal("24.00")


def test_evaluador_credito_por_volumen_conflicto_pronto_pago_y_especiales() -> None:
    repo = InMemoryRepository()
    repo.set_config(
        "pricelist_mapeo_unificado",
        json.dumps({"20": {"moneda": "usd", "categoria": "comercial", "ruta": "binance"}}),
    )
    repo.upsert_regla_dias_credito_volumen(
        {
            "regla_id": "CRED_0_100L",
            "litros_minimo": "0",
            "litros_maximo": "100",
            "dias_credito_max": "7",
            "activo": "true",
        }
    )
    repo.upsert_regla_dias_credito_volumen(
        {
            "regla_id": "CRED_101_9999L",
            "litros_minimo": "100",
            "litros_maximo": "9999",
            "dias_credito_max": "21",
            "activo": "true",
        }
    )
    repo.append_descuento_pronto_pago(
        DescuentoProntoPago(
            regla_id="PP_USD_08",
            marca="GLOBAL OIL",
            categoria="CAJA",
            porcentaje=Decimal("0.08"),
            monedas_aplicables="USD",
            ventana_pago_tipo="entrega",
            ventana_pago_dias=3,
            descripcion="Pronto Pago 8%",
        )
    )

    lineas_bajo_litraje = [
        {
            "producto_id": "1033",
            "codigo": "0761",
            "descripcion": "LIGA PARA FRENOS DOT3 (1x12)",
            "marca": "GLOBAL OIL",
            "presentacion": "1X12",
            "litros_unidad": Decimal("3.5"),
            "cantidad": Decimal("2"),
            "precio_unitario": Decimal("18.00"),
        }
    ]
    # 7 litros -> tope 7 días; pide 15 días + Pronto Pago + prospecto sin partner + especial $2.50
    ev = evaluar_descuentos_cotizacion(
        repo,
        lineas=lineas_bajo_litraje,
        descuentos_codigos=["PP_USD_08"],
        descuentos_especiales=[{"motivo": "Acuerdo feria", "monto_usd": 2.50}],
        lista_precios_id="20",
        lista_precios_nombre="Pago USD Nov 2026",
        moneda_lista="USD",
        partner_odoo_id=None,
        es_primera_compra=True,
        dias_credito_solicitados=15,
    )
    assert ev["ruta_pago"] == RUTA_BINANCE
    assert ev["max_dias_credito_sugerido"] == 7
    assert ev["dias_credito_cumple"] is False
    assert ev["requiere_aprobacion_manual"] is True
    assert len(ev["descuentos_especiales"]) == 1
    assert ev["descuentos_especiales"][0]["motivo"] == "Acuerdo feria"
    assert ev["descuentos_especiales"][0]["monto_estimado_usd"] == 2.5
    assert ev["subtotal_lista"] == 36.0
    assert ev["total_descuentos"] >= 2.5
    assert any("excede el máximo" in a for a in ev["alertas"])
    assert any("Pronto Pago" in a for a in ev["alertas"])
    assert any("cliente nuevo" in a for a in ev["alertas"])

    # Evaluar política sobre un pedido con descuento especial y crédito en prospecto -> retiene
    doc_ped = DocumentoComercial(
        documento_id=99,
        numero="PED-00099",
        tipo=PEDIDO,
        estado=PENDIENTE_APROBACION,
        origen="web",
        captura_id=None,
        partner_odoo_id=None,
        tipo_contacto="prospecto",
        cliente_nombre="Prospecto Test",
        rif="J-123456789",
        email="p@x.com",
        telefono="04141112233",
        direccion="Av 1",
        plus_code="",
        latitud=10.5,
        longitud=-66.9,
        es_primera_compra=True,
        moneda_lista="USD",
        vendedor_email="vend@lubrikca.com",
        fecha_entrega=None,
        condiciones_negociacion="",
        observaciones="",
        subtotal=Decimal("36.00"),
        total_litros=Decimal("7.00"),
        odoo_order_id=None,
        odoo_order_name="",
        error_odoo="",
        creado_por="vend@lubrikca.com",
        creado_en=datetime.now(),
        actualizado_en=datetime.now(),
        dias_credito=15,
        ruta_pago=RUTA_BINANCE,
        descuentos_especiales=({"motivo": "Acuerdo feria", "monto_usd": 2.5},),
        validacion_motor=ev,
    )
    pol = evaluar_politica_auto_envio(doc_ped, repo)
    assert pol["puede_auto_enviar"] is False
    assert any("especial" in m.lower() for m in pol["motivos_retencion"])

    # Caso limpio: ficha completa, crédito dentro del tope y sin descuentos especiales -> auto-envía
    ev_clean = evaluar_descuentos_cotizacion(
        repo,
        lineas=lineas_bajo_litraje,
        descuentos_codigos=[],
        descuentos_especiales=[],
        lista_precios_id="20",
        lista_precios_nombre="Pago USD Nov 2026",
        moneda_lista="USD",
        partner_odoo_id=88,
        es_primera_compra=False,
        dias_credito_solicitados=7,
    )
    assert ev_clean["dias_credito_cumple"] is True
    assert ev_clean["alertas"] == []
    doc_clean = DocumentoComercial(
        documento_id=100,
        numero="PED-00100",
        tipo=PEDIDO,
        estado=BORRADOR,
        origen="web",
        captura_id=None,
        partner_odoo_id=88,
        tipo_contacto="cliente",
        cliente_nombre="Cliente Existente",
        rif="J-123456789",
        email="c@x.com",
        telefono="04141112233",
        direccion="Av 1",
        plus_code="",
        latitud=10.5,
        longitud=-66.9,
        es_primera_compra=False,
        moneda_lista="USD",
        vendedor_email="vend@lubrikca.com",
        fecha_entrega=None,
        condiciones_negociacion="",
        observaciones="",
        subtotal=Decimal("36.00"),
        total_litros=Decimal("7.00"),
        odoo_order_id=None,
        odoo_order_name="",
        error_odoo="",
        creado_por="vend@lubrikca.com",
        creado_en=datetime.now(),
        actualizado_en=datetime.now(),
        dias_credito=7,
        ruta_pago=RUTA_BINANCE,
        descuentos_especiales=(),
        validacion_motor=ev_clean,
    )
    pol_clean = evaluar_politica_auto_envio(doc_clean, repo)
    assert pol_clean["puede_auto_enviar"] is True, pol_clean["motivos_retencion"]


def test_servicio_flujo_multilista_credito_especiales_y_endpoints() -> None:
    repo_cxc = InMemoryRepository()
    repo_cxc.set_config(
        "pricelist_mapeo_unificado",
        json.dumps(
            {
                "21": {"moneda": "ves", "categoria": "comercial", "ruta": "bcv", "vigente": True},
                "20": {
                    "moneda": "usd",
                    "categoria": "comercial",
                    "ruta": "binance",
                    "vigente": True,
                },
            }
        ),
    )
    repo_cxc.upsert_regla_dias_credito_volumen(
        {
            "regla_id": "CRED_HASTA_500L",
            "litros_minimo": "0",
            "litros_maximo": "500",
            "dias_credito_max": "30",
            "activo": "true",
        }
    )
    repo_cxc.upsert_clientes(
        [Cliente(cliente_id="88", nombre="Ronald Socorro", vendedor_email="vend@lubrikca.com")]
    )
    odoo = OdooFalso(
        res_partner__read=[
            {
                "id": 88,
                "name": "Ronald Socorro",
                "vat": "V-13069226",
                "email": "r@x.com",
                "phone": "04120001122",
                "street": "Guatire",
                "street2": "",
                "customer_rank": 1,
            }
        ],
        sale_order__fields_get={
            "name": {},
            "message_ids": {},
            "pricelist_id": {},
            "payment_term_id": {},
        },
        account_payment_term__search_read=[{"id": 7, "name": "21 Días", "line_ids": [1]}],
    )
    catalogo = _CatalogoMultiListaMem(ITEMS_MULTI, LISTAS_MULTI)
    repo_docs = _RepoDocsRutasMem()
    svc = ServicioDocumentos(
        repo_docs,
        catalogo,
        lambda: EscritorOdoo(odoo),
        repo_cxc=repo_cxc,
    )

    # 1. Endpoints GET /api/documentos/listas-precio y GET /api/bots/listas-precio
    web_app.app.dependency_overrides[get_servicio_documentos] = lambda: svc
    pila, c = _client_con_repo(repo_cxc)
    try:
        with pila, pytest.MonkeyPatch.context() as mp:
            mp.setenv("BOT_API_KEY", KEY)
            r_web = c.get("/api/documentos/listas-precio")
            assert r_web.status_code == 200
            listas_json = r_web.json()["items"]
            assert [x["id"] for x in listas_json] == ["21", "20"]
            assert listas_json[1]["ruta_pago"] == RUTA_BINANCE

            r_bot = c.get("/api/bots/listas-precio", headers=BOT)
            assert r_bot.status_code == 200
            assert len(r_bot.json()["items"]) == 2
    finally:
        web_app.app.dependency_overrides.pop(get_servicio_documentos, None)

    # 2. Crear cotización en VES ("21"), luego editar a USD ("20") -> recalcula precio unitario
    cot = svc.crear(
        {
            "tipo": COTIZACION,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "rif": "V-13069226",
            "email": "r@x.com",
            "telefono": "04120001122",
            "direccion": "Guatire",
            "ubicacion": UBICACION,
            "lista_precios_id": "21",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        },
        actor="vend@lubrikca.com",
    )
    assert cot.ruta_pago == RUTA_BCV
    assert cot.subtotal == Decimal("48.00")  # 2 * 24.00

    cot_editada = svc.editar(
        cot.documento_id,
        {"lista_precios_id": "20", "dias_credito": 21},
        actor="vend@lubrikca.com",
    )
    assert cot_editada.moneda_lista == "USD"
    assert cot_editada.ruta_pago == RUTA_BINANCE
    assert cot_editada.dias_credito == 21
    assert cot_editada.subtotal == Decimal("36.00")  # 2 * 18.00 recalculado dinámico!

    # 3. Convertir a pedido con descuento especial -> retiene en PENDIENTE_APROBACION
    ped = svc.convertir_en_pedido(
        cot.documento_id,
        {"descuentos_especiales": [{"motivo": "Bonificación comercial", "monto_usd": 3.0}]},
        actor="vend@lubrikca.com",
    )
    assert ped.estado == PENDIENTE_APROBACION
    assert ped.ruta_pago == RUTA_BINANCE
    assert ped.dias_credito == 21
    assert ped.total_descuentos == Decimal("3.00")

    # 4. Evaluar descuentos con politica_preview activa (sin swallow silencioso) y aprobar
    ev_prev = svc.evaluar_descuentos(
        {
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "lista_precios_id": "20",
            "dias_credito": 21,
            "descuentos_especiales": [{"motivo": "Bonificación comercial", "monto_usd": 3.0}],
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        }
    )
    assert ev_prev["politica_preview"] is not None
    assert ev_prev["politica_preview"]["puede_auto_enviar"] is False

    # 5. Aprobar -> envía a Odoo con pricelist_id=20, payment_term_id=7 y nota con ruta/crédito
    aprobado = svc.aprobar_pedido(ped.documento_id, actor="gerencia@lubrikca.com")
    assert aprobado.estado == ENVIADO_ODOO
    so_odoo = odoo.creados_de("sale.order")[0]
    assert so_odoo["pricelist_id"] == 20
    assert so_odoo["payment_term_id"] == 7
    assert "Binance" in so_odoo["note"]
    assert "21 día(s)" in so_odoo["note"]
    assert "Bonificación comercial" in so_odoo["note"]
