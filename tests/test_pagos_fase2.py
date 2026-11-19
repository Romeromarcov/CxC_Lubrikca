"""Pruebas de Fase 2: Pagos Unificados (CxC + Odoo), CxC-first, Recibos y Zelle de Terceros."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.config import OdooConfig
from cxc.models import Cliente, MetodoPago, Moneda, OrdenVenta, Pago, SerieTasa, TipoTasa
from cxc.odoo.client import OdooXmlRpcReader
from cxc.odoo.directorio import mapear_contacto, mapear_cuenta
from cxc.odoo.escritura import EscritorOdoo, OdooEscrituraError
from cxc.operativos.modelos import (
    CONFIRMADO_CXC,
    ENVIADO_ODOO,
    PENDIENTE_APROBACION,
    PREFIJO_POR_TIPO,
    DocumentoOperativo,
    OperativoInvalido,
    OperativoNoEncontrado,
)
from cxc.operativos.repositorio import _cargar_json_dict, _cargar_json_list
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import CuentaTercero
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web.app import app
from cxc.web.operativos_api import get_repo_terceros, get_servicio_operativos

client = TestClient(app)

VENTAS_USER = {"email": "vendedor@lubrikca.com", "rol": "ventas", "permisos": efectivos("ventas")}
TESO_USER = {
    "email": "tesoreria@lubrikca.com",
    "rol": "tesoreria",
    "permisos": efectivos("tesoreria"),
}
ADMIN_USER = {"email": "admin@lubrikca.com", "rol": "admin", "permisos": efectivos("admin")}


class RepoOperativosMemF2:
    def __init__(self) -> None:
        self.filas: dict[int, DocumentoOperativo] = {}
        self._sig = 1
        self._cons: dict[str, int] = {}

    def crear(self, campos: dict[str, Any]) -> DocumentoOperativo:
        tipo = str(campos["tipo"])
        pref = PREFIJO_POR_TIPO[tipo]
        n = self._cons.get(pref, 0) + 1
        self._cons[pref] = n
        ahora = datetime.now()
        doc_id = self._sig
        self._sig += 1
        doc = DocumentoOperativo(
            documento_id=doc_id,
            numero=f"{pref}-{n:05d}",
            token_preview=str(campos["token_preview"]),
            tipo=tipo,
            estado=str(campos.get("estado") or PENDIENTE_APROBACION),
            origen=str(campos.get("origen") or "web"),
            captura_id=campos.get("captura_id"),
            partner_odoo_id=campos.get("partner_odoo_id"),
            contraparte_nombre=str(campos.get("contraparte_nombre") or ""),
            rif=str(campos.get("rif") or ""),
            monto=campos.get("monto"),
            base_imponible=campos.get("base_imponible"),
            iva=campos.get("iva"),
            moneda=str(campos.get("moneda") or ""),
            metodo_pago=str(campos.get("metodo_pago") or ""),
            banco=str(campos.get("banco") or ""),
            diario_id=campos.get("diario_id"),
            diario_nombre=str(campos.get("diario_nombre") or ""),
            cuenta_contable_id=campos.get("cuenta_contable_id"),
            cuenta_contable_codigo=str(campos.get("cuenta_contable_codigo") or ""),
            cuenta_contable_nombre=str(campos.get("cuenta_contable_nombre") or ""),
            referencia=str(campos.get("referencia") or ""),
            numero_documento=str(campos.get("numero_documento") or ""),
            numero_control=str(campos.get("numero_control") or ""),
            pedido_ref=str(campos.get("pedido_ref") or ""),
            factura_ref=str(campos.get("factura_ref") or ""),
            orden_ref=str(campos.get("orden_ref") or ""),
            categoria=str(campos.get("categoria") or ""),
            concepto=str(campos.get("concepto") or ""),
            sin_factura=bool(campos.get("sin_factura")),
            fecha_documento=campos.get("fecha_documento"),
            fecha_vencimiento=campos.get("fecha_vencimiento"),
            proveedor_telefono=str(campos.get("proveedor_telefono") or ""),
            proveedor_email=str(campos.get("proveedor_email") or ""),
            proveedor_direccion=str(campos.get("proveedor_direccion") or ""),
            notas=str(campos.get("notas") or ""),
            validacion=_cargar_json_dict(campos.get("validacion")),
            faltantes=_cargar_json_list(campos.get("faltantes")),
            aprobado_por=str(campos.get("aprobado_por") or ""),
            aprobado_en=campos.get("aprobado_en"),
            rechazo_motivo=str(campos.get("rechazo_motivo") or ""),
            odoo_model=str(campos.get("odoo_model") or ""),
            odoo_res_id=campos.get("odoo_res_id"),
            odoo_ref=str(campos.get("odoo_ref") or ""),
            error_odoo=str(campos.get("error_odoo") or ""),
            creado_por=str(campos.get("creado_por") or "sistema"),
            creado_en=ahora,
            actualizado_en=ahora,
            es_zelle_tercero=bool(campos.get("es_zelle_tercero")),
            cuenta_tercero_id=campos.get("cuenta_tercero_id"),
            beneficiario_tercero=str(campos.get("beneficiario_tercero") or ""),
            confirmado_cxc=bool(campos.get("confirmado_cxc")),
            confirmado_cxc_por=str(campos.get("confirmado_cxc_por") or ""),
            confirmado_cxc_en=campos.get("confirmado_cxc_en"),
            numero_recibo=str(campos.get("numero_recibo") or ""),
            recibo_emitido_en=campos.get("recibo_emitido_en"),
        )
        self.filas[doc_id] = doc
        return doc

    def obtener(self, documento_id: int) -> DocumentoOperativo | None:
        return self.filas.get(documento_id)

    def obtener_por_token(self, token_preview: str) -> DocumentoOperativo | None:
        return next((d for d in self.filas.values() if d.token_preview == token_preview), None)

    def obtener_por_captura(self, captura_id: int) -> DocumentoOperativo | None:
        return next((d for d in self.filas.values() if d.captura_id == captura_id), None)

    def actualizar(self, documento_id: int, campos: dict[str, Any]) -> DocumentoOperativo:
        d = self.filas[documento_id]
        nuevo = DocumentoOperativo(
            documento_id=d.documento_id,
            numero=d.numero,
            token_preview=d.token_preview,
            tipo=d.tipo,
            estado=campos.get("estado", d.estado),
            origen=d.origen,
            captura_id=d.captura_id,
            partner_odoo_id=campos.get("partner_odoo_id", d.partner_odoo_id),
            contraparte_nombre=campos.get("contraparte_nombre", d.contraparte_nombre),
            rif=campos.get("rif", d.rif),
            monto=campos.get("monto", d.monto),
            base_imponible=campos.get("base_imponible", d.base_imponible),
            iva=campos.get("iva", d.iva),
            moneda=campos.get("moneda", d.moneda),
            metodo_pago=campos.get("metodo_pago", d.metodo_pago),
            banco=campos.get("banco", d.banco),
            diario_id=campos.get("diario_id", d.diario_id),
            diario_nombre=campos.get("diario_nombre", d.diario_nombre),
            cuenta_contable_id=campos.get("cuenta_contable_id", d.cuenta_contable_id),
            cuenta_contable_codigo=campos.get("cuenta_contable_codigo", d.cuenta_contable_codigo),
            cuenta_contable_nombre=campos.get("cuenta_contable_nombre", d.cuenta_contable_nombre),
            referencia=campos.get("referencia", d.referencia),
            numero_documento=campos.get("numero_documento", d.numero_documento),
            numero_control=campos.get("numero_control", d.numero_control),
            pedido_ref=campos.get("pedido_ref", d.pedido_ref),
            factura_ref=campos.get("factura_ref", d.factura_ref),
            orden_ref=campos.get("orden_ref", d.orden_ref),
            categoria=campos.get("categoria", d.categoria),
            concepto=campos.get("concepto", d.concepto),
            sin_factura=campos.get("sin_factura", d.sin_factura),
            fecha_documento=campos.get("fecha_documento", d.fecha_documento),
            fecha_vencimiento=campos.get("fecha_vencimiento", d.fecha_vencimiento),
            proveedor_telefono=campos.get("proveedor_telefono", d.proveedor_telefono),
            proveedor_email=campos.get("proveedor_email", d.proveedor_email),
            proveedor_direccion=campos.get("proveedor_direccion", d.proveedor_direccion),
            notas=campos.get("notas", d.notas),
            validacion=(
                _cargar_json_dict(campos["validacion"]) if "validacion" in campos else d.validacion
            ),
            faltantes=(
                _cargar_json_list(campos["faltantes"]) if "faltantes" in campos else d.faltantes
            ),
            aprobado_por=campos.get("aprobado_por", d.aprobado_por),
            aprobado_en=campos.get("aprobado_en", d.aprobado_en),
            rechazo_motivo=campos.get("rechazo_motivo", d.rechazo_motivo),
            odoo_model=campos.get("odoo_model", d.odoo_model),
            odoo_res_id=campos.get("odoo_res_id", d.odoo_res_id),
            odoo_ref=campos.get("odoo_ref", d.odoo_ref),
            error_odoo=campos.get("error_odoo", d.error_odoo),
            creado_por=d.creado_por,
            creado_en=d.creado_en,
            actualizado_en=datetime.now(),
            es_zelle_tercero=campos.get("es_zelle_tercero", d.es_zelle_tercero),
            cuenta_tercero_id=campos.get("cuenta_tercero_id", d.cuenta_tercero_id),
            beneficiario_tercero=campos.get("beneficiario_tercero", d.beneficiario_tercero),
            confirmado_cxc=campos.get("confirmado_cxc", d.confirmado_cxc),
            confirmado_cxc_por=campos.get("confirmado_cxc_por", d.confirmado_cxc_por),
            confirmado_cxc_en=campos.get("confirmado_cxc_en", d.confirmado_cxc_en),
            numero_recibo=campos.get("numero_recibo", d.numero_recibo),
            recibo_emitido_en=campos.get("recibo_emitido_en", d.recibo_emitido_en),
        )
        self.filas[documento_id] = nuevo
        return nuevo

    def listar(
        self,
        *,
        tipo: str | None = None,
        grupo: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        es_zelle_tercero: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentoOperativo], int, dict[str, int]]:
        base = list(reversed(list(self.filas.values())))
        if tipo:
            base = [d for d in base if d.tipo == tipo]
        elif grupo == "cobranza":
            base = [d for d in base if d.tipo == "pago"]
        elif grupo == "pagos_enviados":
            base = [d for d in base if d.tipo == "pago_proveedor"]
        resumen = {
            "total": len(base),
            "pendientes_aprobacion": sum(1 for d in base if d.estado == PENDIENTE_APROBACION),
            "confirmados_cxc": sum(1 for d in base if d.estado == CONFIRMADO_CXC),
            "incompletos": 0,
            "enviados_odoo": sum(1 for d in base if d.estado == ENVIADO_ODOO),
            "rechazados": 0,
            "errores": 0,
        }
        if es_zelle_tercero is not None:
            base = [d for d in base if d.es_zelle_tercero is es_zelle_tercero]
        if estado:
            base = [d for d in base if d.estado == estado]
        if q and q.strip():
            qn = q.strip().lower()
            base = [
                d
                for d in base
                if qn
                in f"{d.numero} {d.numero_recibo} {d.contraparte_nombre} {d.referencia}".lower()
            ]
        return base[offset : offset + limit], len(base), resumen

    def siguiente_numero_recibo(self, prefijo: str, fecha: date) -> str:
        base = f"{prefijo}-{fecha.strftime('%Y%m%d')}-"
        nums = [
            int(d.numero_recibo[len(base) :])
            for d in self.filas.values()
            if d.numero_recibo.startswith(base) and d.numero_recibo[len(base) :].isdigit()
        ]
        return f"{base}{(max(nums) + 1) if nums else 1:03d}"

    def buscar_duplicados_referencia(
        self, tipo: str, referencia: str, *, excluir_id: int | None = None
    ) -> list[DocumentoOperativo]:
        return []

    def buscar_duplicados_factura(
        self,
        proveedor: str,
        rif: str,
        numero_documento: str,
        *,
        excluir_id: int | None = None,
    ) -> list[DocumentoOperativo]:
        return []


class RepoTercerosMem:
    def __init__(self) -> None:
        self.cuentas: dict[int, CuentaTercero] = {}
        self._sig = 1

    def listar(self, *, solo_activas: bool = False) -> list[CuentaTercero]:
        vals = list(self.cuentas.values())
        return [c for c in vals if c.activo] if solo_activas else vals

    def obtener(self, cuenta_id: int) -> CuentaTercero | None:
        return self.cuentas.get(cuenta_id)

    def crear(self, datos: dict[str, Any], *, actor: str = "") -> CuentaTercero:
        alias = str(datos.get("alias") or "").strip()
        if not alias:
            raise OperativoInvalido("El alias es obligatorio.", ["alias"])
        if any(c.alias.lower() == alias.lower() for c in self.cuentas.values()):
            raise OperativoInvalido(f"Alias duplicado: {alias}", ["alias"])
        prov = datos.get("proveedor_odoo_id")
        if not prov:
            raise OperativoInvalido("Proveedor requerido.", ["proveedor_odoo_id"])
        cid = self._sig
        self._sig += 1
        ahora = datetime.now()
        c = CuentaTercero(
            cuenta_id=cid,
            alias=alias,
            titular=str(datos.get("titular") or ""),
            correo_telefono=str(datos.get("correo_telefono") or ""),
            proveedor_odoo_id=int(prov),
            proveedor_nombre=str(datos.get("proveedor_nombre") or ""),
            diario_odoo_id=int(datos["diario_odoo_id"]) if datos.get("diario_odoo_id") else None,
            diario_odoo_nombre=str(datos.get("diario_odoo_nombre") or ""),
            cuenta_cxp_override_id=(
                int(datos["cuenta_cxp_override_id"])
                if datos.get("cuenta_cxp_override_id")
                else None
            ),
            cuenta_cxc_override_id=(
                int(datos["cuenta_cxc_override_id"])
                if datos.get("cuenta_cxc_override_id")
                else None
            ),
            moneda=str(datos.get("moneda") or "USD").upper(),
            notas=str(datos.get("notas") or ""),
            activo=bool(datos.get("activo", True)),
            creado_por=actor or "sistema",
            creado_en=ahora,
            actualizado_en=ahora,
        )
        self.cuentas[cid] = c
        return c

    def actualizar(self, cuenta_id: int, cambios: dict[str, Any]) -> CuentaTercero:
        c = self.cuentas.get(cuenta_id)
        if c is None:
            raise OperativoNoEncontrado("No encontrada")
        nuevo = CuentaTercero(
            cuenta_id=c.cuenta_id,
            alias=str(cambios.get("alias", c.alias)).strip(),
            titular=str(cambios.get("titular", c.titular)),
            correo_telefono=str(cambios.get("correo_telefono", c.correo_telefono)),
            proveedor_odoo_id=int(cambios.get("proveedor_odoo_id", c.proveedor_odoo_id)),
            proveedor_nombre=str(cambios.get("proveedor_nombre", c.proveedor_nombre)),
            diario_odoo_id=cambios.get("diario_odoo_id", c.diario_odoo_id),
            diario_odoo_nombre=str(cambios.get("diario_odoo_nombre", c.diario_odoo_nombre)),
            cuenta_cxp_override_id=cambios.get("cuenta_cxp_override_id", c.cuenta_cxp_override_id),
            cuenta_cxc_override_id=cambios.get("cuenta_cxc_override_id", c.cuenta_cxc_override_id),
            moneda=str(cambios.get("moneda", c.moneda)),
            notas=str(cambios.get("notas", c.notas)),
            activo=bool(cambios.get("activo", c.activo)),
            creado_por=c.creado_por,
            creado_en=c.creado_en,
            actualizado_en=datetime.now(),
        )
        self.cuentas[cuenta_id] = nuevo
        return nuevo

    def desactivar(self, cuenta_id: int) -> CuentaTercero:
        return self.actualizar(cuenta_id, {"activo": False})


class OdooContableFalso:
    """Doble de Odoo XML-RPC enfocado en pagos, diarios generales y asientos contables."""

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, str, list[Any], dict[str, Any]]] = []
        self.creados: list[tuple[str, dict[str, Any]]] = []
        self.chatter: list[tuple[str, int, str]] = []
        self.asientos_existentes: list[int] = []
        self._seq = 800

    def __call__(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        self.llamadas.append((model, method, args, kwargs))
        if method == "fields_get":
            if model == "res.partner":
                return {
                    "name": {},
                    "property_account_receivable_id": {},
                    "property_account_payable_id": {},
                }
            if model == "account.move":
                return {
                    "move_type": {},
                    "journal_id": {},
                    "date": {},
                    "ref": {},
                    "narration": {},
                    "line_ids": {},
                    "message_ids": {},
                }
            if model == "account.move.line":
                return {
                    "account_id": {},
                    "partner_id": {},
                    "currency_id": {},
                    "amount_currency": {},
                }
            if model == "account.payment":
                return {"memo": {}, "amount": {}, "message_ids": {}}
            return {}
        if model == "res.company" and method == "search_read":
            return [{"id": 1, "currency_id": [2, "VES"]}]
        if model == "res.currency" and method == "search_read":
            return [{"id": 1, "name": "USD"}, {"id": 2, "name": "VES"}]
        if model == "account.journal" and method == "search_read":
            return [
                {"id": 15, "name": "Operaciones Varias", "code": "MISC", "currency_id": False},
                {"id": 22, "name": "Asientos Terceros", "code": "ZT01", "currency_id": [1, "USD"]},
            ]
        if model == "res.partner" and method == "read":
            pid = args[0][0]
            if pid == 501:
                return [
                    {
                        "id": 501,
                        "name": "INVERSIONES LUBRIREPUESTOS CA",
                        "property_account_receivable_id": [1101, "1.1.02 CxC Clientes"],
                        "property_account_payable_id": [2101, "2.1.01 CxP Proveedores"],
                    }
                ]
            if pid == 601:
                return [
                    {
                        "id": 601,
                        "name": "IMPORTADORA GLOBAL SA",
                        "property_account_receivable_id": [1101, "1.1.02 CxC Clientes"],
                        "property_account_payable_id": [2105, "2.1.05 CxP Importadora"],
                    }
                ]
            return []
        if model == "account.move" and method == "search":
            return list(self.asientos_existentes)
        if model == "mail.message" and method == "search_read":
            return []
        if method == "create":
            self._seq += 1
            vals = dict(args[0])
            self.creados.append((model, vals))
            return self._seq
        if method == "message_post":
            self.chatter.append((model, int(args[0][0]), str(kwargs.get("body") or "")))
            return 901
        return []


@pytest.fixture(autouse=True)
def _limpiar_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _montar_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAPTURAS_ODOO_ESCRITURA", "1")
    monkeypatch.setenv("ODOO_WRITE_USERNAME", "u@test")
    monkeypatch.setenv("ODOO_WRITE_PASSWORD", "p")

    odoo = OdooContableFalso()
    repo_op = RepoOperativosMemF2()
    repo_ter = RepoTercerosMem()
    repo_cxc = InMemoryRepository()

    repo_cxc.upsert_clientes([Cliente("501", "INVERSIONES LUBRIREPUESTOS CA", "v@x.com")])
    repo_cxc.add_metodo_pago(MetodoPago("31", "Zelle Propio", Moneda.USD, TipoTasa.BINANCE, True))
    repo_cxc.upsert_ordenes(
        [
            OrdenVenta(
                so_id="S01001",
                cliente_id="501",
                fecha=date(2026, 11, 15),
                fecha_entrega=None,
                monto_total=Decimal("520.00"),
                lista_precios="Pago VES Nov 2026",
                vendedor_email="v@x.com",
                es_primera_compra=False,
            )
        ]
    )
    repo_cxc.upsert_pagos(
        [
            Pago(
                pago_id="9001",
                cliente_id="501",
                monto=Decimal("100.00"),
                moneda=Moneda.USD,
                metodo_pago="31",
                fecha_pago=datetime(2026, 11, 18, 10, 0),
                vendedor_email="v@x.com",
            ),
            Pago(
                pago_id="9002",
                cliente_id="501",
                monto=Decimal("200.00"),
                moneda=Moneda.USD,
                metodo_pago="31",
                fecha_pago=datetime(2026, 11, 19, 9, 0),
                vendedor_email="v@x.com",
            ),
        ]
    )
    repo_cxc.upsert_pagos_proveedores_odoo(
        [
            {
                "pago_id": "7701",
                "numero_odoo": "PBNK1/2026/0010",
                "partner_odoo_id": 601,
                "proveedor_nombre": "IMPORTADORA GLOBAL SA",
                "rif": "J-301112229",
                "monto": Decimal("450.00"),
                "moneda": "USD",
                "diario_id": 31,
                "diario_nombre": "Zelle Propio",
                "referencia": "REF-ODOO-OUT-1",
                "fecha_pago": date(2026, 11, 17),
            }
        ]
    )
    repo_cxc.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 19, 10, 0),
            tasa_bcv=Decimal("50.00"),
            tasa_binance=Decimal("60.00"),
            fuente="test",
        )
    )

    contactos = [
        mapear_contacto(
            {
                "id": 501,
                "name": "INVERSIONES LUBRIREPUESTOS CA",
                "vat": "J-506570297",
                "customer_rank": 1,
                "supplier_rank": 0,
            }
        ),
        mapear_contacto(
            {
                "id": 601,
                "name": "IMPORTADORA GLOBAL SA",
                "vat": "J-301112229",
                "customer_rank": 0,
                "supplier_rank": 1,
            }
        ),
    ]
    cuentas = [
        mapear_cuenta(
            {
                "id": 1101,
                "code": "1.1.02",
                "name": "CxC Clientes",
                "account_type": "asset_receivable",
            }
        ),
        mapear_cuenta(
            {
                "id": 2105,
                "code": "2.1.05",
                "name": "CxP Importadora",
                "account_type": "liability_payable",
            }
        ),
    ]

    svc_op = ServicioOperativos(
        repo_op,
        lambda: EscritorOdoo(odoo),
        repo_cxc=repo_cxc,
        obtener_contactos=lambda: contactos,
        obtener_cuentas=lambda: cuentas,
        diarios_extra={"efectivo_usd": 19, "efectivo_ves": 20},
        repo_terceros=repo_ter,
    )
    app.dependency_overrides[get_repo_terceros] = lambda: repo_ter
    app.dependency_overrides[get_servicio_operativos] = lambda: svc_op
    return odoo, repo_op, repo_ter, repo_cxc


def _como(user: dict[str, Any]) -> Any:
    return patch("cxc.web.app.get_current_user_from_cookie", return_value=user)


def test_escritor_crear_asiento_contable_multimoneda_e_idempotencia() -> None:
    odoo = OdooContableFalso()
    esc = EscritorOdoo(odoo)

    assert esc.diario_general_por_defecto() == 15
    assert esc.leer_cuentas_partner(601) == {"receivable_id": 1101, "payable_id": 2105}

    with pytest.raises(OdooEscrituraError):
        esc.crear_asiento_contable(
            journal_id=15,
            fecha="2026-11-19",
            ref="BAD",
            moneda_codigo="USD",
            lineas=[
                {"account_id": 2105, "debito": Decimal("10"), "credito": Decimal("0")},
                {"account_id": 1101, "debito": Decimal("0"), "credito": Decimal("9")},
            ],
            marca="Z3-BAD",
            narration="descuadre",
        )

    mid = esc.crear_asiento_contable(
        journal_id=15,
        fecha="2026-11-19",
        ref="ZELLE3-REF1",
        moneda_codigo="USD",
        marca="ZELLE3-PAG-00001",
        narration="Zelle 3ro",
        tasa_compania=Decimal("50.00"),
        nota_chatter="Nota Zelle",
        lineas=[
            {
                "account_id": 2105,
                "partner_id": 601,
                "name": "Debito CxP",
                "debito": Decimal("100.00"),
                "credito": Decimal("0"),
            },
            {
                "account_id": 1101,
                "partner_id": 501,
                "name": "Credito CxC",
                "debito": Decimal("0"),
                "credito": Decimal("100.00"),
            },
        ],
    )
    assert mid == 801
    modelo, vals = odoo.creados[-1]
    assert modelo == "account.move"
    assert vals["move_type"] == "entry"
    lns = [ln[2] for ln in vals["line_ids"]]
    assert lns[0]["debit"] == 5000.0 and lns[0]["amount_currency"] == 100.0
    assert lns[1]["credit"] == 5000.0 and lns[1]["amount_currency"] == -100.0
    assert odoo.chatter[-1] == ("account.move", 801, "Nota Zelle")
    assert all(m != "action_post" for _, m, _, _ in odoo.llamadas)

    # Idempotencia si el asiento ya existe
    odoo.asientos_existentes = [801]
    mid2 = esc.crear_asiento_contable(
        journal_id=15,
        fecha="2026-11-19",
        ref="ZELLE3-REF1",
        moneda_codigo="USD",
        marca="ZELLE3-PAG-00001",
        narration="Zelle 3ro",
        lineas=[
            {"account_id": 2105, "debito": Decimal("100"), "credito": Decimal("0")},
            {"account_id": 1101, "debito": Decimal("0"), "credito": Decimal("100")},
        ],
    )
    assert mid2 == 801
    assert len(odoo.creados) == 1


def test_crud_cuentas_terceros_y_permisos(monkeypatch: pytest.MonkeyPatch) -> None:
    _montar_api(monkeypatch)

    # Sin sesion -> 401
    assert client.get("/api/config/cuentas-terceros").status_code == 401

    # Ventas -> 403 al crear cuenta de tercero
    with _como(VENTAS_USER):
        r403 = client.post(
            "/api/config/cuentas-terceros",
            json={"alias": "Zelle Prov X", "proveedor_odoo_id": 601},
        )
        assert r403.status_code == 403

    # Tesoreria -> 201 crear, 200 editar y desactivar, 200 diarios generales
    with _como(TESO_USER):
        r_crea = client.post(
            "/api/config/cuentas-terceros",
            json={
                "alias": "Zelle Importadora Global",
                "titular": "Importadora Global LLC",
                "correo_telefono": "pagos@importadoraglobal.com",
                "proveedor_odoo_id": 601,
                "proveedor_nombre": "IMPORTADORA GLOBAL SA",
                "moneda": "USD",
            },
        )
        assert r_crea.status_code == 201
        cid = r_crea.json()["cuenta_id"]

        # Duplicado -> 422
        assert (
            client.post(
                "/api/config/cuentas-terceros",
                json={"alias": "Zelle Importadora Global", "proveedor_odoo_id": 601},
            ).status_code
            == 422
        )

        r_edit = client.put(
            f"/api/config/cuentas-terceros/{cid}",
            json={"diario_odoo_id": 22, "diario_odoo_nombre": "Asientos Terceros"},
        )
        assert r_edit.status_code == 200
        assert r_edit.json()["diario_odoo_id"] == 22

        r_diarios = client.get("/api/config/diarios-generales")
        assert r_diarios.status_code == 200
        assert len(r_diarios.json()["items"]) == 2

        r_del = client.delete(f"/api/config/cuentas-terceros/{cid}")
        assert r_del.status_code == 200
        assert r_del.json()["activo"] is False


def test_flujo_cxc_first_recibo_y_zelle_tercero_asiento_odoo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    odoo, _repo_op, repo_ter, _repo_cxc = _montar_api(monkeypatch)
    cta = repo_ter.crear(
        {
            "alias": "Zelle Importadora",
            "titular": "Importadora Global LLC",
            "correo_telefono": "zelle@importadora.com",
            "proveedor_odoo_id": 601,
            "proveedor_nombre": "IMPORTADORA GLOBAL SA",
            "diario_odoo_id": 15,
            "moneda": "USD",
        },
        actor="tesoreria",
    )

    with _como(TESO_USER):
        # 1. Auto-detección de Zelle de tercero al crear pago
        r_crea = client.post(
            "/api/operativos",
            json={
                "tipo": "pago",
                "contraparte_nombre": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": "320.00",
                "moneda": "USD",
                "metodo_pago": "zelle",
                "referencia": "Z-99011",
                "beneficiario_tercero": "zelle@importadora.com",
                "pedido_ref": "S01001",
                "fecha_documento": "2026-11-19",
            },
        )
        assert r_crea.status_code == 201
        d = r_crea.json()
        doc_id = d["id"]
        tok = d["token_preview"]
        assert d["es_zelle_tercero"] is True
        assert d["cuenta_tercero_id"] == cta.cuenta_id
        assert d["diario_id"] is None  # Nunca asigna diario de banco propio
        assert d["estado"] == PENDIENTE_APROBACION

        # Antes de confirmar CxC -> recibo da 404
        assert client.get(f"/recibo/{tok}").status_code == 404

        # 2. Confirmar primero en CxC (genera REC-YYYYMMDD-001)
        r_cxc = client.post(
            f"/api/operativos/{doc_id}/confirmar-cxc", json={"enviar_a_odoo": False}
        )
        assert r_cxc.status_code == 200
        d_cxc = r_cxc.json()
        assert d_cxc["estado"] == CONFIRMADO_CXC
        assert d_cxc["confirmado_cxc"] is True
        assert d_cxc["numero_recibo"].startswith("REC-")
        assert d_cxc["puede_aprobar"] is True

        # Recibo HTML imprimible disponible
        r_rec_html = client.get(f"/recibo/{tok}")
        assert r_rec_html.status_code == 200
        assert d_cxc["numero_recibo"] in r_rec_html.text
        assert "IMPORTADORA GLOBAL SA" in r_rec_html.text

        # 3. Enviar a Odoo -> crea account.move (entry), NUNCA account.payment
        r_apr = client.post(f"/api/operativos/{doc_id}/aprobar")
        assert r_apr.status_code == 200
        d_apr = r_apr.json()
        assert d_apr["estado"] == ENVIADO_ODOO
        assert d_apr["odoo_model"] == "account.move"
        assert d_apr["odoo_res_id"] == 801

        assert len(odoo.creados) == 1
        modelo_creado, vals_asiento = odoo.creados[0]
        assert modelo_creado == "account.move"
        assert vals_asiento["move_type"] == "entry"
        assert vals_asiento["journal_id"] == 15
        lineas = [ln[2] for ln in vals_asiento["line_ids"]]
        assert lineas[0]["account_id"] == 2105 and lineas[0]["partner_id"] == 601
        assert lineas[0]["debit"] == 16000.0
        assert lineas[1]["account_id"] == 1101 and lineas[1]["partner_id"] == 501
        assert lineas[1]["credit"] == 16000.0

        # 4. Efectivo aprobado directo tambien confirma CxC y emite segundo recibo
        r_efec = client.post(
            "/api/operativos",
            json={
                "tipo": "pago",
                "contraparte_nombre": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": "80.00",
                "moneda": "USD",
                "metodo_pago": "efectivo",
                "fecha_documento": "2026-11-19",
            },
        )
        assert r_efec.status_code == 201
        id_efec = r_efec.json()["id"]
        r_efec_apr = client.post(f"/api/operativos/{id_efec}/aprobar")
        assert r_efec_apr.status_code == 200
        d_efec = r_efec_apr.json()
        assert d_efec["confirmado_cxc"] is True
        assert d_efec["numero_recibo"].endswith("-002")
        assert d_efec["odoo_model"] == "account.payment"


def test_pagos_unificados_recibidos_y_enviados_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    _odoo, repo_op, _repo_ter, _repo_cxc = _montar_api(monkeypatch)

    with _como(TESO_USER):
        # Crear pago CxC ya ligado a odoo_res_id=9001 -> no debe duplicar con el espejo
        doc_9001 = client.post(
            "/api/operativos",
            json={
                "tipo": "pago",
                "contraparte_nombre": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": "100.00",
                "moneda": "USD",
                "metodo_pago": "zelle",
                "referencia": "REF-9001",
                "fecha_documento": "2026-11-18",
            },
        ).json()
        repo_op.actualizar(
            doc_9001["id"],
            {
                "estado": ENVIADO_ODOO,
                "odoo_model": "account.payment",
                "odoo_res_id": 9001,
                "odoo_ref": "account.payment:9001",
            },
        )

        # Un pago nacido directo en Odoo (9002) debe aparecer con origen_sistema='odoo'
        r_uni = client.get("/api/pagos/unificados?direccion=recibidos")
        assert r_uni.status_code == 200
        data_uni = r_uni.json()
        assert data_uni["resumen"]["origen_cxc"] == 1
        assert data_uni["resumen"]["origen_odoo"] == 1  # 9001 deduplicado!
        assert data_uni["total"] == 2
        origs = {x.get("odoo_res_id"): x["origen_sistema"] for x in data_uni["items"]}
        assert origs[9001] == "cxc"
        assert origs[9002] == "odoo"

        # Filtro solo odoo
        r_odoo = client.get("/api/pagos/unificados?direccion=recibidos&origen=odoo")
        assert r_odoo.status_code == 200
        assert [x["odoo_res_id"] for x in r_odoo.json()["items"]] == [9002]

        # Enviados unificados (incluye espejo outbound 7701)
        r_env_cxc = client.post(
            "/api/operativos",
            json={
                "tipo": "pago_proveedor",
                "contraparte_nombre": "IMPORTADORA GLOBAL SA",
                "monto": "120.00",
                "moneda": "USD",
                "metodo_pago": "transferencia",
                "referencia": "TRF-OUT-9",
                "fecha_documento": "2026-11-19",
            },
        )
        assert r_env_cxc.status_code == 201
        r_env = client.get("/api/pagos/unificados?direccion=enviados")
        assert r_env.status_code == 200
        d_env = r_env.json()
        assert d_env["resumen"]["origen_cxc"] == 1
        assert d_env["resumen"]["origen_odoo"] == 1
        assert {x["origen_sistema"] for x in d_env["items"]} == {"cxc", "odoo"}


def test_odoo_reader_changed_pagos_proveedores() -> None:
    def fake_exec(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if model == "account.payment" and method == "fields_get":
            return {"memo": {}, "amount": {}}
        if model == "account.payment" and method == "search_read":
            return [
                {
                    "id": 7701,
                    "name": "PBNK1/2026/0010",
                    "partner_id": [601, "IMPORTADORA GLOBAL SA"],
                    "amount": 450.0,
                    "currency_id": [1, "USD"],
                    "journal_id": [31, "Zelle Propio"],
                    "date": "2026-11-17",
                    "is_reconciled": True,
                    "state": "paid",
                    "move_id": [9500, "ASIENTO"],
                    "memo": "REF-PROV-1",
                }
            ]
        if model == "res.partner" and method == "read":
            return [{"id": 601, "vat": "J-301112229"}]
        return []

    reader = OdooXmlRpcReader(OdooConfig("https://odoo.test", "db", "u", "p"), execute=fake_exec)
    rows = reader.changed_pagos_proveedores(None)
    assert len(rows) == 1
    assert rows[0]["pago_id"] == "7701"
    assert rows[0]["numero_odoo"] == "PBNK1/2026/0010"
    assert rows[0]["rif"] == "J-301112229"
    assert rows[0]["referencia"] == "REF-PROV-1"
    assert rows[0]["monto"] == Decimal("450.0")
