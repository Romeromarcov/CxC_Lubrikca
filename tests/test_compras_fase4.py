"""Pruebas Fase 4: Compras unificadas (CxC + Odoo), asientos gastos sin factura y Regla R4."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.bancos.conciliacion import (
    AMBIGUO,
    CUENTA,
    CUENTA_COMISIONES,
    PAGO,
    PagoCandidato,
    egresos_desde_operativos,
    proponer,
)
from cxc.bancos.ingesta import MovimientoClasificado
from cxc.bancos.modelos import MovimientoBancario
from cxc.config import OdooConfig
from cxc.models import SerieTasa
from cxc.odoo.client import OdooXmlRpcReader
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
)
from cxc.operativos.repositorio import _cargar_json_dict, _cargar_json_list
from cxc.operativos.servicio import ServicioOperativos
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web.app import app
from cxc.web.operativos_api import get_repo_lotes_caja_chica, get_servicio_operativos
from tests.capturas_dobles import OdooFalso

client = TestClient(app)

VENTAS_USER = {"email": "vendedor@lubrikca.com", "rol": "ventas", "permisos": efectivos("ventas")}
COMPRAS_USER = {
    "email": "compras@lubrikca.com",
    "rol": "ventas",
    "permisos": efectivos("ventas", {"compras.aprobar_documentos": True, "capturas.ver": True}),
}
ADMIN_USER = {"email": "admin@lubrikca.com", "rol": "admin", "permisos": efectivos("admin")}

HOY = date(2026, 11, 19)

CUENTAS_FASE4 = tuple(
    mapear_cuenta(c)
    for c in [
        {
            "id": 710,
            "code": "610101",
            "name": "Papelería y Artículos de Oficina",
            "account_type": "expense",
        },
        {
            "id": 711,
            "code": "610202",
            "name": "Combustible y Viáticos",
            "account_type": "expense",
        },
        {"id": 750, "code": "110101", "name": "Caja Chica USD", "account_type": "asset_cash"},
        {
            "id": 751,
            "code": "110102",
            "name": "Banco Bancamiga VES",
            "account_type": "asset_cash",
        },
    ]
)

CONTACTOS_FASE4 = tuple(
    mapear_contacto(c)
    for c in [
        {
            "id": 601,
            "name": "ESTACION DE SERVICIO SAN JUAN",
            "vat": "J-301112229",
            "supplier_rank": 1,
            "customer_rank": 0,
        }
    ]
)


class RepoOpMem4:
    """Doble en memoria mínimo de ``RepoOperativosSQL`` para pruebas Fase 4."""

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
            aprobado_por="",
            aprobado_en=None,
            rechazo_motivo="",
            odoo_model="",
            odoo_res_id=None,
            odoo_ref="",
            error_odoo="",
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
            lote_caja_chica_id=campos.get("lote_caja_chica_id"),
            cuenta_contrapartida_id=campos.get("cuenta_contrapartida_id"),
            conciliado_banco_huella=str(campos.get("conciliado_banco_huella") or ""),
        )
        self.filas[doc_id] = doc
        return doc

    def actualizar(self, documento_id: int, campos: dict[str, Any]) -> DocumentoOperativo:
        cur = self.filas[documento_id]
        data = {k: getattr(cur, k) for k in cur.__dataclass_fields__}
        for k, v in campos.items():
            if k == "validacion":
                data[k] = _cargar_json_dict(v)
            elif k == "faltantes":
                data[k] = _cargar_json_list(v)
            elif k in data:
                data[k] = v
        data["actualizado_en"] = datetime.now()
        nuevo = DocumentoOperativo(**data)
        self.filas[documento_id] = nuevo
        return nuevo

    def obtener(self, documento_id: int) -> DocumentoOperativo | None:
        return self.filas.get(documento_id)

    def obtener_por_token(self, token: str) -> DocumentoOperativo | None:
        return next((d for d in self.filas.values() if d.token_preview == token), None)

    def obtener_por_captura(self, captura_id: int) -> DocumentoOperativo | None:
        return next((d for d in self.filas.values() if d.captura_id == captura_id), None)

    def listar(
        self,
        *,
        grupo: str | None = None,
        tipo: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        es_zelle_tercero: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DocumentoOperativo], int, dict[str, int]]:
        items = list(self.filas.values())
        if es_zelle_tercero is not None:
            items = [d for d in items if d.es_zelle_tercero is es_zelle_tercero]
        if grupo == "compras_solo":
            items = [d for d in items if d.tipo in ("factura_proveedor", "gasto")]
        if tipo:
            items = [d for d in items if d.tipo == tipo]
        resumen = {
            "total": len(items),
            "pendientes_aprobacion": sum(1 for x in items if x.estado == PENDIENTE_APROBACION),
            "confirmados_cxc": 0,
            "incompletos": sum(1 for x in items if x.estado in (INCOMPLETO, BORRADOR)),
            "enviados_odoo": sum(1 for x in items if x.estado == ENVIADO_ODOO),
            "rechazados": sum(1 for x in items if x.estado == RECHAZADO),
            "errores": 0,
        }
        if estado:
            items = [d for d in items if d.estado == estado]
        if q and q.strip():
            qn = q.strip().lower()
            items = [
                d
                for d in items
                if qn in d.numero.lower()
                or qn in d.contraparte_nombre.lower()
                or qn in d.rif.lower()
            ]
        items.sort(key=lambda x: x.documento_id, reverse=True)
        return items[offset : offset + limit], len(items), resumen

    def buscar_posibles_duplicados(
        self,
        *,
        documento_id_excluir: int | None = None,
        tipo: str,
        monto: Any,
        moneda: str,
        fecha_documento: date | None,
        referencia: str = "",
        numero_documento: str = "",
        contraparte_nombre: str = "",
        rif: str = "",
    ) -> list[DocumentoOperativo]:
        return []

    def resumen_pendientes_aprobacion(self) -> dict[str, int]:
        items = list(self.filas.values())
        return {
            "pagos_recibidos": 0,
            "pagos_enviados": 0,
            "compras": sum(
                1
                for x in items
                if x.tipo in ("factura_proveedor", "gasto") and x.estado == PENDIENTE_APROBACION
            ),
        }

    def siguiente_numero_recibo(self, prefijo: str, fecha: date | None = None) -> str:
        return f"{prefijo}-202611-0001"

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


class RepoLotesMem4:
    """Doble en memoria de ``RepoLotesCajaChicaSQL``."""

    def __init__(self, repo_op: RepoOpMem4) -> None:
        self._repo_op = repo_op
        self._lotes: dict[int, dict[str, Any]] = {}
        self._sig = 1

    def listar(self, limit: int = 50) -> list[dict[str, Any]]:
        out = sorted(self._lotes.values(), key=lambda x: x["lote_id"], reverse=True)
        return [dict(x) for x in out[:limit]]

    def obtener(self, lote_id: int) -> dict[str, Any] | None:
        lote = self._lotes.get(lote_id)
        if lote is None:
            return None
        d = dict(lote)
        d["gastos"] = [
            doc.a_dict()
            for doc in self._repo_op.filas.values()
            if doc.lote_caja_chica_id == lote_id
        ]
        d["n_gastos"] = len(d["gastos"])
        return d

    def crear(
        self,
        *,
        responsable: str,
        moneda: str,
        documento_ids: list[int],
        notas: str = "",
        actor: str = "sistema",
    ) -> dict[str, Any]:
        lid = self._sig
        self._sig += 1
        total = Decimal("0")
        for did in documento_ids:
            doc = self._repo_op.obtener(did)
            if doc is not None:
                total += Decimal(str(doc.monto or 0))
                self._repo_op.actualizar(did, {"lote_caja_chica_id": lid})
        lote = {
            "lote_id": lid,
            "codigo": f"CCH-202611-{lid:03d}",
            "responsable": responsable.strip(),
            "moneda": (moneda or "USD").upper(),
            "monto_total": float(total),
            "estado": "ABIERTO",
            "notas": notas.strip(),
            "n_gastos": len(documento_ids),
            "creado_por": actor,
            "creado_en": datetime.now().isoformat(),
            "cerrado_en": None,
        }
        self._lotes[lid] = lote
        return dict(lote)

    def cerrar(self, lote_id: int, *, estado: str = "CERRADO") -> dict[str, Any] | None:
        lote = self._lotes.get(lote_id)
        if lote is None:
            return None
        lote["estado"] = estado
        lote["cerrado_en"] = datetime.now().isoformat()
        return self.obtener(lote_id)


def _odoo_falso_contable() -> OdooFalso:
    odoo = OdooFalso()
    odoo.r["res.partner:search"] = [601]
    odoo.r["res.company:search_read"] = [{"id": 1, "currency_id": [2, "VES"]}]
    odoo.r["account.move:fields_get"] = {
        **odoo.r["account.move:fields_get"],
        "date": {},
        "line_ids": {},
    }
    odoo.r["account.move.line:fields_get"] = {
        **odoo.r["account.move.line:fields_get"],
        "partner_id": {},
        "currency_id": {},
        "amount_currency": {},
    }
    odoo.r["account.journal:search_read"] = lambda args, _k: (
        [{"id": 15, "name": "Operaciones Varias", "code": "MISC", "currency_id": False}]
        if args
        and any(
            isinstance(d, list) and d[:2] == ["type", "="] and d[2] == "general" for d in args[0]
        )
        else [
            {"id": 99, "name": "Compras USD", "code": "BUSD", "currency_id": [1, "USD"]},
            {"id": 10, "name": "Vendor Bills", "code": "BILL", "currency_id": False},
        ]
    )
    return odoo


def _como(user: dict[str, Any]) -> Any:
    return patch("cxc.web.app.get_current_user_from_cookie", return_value=user)


def _armar_entorno() -> (
    tuple[InMemoryRepository, RepoOpMem4, RepoLotesMem4, OdooFalso, ServicioOperativos]
):
    repo_cxc = InMemoryRepository()
    repo_cxc.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 19, 10, 0),
            tasa_bcv=Decimal("40.00"),
            tasa_binance=Decimal("45.00"),
            fuente="test",
        )
    )
    repo_op = RepoOpMem4()
    repo_lotes = RepoLotesMem4(repo_op)
    odoo = _odoo_falso_contable()
    servicio = ServicioOperativos(
        repo_op,
        lambda: EscritorOdoo(odoo),
        obtener_contactos=lambda: CONTACTOS_FASE4,
        obtener_cuentas=lambda: CUENTAS_FASE4,
        repo_cxc=repo_cxc,
    )
    return repo_cxc, repo_op, repo_lotes, odoo, servicio


@pytest.fixture
def env_web() -> (
    Iterator[tuple[InMemoryRepository, RepoOpMem4, RepoLotesMem4, OdooFalso, ServicioOperativos]]
):
    repo_cxc, repo_op, repo_lotes, odoo, servicio = _armar_entorno()
    app.dependency_overrides[get_servicio_operativos] = lambda: servicio
    app.dependency_overrides[get_repo_lotes_caja_chica] = lambda: repo_lotes
    with patch("cxc.web.app.get_repo", return_value=repo_cxc):
        try:
            yield repo_cxc, repo_op, repo_lotes, odoo, servicio
        finally:
            app.dependency_overrides.clear()


def _mc(
    neto: str,
    *,
    cat: str = "egreso_otro",
    f: date = HOY,
    ref: str = "99001122",
    desc: str = "TRF ESTACION SAN JUAN",
    banco: str = "BANCAMIGA",
    orden: int = 1,
) -> MovimientoClasificado:
    n = Decimal(neto)
    m = MovimientoBancario(
        banco=banco,
        cuenta=banco,
        moneda="VES",
        fecha=f,
        referencia=ref,
        descripcion=desc,
        debito=-n if n < 0 else Decimal("0"),
        credito=n if n > 0 else Decimal("0"),
        saldo=Decimal("1000") + orden,
        orden=orden,
    )
    return MovimientoClasificado(m, cat, "r")


# ---------------------------------------------------------------- Tests Fase 4


def test_gasto_sin_factura_ves_y_usd_multimoneda_crean_entry_balanceado(
    env_web: tuple[InMemoryRepository, RepoOpMem4, RepoLotesMem4, OdooFalso, ServicioOperativos],
) -> None:
    repo_cxc, _repo_op, _lotes, odoo, servicio = env_web

    with _como(ADMIN_USER):
        r_cfg = client.post(
            "/api/config/gastos-contabilidad",
            json={
                "diario_general_id": 15,
                "cuentas_credito_por_metodo": {
                    "caja_chica": {
                        "account_id": 750,
                        "codigo": "110101",
                        "nombre": "Caja Chica USD",
                    },
                    "efectivo_ves": {
                        "account_id": 751,
                        "codigo": "110102",
                        "nombre": "Banco Bancamiga VES",
                    },
                },
                "cuentas_debito_por_categoria": {
                    "combustible": {
                        "account_id": 711,
                        "codigo": "610202",
                        "nombre": "Combustible y Viáticos",
                    }
                },
            },
        )
    assert r_cfg.status_code == 200
    assert r_cfg.json()["config"]["diario_general_id"] == 15

    # 1) Gasto VES sin factura -> entry balanceado VES (Deb 711 / Cred 751), NUNCA in_invoice
    g_ves = servicio.crear(
        {
            "tipo": "gasto",
            "contraparte_nombre": "ESTACION DE SERVICIO SAN JUAN",
            "rif": "J-301112229",
            "monto": "800.00",
            "moneda": "VES",
            "metodo_pago": "efectivo",
            "categoria": "combustible",
            "concepto": "Gasolina ruta Valencia",
            "sin_factura": True,
            "fecha_documento": HOY.isoformat(),
        }
    )
    env_ves = servicio.aprobar_y_enviar_a_odoo(g_ves.documento_id, actor="compras@lubrikca.com")
    assert env_ves.estado == ENVIADO_ODOO
    assert env_ves.odoo_model == "account.move"

    # 2) Gasto USD con tasa BCV 40.00 -> multi-moneda balanceado en VES (25 * 40 = 1000)
    g_usd = servicio.crear(
        {
            "tipo": "gasto",
            "contraparte_nombre": "ESTACION DE SERVICIO SAN JUAN",
            "rif": "J-301112229",
            "monto": "25.00",
            "moneda": "USD",
            "metodo_pago": "caja_chica",
            "categoria": "combustible",
            "concepto": "Viatico chofer",
            "sin_factura": True,
            "fecha_documento": HOY.isoformat(),
        }
    )
    servicio.aprobar_y_enviar_a_odoo(g_usd.documento_id, actor="compras@lubrikca.com")

    moves = odoo.creados_de("account.move")
    assert len(moves) == 2
    assert all(m["move_type"] == "entry" for m in moves)
    assert all("invoice_line_ids" not in m for m in moves)

    mv_ves, mv_usd = moves
    assert mv_ves["journal_id"] == 15
    assert mv_ves["ref"] == "S/N-GAS-00001"
    l_ves = [x[2] for x in mv_ves["line_ids"]]
    assert (
        l_ves[0]["account_id"] == 711 and l_ves[0]["debit"] == 800.0 and l_ves[0]["credit"] == 0.0
    )
    assert (
        l_ves[1]["account_id"] == 751 and l_ves[1]["credit"] == 800.0 and l_ves[1]["debit"] == 0.0
    )

    l_usd = [x[2] for x in mv_usd["line_ids"]]
    assert l_usd[0]["account_id"] == 711
    assert l_usd[0]["currency_id"] == 1 and l_usd[0]["amount_currency"] == 25.0
    assert l_usd[0]["debit"] == 1000.0
    assert l_usd[1]["account_id"] == 750
    assert l_usd[1]["currency_id"] == 1 and l_usd[1]["amount_currency"] == -25.0
    assert l_usd[1]["credit"] == 1000.0


def test_factura_proveedor_con_numero_sigue_creando_in_invoice(
    env_web: tuple[InMemoryRepository, RepoOpMem4, RepoLotesMem4, OdooFalso, ServicioOperativos],
) -> None:
    _repo_cxc, _repo_op, _lotes, odoo, servicio = env_web
    fac = servicio.crear(
        {
            "tipo": "factura_proveedor",
            "contraparte_nombre": "ESTACION DE SERVICIO SAN JUAN",
            "rif": "J-301112229",
            "numero_documento": "FAC-9001",
            "monto": "1200.00",
            "base_imponible": "1034.48",
            "iva": "165.52",
            "moneda": "VES",
            "categoria": "combustible",
            "concepto": "Factura fiscal lubricantes",
            "fecha_documento": HOY.isoformat(),
        }
    )
    servicio.aprobar_y_enviar_a_odoo(fac.documento_id, actor="compras@lubrikca.com")
    moves = odoo.creados_de("account.move")
    assert len(moves) == 1
    assert moves[0]["move_type"] == "in_invoice"
    assert moves[0]["journal_id"] == 10
    assert moves[0]["ref"] == "FAC-9001"


def test_odoo_reader_changed_compras_gastos_mapea_facturas_nc_y_asientos() -> None:
    llamadas: list[tuple[str, list[Any]]] = []

    def fake_exec(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if model == "account.move" and method == "search_read":
            dom = args[0]
            llamadas.append((model, dom))
            if any(isinstance(d, list) and d[0] == "move_type" and d[1] == "in" for d in dom):
                return [
                    {
                        "id": 8801,
                        "name": "BILL/2026/0015",
                        "move_type": "in_invoice",
                        "partner_id": [601, "IMPORTADORA NORTE CA"],
                        "amount_total": 3500.0,
                        "currency_id": [2, "VES"],
                        "journal_id": [10, "Compras VES"],
                        "ref": "F-00451",
                        "invoice_date": "2026-11-18",
                        "date": "2026-11-18",
                        "state": "posted",
                        "payment_state": "not_paid",
                    },
                    {
                        "id": 8802,
                        "name": "RBILL/2026/0003",
                        "move_type": "in_refund",
                        "partner_id": [601, "IMPORTADORA NORTE CA"],
                        "amount_total": 200.0,
                        "currency_id": [1, "USD"],
                        "journal_id": [10, "Compras VES"],
                        "ref": "NC-12",
                        "invoice_date": "2026-11-19",
                        "date": "2026-11-19",
                        "state": "draft",
                        "payment_state": "not_paid",
                    },
                ]
            return [
                {
                    "id": 8803,
                    "name": "MISC/2026/0099",
                    "move_type": "entry",
                    "partner_id": [601, "IMPORTADORA NORTE CA"],
                    "amount_total": 40.0,
                    "currency_id": [1, "USD"],
                    "journal_id": [15, "Operaciones Varias"],
                    "ref": "S/N-GAS-00012",
                    "invoice_date": False,
                    "date": "2026-11-19",
                    "state": "posted",
                    "payment_state": "",
                }
            ]
        if model == "res.partner" and method == "read":
            return [{"id": 601, "vat": "J-301112229"}]
        return []

    reader = OdooXmlRpcReader(OdooConfig("https://odoo.test", "db", "u", "p"), execute=fake_exec)
    rows = reader.changed_compras_gastos(None)
    assert len(rows) == 3
    por_id = {r["move_id"]: r for r in rows}
    assert por_id["8801"]["tipo_cxc"] == "factura_proveedor"
    assert por_id["8801"]["moneda"] == "VES"
    assert por_id["8801"]["rif"] == "J-301112229"
    assert por_id["8802"]["move_type"] == "in_refund"
    assert por_id["8802"]["moneda"] == "USD"
    assert por_id["8803"]["move_type"] == "entry"
    assert por_id["8803"]["tipo_cxc"] == "gasto"
    assert por_id["8803"]["referencia"] == "S/N-GAS-00012"
    assert len(llamadas) == 2


def test_compras_unificadas_endpoint_deduplica_filtra_y_exige_sesion(
    env_web: tuple[InMemoryRepository, RepoOpMem4, RepoLotesMem4, OdooFalso, ServicioOperativos],
) -> None:
    repo_cxc, repo_op, _lotes, _odoo, servicio = env_web

    assert client.get("/api/compras/unificadas").status_code == 401

    d_env = servicio.crear(
        {
            "tipo": "gasto",
            "contraparte_nombre": "ESTACION DE SERVICIO SAN JUAN",
            "monto": "500.00",
            "moneda": "VES",
            "categoria": "combustible",
            "concepto": "Gasolina camioneta",
            "sin_factura": True,
            "fecha_documento": HOY.isoformat(),
        }
    )
    repo_op.actualizar(
        d_env.documento_id,
        {
            "estado": ENVIADO_ODOO,
            "odoo_model": "account.move",
            "odoo_res_id": 9101,
            "odoo_ref": "account.move:9101",
        },
    )
    servicio.crear(
        {
            "tipo": "factura_proveedor",
            "contraparte_nombre": "LUBRICANTES DEL SUR",
            "numero_documento": "FAC-222",
            "monto": "2500.00",
            "moneda": "VES",
            "fecha_documento": HOY.isoformat(),
        }
    )

    repo_cxc.upsert_compras_gastos_odoo(
        [
            # Mismo move_id 9101 -> deduplicado y marcado en_espejo_odoo=True sobre item CxC
            {
                "move_id": "9101",
                "numero_odoo": "MISC/2026/0010",
                "move_type": "entry",
                "tipo_cxc": "gasto",
                "partner_odoo_id": 601,
                "proveedor_nombre": "ESTACION DE SERVICIO SAN JUAN",
                "rif": "J-301112229",
                "monto_total": Decimal("500.00"),
                "moneda": "VES",
                "diario_id": 15,
                "diario_nombre": "Operaciones Varias",
                "referencia": "S/N-GAS-00001",
                "fecha_documento": HOY,
                "estado_odoo": "posted",
                "estado_pago": "",
            },
            # Nacido directo en Odoo
            {
                "move_id": "9102",
                "numero_odoo": "BILL/2026/0088",
                "move_type": "in_invoice",
                "tipo_cxc": "factura_proveedor",
                "partner_odoo_id": 602,
                "proveedor_nombre": "PROVEEDOR DIRECTO ODOO CA",
                "rif": "J-409998881",
                "monto_total": Decimal("7400.00"),
                "moneda": "VES",
                "diario_id": 10,
                "diario_nombre": "Compras VES",
                "referencia": "FAC-ODOO-88",
                "fecha_documento": HOY,
                "estado_odoo": "posted",
                "estado_pago": "not_paid",
            },
        ]
    )

    with _como(VENTAS_USER):
        r = client.get("/api/compras/unificadas")
    assert r.status_code == 200
    data = r.json()
    assert data["resumen"]["origen_cxc"] == 2
    assert data["resumen"]["origen_odoo"] == 1
    assert data["total"] == 3
    por_num = {it["numero"]: it for it in data["items"]}
    assert por_num["GAS-00001"]["en_espejo_odoo"] is True
    assert por_num["GAS-00001"]["asiento_contable"] is True
    assert por_num["BILL/2026/0088"]["origen_sistema"] == "odoo"
    assert por_num["BILL/2026/0088"]["solo_lectura"] is True

    with _como(VENTAS_USER):
        r_odoo = client.get("/api/compras/unificadas?origen=odoo&q=DIRECTO")
        r_gas = client.get("/api/compras/unificadas?tipo=gasto")
    assert [x["numero"] for x in r_odoo.json()["items"]] == ["BILL/2026/0088"]
    assert all(x["tipo"] == "gasto" for x in r_gas.json()["items"])


def test_regla_r4_conciliacion_bancaria_y_prioridades() -> None:
    repo = RepoOpMem4()
    g1 = repo.crear(
        {
            "tipo": "gasto",
            "token_preview": "t1",
            "estado": PENDIENTE_APROBACION,
            "contraparte_nombre": "ESTACION SAN JUAN",
            "rif": "J-301112229",
            "monto": Decimal("350.00"),
            "moneda": "VES",
            "referencia": "44556677",
            "fecha_documento": HOY,
            "cuenta_contable_codigo": "610202",
            "cuenta_contable_nombre": "Combustible",
        }
    )
    g2 = repo.crear(
        {
            "tipo": "gasto",
            "token_preview": "t2",
            "estado": PENDIENTE_APROBACION,
            "contraparte_nombre": "OTRO COMERCIO",
            "monto": Decimal("350.00"),
            "moneda": "VES",
            "referencia": "00001111",
            "fecha_documento": HOY,
            "cuenta_contable_codigo": "610999",
            "cuenta_contable_nombre": "Otros",
        }
    )
    pprov = repo.crear(
        {
            "tipo": "pago_proveedor",
            "token_preview": "t3",
            "estado": ENVIADO_ODOO,
            "contraparte_nombre": "IMPORTADORA GLOBAL",
            "monto": Decimal("1200.00"),
            "moneda": "VES",
            "referencia": "778899",
            "fecha_documento": HOY,
        }
    )
    g_usd = repo.crear(
        {
            "tipo": "gasto",
            "token_preview": "t4",
            "estado": PENDIENTE_APROBACION,
            "contraparte_nombre": "LOCAL USD",
            "monto": Decimal("99.00"),
            "moneda": "USD",
            "fecha_documento": HOY,
        }
    )
    g_borrador = repo.crear(
        {
            "tipo": "gasto",
            "token_preview": "t5",
            "estado": INCOMPLETO,
            "contraparte_nombre": "INCOMPLETO",
            "monto": Decimal("88.00"),
            "moneda": "VES",
            "fecha_documento": HOY,
        }
    )

    cands = egresos_desde_operativos([g1, g2, pprov, g_usd, g_borrador])
    assert [c.doc_id for c in cands] == [
        g1.documento_id,
        g2.documento_id,
        pprov.documento_id,
        g_usd.documento_id,
    ]

    # 1) Desempate entre g1 y g2 por referencia + RIF + nombre
    (pr1,) = proponer([_mc("-350.00", ref="44556677", desc="PAGO J301112229 SAN JUAN")], [], cands)
    assert (pr1.tipo, pr1.regla, pr1.candidatos) == (CUENTA, "R4", [g1.documento_id])
    assert "610202" in pr1.cuenta

    # 2) Débito vs pago_proveedor -> tipo PAGO, regla R4
    (pr2,) = proponer([_mc("-1200.00", ref="00778899", desc="TRF IMPORTADORA")], [], cands)
    assert (pr2.tipo, pr2.regla, pr2.pago_nombre, pr2.candidatos) == (
        PAGO,
        "R4",
        pprov.numero,
        [pprov.documento_id],
    )

    # 3) Empate exacto produce AMBIGUO
    g_a = repo.crear(
        {
            "tipo": "gasto",
            "token_preview": "ta",
            "estado": PENDIENTE_APROBACION,
            "contraparte_nombre": "X",
            "monto": Decimal("50.00"),
            "moneda": "VES",
            "fecha_documento": HOY,
        }
    )
    g_b = repo.crear(
        {
            "tipo": "gasto",
            "token_preview": "tb",
            "estado": PENDIENTE_APROBACION,
            "contraparte_nombre": "Y",
            "monto": Decimal("50.00"),
            "moneda": "VES",
            "fecha_documento": HOY,
        }
    )
    (pr_amb,) = proponer(
        [_mc("-50.00", ref="999", desc="DEBITO")],
        [],
        egresos_desde_operativos([g_a, g_b]),
    )
    assert (pr_amb.tipo, pr_amb.regla) == (AMBIGUO, "R4")

    # 4) Prioridad R1 > R2 > R4
    (pr_r1,) = proponer([_mc("-350.00", cat="comision_bancaria", ref="44556677")], [], cands)
    assert (pr_r1.tipo, pr_r1.regla, pr_r1.cuenta) == (CUENTA, "R1", CUENTA_COMISIONES)

    pago_r2 = PagoCandidato(
        pago_id=55,
        nombre="PBAMI/2026/00055",
        fecha=HOY,
        monto=Decimal("350.00"),
        entrante=False,
        memo="44556677",
        partner="Prov",
        partner_rifs=frozenset(),
        ya_conciliado=False,
    )
    (pr_r2,) = proponer([_mc("-350.00", ref="44556677")], [pago_r2], cands)
    assert (pr_r2.tipo, pr_r2.regla, pr_r2.pago_id) == (PAGO, "R2", 55)

    # 5) Guarda de moneda: banco BANCAMIGA (VES) no empata contra gasto USD 99.00
    (pr_mon,) = proponer([_mc("-99.00", banco="BANCAMIGA")], [], cands)
    assert pr_mon.regla != "R4"


def test_config_gastos_contabilidad_permisos_y_lotes_caja_chica(
    env_web: tuple[InMemoryRepository, RepoOpMem4, RepoLotesMem4, OdooFalso, ServicioOperativos],
) -> None:
    _repo_cxc, repo_op, _lotes, _odoo, servicio = env_web

    assert client.get("/api/config/gastos-contabilidad").status_code == 401
    with _como(VENTAS_USER):
        assert (
            client.post(
                "/api/config/gastos-contabilidad", json={"diario_general_id": 15}
            ).status_code
            == 403
        )

    g1 = servicio.crear(
        {
            "tipo": "gasto",
            "contraparte_nombre": "ESTACION DE SERVICIO SAN JUAN",
            "monto": "30.00",
            "moneda": "USD",
            "metodo_pago": "caja_chica",
            "categoria": "combustible",
            "concepto": "Gasolina",
            "sin_factura": True,
            "fecha_documento": HOY.isoformat(),
        }
    )
    g2 = servicio.crear(
        {
            "tipo": "gasto",
            "contraparte_nombre": "FERRETERIA NORTE",
            "monto": "20.00",
            "moneda": "USD",
            "metodo_pago": "caja_chica",
            "categoria": "mantenimiento",
            "concepto": "Bombillos",
            "sin_factura": True,
            "fecha_documento": HOY.isoformat(),
        }
    )
    # Simular g1 ya enviado a Odoo y aún así permitir conciliación bancaria posterior
    repo_op.actualizar(
        g1.documento_id,
        {
            "estado": ENVIADO_ODOO,
            "odoo_model": "account.move",
            "odoo_res_id": 9200,
            "odoo_ref": "account.move:9200",
        },
    )

    with _como(COMPRAS_USER):
        r_conc = client.get("/api/compras/conciliacion-gastos")
        assert r_conc.status_code == 200
        assert r_conc.json()["total_egresos"] == 2

        r_vinc = client.post(
            "/api/compras/conciliacion-gastos/vincular",
            json={"documento_id": g1.documento_id, "huella": "HUELLA-BANCO-123"},
        )
        assert r_vinc.status_code == 200
        assert r_vinc.json()["documento"]["conciliado_banco_huella"] == "HUELLA-BANCO-123"

        r_desv = client.post(
            "/api/compras/conciliacion-gastos/vincular",
            json={"documento_id": g1.documento_id, "huella": ""},
        )
        assert r_desv.status_code == 200
        assert r_desv.json()["documento"]["conciliado_banco_huella"] == ""

        r_lote = client.post(
            "/api/compras/lotes-caja-chica",
            json={
                "responsable": "Carlos Cajero",
                "moneda": "USD",
                "documento_ids": [g1.documento_id, g2.documento_id],
                "notas": "Rendición semana 47",
            },
        )
        assert r_lote.status_code == 201
        lote_id = r_lote.json()["lote_id"]
        assert r_lote.json()["monto_total"] == 50.0
        assert r_lote.json()["n_gastos"] == 2

        r_lista = client.get("/api/compras/lotes-caja-chica")
        assert r_lista.status_code == 200 and len(r_lista.json()["items"]) == 1

        r_cerrar = client.post(
            f"/api/compras/lotes-caja-chica/{lote_id}/cerrar",
            json={"contabilizar": True},
        )
        assert r_cerrar.status_code == 200
        d_cerr = r_cerrar.json()
        assert d_cerr["ok"] is True
        assert d_cerr["enviados"] == [g2.numero]
        assert d_cerr["lote"]["estado"] == "CONTABILIZADO"


def test_compras_gastos_odoo_y_lotes_sql_si_hay_postgres() -> None:
    from sqlalchemy import delete

    from cxc.db import schema
    from cxc.db.engine import make_engine
    from cxc.db.postgres_repository import PostgresRepository
    from cxc.operativos.repositorio import RepoLotesCajaChicaSQL

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("Sin DATABASE_URL para probar compras_gastos_odoo / lotes_caja_chica SQL.")
    eng = make_engine(url)
    schema.metadata.create_all(
        eng,
        tables=[
            schema.compras_gastos_odoo,
            schema.lotes_caja_chica,
            schema.documentos_operativos,
        ],
        checkfirst=True,
    )
    repo_sql = PostgresRepository(eng)
    repo_lotes_sql = RepoLotesCajaChicaSQL(eng)
    created_lote_id: int | None = None
    with eng.begin() as conn:
        conn.execute(
            delete(schema.compras_gastos_odoo).where(
                schema.compras_gastos_odoo.c.move_id == "TEST-F4-999"
            )
        )
    try:
        repo_sql.upsert_compras_gastos_odoo(
            [
                {
                    "move_id": "TEST-F4-999",
                    "numero_odoo": "BILL/TEST/999",
                    "move_type": "in_invoice",
                    "tipo_cxc": "factura_proveedor",
                    "partner_odoo_id": 601,
                    "proveedor_nombre": "PROV SQL TEST",
                    "rif": "J-301112229",
                    "monto_total": Decimal("123.45"),
                    "moneda": "VES",
                    "diario_id": 10,
                    "diario_nombre": "Compras VES",
                    "referencia": "FAC-SQL-1",
                    "fecha_documento": HOY,
                    "estado_odoo": "posted",
                    "estado_pago": "not_paid",
                }
            ]
        )
        rows = [r for r in repo_sql.all_compras_gastos_odoo() if r["move_id"] == "TEST-F4-999"]
        assert len(rows) == 1 and rows[0]["monto_total"] == Decimal("123.45")

        lote = repo_lotes_sql.crear(
            responsable="QA Caja SQL",
            moneda="USD",
            documento_ids=[],
            notas="Test SQL",
            actor="pytest",
        )
        created_lote_id = int(lote["lote_id"])
        cerrado = repo_lotes_sql.cerrar(created_lote_id, estado="CERRADO")
        assert cerrado is not None and cerrado["estado"] == "CERRADO"
    finally:
        with eng.begin() as conn:
            conn.execute(
                delete(schema.compras_gastos_odoo).where(
                    schema.compras_gastos_odoo.c.move_id == "TEST-F4-999"
                )
            )
            if created_lote_id is not None:
                conn.execute(
                    delete(schema.lotes_caja_chica).where(
                        schema.lotes_caja_chica.c.lote_id == created_lote_id
                    )
                )
