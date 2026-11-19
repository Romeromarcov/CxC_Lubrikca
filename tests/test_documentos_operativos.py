"""Pruebas de Documentos Operativos (Pagos, Facturas Prov., Pagos Prov. y Gastos sin Factura)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.capturas.modelos import ENVIADO
from cxc.capturas.modelos import INCOMPLETO as CAP_INCOMPLETO
from cxc.capturas.servicio import ServicioCapturas
from cxc.models import Cliente, OrdenVenta, SerieTasa
from cxc.odoo.directorio import mapear_contacto, mapear_cuenta
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.modelos import (
    BORRADOR,
    ENVIADO_ODOO,
    INCOMPLETO,
    PENDIENTE_APROBACION,
    PREFIJO_POR_TIPO,
    RECHAZADO,
    DocumentoOperativo,
    OperativoInvalido,
)
from cxc.operativos.repositorio import _cargar_json_dict, _cargar_json_list
from cxc.operativos.servicio import ServicioOperativos
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from cxc.web.operativos_api import get_servicio_operativos
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria

client = TestClient(app)
KEY = "clave-bot-operativos"
BOT = {"X-Bot-Key": KEY}

VENTAS_USER = {"email": "vendedor@lubrikca.com", "rol": "ventas", "permisos": efectivos("ventas")}
AUDITOR_USER = {
    "email": "auditor@lubrikca.com",
    "rol": "auditor",
    "permisos": efectivos("auditor"),
}
TESO_USER = {
    "email": "tesoreria@lubrikca.com",
    "rol": "tesoreria",
    "permisos": efectivos("tesoreria"),
}
GERENTE_USER = {
    "email": "gerencia@lubrikca.com",
    "rol": "gerente_ventas",
    "permisos": efectivos("gerente_ventas"),
}
SOLO_PAGOS_USER = {
    "email": "cajapago@lubrikca.com",
    "rol": "ventas",
    "permisos": efectivos("ventas", {"cobranza.aprobar_pagos": True}),
}
SOLO_COMPRAS_USER = {
    "email": "compras@lubrikca.com",
    "rol": "ventas",
    "permisos": efectivos("ventas", {"compras.aprobar_documentos": True, "capturas.ver": True}),
}


class RepoOperativosMemoria:
    """Doble en memoria con la misma interfaz que ``RepoOperativosSQL``."""

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
        valores = {
            "estado": campos.get("estado", d.estado),
            "partner_odoo_id": campos.get("partner_odoo_id", d.partner_odoo_id),
            "contraparte_nombre": campos.get("contraparte_nombre", d.contraparte_nombre),
            "rif": campos.get("rif", d.rif),
            "monto": campos.get("monto", d.monto),
            "base_imponible": campos.get("base_imponible", d.base_imponible),
            "iva": campos.get("iva", d.iva),
            "moneda": campos.get("moneda", d.moneda),
            "metodo_pago": campos.get("metodo_pago", d.metodo_pago),
            "banco": campos.get("banco", d.banco),
            "diario_id": campos.get("diario_id", d.diario_id),
            "diario_nombre": campos.get("diario_nombre", d.diario_nombre),
            "cuenta_contable_id": campos.get("cuenta_contable_id", d.cuenta_contable_id),
            "cuenta_contable_codigo": campos.get(
                "cuenta_contable_codigo", d.cuenta_contable_codigo
            ),
            "cuenta_contable_nombre": campos.get(
                "cuenta_contable_nombre", d.cuenta_contable_nombre
            ),
            "referencia": campos.get("referencia", d.referencia),
            "numero_documento": campos.get("numero_documento", d.numero_documento),
            "numero_control": campos.get("numero_control", d.numero_control),
            "pedido_ref": campos.get("pedido_ref", d.pedido_ref),
            "factura_ref": campos.get("factura_ref", d.factura_ref),
            "orden_ref": campos.get("orden_ref", d.orden_ref),
            "categoria": campos.get("categoria", d.categoria),
            "concepto": campos.get("concepto", d.concepto),
            "sin_factura": campos.get("sin_factura", d.sin_factura),
            "fecha_documento": campos.get("fecha_documento", d.fecha_documento),
            "fecha_vencimiento": campos.get("fecha_vencimiento", d.fecha_vencimiento),
            "proveedor_telefono": campos.get("proveedor_telefono", d.proveedor_telefono),
            "proveedor_email": campos.get("proveedor_email", d.proveedor_email),
            "proveedor_direccion": campos.get("proveedor_direccion", d.proveedor_direccion),
            "notas": campos.get("notas", d.notas),
            "validacion": (
                _cargar_json_dict(campos["validacion"]) if "validacion" in campos else d.validacion
            ),
            "faltantes": (
                _cargar_json_list(campos["faltantes"]) if "faltantes" in campos else d.faltantes
            ),
            "aprobado_por": campos.get("aprobado_por", d.aprobado_por),
            "aprobado_en": campos.get("aprobado_en", d.aprobado_en),
            "rechazo_motivo": campos.get("rechazo_motivo", d.rechazo_motivo),
            "odoo_model": campos.get("odoo_model", d.odoo_model),
            "odoo_res_id": campos.get("odoo_res_id", d.odoo_res_id),
            "odoo_ref": campos.get("odoo_ref", d.odoo_ref),
            "error_odoo": campos.get("error_odoo", d.error_odoo),
            "actualizado_en": datetime.now(),
        }
        nuevo = DocumentoOperativo(
            documento_id=d.documento_id,
            numero=d.numero,
            token_preview=d.token_preview,
            tipo=d.tipo,
            origen=d.origen,
            captura_id=d.captura_id,
            creado_por=d.creado_por,
            creado_en=d.creado_en,
            **valores,
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
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentoOperativo], int, dict[str, int]]:
        base = list(reversed(list(self.filas.values())))
        if tipo:
            base = [d for d in base if d.tipo == tipo]
        elif grupo == "cobranza":
            base = [d for d in base if d.tipo == "pago"]
        elif grupo == "compras":
            base = [d for d in base if d.tipo != "pago"]

        resumen = {
            "total": len(base),
            "pendientes_aprobacion": sum(1 for d in base if d.estado == PENDIENTE_APROBACION),
            "incompletos": sum(1 for d in base if d.estado in (INCOMPLETO, BORRADOR)),
            "enviados_odoo": sum(1 for d in base if d.estado == ENVIADO_ODOO),
            "rechazados": sum(1 for d in base if d.estado == RECHAZADO),
            "errores": sum(1 for d in base if d.estado == "ERROR"),
        }
        if estado:
            base = [d for d in base if d.estado == estado]
        if q and q.strip():
            qn = q.strip().lower()
            base = [
                d
                for d in base
                if qn
                in (
                    f"{d.numero} {d.contraparte_nombre} {d.rif} {d.referencia} "
                    f"{d.numero_documento} {d.concepto} {d.odoo_ref}"
                ).lower()
            ]
        return base[offset : offset + limit], len(base), resumen

    def buscar_duplicados_referencia(
        self, tipo: str, referencia: str, *, excluir_id: int | None = None
    ) -> list[DocumentoOperativo]:
        rc = referencia.strip().lower()
        return [
            d
            for d in self.filas.values()
            if d.tipo == tipo
            and d.referencia.strip().lower() == rc
            and d.estado != RECHAZADO
            and d.documento_id != excluir_id
        ]

    def buscar_duplicados_factura(
        self,
        proveedor: str,
        rif: str,
        numero_documento: str,
        *,
        excluir_id: int | None = None,
    ) -> list[DocumentoOperativo]:
        nc = numero_documento.strip().lower()
        out = []
        for d in self.filas.values():
            if d.tipo not in ("factura_proveedor", "gasto") or d.estado == RECHAZADO:
                continue
            if d.documento_id == excluir_id or d.numero_documento.strip().lower() != nc:
                continue
            if (rif and d.rif and rif.upper() == d.rif.upper()) or (
                proveedor and d.contraparte_nombre.lower() == proveedor.lower()
            ):
                out.append(d)
        return out


CONTACTOS_PRUEBA = [
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
            "name": "LUBRICANTES INDUSTRIALES DEL SUR CA",
            "vat": "J-301112229",
            "customer_rank": 0,
            "supplier_rank": 1,
        }
    ),
]

CUENTAS_PRUEBA = [
    mapear_cuenta(
        {
            "id": 710,
            "code": "510101",
            "name": "Compras de Mercancía / Lubricantes",
            "account_type": "expense_direct_cost",
        }
    ),
    mapear_cuenta(
        {
            "id": 720,
            "code": "610205",
            "name": "Gastos de Combustible y Flota",
            "account_type": "expense",
        }
    ),
    mapear_cuenta(
        {
            "id": 730,
            "code": "610210",
            "name": "Papelería y Artículos de Oficina",
            "account_type": "expense",
        }
    ),
    mapear_cuenta(
        {
            "id": 740,
            "code": "610299",
            "name": "Gastos Diversos / Refrigerios",
            "account_type": "expense",
        }
    ),
]


def _como(user: dict[str, Any]) -> Any:
    return patch("cxc.web.app.get_current_user_from_cookie", return_value=user)


def _montar_entorno(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RepoMemoria, RepoOperativosMemoria, OdooFalso, InMemoryRepository]:
    monkeypatch.setenv("BOT_API_KEY", KEY)
    monkeypatch.setenv("CAPTURAS_ODOO_ESCRITURA", "1")
    monkeypatch.setenv("ODOO_WRITE_USERNAME", "u@test")
    monkeypatch.setenv("ODOO_WRITE_PASSWORD", "p")

    repo_cap = RepoMemoria()
    repo_op = RepoOperativosMemoria()
    odoo = OdooFalso(res_partner__search=[501])
    repo_cxc = InMemoryRepository()
    repo_cxc.upsert_clientes([Cliente("501", "INVERSIONES LUBRIREPUESTOS CA", "v@x.com")])
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
    repo_cxc.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 18, 12, 0),
            tasa_bcv=Decimal("45.00"),
            tasa_binance=Decimal("54.00"),
            fuente="test",
        )
    )

    svc_cap = ServicioCapturas(
        repo_cap,
        EspejoFalso(),
        lambda: EscritorOdoo(odoo),
        diarios={"efectivo_usd": 19, "efectivo_ves": 20},
    )
    svc_op = ServicioOperativos(
        repo_op,
        lambda: EscritorOdoo(odoo),
        repo_cxc=repo_cxc,
        obtener_contactos=lambda: CONTACTOS_PRUEBA,
        obtener_cuentas=lambda: CUENTAS_PRUEBA,
        diarios_extra={"efectivo_usd": 19, "efectivo_ves": 20},
        repo_capturas=repo_cap,
    )
    app.dependency_overrides[get_servicio] = lambda: svc_cap
    app.dependency_overrides[get_servicio_operativos] = lambda: svc_op
    return repo_cap, repo_op, odoo, repo_cxc


@pytest.fixture(autouse=True)
def _limpiar_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


# ── Flujo completo: Pago Recibido (Transferencia & Efectivo) ─────────────────


def test_flujo_pago_transferencia_bot_preview_aprobacion_y_odoo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo_cap, repo_op, odoo, _repo_cxc = _montar_entorno(monkeypatch)

    # 1. Validación en vivo desde bot identifica cliente, banco Banesco, equiv USD y orden abierta
    r_val = client.post(
        "/api/bots/operativos/validar",
        headers=BOT,
        json={
            "tipo": "pago",
            "datos": {
                "cliente": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": "4500.00",
                "moneda": "VES",
                "metodo_pago": "pago móvil",
                "referencia": "REF-991",
                "banco": "Banesco",
                "fecha_pago": "2026-11-18",
            },
        },
    )
    assert r_val.status_code == 200
    v = r_val.json()
    assert v["contraparte_resuelta"]["id"] == 501
    assert v["diario_sugerido"]["id"] == 32
    assert v["equivalente_usd"]["usd_bcv"] == 100.0
    assert v["ordenes_abiertas"][0]["so_id"] == "S01001"

    # 2. Bot ingesta con requiere_aprobacion_cxc=True -> NO escribe a Odoo todavía
    r_ing = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "pago",
            "draft": {
                "cliente": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": "4500.00",
                "moneda": "VES",
                "metodo_pago": "pago móvil",
                "referencia": "REF-991",
                "banco": "Banesco",
                "fecha_pago": "2026-11-18",
                "pedido_ref": "S01001",
                "requiere_aprobacion_cxc": True,
            },
            "source": {"vendedor_email": "cobros@lubrikca.com"},
            "idempotency_key": "pay-t1",
        },
    )
    assert r_ing.status_code == 201
    body = r_ing.json()
    assert body["estado"] == ENVIADO
    assert body["odoo_ref"] == ""
    assert odoo.creados == []
    res = body["resultado"]
    assert res["modo"] == "pendiente_aprobacion_cxc"
    assert res["es_operativo"] is True
    assert res["documento_numero"] == "PAG-00001"
    assert res["documento_estado"] == PENDIENTE_APROBACION

    # 3. Preview HTML público responde 200 y contiene la info + orden abierta
    r_prev = client.get(res["preview_path"])
    assert r_prev.status_code == 200
    assert "PAG-00001" in r_prev.text
    assert "S01001" in r_prev.text
    assert "Pendiente por aprobación" in r_prev.text

    # 4. Usuario autorizado en CxC aprueba -> crea account.payment draft (memo limpio + chatter)
    with _como(TESO_USER):
        r_apr = client.post(f"/api/operativos/{res['documento_id']}/aprobar")
    assert r_apr.status_code == 200
    apr = r_apr.json()
    assert apr["estado"] == ENVIADO_ODOO
    assert apr["aprobado_por"] == "tesoreria@lubrikca.com"
    assert apr["odoo_model"] == "account.payment"
    pagos = odoo.creados_de("account.payment")
    assert len(pagos) == 1
    assert (pagos[0]["payment_type"], pagos[0]["partner_type"]) == ("inbound", "customer")
    assert pagos[0]["journal_id"] == 32
    assert pagos[0]["memo"] == "REF-991"
    chatter = [txt for mod, _, txt in odoo.chatter if mod == "account.payment"]
    assert len(chatter) == 1
    assert (
        "CXC-PAG-00001" in chatter[0]
        and "tesoreria@lubrikca.com" in chatter[0]
        and "S01001" in chatter[0]
    )
    assert repo_op.obtener(res["documento_id"]).odoo_ref == "account.payment:101"  # type: ignore[union-attr]


def test_pago_en_efectivo_envia_memo_vacio_y_nota_en_chatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo_cap, _repo_op, odoo, _ = _montar_entorno(monkeypatch)
    r_ing = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "pago",
            "draft": {
                "cliente": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": "250.00",
                "moneda": "USD",
                "metodo_pago": "efectivo",
                "diario_id": 19,
                "fecha_pago": "2026-11-18",
                "requiere_aprobacion_cxc": True,
            },
            "source": {"vendedor_email": "cobros@lubrikca.com"},
            "idempotency_key": "pay-cash-1",
        },
    )
    doc_id = r_ing.json()["resultado"]["documento_id"]
    with _como(TESO_USER):
        r_apr = client.post(f"/api/operativos/{doc_id}/aprobar")
    assert r_apr.status_code == 200
    pago_vals = odoo.creados_de("account.payment")[0]
    assert "memo" not in pago_vals
    assert pago_vals["journal_id"] == 19
    assert "Sin referencia (efectivo)" in odoo.chatter[0][2]


# ── Flujo Factura de Proveedor, Pago a Proveedor y Gasto sin Factura ─────────


def test_factura_proveedor_sugiere_cuenta_usa_diario_ves_y_deja_chatter_y_narration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo_cap, _repo_op, odoo, _ = _montar_entorno(monkeypatch)
    odoo.r["res.partner:search"] = []  # proveedor nuevo -> lo crea al aprobar

    r_ing = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "factura_proveedor",
            "draft": {
                "proveedor": "DISTRIBUIDORA NUEVA ACEITES CA",
                "rif": "J-409876543",
                "numero_factura": "FAC-8821",
                "numero_control": "00-1122",
                "fecha_emision": "2026-11-18",
                "fecha_vencimiento": "2026-12-03",
                "moneda": "USD",
                "base_imponible": "100.00",
                "iva": "16.00",
                "monto_total": "116.00",
                "concepto": "Compra de lubricantes para inventario",
                "requiere_aprobacion_cxc": True,
            },
            "source": {"vendedor_email": "compras@lubrikca.com"},
            "idempotency_key": "fac-prov-1",
        },
    )
    assert r_ing.status_code == 201
    res = r_ing.json()["resultado"]
    assert res["documento_numero"] == "FPR-00001"
    assert "510101" in res["cuenta_sugerida"]
    assert any("Proveedor nuevo" in a for a in res["alertas"])
    assert odoo.creados == []

    with _como(GERENTE_USER):
        r_apr = client.post(f"/api/operativos/{res['documento_id']}/aprobar")
    assert r_apr.status_code == 200
    assert r_apr.json()["odoo_model"] == "account.move"

    provs = odoo.creados_de("res.partner")
    moves = odoo.creados_de("account.move")
    assert len(provs) == 1 and provs[0]["supplier_rank"] == 1
    assert len(moves) == 1
    mv = moves[0]
    assert mv["move_type"] == "in_invoice"
    assert mv["journal_id"] == 10  # diario de compras VES por defecto (NO id=99 USD)
    assert mv["ref"] == "FAC-8821"
    linea = mv["invoice_line_ids"][0][2]
    assert linea["price_unit"] == 100.0
    assert linea["account_id"] == 710
    assert linea["tax_ids"] == [(6, 0, [])]
    assert "CXC-FPR-00001" in mv["narration"]
    chatter_mv = [txt for mod, _, txt in odoo.chatter if mod == "account.move"]
    assert len(chatter_mv) == 1
    assert "CXC-FPR-00001" in chatter_mv[0] and "gerencia@lubrikca.com" in chatter_mv[0]


def test_gasto_sin_factura_no_queda_incompleto_y_genera_ref_sn_en_odoo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo_cap, _repo_op, odoo, _ = _montar_entorno(monkeypatch)
    odoo.r["res.partner:search"] = [601]

    r_ing = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "gasto",
            "draft": {
                "proveedor": "ESTACION DE SERVICIO SAN JUAN",
                "fecha_emision": "2026-11-18",
                "moneda": "VES",
                "monto_total": "1800.00",
                "concepto": "Gasoil para camión de despacho",
                "metodo_pago": "pago móvil",
                "referencia": "445566",
                "requiere_aprobacion_cxc": True,
            },
            "source": {"vendedor_email": "logistica@lubrikca.com"},
            "idempotency_key": "gasto-sn-1",
        },
    )
    assert r_ing.status_code == 201
    res = r_ing.json()["resultado"]
    assert res["documento_numero"] == "GAS-00001"
    assert res["documento_estado"] == PENDIENTE_APROBACION
    assert res["faltantes"] == []
    assert "610205" in res["cuenta_sugerida"]

    # Preview refleja "Sin factura"
    r_prev = client.get(res["preview_path"])
    assert r_prev.status_code == 200 and "Sin factura" in r_prev.text

    with _como(TESO_USER):
        r_apr = client.post(f"/api/operativos/{res['documento_id']}/aprobar")
    assert r_apr.status_code == 200
    mv = odoo.creados_de("account.move")[0]
    assert mv["ref"] == "S/N-GAS-00001"
    assert mv["journal_id"] == 10
    assert mv["invoice_line_ids"][0][2]["account_id"] == 720
    assert "Gasto sin factura" in odoo.chatter[0][2]


def test_pago_a_proveedor_referencia_factura_en_chatter_y_memo_limpio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo_cap, _repo_op, odoo, _ = _montar_entorno(monkeypatch)
    odoo.r["res.partner:search"] = [601]
    odoo.r["account.move:search"] = [900]
    r_ing = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "pago_proveedor",
            "draft": {
                "proveedor": "LUBRICANTES INDUSTRIALES DEL SUR CA",
                "factura_ref": "F-001",
                "monto": "850.00",
                "moneda": "USD",
                "metodo_pago": "zelle",
                "referencia": "ZEL-778",
                "banco": "Zelle Fluasa",
                "fecha_pago": "2026-11-18",
                "requiere_aprobacion_cxc": True,
            },
            "source": {"vendedor_email": "compras@lubrikca.com"},
            "idempotency_key": "ppr-1",
        },
    )
    doc_id = r_ing.json()["resultado"]["documento_id"]
    with _como(TESO_USER):
        r_apr = client.post(f"/api/operativos/{doc_id}/aprobar")
    assert r_apr.status_code == 200
    p = odoo.creados_de("account.payment")[0]
    assert (p["payment_type"], p["partner_type"]) == ("outbound", "supplier")
    assert p["memo"] == "ZEL-778"
    assert "[account.move 900]" in odoo.chatter[0][2]


# ── Incompleto + Completar desde Preview / Telegram + Duplicados ─────────────


def test_pago_incompleto_se_completa_en_preview_o_bot_sin_tocar_odoo_hasta_aprobar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo_cap, repo_op, odoo, _ = _montar_entorno(monkeypatch)
    r_ing = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "pago",
            "draft": {
                "cliente": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": "300.00",
                "moneda": "USD",
                "metodo_pago": "transferencia",
                "banco": "Zelle Fluasa",
                "requiere_aprobacion_cxc": True,
            },
            "source": {"auto": True},
            "idempotency_key": "pay-inc-1",
        },
    )
    assert r_ing.status_code == 201
    body = r_ing.json()
    assert body["estado"] == CAP_INCOMPLETO
    res = body["resultado"]
    assert res["documento_estado"] == INCOMPLETO
    assert "referencia" in res["faltantes"]

    # Preview muestra formulario de completar
    r_prev = client.get(res["preview_path"])
    assert (
        r_prev.status_code == 200 and "Completar Datos y Enviar a Cola de Aprobación" in r_prev.text
    )

    # Confirmar por Preview completa el documento -> PENDIENTE_APROBACION (Odoo intacto)
    token = res["token_preview"]
    r_conf = client.post(f"/comprobante/{token}/confirmar", json={"referencia": "REF-COMP-1"})
    assert r_conf.status_code == 200
    assert r_conf.json()["estado"] == PENDIENTE_APROBACION
    assert odoo.creados == []

    # Duplicado de referencia detectado en validación en vivo
    r_dup = client.post(
        "/api/bots/operativos/validar",
        headers=BOT,
        json={
            "tipo": "pago",
            "datos": {
                "cliente": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": "300.00",
                "moneda": "USD",
                "metodo_pago": "transferencia",
                "referencia": "REF-COMP-1",
                "banco": "Zelle Fluasa",
            },
        },
    )
    assert r_dup.status_code == 200
    assert r_dup.json()["posibles_duplicados"]
    assert any("duplicada" in a.lower() for a in r_dup.json()["alertas"])
    assert repo_op.obtener(res["documento_id"]).referencia == "REF-COMP-1"  # type: ignore[union-attr]


def test_completar_captura_desde_bot_actualiza_documento_operativo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo_cap, repo_op, odoo, _ = _montar_entorno(monkeypatch)
    r_ing = client.post(
        "/api/bots/capturas",
        headers=BOT,
        json={
            "kind": "pago",
            "draft": {
                "cliente": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": "100",
                "moneda": "USD",
                "banco": "Zelle Fluasa",
                "requiere_aprobacion_cxc": True,
            },
            "source": {"auto": True},
            "idempotency_key": "pay-bot-comp",
        },
    )
    cap_id = r_ing.json()["id"]
    doc_id = r_ing.json()["resultado"]["documento_id"]
    r_cmp = client.post(
        f"/api/bots/capturas/{cap_id}/completar",
        headers=BOT,
        json={"cambios": {"referencia": "BOT-COMP-77", "metodo_pago": "zelle"}},
    )
    assert r_cmp.status_code == 200
    d_res = r_cmp.json()["resultado"]
    assert d_res["documento_id"] == doc_id
    assert d_res["documento_estado"] == PENDIENTE_APROBACION
    assert repo_op.obtener(doc_id).referencia == "BOT-COMP-77"  # type: ignore[union-attr]
    assert odoo.creados == []


# ── Permisos, Seguridad Auth Cerrada y Rechazo ───────────────────────────────


def test_rutas_api_exigen_sesion_o_bot_key_y_permisos_granulares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _montar_entorno(monkeypatch)
    # 1. Sin auth -> 401
    assert client.get("/api/operativos").status_code == 401
    assert client.post("/api/operativos", json={}).status_code == 401
    assert client.post("/api/operativos/1/aprobar").status_code == 401
    assert client.post("/api/bots/operativos/validar", json={"tipo": "pago"}).status_code == 401
    assert client.get("/api/bots/operativos/catalogos").status_code == 401

    # Crear 1 pago y 1 gasto via admin/tesoreria
    with _como(TESO_USER):
        r_p = client.post(
            "/api/operativos",
            json={
                "tipo": "pago",
                "cliente": "INVERSIONES LUBRIREPUESTOS CA",
                "monto": 100,
                "moneda": "USD",
                "referencia": "R10",
                "banco": "Zelle Fluasa",
            },
        )
        r_g = client.post(
            "/api/operativos",
            json={
                "tipo": "gasto",
                "proveedor": "COMERCIO X",
                "fecha_emision": "2026-11-18",
                "moneda": "VES",
                "monto_total": 500,
            },
        )
    pago_id = r_p.json()["id"]
    gasto_id = r_g.json()["id"]

    # 2. Ventas / Auditor -> 403 al aprobar o rechazar
    with _como(AUDITOR_USER):
        assert client.post(f"/api/operativos/{pago_id}/aprobar").status_code == 403
        assert (
            client.post(f"/api/operativos/{gasto_id}/rechazar", json={"motivo": "x"}).status_code
            == 403
        )
    with _como(VENTAS_USER):
        assert client.post(f"/api/operativos/{pago_id}/aprobar").status_code == 403

    # 3. Solo pagos -> aprueba pago OK, 403 en gasto
    with _como(SOLO_PAGOS_USER):
        assert client.post(f"/api/operativos/{gasto_id}/aprobar").status_code == 403
        assert client.post(f"/api/operativos/{pago_id}/aprobar").status_code == 200

    # 4. Solo compras -> 403 en pago (rechazo), rechaza gasto OK
    with _como(SOLO_COMPRAS_USER):
        assert (
            client.post(f"/api/operativos/{pago_id}/rechazar", json={"motivo": "no"}).status_code
            == 403
        )
        r_rec = client.post(f"/api/operativos/{gasto_id}/rechazar", json={"motivo": "duplicado"})
        assert r_rec.status_code == 200
        assert r_rec.json()["estado"] == RECHAZADO


def test_validador_alerta_desfase_matematico_y_banco_desconocido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _montar_entorno(monkeypatch)
    r_fac = client.post(
        "/api/bots/operativos/validar",
        headers=BOT,
        json={
            "tipo": "factura_proveedor",
            "datos": {
                "proveedor": "LUBRICANTES INDUSTRIALES DEL SUR CA",
                "numero_factura": "F-77",
                "fecha_emision": "2026-11-18",
                "moneda": "VES",
                "base_imponible": "100",
                "iva": "16",
                "monto_total": "150",
            },
        },
    )
    assert r_fac.status_code == 200
    assert any("difiere del total" in a for a in r_fac.json()["alertas"])

    r_pay = client.post(
        "/api/bots/operativos/validar",
        headers=BOT,
        json={
            "tipo": "pago_proveedor",
            "datos": {
                "proveedor": "OTRO PROVEEDOR NO REGISTRADO",
                "monto": "50",
                "moneda": "USD",
                "referencia": "REF1",
                "banco": "Banco Inventado",
            },
        },
    )
    assert r_pay.status_code == 200
    assert r_pay.json()["diario_sugerido"] is None
    assert any("Banco / diario no resuelto" in a for a in r_pay.json()["alertas"])


def test_servicio_operativos_exige_interruptor_escritura(monkeypatch: pytest.MonkeyPatch) -> None:
    _montar_entorno(monkeypatch)
    monkeypatch.setenv("CAPTURAS_ODOO_ESCRITURA", "0")
    repo_op = RepoOperativosMemoria()
    svc = ServicioOperativos(repo_op, lambda: None)
    doc = svc.crear(
        {
            "tipo": "gasto",
            "proveedor": "Comercio",
            "fecha_emision": "2026-11-18",
            "moneda": "VES",
            "monto_total": 100,
        }
    )
    with pytest.raises(OperativoInvalido, match="desactivada"):
        svc.aprobar_y_enviar_a_odoo(doc.documento_id, actor="admin@test")
