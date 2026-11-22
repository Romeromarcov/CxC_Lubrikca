"""Pruebas de Cotizaciones, Preview, Motor de Descuentos, Aprobación en CxC y Envío a Odoo."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.capturas.modelos import CapturaInvalida, validar
from cxc.capturas.servicio import ServicioCapturas
from cxc.documentos.catalogo import (
    ItemCatalogo,
    ListaComercialActiva,
    buscar_productos,
    elegir_lista_comercial_ves_activa,
    resolver_producto_unico,
)
from cxc.documentos.modelos import (
    BORRADOR,
    COTIZACION,
    ENVIADO_ODOO,
    PEDIDO,
    PENDIENTE_APROBACION,
    RECHAZADO,
    DocumentoComercial,
    DocumentoInvalido,
    LineaDocumento,
)
from cxc.documentos.servicio import DocumentoYaEnviado, ServicioDocumentos
from cxc.models import (
    Cliente,
    DescuentoDiferencialCambiario,
    DescuentoProntoPago,
    DescuentoRecompra,
    DescuentoVolumen,
    EstadoVinculacion,
    ExclusionRegla,
    Moneda,
    OrdenVenta,
    PromocionPrimeraCompra,
    TipoTasa,
    Vinculacion,
)
from cxc.odoo.directorio import LectorDirectorioOdoo, mapear_contacto
from cxc.odoo.escritura import EscritorOdoo
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from cxc.web.directorio_api import get_lector_odoo, limpiar_cache_directorio
from cxc.web.documentos_api import get_servicio_documentos
from tests.capturas_dobles import UBICACION, EspejoFalso, OdooFalso, RepoMemoria

client = TestClient(app)
KEY = "clave-bot-docs"
BOT = {"X-Bot-Key": KEY}
VENTAS_USER = {"email": "vendedor@lubrikca.com", "rol": "ventas", "permisos": efectivos("ventas")}
GERENTE_USER = {
    "email": "gerencia@lubrikca.com",
    "rol": "gerente_ventas",
    "permisos": efectivos("gerente_ventas"),
}

CATALOGO_PRUEBA = [
    ItemCatalogo(
        "1033",
        "0761",
        "LIGA PARA FRENOS DOT3 (1x12)",
        "GLOBAL OIL",
        "1X12",
        Decimal("3.5"),
        Decimal("18.00"),
        Decimal("24.00"),
    ),
    ItemCatalogo(
        "1034",
        "0762",
        "LIGA PARA FRENOS DOT3 (1x6)",
        "GLOBAL OIL",
        "1X6",
        Decimal("1.75"),
        Decimal("9.50"),
        Decimal("13.00"),
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
    ),
    ItemCatalogo(
        "1200",
        "0801",
        "ELITE API SP SAE 20W-50 (1x12)",
        "GLOBAL OIL",
        "1X12",
        Decimal("11.35"),
        Decimal("42.00"),
        Decimal("58.00"),
    ),
    ItemCatalogo(
        "1300",
        "0950",
        "HIDRAULICO ISO 68 (Paila)",
        "SINOCO",
        "PAILA",
        Decimal("19.0"),
        Decimal("55.00"),
        Decimal("76.00"),
    ),
]


class CatalogoMemoria:
    def __init__(self, items: Sequence[ItemCatalogo]) -> None:
        self.items = list(items)

    def listar(self, *, refresh: bool = False) -> list[ItemCatalogo]:
        return list(self.items)

    def lista_comercial_defecto(self, *, refresh: bool = False) -> ListaComercialActiva:
        return ListaComercialActiva("21", "Pago VES Nov 2026", "VES")


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
            descuentos_elegidos=tuple(cabecera.get("descuentos_elegidos") or ()),
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
        for d in self.docs.values():
            if d.token_preview and d.token_preview == token_preview:
                return d
        return None

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
            "pedidos_pendientes_aprobacion": sum(
                1 for d in vals if d.estado == "PENDIENTE_APROBACION"
            ),
            "pedidos_borrador": sum(
                1
                for d in vals
                if d.estado in ("BORRADOR", "PENDIENTE_APROBACION") and d.tipo == "pedido"
            ),
            "pedidos_auto_enviados": sum(
                1 for d in vals if d.estado == "ENVIADO_ODOO" and d.auto_enviado
            ),
            "enviados_odoo": sum(1 for d in vals if d.estado == "ENVIADO_ODOO"),
            "rechazados": sum(1 for d in vals if d.estado == "RECHAZADO"),
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
            token_preview=str(cabecera.get("token_preview", actual.token_preview)),
            lista_precios_id=str(cabecera.get("lista_precios_id", actual.lista_precios_id)),
            lista_precios_nombre=str(
                cabecera.get("lista_precios_nombre", actual.lista_precios_nombre)
            ),
            descuentos_elegidos=tuple(
                cabecera.get("descuentos_elegidos", actual.descuentos_elegidos)
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


def _armar_repo_motor_prueba() -> InMemoryRepository:
    """Siembra el catálogo real de reglas de descuento e historial de clientes en memoria."""
    repo = InMemoryRepository()
    repo.set_config("valid_pricelists_ves", "10,21")
    repo.set_config("valid_pricelists_usd", "11,20")

    # Reglas de descuento reales de CxC
    repo.append_descuento_pronto_pago(
        DescuentoProntoPago(
            regla_id="PP_GLOBAL_CAJA_08",
            marca="GLOBAL OIL",
            categoria="CAJA",
            porcentaje=Decimal("0.08"),
            monedas_aplicables="USD",
            ventana_pago_tipo="entrega",
            ventana_pago_dias=3,
            descripcion="Pronto Pago 8% Cajas Global Oil",
        )
    )
    repo.append_descuento_pronto_pago(
        DescuentoProntoPago(
            regla_id="PP_VES_20",
            marca="*",
            categoria="*",
            porcentaje=Decimal("0.20"),
            monedas_aplicables="VES",
            listas_aplicables="LISTAS_VES",
            listas_excluidas="LISTAS_USD",
            ventana_pago_tipo="entrega",
            ventana_pago_dias=3,
            descripcion="Pronto Pago 20% VES",
        )
    )
    repo.append_descuento_volumen(
        DescuentoVolumen(
            regla_id="VOL_SINOCO_PAILA_1",
            marca="SINOCO",
            categoria="PAILA",
            porcentaje=Decimal("0.0452"),
            min_unidades=Decimal("10"),
            max_unidades=Decimal("19"),
            unidad_medida="UNIDADES",
            descripcion="Volumen Sinoco Paila 10-19 (4.52%)",
        )
    )
    repo.append_promocion_primera_compra(
        PromocionPrimeraCompra(
            regla_id="PRIMERA_COMPRA_COMERCIAL_2PCT",
            tipo_beneficio="porcentaje",
            valor=Decimal("0.02"),
            compra_minima=Decimal("1"),
            marca="GLOBAL OIL",
            categoria="CAJA",
            unidad_medida="CAJAS",
            solo_primera_compra=True,
            descripcion="2% Descuento Primera Compra",
        )
    )
    repo.append_descuento_recompra(
        DescuentoRecompra(
            regla_id="REC_GLOBAL_TRAMO1",
            marca="GLOBAL OIL",
            categoria="CAJA",
            min_unidades=Decimal("2"),
            max_unidades=Decimal("4"),
            unidad_medida="CAJAS",
            porcentaje=Decimal("0.03"),
            ventana_pago_tipo="vencimiento",
            ventana_pago_dias=5,
            descripcion="Recompra 3% (2-4 cajas)",
        )
    )
    repo.append_descuento_diferencial_cambiario(
        DescuentoDiferencialCambiario(
            regla_id="DIF_35_VES",
            tipo_diferencial="fijo_35_ves_usd",
            porcentaje_fijo=Decimal("0.35"),
            listas_aplicables="LISTAS_VES",
            listas_excluidas="LISTAS_USD",
            descripcion="35% Fijo VES a USD",
        )
    )
    repo.save_exclusion(ExclusionRegla(regla_tipo_a="primera_compra", regla_tipo_b="recurrencia"))
    repo.save_exclusion(ExclusionRegla(regla_tipo_a="contado_ves", regla_tipo_b="bcv_completo"))

    # Cliente 88 (Ronald Socorro): YA tiene orden previa pagada -> NO 1ra compra, SÍ Recompra
    hoy = date.today()
    repo.upsert_clientes(
        [Cliente(cliente_id="88", nombre="Ronald Socorro", vendedor_email="v@x.com")]
    )
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S00500",
                cliente_id="88",
                fecha=hoy,
                fecha_entrega=hoy,
                monto_total=Decimal("100.00"),
                lista_precios="21",
                vendedor_email="v@x.com",
                es_primera_compra=True,
                dias_credito=15,
            )
        ]
    )
    repo.add_vinculacion(
        Vinculacion(
            vinc_id="V-500",
            pago_id="P-500",
            so_id="S00500",
            monto_aplicado=Decimal("100.00"),
            hora_pago_confirmada=datetime.now(),
            tasa_bcv_aplicada=Decimal("40"),
            tasa_binance_aplicada=Decimal("55"),
            es_tasa_heredada=False,
            equiv_usd_bcv=Decimal("100.00"),
            equiv_usd_binance=Decimal("100.00"),
            estado=EstadoVinculacion.CONCILIADO,
            moneda_abono=Moneda.USD,
            tipo_tasa_abono=TipoTasa.N_A,
        )
    )
    return repo


# ── Lista Comercial por Defecto y Catálogo ───────────────────────────────────


def test_seleccion_lista_comercial_ves_por_defecto() -> None:
    listas_prod = [
        {"id": 10, "name": "Pago VES Sept 2026", "active": False},
        {"id": 20, "name": "Pago USD Nov 2026", "active": True},
        {"id": 21, "name": "Pago VES Nov 2026", "active": True},
        {"id": 23, "name": "Pago VES Foranea Nov 2026", "active": True},
        {"id": 27, "name": "Industrial 3% VES Nov 2026", "active": True},
    ]
    elegida = elegir_lista_comercial_ves_activa(listas_prod)
    assert elegida.lista_id == "21" and elegida.nombre == "Pago VES Nov 2026"

    listas_qa = [
        {"id": 10, "name": "Pago VES Sept 2026", "active": True},
        {"id": 11, "name": "Pago USD Sept 2026", "active": True},
    ]
    elegida_qa = elegir_lista_comercial_ves_activa(listas_qa)
    assert elegida_qa.lista_id == "10" and elegida_qa.nombre == "Pago VES Sept 2026"


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


# ── Motor de Descuentos en Cotizaciones ──────────────────────────────────────


def test_evaluador_descuentos_valida_primera_compra_recompra_volumen_y_exclusiones() -> None:
    repo_cxc = _armar_repo_motor_prueba()
    svc = ServicioDocumentos(
        RepoDocumentosMemoria(), CatalogoMemoria(CATALOGO_PRUEBA), lambda: None, repo_cxc=repo_cxc
    )

    cat_desc = svc.catalogo_descuentos()
    assert len(cat_desc) == 6

    # Caso A: Cliente 88 ya tiene S00500 -> PRIMERA_COMPRA_COMERCIAL_2PCT alerta incumplimiento!
    ev_falla_1ra = svc.evaluar_descuentos(
        {
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "descuentos_codigos": ["PRIMERA_COMPRA_COMERCIAL_2PCT"],
            "lineas": [{"codigo": "0761", "cantidad": 3}],
        }
    )
    assert ev_falla_1ra["cumple_todo"] is False
    assert any("no califica como primera compra" in a for a in ev_falla_1ra["alertas"])

    # Caso B: Cliente 88 con 3 cajas y S00500 pagada -> REC_GLOBAL_TRAMO1 (3%) CUMPLE sin alertas!
    ev_ok_recompra = svc.evaluar_descuentos(
        {
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "descuentos_codigos": ["REC_GLOBAL_TRAMO1"],
            "lineas": [{"codigo": "0761", "cantidad": 3}],  # 3 * $24 = $72.00
        }
    )
    assert ev_ok_recompra["cumple_todo"] is True and ev_ok_recompra["alertas"] == []
    assert ev_ok_recompra["subtotal_lista"] == 72.0
    assert ev_ok_recompra["total_descuentos"] == 2.16  # 3% de 72
    assert ev_ok_recompra["subtotal_neto"] == 69.84

    # Caso C: Volumen Sinoco exige mín. 10 pailas -> con 4 pailas alerta incumplimiento
    ev_vol_bajo = svc.evaluar_descuentos(
        {
            "cliente_nombre": "Industria Nueva",
            "descuentos_codigos": ["VOL_SINOCO_PAILA_1"],
            "lineas": [{"codigo": "0950", "cantidad": 4}],
        }
    )
    assert ev_vol_bajo["cumple_todo"] is False
    assert any("No alcanza el umbral mínimo de 10" in a for a in ev_vol_bajo["alertas"])

    # Caso D: Exclusión mutua (PP_VES_20 vs DIF_35_VES) -> alerta y anula el menor
    ev_excl = svc.evaluar_descuentos(
        {
            "cliente_nombre": "Prospecto Nuevo",
            "descuentos_codigos": ["PP_VES_20", "DIF_35_VES"],
            "lineas": [{"codigo": "0761", "cantidad": 5}],  # 5 * $24 = $120.00
        }
    )
    assert ev_excl["cumple_todo"] is False
    assert any("Exclusión mutua" in a for a in ev_excl["alertas"])
    # Solo suma el ganador (35% de $120 = $42.00), no 55%
    assert ev_excl["total_descuentos"] == 42.0


# ── Servicio: Precios Normales en Odoo + Nota Completa en Chatter ────────────


def test_pedido_aprobado_va_a_odoo_sin_descuento_en_lineas_y_con_nota_en_chatter() -> None:
    odoo = OdooFalso(
        res_partner__read=[
            {"id": 88, "name": "Ronald Socorro", "vat": "V-13069226", "customer_rank": 0}
        ]
    )
    repo_docs = RepoDocumentosMemoria()
    repo_cxc = _armar_repo_motor_prueba()
    svc = ServicioDocumentos(
        repo_docs, CatalogoMemoria(CATALOGO_PRUEBA), lambda: EscritorOdoo(odoo), repo_cxc=repo_cxc
    )

    # 1. Crear cotización con lista comercial por defecto (Pago VES Nov 2026) y Recompra 3% + PP 8%
    cot = svc.crear(
        {
            "tipo": COTIZACION,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "direccion": "Guatire",
            "ubicacion": UBICACION,
            "descuentos_codigos": ["REC_GLOBAL_TRAMO1", "PP_GLOBAL_CAJA_08"],
            "condiciones_negociacion": "Pago divisas a 3 días de entrega",
            "lineas": [{"codigo": "0761", "cantidad": 3}],  # 3 * $24.00 = $72.00
        }
    )
    assert cot.numero == "COT-00001" and cot.estado == BORRADOR
    assert cot.lista_precios_nombre == "Pago VES Nov 2026"
    assert cot.token_preview != ""
    assert cot.subtotal == Decimal("72.00")
    assert cot.total_descuentos == Decimal("7.92")  # 3% ($2.16) + 8% ($5.76)
    assert cot.subtotal_neto == Decimal("64.08")
    assert odoo.creados == []

    # 2. Convertir en pedido -> pasa a PENDIENTE_APROBACION en CxC (sin tocar Odoo todavía)
    ped = svc.convertir_por_token(cot.token_preview)
    assert ped.numero == "PED-00001" and ped.estado == PENDIENTE_APROBACION
    assert odoo.creados == []

    # 3. Autorizar en CxC -> envía a Odoo con precios normales (SIN discount en línea) + chatter
    aprobado = svc.aprobar_pedido(ped.documento_id, actor="gerencia@lubrikca.com")
    assert aprobado.estado == ENVIADO_ODOO
    assert aprobado.aprobado_por == "gerencia@lubrikca.com"
    assert aprobado.odoo_order_name == "S00700"

    orden_odoo = odoo.creados_de("sale.order")[0]
    assert orden_odoo["pricelist_id"] == 21
    linea_odoo = orden_odoo["order_line"][0][2]
    assert linea_odoo["product_id"] == 1033
    assert linea_odoo["price_unit"] == 24.0  # Precio normal de Pago VES Nov 2026
    assert "discount" not in linea_odoo  # NUNCA se inserta descuento en las líneas de Odoo

    # El detalle completo de descuentos quedó tanto en note como en el chatter de sale.order
    chatter_so = [txt for mod, _, txt in odoo.chatter if mod == "sale.order"]
    assert len(chatter_so) == 1
    assert "REC_GLOBAL_TRAMO1" in chatter_so[0]
    assert "PP_GLOBAL_CAJA_08" in chatter_so[0]
    assert "NO insertados en líneas de Odoo" in chatter_so[0]
    assert "gerencia@lubrikca.com" in chatter_so[0]

    # En CxC los descuentos sí permanecen guardados en el documento
    guardado_cxc = svc.obtener(ped.documento_id)
    assert guardado_cxc.total_descuentos == Decimal("7.92")
    assert len(guardado_cxc.descuentos_elegidos) == 2

    with pytest.raises(DocumentoYaEnviado):
        svc.editar(ped.documento_id, {"observaciones": "cambio tardío"})


def test_cliente_nuevo_sin_ficha_queda_pendiente_pero_exige_datos_al_aprobar() -> None:
    odoo = OdooFalso()
    svc = ServicioDocumentos(
        RepoDocumentosMemoria(), CatalogoMemoria(CATALOGO_PRUEBA), lambda: EscritorOdoo(odoo)
    )
    ped = svc.crear(
        {
            "tipo": PEDIDO,
            "cliente_nombre": "Prospecto Sin RIF",
            "lineas": [{"producto_id": "1077", "cantidad": 1}],
        }
    )
    assert ped.estado == PENDIENTE_APROBACION
    with pytest.raises(DocumentoInvalido) as exc:
        svc.aprobar_pedido(ped.documento_id, actor="gerencia@lubrikca.com")
    assert set(exc.value.faltan) >= {"rif", "email", "direccion", "ubicacion"}


# ── Integración End-to-End: Bot + Preview HTML + Conversión + Aprobación ────


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
        ],
        res_partner__read=[
            {"id": 88, "name": "Ronald Socorro", "vat": "V-13069226", "customer_rank": 0}
        ],
    )
    repo_cap = RepoMemoria()
    espejo = EspejoFalso(productos={"0761": ["1033"], "LIGA PARA FRENOS DOT3 (1x12)": ["1033"]})
    svc_cap = ServicioCapturas(repo_cap, espejo, lambda: EscritorOdoo(odoo))
    repo_docs = RepoDocumentosMemoria()
    repo_cxc = _armar_repo_motor_prueba()
    svc_docs = ServicioDocumentos(
        repo_docs,
        CatalogoMemoria(CATALOGO_PRUEBA),
        lambda: EscritorOdoo(odoo),
        repo_cxc=repo_cxc,
    )
    app.dependency_overrides[get_servicio] = lambda: svc_cap
    app.dependency_overrides[get_servicio_documentos] = lambda: svc_docs
    app.dependency_overrides[get_lector_odoo] = lambda: LectorDirectorioOdoo(odoo)
    yield svc_docs, repo_docs, odoo
    app.dependency_overrides.pop(get_servicio, None)
    app.dependency_overrides.pop(get_servicio_documentos, None)
    app.dependency_overrides.pop(get_lector_odoo, None)
    limpiar_cache_directorio()


def test_flujo_completo_bot_preview_conversion_permisos_y_aprobacion(
    entorno_docs: tuple[ServicioDocumentos, RepoDocumentosMemoria, OdooFalso],
) -> None:
    _, repo_docs, odoo = entorno_docs

    # 1. Bot consulta catálogo de productos (con lista comercial por defecto) y descuentos
    r_cat = client.get("/api/bots/catalogo?q=0761", headers=BOT)
    assert r_cat.status_code == 200
    assert r_cat.json()["lista_defecto"]["nombre"] == "Pago VES Nov 2026"
    assert r_cat.json()["unico"]["precio_ves"] == 24.0

    r_desc_cat = client.get("/api/bots/descuentos/catalogo", headers=BOT)
    assert r_desc_cat.status_code == 200 and r_desc_cat.json()["total"] == 6

    # 2. Bot evalúa un descuento que NO cumple (1ra compra a cliente previo) -> alerta
    r_eval_bad = client.post(
        "/api/bots/descuentos/evaluar",
        headers=BOT,
        json={
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "descuentos_codigos": ["PRIMERA_COMPRA_COMERCIAL_2PCT"],
            "lineas": [{"producto_id": "1033", "cantidad": 3}],
        },
    )
    assert r_eval_bad.status_code == 200
    assert r_eval_bad.json()["cumple_todo"] is False
    assert len(r_eval_bad.json()["alertas"]) >= 1

    # 3. Bot crea Cotización con descuento válido -> recibe totales + link de Preview
    r_cot = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "cotizacion",
            "draft": {
                "cliente": "Ronald Socorro",
                "partner_odoo_id": 88,
                "descuentos_codigos": ["REC_GLOBAL_TRAMO1"],
                "condiciones_negociacion": "Recompra 3% validada por motor",
                "lineas": [
                    {
                        "producto_id": "1033",
                        "producto": "LIGA PARA FRENOS DOT3 (1x12)",
                        "cantidad": "3",
                    }
                ],
            },
            "source": {"vendedor_email": "vendedor@lubrikca.com"},
            "idempotency_key": "cot-preview-1",
        },
    )
    assert r_cot.status_code == 201
    res_cot = r_cot.json()["resultado"]
    assert res_cot["documento_numero"] == "COT-00001"
    assert res_cot["lista_precios_nombre"] == "Pago VES Nov 2026"
    assert res_cot["subtotal"] == 72.0
    assert res_cot["total_descuentos"] == 2.16
    assert res_cot["preview_path"].startswith("/cotizacion/")
    preview_path = res_cot["preview_path"]

    # 4. Abrir el enlace del Preview HTML (sin sesión) -> vista solo lectura sin conversión pública
    r_html = client.get(preview_path)
    assert r_html.status_code == 200
    assert "COT-00001" in r_html.text
    assert "Pago VES Nov 2026" in r_html.text
    assert "solo lectura" in r_html.text.lower()

    # 5. Enlace público bloqueado para mutación (403); conversión pasa por vendedor autenticado
    r_conv_pub = client.post(
        f"{preview_path}/convertir-pedido",
        json={"condiciones_negociacion": "Intento sin sesión"},
    )
    assert r_conv_pub.status_code == 403
    assert len(odoo.creados_de("sale.order")) == 0

    with patch("cxc.web.app.get_current_user_from_cookie", return_value=VENTAS_USER):
        r_conv_prev = client.post(
            "/api/documentos/1/convertir-pedido",
            json={"condiciones_negociacion": "Confirmado por vendedor en CxC"},
        )
    assert r_conv_prev.status_code == 200
    assert r_conv_prev.json()["numero"] == "PED-00001"
    assert r_conv_prev.json()["estado"] == PENDIENTE_APROBACION
    assert len(odoo.creados_de("sale.order")) == 0

    # 6. Un vendedor sin permiso `ventas.aprobar_pedidos` NO puede autorizar el pedido (403)
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=VENTAS_USER):
        r_list_v = client.get("/api/documentos")
        assert r_list_v.status_code == 200 and r_list_v.json()["puede_aprobar"] is False
        assert client.post("/api/documentos/1/aprobar").status_code == 403
        assert client.post("/api/documentos/1/enviar-odoo").status_code == 403

    # 7. Gerente / persona autorizada aprueba en CxC -> envía a Odoo sin discount en línea + chatter
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=GERENTE_USER):
        r_list_g = client.get("/api/documentos")
        assert r_list_g.status_code == 200 and r_list_g.json()["puede_aprobar"] is True
        r_aprob = client.post("/api/documentos/1/aprobar")
        assert r_aprob.status_code == 200
        assert r_aprob.json()["estado"] == ENVIADO_ODOO
        assert r_aprob.json()["odoo_order_name"] == "S00700"

    so_creada = odoo.creados_de("sale.order")[0]
    assert so_creada["pricelist_id"] == 21
    ln_so = so_creada["order_line"][0][2]
    assert ln_so["price_unit"] == 24.0 and "discount" not in ln_so
    assert any("REC_GLOBAL_TRAMO1" in txt for mod, _, txt in odoo.chatter if mod == "sale.order")

    # 8. También probar conversión a Pedido directamente desde el botón del Bot de Telegram
    r_cot2 = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "cotizacion",
            "draft": {
                "cliente": "Ronald Socorro",
                "partner_odoo_id": 88,
                "lineas": [{"producto_id": "1033", "producto": "DOT3", "cantidad": "2"}],
            },
            "source": {"vendedor_email": "vendedor@lubrikca.com"},
            "idempotency_key": "cot-bot-btn-2",
        },
    )
    doc2_id = r_cot2.json()["resultado"]["documento_id"]
    r_conv_bot = client.post(
        f"/api/bots/documentos/{doc2_id}/convertir-pedido",
        headers=BOT,
        json={"vendedor_email": "vendedor@lubrikca.com"},
    )
    assert r_conv_bot.status_code == 200
    assert r_conv_bot.json()["numero"] == f"PED-{doc2_id:05d}"
    assert r_conv_bot.json()["estado"] == PENDIENTE_APROBACION

    # Y probar rechazo por persona autorizada
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=GERENTE_USER):
        r_rech = client.post(
            f"/api/documentos/{doc2_id}/rechazar", json={"motivo": "Sin disponibilidad"}
        )
        assert r_rech.status_code == 200 and r_rech.json()["estado"] == RECHAZADO


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
                marca="GLOBAL OIL",
                volumen=Decimal("3.5"),
                peso=Decimal("4.0"),
                unidades_por_paleta=Decimal("50"),
            )
        )
    try:
        CatalogoSQLOdoo.limpiar_cache()
        cat = CatalogoSQLOdoo(eng, lambda: {"999901": (Decimal("19.50"), Decimal("25.00"))})
        repo = RepoDocumentosSQL(eng)
        repo_cxc = _armar_repo_motor_prueba()
        svc = ServicioDocumentos(repo, cat, lambda: None, repo_cxc=repo_cxc)

        creado = svc.crear(
            {
                "tipo": "cotizacion",
                "cliente_nombre": "Prospecto SQL C.A.",
                "descuentos_codigos": ["PRIMERA_COMPRA_COMERCIAL_2PCT"],
                "lineas": [{"codigo": "TEST-0761", "cantidad": "3"}],
            },
            origen="bot",
            captura_id=777,
            actor="bot",
        )
        assert creado.numero == "COT-00001"
        assert creado.moneda_lista == "VES"
        assert creado.subtotal == Decimal("75.00")
        assert creado.total_descuentos == Decimal("1.50")  # 2% primera compra
        assert creado.subtotal_neto == Decimal("73.50")
        assert creado.token_preview != ""

        por_tok = repo.obtener_por_token(creado.token_preview)
        assert por_tok is not None and por_tok.documento_id == creado.documento_id

        convertido = svc.convertir_en_pedido(creado.documento_id, actor="bot")
        assert convertido.numero == "PED-00001" and convertido.estado == PENDIENTE_APROBACION

        lista, total, res = svc.listar(q="Prospecto SQL")
        assert total == 1 and res["pedidos_pendientes_aprobacion"] == 1 and len(lista) == 1
    finally:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE documentos_comerciales_lineas, documentos_comerciales "
                    "RESTART IDENTITY CASCADE"
                )
            )
            conn.execute(delete(schema.catalogo).where(schema.catalogo.c.producto_id == "999901"))
