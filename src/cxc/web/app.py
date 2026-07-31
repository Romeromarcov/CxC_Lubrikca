import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import traceback
from dataclasses import replace as dataclasses_replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import BackgroundTasks, Cookie, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cxc.auth import (
    NOMBRES_ROLES,
    ROLES_PERMISOS,
    autenticar_usuario,
    buscar_usuario_plataforma,
    crear_session_token,
    obtener_usuarios_plataforma,
    registrar_o_actualizar_usuario,
    verificar_session_token,
    verificar_usuario_odoo_activo,
)
from cxc.config import AppConfig
from cxc.db.postgres_repository import PostgresRepository
from cxc.engine.runner import EngineRunner
from cxc.models import Cliente, EstadoVinculacion, Moneda, OrdenVenta, TipoTasa, Vinculacion
from cxc.odoo.client import PAGO_ESTADOS_CONFIRMADOS, OdooXmlRpcReader, _connect
from cxc.odoo.price import OdooPriceResolver
from cxc.reconciliation.reconcile import OdooFacturasReader, Reconciler
from cxc.repositories import Repository
from cxc.sheets.gateway import GspreadGateway
from cxc.sheets.repository import SheetsRepository
from cxc.sync.incremental import IncrementalSync

logger = logging.getLogger("cxc.web.app")

# Reconfigure stdout to use UTF-8
sys.stdout.reconfigure(encoding="utf-8")

app = FastAPI(title="CxC Lubrikca Billing Dashboard")

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def parse_decimal_safe(val) -> Decimal:
    if not val:
        return Decimal("0")
    s = str(val).strip()
    s = s.replace("$", "").replace("€", "").replace("Bs.", "").replace("Bs", "").strip()
    if not s:
        return Decimal("0")
    # European/Spanish format check: comma for decimals
    if "," in s:
        if "." in s and s.find(".") < s.find(","):
            s = s.replace(".", "").replace(",", ".")
        elif "." not in s:
            s = s.replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return Decimal("0")


# Estados de sale.order que NUNCA deben entrar a un reporte, bandeja o
# cálculo de cobranza: Cancelada (cancel), Cotización en cualquiera de sus
# dos sub-estados Odoo (draft/sent). Regla global — ver auditoría.
#
# Excepción de negocio: una orden CANCELADA cuya mercancía ya salió de
# almacén (ALM/OUT, stock.picking saliente en estado "done") y el cliente
# no la devolvió sigue siendo una venta real -- Odoo permite cancelar una SO
# después del despacho y eso no deshace la entrega. Ver
# get_live_delivered_not_returned() / parámetro entrega_valida.
ESTADOS_ORDEN_EXCLUIDOS = frozenset({"cancel", "cancelled", "draft", "sent"})


def orden_excluida(o: Any, live_state: str | None = None, entrega_valida: bool = False) -> bool:
    """True si la orden debe excluirse de cualquier reporte/bandeja/cálculo.

    Usa el estado en vivo de Odoo si se provee (más fresco que el mirror);
    si no, cae al `estado_orden` ya sincronizado en la orden. `entrega_valida`
    es la excepción de negocio: una orden cancelada con entrega ALM/OUT sin
    devolver no se excluye (ver comentario de ESTADOS_ORDEN_EXCLUIDOS).
    """
    st = (
        live_state if live_state is not None else str(getattr(o, "estado_orden", "sale") or "")
    ).strip().lower()
    if st not in ESTADOS_ORDEN_EXCLUIDOS:
        return False
    return not (st in ("cancel", "cancelled") and entrega_valida)


def get_live_so_states(so_names: list[str]) -> dict[str, str]:
    """Estado EN VIVO de cada sale.order en Odoo, para usar con orden_excluida.

    El espejo local (estado_orden) puede quedar desactualizado si una orden
    se cancela/revierte en Odoo y el sync incremental no la vuelve a traer
    (ventana delta de 48h vencida, downtime del servidor, etc.) -- verificado
    en vivo: una orden cancelada de $161,679.06 seguía contando como venta
    confirmada porque el espejo nunca se refrescó. Best-effort: si Odoo no
    responde, se devuelve vacío y el llamador cae al estado local.
    """
    if not so_names:
        return {}
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        if not execute:
            return {}
        so_recs = execute(
            "sale.order", "search_read", [[["name", "in", so_names]]], {"fields": ["name", "state"]}
        )
        return {
            str(s["name"]).strip(): str(s.get("state", "")).strip().lower()
            for s in so_recs
            if str(s.get("name", "")).strip()
        }
    except Exception as e:
        logger.warning("Error consultando estado en vivo de órdenes en Odoo: %s", e)
        return {}


def get_live_delivered_not_returned(so_names: list[str], execute: Any = None) -> set[str]:
    """SO names con entrega ALM/OUT (stock.picking saliente, estado "done") y SIN devolución.

    Es la excepción de negocio de ESTADOS_ORDEN_EXCLUIDOS: una orden CANCELADA
    en Odoo después de que la mercancía ya salió de almacén sigue siendo una
    venta real -- cancelar la SO no deshace el despacho. Acepta un `execute`
    ya conectado para reusar la conexión del llamador; si no se provee, abre
    una propia. Best-effort: ante cualquier error devuelve vacío.
    """
    if not so_names:
        return set()
    if execute is None:
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
        except Exception as e:
            logger.warning("Error conectando a Odoo en get_live_delivered_not_returned: %s", e)
            return set()
    if not execute:
        return set()
    try:
        so_records = execute(
            "sale.order",
            "search_read",
            [[["name", "in", so_names]]],
            {"fields": ["name", "picking_ids"]},
        )
        picking_to_so: dict[int, str] = {}
        for s in so_records:
            sname = str(s.get("name", "")).strip()
            p_ids = s.get("picking_ids") or []
            if sname and isinstance(p_ids, list | tuple):
                for pid in p_ids:
                    picking_to_so[pid] = sname
        if not picking_to_so:
            return set()
        pickings = execute(
            "stock.picking",
            "search_read",
            [[["id", "in", list(picking_to_so.keys())], ["state", "=", "done"]]],
            {"fields": ["id", "picking_type_code", "return_id"]},
        )
        delivered: set[str] = set()
        returned: set[str] = set()
        for p in pickings:
            so_name = picking_to_so.get(p["id"])
            if not so_name:
                continue
            if bool(p.get("return_id")) or str(p.get("picking_type_code")) == "incoming":
                returned.add(so_name)
            elif str(p.get("picking_type_code")) == "outgoing":
                delivered.add(so_name)
        return delivered - returned
    except Exception as e:
        logger.warning("Error consultando entregas en get_live_delivered_not_returned: %s", e)
        return set()


def resolve_vendedores_por_partner(execute: Any, partner_ids: set[int]) -> dict[int, str]:
    """Email del vendedor (res.users.login) asignado a cada partner (res.partner.user_id).

    Mismo criterio que ``OdooXmlRpcReader._vendedor_por_partner`` (odoo/client.py),
    reimplementado aquí sobre el ``execute`` crudo para no depender de esa clase.
    """
    if not partner_ids:
        return {}
    try:
        partners = execute(
            "res.partner", "read", [list(partner_ids)], {"fields": ["id", "user_id"]}
        )
        uids = {
            int(p["user_id"][0])
            for p in partners
            if isinstance(p.get("user_id"), list | tuple) and p["user_id"]
        }
        logins: dict[int, str] = {}
        if uids:
            users = execute("res.users", "read", [list(uids)], {"fields": ["id", "login"]})
            logins = {int(u["id"]): str(u.get("login") or "") for u in users}
        out: dict[int, str] = {}
        for p in partners:
            u = p.get("user_id")
            uid = u[0] if isinstance(u, list | tuple) and u else None
            out[int(p["id"])] = logins.get(int(uid), "") if uid else ""
        return out
    except Exception as e:
        logger.warning("Error resolviendo vendedores por partner: %s", e)
        return {}


def get_live_pagos_confirmados(execute: Any) -> list[dict[str, Any]]:
    """Pagos de cliente CONFIRMADOS en vivo desde Odoo -- espejo exacto.

    ``account.payment`` no tiene estado "posted" (eso es de account.move);
    sus estados confirmados son in_process/paid. NO filtra por
    ``is_reconciled`` -- ese es justo el bug que esto reemplaza: el sync
    incremental (``changed_pagos`` en odoo/client.py) SOLO trae pagos
    is_reconciled=False a la hoja local "Pagos" (esa hoja existe para
    sugerir vinculaciones manuales, no para totalizar cobranza), así que
    en cuanto Odoo reconcilia un pago contra una factura, el sync deja de
    traerlo -- verificado en vivo: de 882 pagos confirmados en Odoo, 673
    (76%) ya estaban reconciliados y el total de cobranza del dashboard
    quedaba ~$16,562 por debajo del real de Odoo.

    Usa ``amount_ref`` (el mismo campo "Importe referencia" que Odoo
    muestra en su propia lista de pagos, ya en USD) en vez de recalcular
    la tasa BCV nosotros mismos -- así no hay forma de que la tasa usada
    difiera de la que usó Odoo para esa transacción puntual. Verificado en
    vivo: sum(amount_ref) para este dominio = $234,091.96, exactamente el
    total que muestra Odoo en Contabilidad > Pagos del cliente.
    """
    recs = execute(
        "account.payment",
        "search_read",
        [
            [
                ["payment_type", "=", "inbound"],
                ["partner_type", "=", "customer"],
                ["state", "in", PAGO_ESTADOS_CONFIRMADOS],
            ]
        ],
        {
            "fields": [
                "id",
                "amount",
                "amount_ref",
                "currency_id",
                "journal_id",
                "partner_id",
                "date",
            ]
        },
    )
    partner_ids = {
        int(p["partner_id"][0])
        for p in recs
        if isinstance(p.get("partner_id"), list | tuple) and p["partner_id"]
    }
    vendedores = resolve_vendedores_por_partner(execute, partner_ids)
    for r in recs:
        pinfo = r.get("partner_id")
        pid = pinfo[0] if isinstance(pinfo, list | tuple) and pinfo else None
        r["vendedor_email"] = vendedores.get(int(pid), "") if pid else ""
    return recs


def get_live_pagos_conciliados(execute: Any) -> list[dict[str, Any]]:
    """Pagos de cliente RECONCILIADOS en Odoo, con su(s) factura(s), monto

    conciliado y residual -- una fila por pago (no una por orden, para no
    duplicar un pago que reconcilia facturas de varias órdenes).

    Bug corregido: ``pagos_reconciliados_por_orden`` (odoo/client.py) leía
    el campo "invoice_ids" de account.payment -- ese campo lo llena el
    wizard "Registrar Pago"; en pagos reconciliados por matching de banco o
    manualmente (el caso normal acá) queda SIEMPRE vacío. El campo que sí
    refleja la reconciliación real es "reconciled_invoice_ids". Verificado
    en vivo: de 673 pagos con is_reconciled=True, 0 tenían invoice_ids
    poblado y los 673 tenían reconciled_invoice_ids -- la tabla de "Pagos
    Conciliados" quedaba siempre vacía.

    "Monto conciliado" = ``amount_ref`` (ya en USD, mismo campo que Odoo
    usa en su propia lista de pagos) menos ``amount_available_for_refund``
    (lo que del pago sigue disponible/sin aplicar). "Residual" es el saldo
    de la(s) factura(s) asociada(s) (``amount_residual_usd``) -- si sigue
    siendo > 0, esa factura quedó parcialmente pagada por este (u otro)
    pago.
    """
    pagos = execute(
        "account.payment",
        "search_read",
        [
            [
                ["payment_type", "=", "inbound"],
                ["partner_type", "=", "customer"],
                ["state", "in", PAGO_ESTADOS_CONFIRMADOS],
                ["is_reconciled", "=", True],
            ]
        ],
        {
            "fields": [
                "id",
                "partner_id",
                "amount",
                "amount_ref",
                "amount_available_for_refund",
                "currency_id",
                "journal_id",
                "date",
                "reconciled_invoice_ids",
            ]
        },
    )
    if not pagos:
        return []

    all_inv_ids: set[int] = set()
    for p in pagos:
        all_inv_ids.update(p.get("reconciled_invoice_ids") or [])

    invoices_map: dict[int, dict[str, Any]] = {}
    if all_inv_ids:
        invs = execute(
            "account.move",
            "read",
            [list(all_inv_ids)],
            {
                "fields": [
                    "id",
                    "name",
                    "invoice_origin",
                    "move_type",
                    "state",
                    "amount_total_signed_usd",
                    "amount_residual_usd",
                ]
            },
        )
        invoices_map = {i["id"]: i for i in invs}

    partner_ids = {
        int(p["partner_id"][0])
        for p in pagos
        if isinstance(p.get("partner_id"), list | tuple) and p["partner_id"]
    }
    vendedores = resolve_vendedores_por_partner(execute, partner_ids)

    result: list[dict[str, Any]] = []
    for p in pagos:
        inv_ids = p.get("reconciled_invoice_ids") or []
        invs = [invoices_map[i] for i in inv_ids if i in invoices_map]
        facturas = [
            {
                "factura_id": str(i.get("name", "")),
                "so_id": str(i.get("invoice_origin") or ""),
                "monto_usd": float(i.get("amount_total_signed_usd") or 0.0),
                "residual_usd": float(i.get("amount_residual_usd") or 0.0),
                "tipo": str(i.get("move_type") or ""),
            }
            for i in invs
        ]
        residual_facturas = sum(f["residual_usd"] for f in facturas)

        monto_ref = parse_decimal_safe(str(p.get("amount_ref") or "0"))
        residual_pago = parse_decimal_safe(str(p.get("amount_available_for_refund") or "0"))
        monto_conciliado = monto_ref - residual_pago

        pinfo = p.get("partner_id")
        pid = pinfo[0] if isinstance(pinfo, list | tuple) and pinfo else None
        curr_info = p.get("currency_id")
        journal_info = p.get("journal_id")
        result.append(
            {
                "pago_id": str(p.get("id", "")),
                "cliente_id": str(pid) if pid else "",
                "cliente_nombre": pinfo[1]
                if isinstance(pinfo, list | tuple) and len(pinfo) > 1
                else "",
                "fecha_pago": str(p.get("date") or "")[:10],
                "moneda": curr_info[1]
                if isinstance(curr_info, list | tuple) and len(curr_info) > 1
                else "USD",
                "metodo_pago": journal_info[1]
                if isinstance(journal_info, list | tuple) and len(journal_info) > 1
                else "",
                "monto_original": float(p.get("amount") or 0.0),
                "monto_ref_usd": float(monto_ref),
                "monto_conciliado_usd": float(monto_conciliado),
                "residual_pago_usd": float(residual_pago),
                "residual_facturas_usd": round(residual_facturas, 2),
                "facturas": facturas,
                "so_ids": sorted({f["so_id"] for f in facturas if f["so_id"]}),
                "vendedor_email": vendedores.get(int(pid), "") if pid else "",
            }
        )
    return result


def _resincronizar_vinculaciones_con_odoo(repo: Any, execute: Any) -> list[dict[str, Any]]:
    """Regla general del sistema: Odoo siempre prevalece.

    Si un pago se vinculó localmente a una orden (``Vinculacion.so_id``) y
    Odoo luego lo reconcilió contra una factura/orden DISTINTA, la
    Vinculación local debe seguir a Odoo -- no quedarse con la asignación
    manual vieja. Compara cada ``Vinculacion`` cuyo pago ya esté
    reconciliado en Odoo (``get_live_pagos_conciliados``) contra el/los
    ``so_id`` reales que Odoo le asignó, y re-apunta cuando divergen.

    Caso simple (1 Vinculación local, Odoo reconcilió contra 1 sola orden):
    se re-apunta automáticamente y se deja rastro en ``BandejaAuditoria``
    (qué decía antes, qué dice ahora). Caso ambiguo (el pago cubre varias
    Vinculaciones locales o Odoo lo reconcilió contra varias órdenes a la
    vez): no se auto-corrige -- solo se registra la discrepancia en
    auditoría para revisión manual, porque no hay forma inequívoca de
    saber qué Vinculación local corresponde a cuál orden de Odoo sin más
    contexto.

    Devuelve la lista de cambios/discrepancias detectados (para logging).
    """
    conciliados_por_pago = {c["pago_id"]: c for c in get_live_pagos_conciliados(execute)}
    if not conciliados_por_pago:
        return []

    vincs_por_pago: dict[str, list[Vinculacion]] = {}
    for v in repo.all_vinculaciones():
        vincs_por_pago.setdefault(v.pago_id, []).append(v)

    cambios: list[dict[str, Any]] = []
    vincs_a_actualizar: list[Vinculacion] = []
    for pago_id, vincs_locales in vincs_por_pago.items():
        conciliado = conciliados_por_pago.get(pago_id)
        if not conciliado:
            continue  # este pago aun no esta reconciliado en Odoo -- nada que verificar
        so_ids_odoo = set(conciliado["so_ids"])
        if not so_ids_odoo:
            continue

        so_ids_locales = {v.so_id for v in vincs_locales}
        if so_ids_locales == so_ids_odoo:
            continue  # ya coincide

        if len(vincs_locales) == 1 and len(so_ids_odoo) == 1:
            v = vincs_locales[0]
            so_id_nuevo = next(iter(so_ids_odoo))
            vincs_a_actualizar.append(dataclasses_replace(v, so_id=so_id_nuevo))
            cambios.append(
                {
                    "pago_id": pago_id,
                    "so_id_anterior": v.so_id,
                    "so_id_nuevo": so_id_nuevo,
                    "requiere_revision_manual": False,
                }
            )
        else:
            cambios.append(
                {
                    "pago_id": pago_id,
                    "so_id_anterior": ", ".join(sorted(so_ids_locales)),
                    "so_id_nuevo": ", ".join(sorted(so_ids_odoo)),
                    "requiere_revision_manual": True,
                }
            )

    if vincs_a_actualizar:
        repo.update_vinculaciones(vincs_a_actualizar)

    if cambios and hasattr(repo, "append_auditoria_rows"):
        ahora = datetime.now()
        audit_rows = [
            {
                "audit_id": f"RELINK_{c['pago_id']}_{ahora.strftime('%Y%m%d%H%M%S')}",
                "pago_id": c["pago_id"],
                "so_id": c["so_id_nuevo"],
                "tipo_auditoria": (
                    "vinculacion_discrepancia_multi_orden"
                    if c["requiere_revision_manual"]
                    else "vinculacion_revinculada_por_odoo"
                ),
                "motor_calcula_usd": "",
                "odoo_registrado_usd": "",
                "diferencia_usd": "",
                "detalle_odoo": (
                    f"Odoo reconcilió el pago {c['pago_id']} contra: {c['so_id_nuevo']}"
                ),
                "detalle_motor": f"Vinculación local apuntaba a: {c['so_id_anterior']}",
                "estado": "pendiente_revision" if c["requiere_revision_manual"] else "aplicado",
                "revisado_por": "",
                "timestamp_audit": ahora.isoformat(),
            }
            for c in cambios
        ]
        try:
            repo.append_auditoria_rows(audit_rows)
        except Exception as e_aud:
            logger.warning("Error guardando auditoría de re-vinculación por Odoo: %s", e_aud)

    return cambios


def get_reconciled_pago_ids_odoo(execute: Any, pago_ids: list[str]) -> set[str]:
    """IDs (str) de ``account.payment`` que ya no son válidos como "pago sin

    asignar": reconciliados en Odoo, o en un estado distinto de
    in_process/paid (account.payment no tiene estado "posted" -- ese es de
    account.move). ``pago_id`` local es el ID numérico de Odoo
    (``map_pago``: ``pago_id=str(rec["id"])``), nunca el campo "name".
    """
    reconciled: set[str] = set()
    p_ids = [int(pid) for pid in pago_ids if pid.isdigit()]
    if not p_ids:
        return reconciled
    odoo_pagos = execute(
        "account.payment",
        "search_read",
        [[["id", "in", p_ids]]],
        {"fields": ["id", "is_reconciled", "state", "reconciled_invoices_count"]},
    )
    for op in odoo_pagos:
        pid_str = str(op.get("id", "")).strip()
        is_rec = (
            bool(op.get("is_reconciled"))
            or int(op.get("reconciled_invoices_count") or 0) > 0
            or str(op.get("state")) not in PAGO_ESTADOS_CONFIRMADOS
        )
        if is_rec and pid_str:
            reconciled.add(pid_str)
    return reconciled


def pago_monto_usd(monto_raw: Decimal, moneda: str, bcv_rate: Decimal) -> Decimal:
    """Equivalente USD de un monto de pago -- usa la tasa BCV del día del pago

    (mismo criterio que ``/api/resumen`` y el resto de reportes agregados).
    Nunca trata un monto en VES como si ya fuera USD.
    """
    if moneda == "VES" and bcv_rate > Decimal("0"):
        return monto_raw / bcv_rate
    return monto_raw


def usd_bcv_to_binance(
    usd_via_bcv: Decimal, moneda: str, bcv_rate: Decimal, binance_rate: Decimal
) -> Decimal:
    """Reexpresa un equivalente USD (calculado con tasa BCV) usando tasa

    Binance en su lugar -- ancla el monto original en VES (usd_via_bcv *
    bcv_rate) y lo reconvierte con binance_rate, para mostrar ambas
    referencias de un mismo pago en VES sin recalcular desde la celda cruda.
    Para pagos en USD el valor no cambia (no hay tasa que aplicar).
    """
    if moneda == "VES" and binance_rate > Decimal("0"):
        return usd_via_bcv * bcv_rate / binance_rate
    return usd_via_bcv


# Models for POST requests
class VinculacionRequest(BaseModel):
    pago_id: str
    so_id: str
    monto_aplicado: float


class TasaRequest(BaseModel):
    tasa_bcv: float
    tasa_binance: float


class FeriadoRequest(BaseModel):
    fecha: str
    descripcion: str


class DescuentoMarcaRequest(BaseModel):
    marca: str
    categoria: str
    tipo_descuento: str
    porcentaje: float
    vigencia_desde: str
    vigencia_hasta: str | None = None
    listas_aplicables: str = "*"


class MetaRequest(BaseModel):
    cash_window_business_days: int
    descuento_recompra: float


class PricelistMapRequest(BaseModel):
    valid_pricelists_usd: list[str]
    valid_pricelists_ves: list[str]


class VincularMasivoRequest(BaseModel):
    items: list[VinculacionRequest]


class TasaBinanceEditRequest(BaseModel):
    tasa_binance: float


class TasaBcvVarianteRequest(BaseModel):
    variante: str  # "USD" | "EUR"


class PromocionRequest(BaseModel):
    tipo_beneficio: str = "producto"  # 'producto' | 'porcentaje'
    productos: str = ""  # CSV de SKUs de regalo
    valor: float = 1.0  # cantidad o pct (0.02 = 2%)
    compra_minima: float = 0.0  # unidades Comercial mínimas para el regalo
    descuento_fallback: float = 0.0  # pct si no alcanza compra_minima
    regalo_tipo: str = "solo_uno"  # 'solo_uno' | 'conjunto'
    categorias_aplica: str = "Comercial"  # CSV de categorías que califican
    solo_primera_compra: bool = (
        False  # False = Recurrente (cada compra >= min), True = Solo 1era compra
    )
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True


class ExclusionRequest(BaseModel):
    regla_tipo_a: str
    regla_tipo_b: str
    activo: bool = True


class ProntoPagoRequest(BaseModel):
    marca: str = "*"
    categoria: str = "*"
    dias_gracia: int = 3
    porcentaje: float = 0.05
    min_cantidad: float | None = 0.0
    max_cantidad: float | None = 999999.0
    unidad_medida: str | None = "CAJAS"
    tipo_beneficio: str | None = "descuento"
    monedas_aplicables: str = "*"
    listas_aplicables: str = "*"
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True


class VolumenRequest(BaseModel):
    marca: str = "*"
    categoria: str = "*"
    litros_minimo: float = 0.0
    min_cantidad: float | None = None
    max_cantidad: float = 999999.0
    unidad_medida: str = "LITROS"
    porcentaje: float = 0.05
    tipo_evaluacion: str = "orden"
    dias_evaluacion: int = 30
    listas_aplicables: str = "*"
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True


class EliminarDescuentoRequest(BaseModel):
    tabla: str
    regla_id: str


def get_ui_pricelist_ids(repo) -> tuple[list[int], list[int]]:
    try:
        rows = repo._g.read_rows("_Meta")
        meta = {r.get("key"): r.get("value", "") for r in rows if r.get("key")}

        def _parse(val_str: str, default_val: int) -> list[int]:
            if not val_str:
                return [default_val]
            if "," in val_str:
                parts = [p.strip() for p in val_str.split(",") if p.strip()]
            else:
                parts = [c for c in val_str.strip() if c.isdigit()]
            res = [int(p) for p in parts if p.isdigit()]
            return res if res else [default_val]

        usd_ids = _parse(
            meta.get("valid_pricelists_usd"), int(os.environ.get("ODOO_PRICELIST_USD", "4"))
        )
        ves_ids = _parse(
            meta.get("valid_pricelists_ves"), int(os.environ.get("ODOO_PRICELIST_BCV", "5"))
        )
        return usd_ids, ves_ids
    except Exception as e:
        logger.warning("Error reading pricelists from _Meta: %s", e)
        return [int(os.environ.get("ODOO_PRICELIST_USD", "4"))], [
            int(os.environ.get("ODOO_PRICELIST_BCV", "5"))
        ]


def resolve_effective_pricelist_price(
    product_tmpl_id: int,
    order_date: date,
    candidate_pricelist_ids: list[int],
    pricelist_items: list[dict],
) -> Decimal | None:
    if not candidate_pricelist_ids or not pricelist_items:
        return None
    matched = []
    for r in pricelist_items:
        pl_id = (
            r["pricelist_id"][0]
            if isinstance(r["pricelist_id"], list | tuple)
            else r["pricelist_id"]
        )
        if candidate_pricelist_ids and pl_id not in candidate_pricelist_ids:
            continue

        pt_raw = r.get("product_tmpl_id")
        pt_id = pt_raw[0] if isinstance(pt_raw, list | tuple) else pt_raw
        if pt_id != product_tmpl_id:
            continue

        d_start_str = r.get("date_start")
        d_end_str = r.get("date_end")

        d_start = datetime.strptime(d_start_str[:10], "%Y-%m-%d").date() if d_start_str else None
        d_end = datetime.strptime(d_end_str[:10], "%Y-%m-%d").date() if d_end_str else None

        if d_start and order_date < d_start:
            continue
        if d_end and order_date > d_end:
            continue

        price = Decimal(str(r.get("fixed_price") or "0"))
        matched.append((d_start or date.min, price))

    if matched:
        matched.sort(key=lambda x: x[0], reverse=True)
        return matched[0][1]

    return None


def extract_product_tmpl_id(prod_raw: Any) -> int | None:
    if isinstance(prod_raw, int | float):
        return int(prod_raw)
    if isinstance(prod_raw, str):
        if prod_raw.startswith("["):
            try:
                import json

                parsed = json.loads(prod_raw.replace("'", '"'))
                return int(parsed[0])
            except Exception:
                import re

                m = re.search(r"\d+", prod_raw)
                if m:
                    return int(m.group())
        elif prod_raw.isdigit():
            return int(prod_raw)
    return None


class RecompraRequest(BaseModel):
    marca: str = "GLOBAL OIL"
    categoria: str = "CAJA"
    listas_aplicables: str = "*"
    porcentaje: float = 0.03
    max_usos_mes: int = 1
    dias_ventana: int = 30
    min_cajas: int = 2
    max_cajas: int = 4
    unidad_medida: str | None = "CAJAS"
    tipo_beneficio: str | None = "descuento"
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True


class ProductoPromoRequest(BaseModel):
    productos: str = "*"
    marca: str = "*"
    categoria: str = "*"
    min_cantidad: float | None = 0.0
    max_cantidad: float | None = 999999.0
    unidad_medida: str | None = "CAJAS"
    tipo_beneficio: str | None = "descuento"
    porcentaje: float = 0.05
    monedas_aplicables: str = "*"
    listas_aplicables: str = "*"
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True


class DiferencialCambiarioRequest(BaseModel):
    nombre: str
    tipo_diferencial: str  # 'fijo_35_ves_usd' | 'equiparar_binance' | 'diferencial_bcv_binance'
    tipo_calculo: str  # 'fijo' | 'variable'
    porcentaje_fijo: float = 0.35
    marca: str = "*"
    categoria: str = "*"
    monedas_aplicables: str = "*"
    listas_aplicables: str = "*"
    unidad_medida: str | None = "USD"
    min_cantidad: float | None = 0.0
    max_cantidad: float | None = 999999.0
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True


class ToggleDescuentoRequest(BaseModel):
    tabla: str
    regla_id: str
    activo: bool


class DescuentoVolumenRequest(BaseModel):
    marca: str
    categoria: str
    litros_minimo: float
    porcentaje: float
    tipo_evaluacion: str = "orden"
    dias_evaluacion: int = 30
    vigencia_desde: str
    vigencia_hasta: str | None = None
    listas_aplicables: str = "*"


def _fresh_sheets_repo(config: AppConfig) -> SheetsRepository:
    """Instancia nueva de ``SheetsRepository`` -- bypasea el cache de lectura

    de 120s de ``GspreadGateway`` (ver ``gateway.py``) para no calcular sobre
    datos potencialmente obsoletos. La usan ``get_repo()`` (backend sheets) y
    ``recalculate_all``/``recalculate_all_orders``.
    """
    _sid = config.sheets.spreadsheet_id
    print(
        f"DEBUG: GOOGLE_SHEETS_SPREADSHEET_ID: length={len(_sid)}, repr={_sid!r}",
        file=sys.stderr,
    )
    if os.environ.get("GOOGLE_TOKEN_JSON"):
        gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        gateway = GspreadGateway(config.sheets.spreadsheet_id, config.sheets.service_account_file)
    return SheetsRepository(gateway)


_repo_cache: Repository | None = None


def get_repo() -> Repository:
    """Repositorio activo -- ``REPO_BACKEND`` (env var, "sheets" por defecto)

    decide la implementación. Postgres se cachea (reusa el pool de
    conexiones, ver ``PostgresRepository``/``cxc.db.engine``); Sheets se
    reconstruye la primera vez igual que siempre.
    """
    global _repo_cache
    if _repo_cache is None:
        config = AppConfig.from_env()
        if config.database.repo_backend == "postgres":
            if not config.database.url:
                raise RuntimeError(
                    "REPO_BACKEND=postgres requiere configurar DATABASE_URL"
                )
            _repo_cache = PostgresRepository.from_url(config.database.url)
        else:
            _repo_cache = _fresh_sheets_repo(config)
    return _repo_cache


def _closest_serie_row(dt: datetime, rows: list[dict]) -> dict | None:
    closest_row = None
    min_diff = None

    for r in rows:
        ts_str = r.get("timestamp")
        if not ts_str:
            continue
        try:
            ts_str = ts_str.replace("T", " ")
            if "." in ts_str:
                ts_str = ts_str.split(".")[0]
            row_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                row_dt = datetime.strptime(ts_str[:16], "%Y-%m-%d %H:%M")
            except Exception:
                continue

        diff = abs((dt - row_dt).total_seconds())
        if min_diff is None or diff < min_diff:
            min_diff = diff
            closest_row = r

    return closest_row


def get_rate_for_datetime(dt: datetime, rows: list[dict] = None) -> tuple[Decimal, Decimal]:
    if rows is None:
        repo = get_repo()
        rows = repo._g.read_rows("SerieTasas")
    if not rows:
        return Decimal("36.5"), Decimal("38.0")

    closest_row = _closest_serie_row(dt, rows)
    if closest_row:
        return parse_decimal_safe(closest_row.get("tasa_bcv")), parse_decimal_safe(
            closest_row.get("tasa_binance")
        )

    return Decimal("36.5"), Decimal("38.0")


def get_bcv_euro_rate_for_datetime(dt: datetime, rows: list[dict]) -> Decimal | None:
    """Tasa BCV-EUR (SerieTasa.tasa_bcv_euro) más cercana a `dt`.

    None si no hay ninguna fila con esa columna capturada (huérfana en la
    mayoría de los despliegues -- el scraper la captura pero nada la usaba).
    """
    candidatas = [r for r in rows if parse_decimal_safe(r.get("tasa_bcv_euro", "0")) > Decimal("0")]
    closest_row = _closest_serie_row(dt, candidatas)
    if closest_row:
        return parse_decimal_safe(closest_row.get("tasa_bcv_euro"))
    return None


def get_binance_rate_for_date(fecha: date, rows: list[dict]) -> Decimal | None:
    """Tasa Binance promedio del día EXACTO `fecha`, desde ``TasasHistoricasAuditoria``.

    A diferencia de ``get_rate_for_datetime`` (que busca la fila de
    ``SerieTasas`` más cercana en el tiempo, sin tope de un mismo día -- si
    esa hoja tiene un hueco alrededor de la fecha buscada, puede devolver la
    tasa de OTRO día en silencio), esta función NO cae a un día distinto:
    Odoo no tiene noción de tasa Binance, así que la única fuente confiable
    para una fecha puntual es el histórico diario ya sembrado
    (``scripts/cargar_tasas_historicas.py``). Devuelve ``None`` si ese día
    no tiene fila -- quien llama decide el fallback.
    """
    fecha_str = fecha.isoformat()
    for r in rows:
        if str(r.get("fecha", ""))[:10] == fecha_str:
            val = parse_decimal_safe(r.get("tasa_binance_promedio_diario", "0"))
            if val > Decimal("0"):
                return val
    return None


def get_eur_rate_for_date(fecha: date, rows: list[dict]) -> Decimal | None:
    """Tasa BCV-EUR oficial del día EXACTO `fecha`, desde ``TasasHistoricasAuditoria``.

    Mismo criterio que ``get_binance_rate_for_date`` (lookup por día exacto,
    sin caer a otro día): la tabla ya trae ``tasa_bcv_euro`` (Odoo
    ``res.currency.rate``, sembrado por ``scripts/cargar_tasas_historicas.py``).
    Devuelve ``None`` si ese día no tiene fila -- quien llama decide el fallback.
    """
    fecha_str = fecha.isoformat()
    for r in rows:
        if str(r.get("fecha", ""))[:10] == fecha_str:
            val = parse_decimal_safe(r.get("tasa_bcv_euro", "0"))
            if val > Decimal("0"):
                return val
    return None


def resolve_metodo_pago_nombre(execute: Any) -> dict[int, str]:
    """Mapa id -> nombre de ``account.journal`` (diario/método de pago real).

    Único resolver compartido: antes esta lectura se repetía de formas
    distintas en cada endpoint (unpacking manual de ``journal_id`` en
    lecturas en vivo de ``account.payment`` vs. una lectura de
    ``account.journal`` separada solo dentro de ``/api/reporte/diario``).
    El catálogo de diarios es chico -- se trae completo, no filtrado por id.
    """
    if not execute:
        return {}
    journals = execute("account.journal", "search_read", [], {"fields": ["id", "name"]})
    return {int(j["id"]): str(j.get("name") or "") for j in journals}


def resolve_vendedor_validado(
    cliente_id: str,
    so_id: str | None,
    clientes_map: dict[str, Cliente],
    ordenes_map: dict[str, OrdenVenta],
) -> tuple[str, bool]:
    """Vendedor "vigente" de un pago, y si difiere del vendedor de la orden.

    Los clientes a veces cambian de vendedor con el tiempo -- ``Cliente.vendedor_email``
    (re-sincronizado desde ``res.partner.user_id`` en cada corrida) es la fuente
    más actual; ``OrdenVenta.vendedor_email`` quedó fijado al vendedor vigente
    cuando esa orden se creó/sincronizó. Si difieren, se marca
    ``vendedor_mismatch=True`` para que un humano lo revise -- nunca se
    autocorrige nada (mismo criterio que la detección de duplicados).
    """
    cliente = clientes_map.get(cliente_id)
    vendedor_cliente = (cliente.vendedor_email if cliente else "") or ""
    orden = ordenes_map.get(so_id) if so_id else None
    vendedor_orden = (orden.vendedor_email if orden else "") or ""

    vendedor = vendedor_cliente or vendedor_orden or "Sin Vendedor"
    v_cliente_norm = vendedor_cliente.strip().lower()
    v_orden_norm = vendedor_orden.strip().lower()
    mismatch = bool(vendedor_cliente and vendedor_orden and v_cliente_norm != v_orden_norm)
    return vendedor, mismatch


async def run_sync_in_background():
    """Daemon de sincronización incremental Odoo → Sheets.

    Primera iteración: siempre hace lookback de 7 días para capturar
    órdenes/pagos que llegaron mientras el servidor estuvo caído (downtime).
    Iteraciones siguientes: usa el cursor incremental normal (delta 48h solapado).
    """
    _first_run = True
    while True:
        try:
            config = AppConfig.from_env()
            repo = get_repo()
            reader = OdooXmlRpcReader(config.odoo)

            if _first_run:
                print("FastAPI Daemon: Primera corrida — lookback 7 días para recuperar ventas.")
                since_override = datetime.now() - timedelta(days=7)
                clientes = reader.changed_clientes(since_override)
                ordenes = reader.changed_ordenes(since_override)
                lineas = reader.changed_lineas(since_override)
                pagos = reader.changed_pagos(since_override)
                repo.upsert_clientes(clientes)
                repo.upsert_ordenes(ordenes)
                repo.upsert_lineas(lineas)
                repo.upsert_pagos(pagos)
                repo.set_last_sync(datetime.now())
                total_first = len(clientes) + len(ordenes) + len(lineas) + len(pagos)
                _REPORTE_SALDOS_CACHE["data"] = None
                _REPORTE_SALDOS_CACHE["timestamp"] = 0.0
                print(f"FastAPI Daemon: Primera corrida completada. {total_first} filas.")
                if total_first > 0:
                    # En un hilo aparte: Reconciler hace una llamada XML-RPC a
                    # Odoo POR ORDEN -- con cientos de órdenes puede tardar
                    # minutos. Ejecutarlo inline bloquearía el event loop
                    # entero (incluido el health check de Railway), causando
                    # 502 "Application failed to respond" en el servidor.
                    await asyncio.to_thread(recalculate_all_orders)
                _first_run = False
            else:
                print("FastAPI Daemon: Iniciando ciclo de sync incremental...")
                sync = IncrementalSync(repo, reader)
                result = sync.run(datetime.now())
                if result.total > 0:
                    _REPORTE_SALDOS_CACHE["data"] = None
                    _REPORTE_SALDOS_CACHE["timestamp"] = 0.0
                    # Sincronización bidireccional: si Odoo reportó cambios
                    # (ej. un pago editado en monto/fecha/cliente), se
                    # recalculan motor y reconciliación para reflejarlo sin
                    # esperar a que un humano vincule algo manualmente. En
                    # hilo aparte por la misma razón que arriba.
                    await asyncio.to_thread(recalculate_all_orders)
                print(f"FastAPI Daemon: Sync completado. {result.total} filas actualizadas.")
        except Exception as e:
            print(f"Error en daemon de sincronización: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        await asyncio.sleep(300)


@app.api_route("/api/sync/manual", methods=["GET", "POST"])
async def api_sync_manual(lookback_days: int = 7):
    try:
        config = AppConfig.from_env()
        repo = get_repo()
        reader = OdooXmlRpcReader(config.odoo)

        from datetime import timedelta

        since = repo.get_last_sync()
        if since and lookback_days > 0:
            override_since = datetime.now() - timedelta(days=lookback_days)
            clientes = reader.changed_clientes(override_since)
            ordenes = reader.changed_ordenes(override_since)
            lineas = reader.changed_lineas(override_since)
            pagos = reader.changed_pagos(override_since)
            repo.upsert_clientes(clientes)
            repo.upsert_ordenes(ordenes)
            repo.upsert_lineas(lineas)
            repo.upsert_pagos(pagos)
            repo.set_last_sync(datetime.now())
            total = len(clientes) + len(ordenes) + len(lineas) + len(pagos)
        else:
            sync = IncrementalSync(repo, reader)
            res = sync.run(datetime.now())
            total = res.total

        _REPORTE_SALDOS_CACHE["data"] = None
        _REPORTE_SALDOS_CACHE["timestamp"] = 0.0

        return {
            "status": "ok",
            "total_actualizados": total,
            "mensaje": f"Sincronización completada. {total} registros actualizados.",
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


async def run_scraper_in_background():
    # Esperar 30 segundos tras el arranque inicial antes del primer scrape de tasas
    await asyncio.sleep(30)
    while True:
        try:
            print("FastAPI Daemon: Iniciando ciclo de scraping de tasas (BCV y Binance)...")
            from cxc.alerts import build_alerter
            from cxc.scraper.bcv import OdooBcvClient
            from cxc.scraper.binance import BinanceClient
            from cxc.scraper.rates_scraper import RatesScraper

            config = AppConfig.from_env()
            repo = get_repo()
            scraper = RatesScraper(
                repo,
                BinanceClient(config.binance),
                OdooBcvClient(config.odoo),
                build_alerter(config.alert),
                config.scraper_policy,
            )
            from datetime import timedelta

            now_utc = datetime.now(UTC)
            now_caracas = (now_utc - timedelta(hours=4)).replace(tzinfo=None)
            fila = scraper.run(now_caracas)
            print(
                f"FastAPI Daemon: Tasas actualizadas. BCV={fila.tasa_bcv} "
                f"Binance={fila.tasa_binance}"
            )
        except Exception as e:
            print(f"Error en daemon de scraping de tasas: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        # Repetir cada 1 hora (3600 segundos)
        await asyncio.sleep(3600)


@app.on_event("startup")
async def startup_event():
    # Start the synchronization and scraping daemon loops in the background
    asyncio.create_task(run_sync_in_background())
    asyncio.create_task(run_scraper_in_background())


SECRET_KEY = os.environ.get("SESSION_SECRET", "lubrikca_cxc_secret_key_2026")


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterPasswordRequest(BaseModel):
    email: str
    password: str


class ResetPasswordRequest(BaseModel):
    email: str
    password: str


class CambiarRolRequest(BaseModel):
    email: str
    nuevo_rol: str


def get_current_user_from_cookie(cxc_session: str | None = None) -> dict[str, Any] | None:
    if not cxc_session:
        return None
    email = verificar_session_token(cxc_session, SECRET_KEY)
    if not email:
        return None
    try:
        repo = get_repo()
        u_row = buscar_usuario_plataforma(repo, email)
        if not u_row or u_row.get("activo") == "FALSE":
            return None
        rol = u_row.get("rol", "ventas")
        return {
            "email": email,
            "nombre": u_row.get("nombre_odoo") or email,
            "rol": rol,
            "nombre_rol": NOMBRES_ROLES.get(rol, "Ventas"),
            "permisos": ROLES_PERMISOS.get(rol, ["reporte"]),
        }
    except Exception as e:
        logger.warning("Error buscando usuario de sesión %s: %s", email, e)
        return None


# --- MULTI-PAGE & AUTH ROUTES ---


@app.get("/", response_class=HTMLResponse)
async def read_root(cxc_session: str | None = Cookie(default=None)):
    user = get_current_user_from_cookie(cxc_session)
    if not user:
        return RedirectResponse(url="/login")
    first_perm = user["permisos"][0] if user["permisos"] else "reporte"
    return RedirectResponse(url=f"/{first_perm}")


@app.get("/login", response_class=HTMLResponse)
async def serve_login(cxc_session: str | None = Cookie(default=None)):
    user = get_current_user_from_cookie(cxc_session)
    if user:
        first_perm = user["permisos"][0] if user["permisos"] else "reporte"
        return RedirectResponse(url=f"/{first_perm}")
    login_path = os.path.join(static_dir, "login.html")
    if not os.path.exists(login_path):
        return HTMLResponse(
            "<html><body><h1>Error</h1><p>Archivo static/login.html no encontrado</p></body></html>"
        )
    with open(login_path, encoding="utf-8") as f:
        return f.read()


@app.get("/logout")
async def handle_logout_get(response: Response):
    res = RedirectResponse(url="/login")
    res.delete_cookie(key="cxc_session")
    return res


@app.post("/api/auth/logout")
async def handle_logout_post():
    res = Response(
        content=json.dumps({"status": "success", "message": "Sesión cerrada"}),
        media_type="application/json",
    )
    res.delete_cookie(key="cxc_session")
    return res


def render_page_or_login(page_name: str, cxc_session: str | None):
    user = get_current_user_from_cookie(cxc_session)
    if not user:
        return RedirectResponse(url="/login")
    if page_name not in user["permisos"] and user["rol"] != "admin":
        first_perm = user["permisos"][0] if user["permisos"] else "reporte"
        return RedirectResponse(url=f"/{first_perm}")

    index_path = os.path.join(static_dir, "index.html")
    if not os.path.exists(index_path):
        return HTMLResponse(
            "<html><body><h1>Servidor Iniciado</h1><p>Frontend no encontrado</p></body></html>"
        )
    with open(index_path, encoding="utf-8") as f:
        return f.read()


@app.get("/facturacion", response_class=HTMLResponse)
async def page_facturacion(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("facturacion", cxc_session)


@app.get("/cobranza", response_class=HTMLResponse)
async def page_cobranza(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("cobranza", cxc_session)


@app.get("/dashboard", response_class=HTMLResponse)
async def page_dashboard(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("dashboard", cxc_session)


@app.get("/conciliaciones")
async def page_conciliaciones():
    """"Conciliaciones" se unificó con "Cobranza" en una sola página.

    Redirect para bookmarks/links viejos -- la tabla y sus 4 endpoints
    de origen (sugerencias, mapa-vinculaciones, pagos-historial, cobranza)
    fueron reemplazados por ``GET /api/cobranza/pagos``.
    """
    return RedirectResponse(url="/cobranza")


@app.get("/ventas", response_class=HTMLResponse)
async def page_ventas(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("ventas", cxc_session)


@app.get("/reporte", response_class=HTMLResponse)
async def page_reporte(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("reporte", cxc_session)


@app.get("/auditoria", response_class=HTMLResponse)
async def page_auditoria(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("auditoria", cxc_session)


@app.get("/configuracion", response_class=HTMLResponse)
async def page_configuracion(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("configuracion", cxc_session)


# --- AUTH & ADMIN API ENDPOINTS ---


@app.post("/api/auth/login")
async def api_auth_login(req: LoginRequest):
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        repo = get_repo()

        user_info, err_msg = autenticar_usuario(execute, repo, req.email, req.password)
        if not user_info:
            raise HTTPException(status_code=401, detail=err_msg or "Credenciales inválidas")

        token = crear_session_token(req.email, SECRET_KEY)
        first_perm = user_info["permisos"][0] if user_info["permisos"] else "reporte"

        res = Response(
            content=json.dumps(
                {"status": "success", "user": user_info, "redirect": f"/{first_perm}"}
            ),
            media_type="application/json",
        )
        res.set_cookie(
            key="cxc_session", value=token, httponly=True, max_age=86400 * 7, samesite="lax"
        )
        return res
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/auth/register-password")
async def api_auth_register_password(req: RegisterPasswordRequest):
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        repo = get_repo()

        odoo_user = verificar_usuario_odoo_activo(execute, req.email)
        if not odoo_user:
            raise HTTPException(
                status_code=400,
                detail="El correo ingresado no pertenece a un usuario activo en Odoo ERP.",
            )

        u_row = registrar_o_actualizar_usuario(
            repo,
            email=req.email,
            password=req.password,
            nombre_odoo=odoo_user.get("name") or "",
            activo=True,
        )

        token = crear_session_token(req.email, SECRET_KEY)
        rol = u_row.get("rol", "ventas")
        permisos = ROLES_PERMISOS.get(rol, ["reporte"])
        first_perm = permisos[0] if permisos else "reporte"

        res = Response(
            content=json.dumps(
                {
                    "status": "success",
                    "message": "Contraseña creada exitosamente",
                    "redirect": f"/{first_perm}",
                }
            ),
            media_type="application/json",
        )
        res.set_cookie(
            key="cxc_session", value=token, httponly=True, max_age=86400 * 7, samesite="lax"
        )
        return res
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/auth/reset-password")
async def api_auth_reset_password(req: ResetPasswordRequest):
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        repo = get_repo()

        odoo_user = verificar_usuario_odoo_activo(execute, req.email)
        if not odoo_user:
            raise HTTPException(
                status_code=400,
                detail="El correo ingresado no pertenece a un usuario activo en Odoo ERP.",
            )

        registrar_o_actualizar_usuario(
            repo,
            email=req.email,
            password=req.password,
            nombre_odoo=odoo_user.get("name") or "",
            activo=True,
        )
        return {"status": "success", "message": "Contraseña restablecida exitosamente."}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/auth/me")
async def api_auth_me(cxc_session: str | None = Cookie(default=None)):
    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user


@app.get("/api/admin/usuarios")
async def api_admin_list_usuarios(cxc_session: str | None = Cookie(default=None)):
    user = get_current_user_from_cookie(cxc_session)
    if not user or user["rol"] != "admin":
        raise HTTPException(
            status_code=403, detail="Acceso denegado: Se requiere rol Administrador"
        )
    repo = get_repo()
    rows = obtener_usuarios_plataforma(repo)
    clean_rows = []
    for r in rows:
        clean_rows.append(
            {
                "email": r.get("email"),
                "nombre_odoo": r.get("nombre_odoo"),
                "rol": r.get("rol", "ventas"),
                "nombre_rol": NOMBRES_ROLES.get(r.get("rol", "ventas"), "Ventas"),
                "activo": r.get("activo") == "TRUE",
                "fecha_registro": r.get("fecha_registro"),
            }
        )
    return clean_rows


@app.post("/api/admin/cambiar-rol")
async def api_admin_cambiar_rol(
    req: CambiarRolRequest, cxc_session: str | None = Cookie(default=None)
):
    user = get_current_user_from_cookie(cxc_session)
    if not user or user["rol"] != "admin":
        raise HTTPException(
            status_code=403, detail="Acceso denegado: Se requiere rol Administrador"
        )
    if req.nuevo_rol not in ROLES_PERMISOS:
        raise HTTPException(status_code=400, detail=f"Rol '{req.nuevo_rol}' no es válido.")
    repo = get_repo()
    u_row = buscar_usuario_plataforma(repo, req.email)
    if not u_row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    u_row["rol"] = req.nuevo_rol
    repo._g.upsert_row("UsuariosPlataforma", "email", u_row)
    return {
        "status": "success",
        "message": f"Rol de {req.email} actualizado a {NOMBRES_ROLES.get(req.nuevo_rol)}.",
    }


@app.post("/api/admin/recalcular-todo")
async def api_admin_recalcular_todo(
    background_tasks: BackgroundTasks, cxc_session: str | None = Cookie(default=None)
):
    """Fuerza un recálculo completo del motor de descuentos y la reconciliación.

    El sync incremental solo dispara ``recalculate_all_orders`` cuando detecta
    un cambio real en Odoo para una orden -- si nada cambió (p.ej. después de
    un fix de código como la corrección del fallback de precio, que no toca
    datos de Odoo), la Bandeja se queda con el valor calculado la última vez.
    Este endpoint deja que Admin/Gerencia fuercen el recálculo cuando lo
    necesiten, sin depender de que algo cambie primero en Odoo.
    """
    user = get_current_user_from_cookie(cxc_session)
    if not user or user["rol"] not in ("admin", "gerente_ventas"):
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: Se requiere rol Administrador o Gerente de Ventas",
        )
    background_tasks.add_task(recalculate_all_orders)
    return {
        "status": "success",
        "message": "Recálculo completo del motor de descuentos iniciado en segundo plano.",
    }


@app.get("/api/resumen")
async def get_resumen():
    try:
        repo = get_repo()
        # 1. Total por cobrar (Orders not invoiced)
        ordenes = repo.all_ordenes()
        so_names_r = [o.so_id for o in ordenes]
        so_states_map = get_live_so_states(so_names_r)
        entrega_valida_set = get_live_delivered_not_returned(so_names_r)
        total_por_cobrar = sum(
            o.monto_total
            for o in ordenes
            if not o.facturada
            and not orden_excluida(
                o,
                live_state=so_states_map.get(o.so_id),
                entrega_valida=o.so_id in entrega_valida_set,
            )
        )

        # 2. Pagos sin asignar (saldo real -- no todo-o-nada -- en USD y VES)
        pagos = repo._g.read_rows("Pagos")
        vincs = repo.all_vinculaciones()
        linked_amounts: dict[str, Decimal] = {}
        for v in vincs:
            prev = linked_amounts.get(v.pago_id, Decimal("0"))
            linked_amounts[v.pago_id] = prev + v.monto_aplicado
        tasas_rows = repo._g.read_rows("SerieTasas")

        # Igual que /api/conciliaciones/sugerencias: excluir pagos ya
        # reconciliados directamente en Odoo (via factura, sin pasar por una
        # Vinculacion de este sistema) -- si no, esta tarjeta suma pagos que
        # ya estan conciliados como si siguieran "sin asignar".
        reconciled_pagos_set: set[str] = set()
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            if execute:
                p_ids_str = [str(p.get("pago_id", "")).strip() for p in pagos if p.get("pago_id")]
                reconciled_pagos_set = get_reconciled_pago_ids_odoo(execute, p_ids_str)
        except Exception as e_odoo:
            logger.warning("Error consultando Odoo en get_resumen: %s", e_odoo)

        pagos_pendientes_usd = Decimal("0")
        pagos_pendientes_ves = Decimal("0")
        for p in pagos:
            pid = str(p.get("pago_id", ""))
            if not pid or pid in reconciled_pagos_set:
                continue
            try:
                moneda = str(p.get("moneda", "USD") or "USD").upper().strip()
                fecha_str = str(p.get("fecha_pago", ""))[:10]
                try:
                    fecha_dt = (
                        datetime.strptime(fecha_str, "%Y-%m-%d") if fecha_str else datetime.now()
                    )
                except ValueError:
                    fecha_dt = datetime.now()
                bcv_rate, _ = get_rate_for_datetime(fecha_dt, tasas_rows)

                # linked_amounts (Vinculacion.monto_aplicado) siempre esta en
                # USD -- convertir el monto original ANTES de restar, nunca
                # restar un monto en VES contra un aplicado en USD.
                monto_original_raw = parse_decimal_safe(p.get("monto", "0"))
                monto_original_usd = pago_monto_usd(monto_original_raw, moneda, bcv_rate)
                saldo_usd = monto_original_usd - linked_amounts.get(pid, Decimal("0"))
                if saldo_usd <= Decimal("0.05"):
                    continue

                pagos_pendientes_usd += saldo_usd
                pagos_pendientes_ves += saldo_usd * bcv_rate
            except Exception:
                pass

        # 3. Alertas rojas in Conciliación
        concs = repo.all_conciliaciones()
        alertas_rojas = sum(1 for c in concs if c.resultado.value == "rojo")

        return {
            "total_por_cobrar_usd": float(total_por_cobrar),
            "pagos_sin_asignar_usd": float(pagos_pendientes_usd),
            "pagos_sin_asignar_ves": float(pagos_pendientes_ves),
            "alertas_reconciliacion": alertas_rojas,
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/ordenes-pendientes/{cliente_id}")
async def get_ordenes_pendientes(cliente_id: str):
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        vincs = repo.all_vinculaciones()

        # Calculate sum of linked payment amounts per so_id
        linked_by_so = {}
        for v in vincs:
            # We assume linked amount is matching currency of the order (USD)
            # or in terms of the applied amount
            linked_by_so[v.so_id] = linked_by_so.get(v.so_id, Decimal("0")) + v.monto_aplicado

        # Filter outstanding orders for this client
        pendientes = []
        for o in ordenes:
            if orden_excluida(o):
                continue
            if o.cliente_id == cliente_id and not o.facturada:
                pagado = linked_by_so.get(o.so_id, Decimal("0"))
                saldo = o.monto_total - pagado

                # Show only orders that still have a outstanding balance (> $0.05)
                if saldo > Decimal("0.05"):
                    pendientes.append(
                        {
                            "so_id": o.so_id,
                            "fecha": o.fecha.isoformat(),
                            "monto_total": float(o.monto_total),
                            "saldo_pendiente": float(saldo),
                            "vendedor": o.vendedor_email,
                        }
                    )
        return pendientes
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/vincular")
async def post_vincular(req: VinculacionRequest, background_tasks: BackgroundTasks):
    try:
        repo = get_repo()

        # Fetch latest exchange rates from SerieTasas
        last_tasa = repo.last_serie_tasa()
        tasa_bcv = last_tasa.tasa_bcv if last_tasa else Decimal("36.5")
        tasa_binance = last_tasa.tasa_binance if last_tasa else Decimal("38.0")

        # Fetch payment to get currency
        pago = repo.get_pago(req.pago_id)
        if not pago:
            raise HTTPException(status_code=404, detail="Pago no encontrado.")

        monto_dec = Decimal(str(req.monto_aplicado))

        # Calculate equivalents
        if pago.moneda == "USD":
            equiv_usd_bcv = monto_dec
            equiv_usd_binance = monto_dec
            equiv_ves_bcv = monto_dec * tasa_bcv
            equiv_ves_binance = monto_dec * tasa_binance
        else:
            equiv_usd_bcv = monto_dec / tasa_bcv
            equiv_usd_binance = monto_dec / tasa_binance
            equiv_ves_bcv = monto_dec
            equiv_ves_binance = monto_dec

        vinc_id = f"VINC_{req.pago_id}_{req.so_id}"
        vinc = Vinculacion(
            vinc_id=vinc_id,
            pago_id=req.pago_id,
            so_id=req.so_id,
            monto_aplicado=monto_dec,
            hora_pago_confirmada=datetime.combine(pago.fecha_pago, datetime.min.time()),
            tasa_bcv_aplicada=tasa_bcv,
            tasa_binance_aplicada=tasa_binance,
            es_tasa_heredada=False,
            equiv_usd_bcv=equiv_usd_bcv,
            equiv_usd_binance=equiv_usd_binance,
            equiv_ves_bcv=equiv_ves_bcv,
            equiv_ves_binance=equiv_ves_binance,
            confirmado_por="Panel de Control Web",
            timestamp_registro=datetime.now(),
            estado=EstadoVinculacion.PENDIENTE,
            moneda_abono=Moneda(pago.moneda),
            tipo_tasa_abono=TipoTasa.BCV,
        )

        # Write vinculacion row
        repo.update_vinculacion(vinc)

        # Trigger background run of Engine and Reconciler to refresh totals in Sheets
        background_tasks.add_task(recalculate_all, req.so_id)

        return {
            "status": "success",
            "message": "Vinculación guardada. Recálculo en segundo plano iniciado.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def recalculate_all(so_id: str):
    try:
        print(f"Recalculando orden {so_id}...")
        config = AppConfig.from_env()
        # Postgres: reusar el pool compartido (get_repo()) -- crear un
        # engine nuevo en cada llamada (esta función corre por cada
        # vinculación manual, ademas del sync cada ~5 min) agotaría las
        # conexiones. Sheets: instancia nueva a propósito (comportamiento
        # preexistente, sin cambios) -- ver _fresh_sheets_repo().
        repo = (
            get_repo()
            if config.database.repo_backend == "postgres"
            else _fresh_sheets_repo(config)
        )
        execute = _connect(config.odoo)
        usd_lists, ves_lists = get_valid_pricelists_usd_and_ves(repo)
        primary_usd_id = int(usd_lists[0]) if usd_lists and usd_lists[0].isdigit() else 4
        primary_ves_id = int(ves_lists[0]) if ves_lists and ves_lists[0].isdigit() else 5
        pricelist_ids = {
            config.engine.lista_usd: primary_usd_id,
            config.engine.lista_bcv: primary_ves_id,
        }
        resolver = OdooPriceResolver(execute, pricelist_ids)
        runner = EngineRunner(repo, resolver, config.engine)

        # Calculate this SO
        runner.run_orden(so_id, date.today())

        # Run Reconciler to sync semaphores
        facturas = OdooFacturasReader(execute)
        Reconciler(repo, facturas, config.reconciliation).run()
        print(f"Recálculo de {so_id} completado con éxito.")
    except Exception as e:
        print(f"Error al recalcular {so_id}: {e}", file=sys.stderr)


def recalculate_all_orders():
    """Recalcula el motor de descuentos y la reconciliación para TODAS las

    órdenes. Se dispara tras cada ciclo del sync incremental que detectó
    cambios (clientes/órdenes/líneas/pagos) -- sincronización "bidireccional":
    si algo cambia en Odoo (ej. un pago editado en monto/fecha/cliente), el
    sync ya refresca el espejo cada 5 min, pero sin esto Bandeja/Conciliación
    solo se refrescaban cuando un humano vinculaba manualmente algo desde la
    UI. Reutiliza la misma lógica que recalculate_all(so_id), pero para
    runner.run_all() en vez de una sola orden.
    """
    try:
        print("Recalculando motor de descuentos y reconciliación (todas las órdenes)...")
        config = AppConfig.from_env()
        repo = (
            get_repo()
            if config.database.repo_backend == "postgres"
            else _fresh_sheets_repo(config)
        )
        execute = _connect(config.odoo)

        if execute:
            try:
                cambios = _resincronizar_vinculaciones_con_odoo(repo, execute)
                if cambios:
                    print(f"Re-vinculación por Odoo: {len(cambios)} discrepancia(s) revisada(s).")
            except Exception as e_relink:
                print(f"Error re-sincronizando Vinculaciones con Odoo: {e_relink}", file=sys.stderr)

        usd_lists, ves_lists = get_valid_pricelists_usd_and_ves(repo)
        primary_usd_id = int(usd_lists[0]) if usd_lists and usd_lists[0].isdigit() else 4
        primary_ves_id = int(ves_lists[0]) if ves_lists and ves_lists[0].isdigit() else 5
        pricelist_ids = {
            config.engine.lista_usd: primary_usd_id,
            config.engine.lista_bcv: primary_ves_id,
        }
        resolver = OdooPriceResolver(execute, pricelist_ids)
        runner = EngineRunner(repo, resolver, config.engine)

        resultados = runner.run_all(date.today())

        facturas = OdooFacturasReader(execute)
        Reconciler(repo, facturas, config.reconciliation).run()
        print(f"Recálculo completo: {len(resultados)} órdenes procesadas.")
    except Exception as e:
        print(f"Error al recalcular todas las órdenes: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


_REPORTE_SALDOS_CACHE: dict[str, Any] = {"data": None, "timestamp": 0.0}
_REPORTE_CACHE_TTL = 0.0


@app.get("/api/auditoria-descuentos")
async def get_auditoria_descuentos(
    cxc_session: str | None = Cookie(default=None),
    estado: str | None = None,
    tipo: str | None = None,
):
    """Devuelve la bandeja de auditoría de descuentos y NCs.

    Parámetros opcionales de filtro:
    - estado: 'pendiente' | 'revisado' | 'aprobado'
    - tipo: 'descuento_orden' | 'descuento_factura' | 'nota_credito'
    """
    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    try:
        repo = get_repo()
        rows = repo.all_auditoria() if hasattr(repo, "all_auditoria") else []
        if estado:
            rows = [r for r in rows if r.get("estado", "") == estado]
        if tipo:
            rows = [r for r in rows if r.get("tipo_auditoria", "") == tipo]
        return {"items": rows, "total": len(rows)}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


class AuditoriaEstadoRequest(BaseModel):
    audit_id: str
    estado: str  # 'revisado' | 'aprobado' | 'rechazado'


@app.patch("/api/auditoria-descuentos/{audit_id}")
async def patch_auditoria_estado(
    audit_id: str,
    req: AuditoriaEstadoRequest,
    cxc_session: str | None = Cookie(default=None),
):
    """Actualiza el estado de una fila de auditoría (revisado/aprobado/rechazado).
    Solo accesible por usuarios con rol 'admin' o 'contabilidad'.
    """
    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if user.get("rol") not in ("admin", "contabilidad"):
        raise HTTPException(status_code=403, detail="Sin permisos para actualizar auditoría")
    try:
        repo = get_repo()
        if hasattr(repo, "update_auditoria_estado"):
            repo.update_auditoria_estado(
                audit_id=audit_id,
                estado=req.estado,
                revisado_por=user.get("nombre") or user.get("email") or "desconocido",
            )
        return {"status": "ok", "audit_id": audit_id, "nuevo_estado": req.estado}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/reporte-saldos")
async def get_reporte_saldos(refresh: bool = False):
    try:
        import time

        now_ts = time.time()
        if (
            not refresh
            and _REPORTE_SALDOS_CACHE["data"] is not None
            and now_ts - float(_REPORTE_SALDOS_CACHE["timestamp"]) < _REPORTE_CACHE_TTL
        ):
            return _REPORTE_SALDOS_CACHE["data"]

        repo = get_repo()
        ordenes = repo.all_ordenes()
        vincs = repo.all_vinculaciones()
        concs = {c.so_id: c for c in repo.all_conciliaciones()}

        # Load clients once
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {r.get("cliente_id"): r.get("nombre", "") for r in clientes_rows}

        execute = None
        config = AppConfig.from_env()
        try:
            execute = _connect(config.odoo)
        except Exception as e_conn:
            logger.warning("No se pudo conectar a Odoo en get_reporte_saldos: %s", e_conn)

        # Query Odoo SOs for seller (user_id), payment terms & picking_ids
        so_ids_names = [o.so_id for o in ordenes]
        so_odoo_data = {}
        all_picking_ids = set()
        if execute and so_ids_names:
            try:
                so_records = execute(
                    "sale.order",
                    "search_read",
                    [[["name", "in", so_ids_names]]],
                    {
                        "fields": [
                            "name",
                            "user_id",
                            "payment_term_id",
                            "date_order",
                            "state",
                            "delivery_status",
                            "invoice_status",
                            "picking_ids",
                        ]
                    },
                )
                for s in so_records:
                    s_name = s.get("name")
                    u_info = s.get("user_id")
                    t_info = s.get("payment_term_id")
                    vendedor_name = (
                        u_info[1]
                        if isinstance(u_info, list | tuple) and len(u_info) > 1
                        else "Sin Vendedor"
                    )
                    term_name = (
                        t_info[1]
                        if isinstance(t_info, list | tuple) and len(t_info) > 1
                        else "Contado"
                    )
                    so_odoo_data[s_name] = {
                        "vendedor": vendedor_name,
                        "payment_term_name": term_name,
                        "date_order": s.get("date_order"),
                        "state": s.get("state"),
                        "delivery_status": s.get("delivery_status"),
                        "invoice_status": s.get("invoice_status"),
                    }
                    p_ids = s.get("picking_ids")
                    if p_ids and isinstance(p_ids, list | tuple):
                        all_picking_ids.update(p_ids)
            except Exception as e_so:
                logger.warning("Error consultando sale.order en Odoo: %s", e_so)

        # Query Odoo Pickings bound to active SOs or picking_ids (no full scan)
        picking_delivery_map = {}
        picking_return_set = set()
        if execute and so_ids_names:
            try:
                domain_pickings = [["state", "=", "done"]]
                if all_picking_ids:
                    domain_pickings.append(["id", "in", list(all_picking_ids)])
                else:
                    domain_pickings.append(["origin", "in", so_ids_names])

                pickings = execute(
                    "stock.picking",
                    "search_read",
                    [domain_pickings],
                    {
                        "fields": [
                            "id",
                            "origin",
                            "sale_id",
                            "date_done",
                            "scheduled_date",
                            "picking_type_code",
                            "return_id",
                        ]
                    },
                )
                p_by_id = {p["id"]: p for p in pickings}
                for p in pickings:
                    so_name = None
                    s_info = p.get("sale_id")
                    if isinstance(s_info, list | tuple) and len(s_info) > 1:
                        so_name = s_info[1]
                    elif p.get("origin"):
                        for name in so_ids_names:
                            if name and name in str(p["origin"]):
                                so_name = name
                                break
                    if not so_name and p.get("return_id"):
                        ret_parent_id = (
                            p["return_id"][0] if isinstance(p["return_id"], list | tuple) else None
                        )
                        if ret_parent_id and ret_parent_id in p_by_id:
                            parent_p = p_by_id[ret_parent_id]
                            ps_info = parent_p.get("sale_id")
                            if isinstance(ps_info, list | tuple) and len(ps_info) > 1:
                                so_name = ps_info[1]

                    p_code = str(p.get("picking_type_code") or "")
                    is_return = (
                        bool(p.get("return_id"))
                        or (p_code == "incoming")
                        or ("Devolución" in str(p.get("origin") or ""))
                        or ("Return" in str(p.get("origin") or ""))
                    )

                    if so_name:
                        if is_return:
                            picking_return_set.add(so_name)
                        elif p_code == "outgoing":
                            dt_done = p.get("date_done") or p.get("scheduled_date")
                            if dt_done:
                                dt_str = str(dt_done).split(" ")[0]
                                if (
                                    so_name not in picking_delivery_map
                                    or dt_str > picking_delivery_map[so_name]
                                ):
                                    picking_delivery_map[so_name] = dt_str
            except Exception as e_pic:
                logger.warning("Error consultando stock.picking en Odoo: %s", e_pic)

        # Compute payments per SO from manual Vinculaciones (Google Sheets)
        pagos_by_so = {}
        for v in vincs:
            if v.so_id not in pagos_by_so:
                pagos_by_so[v.so_id] = {
                    "abono_bcv": Decimal("0"),
                    "abono_binance": Decimal("0"),
                    "ultimo_abono": None,
                    "tiene_vinc_manual": True,
                }

            # BCV equivalent
            eq_bcv = v.equiv_usd_bcv if v.equiv_usd_bcv is not None else v.monto_aplicado
            pagos_by_so[v.so_id]["abono_bcv"] += eq_bcv

            # Binance equivalent
            if v.equiv_usd_binance is not None:
                eq_binance = v.equiv_usd_binance
            else:
                eq_binance = v.monto_aplicado
            pagos_by_so[v.so_id]["abono_binance"] += eq_binance

            # Track latest payment date
            if v.hora_pago_confirmada:
                dt_pago_str = v.hora_pago_confirmada.strftime("%Y-%m-%d")
                curr_last = pagos_by_so[v.so_id]["ultimo_abono"]
                if not curr_last or dt_pago_str > curr_last:
                    pagos_by_so[v.so_id]["ultimo_abono"] = dt_pago_str

        # Los abonos de Odoo se calculan más abajo (post invoices_by_so) usando
        # amount_total - amount_residual de cada factura. Ese campo siempre es exacto.

        # Load UI configured pricelist IDs (USD & VES) from _Meta
        usd_ids, ves_ids = get_ui_pricelist_ids(repo)
        rules_usd = (
            execute(
                "product.pricelist.item",
                "search_read",
                [[["pricelist_id", "in", usd_ids], ["compute_price", "=", "fixed"]]],
                {
                    "fields": [
                        "pricelist_id",
                        "product_tmpl_id",
                        "fixed_price",
                        "date_start",
                        "date_end",
                    ]
                },
            )
            if execute
            else []
        )

        all_lines = repo._g.read_rows("LineasOrden")
        lines_by_so = {}
        for r in all_lines:
            so = r.get("so_id", "")
            if so:
                lines_by_so.setdefault(so, []).append(r)

        bandeja_rows = repo.all_bandeja()
        bandeja_map = {b.so_id: b for b in bandeja_rows}

        def parse_term_days(t_name: str) -> int:
            if not t_name:
                return 0
            t_low = t_name.lower().strip()
            if "immediate" in t_low or "contado" in t_low:
                return 0
            import re

            m = re.search(r"(\d+)\s*(dias|días|days|day|día)", t_low)
            if m:
                return int(m.group(1))
            return 0

        # Read discount rules for theoretical evaluation when order is not in BandejaFacturacion
        repo.descuentos_marca_categoria()

        # Read rates series to convert VES invoice residual to USD
        tasas_rows = repo._g.read_rows("SerieTasas")
        rates_map = {}
        for r in tasas_rows:
            ts = str(r.get("timestamp", ""))[:10]
            tbcv = r.get("tasa_bcv")
            if ts and tbcv:
                with contextlib.suppress(Exception):
                    rates_map[ts] = float(tbcv)
        last_bcv_val = list(rates_map.values())[-1] if rates_map else 742.23

        # Fetch posted invoices & credit notes from Odoo in batch for all orders
        so_ids = [o.so_id for o in ordenes]
        invoices_by_so: dict[str, list[dict]] = {}  # out_invoice only
        ncs_by_so: dict[str, list[dict]] = {}  # out_refund (notas de crédito)
        invoice_ids_all: list[int] = []  # for fetching move lines
        inv_name_to_so: dict[str, str] = {}
        inv_id_to_so: dict[int, str] = {}
        # SOs cuya(s) factura(s) Odoo ya estan "Pagada" (paid) o "En proceso de
        # pago" (in_payment): salen del reporte general de CxC (Tarea 2).
        so_pagada_en_odoo: set[str] = set()
        try:
            if execute:
                # 1. Fetch out_invoice records
                invoices = execute(
                    "account.move",
                    "search_read",
                    [
                        [
                            ["invoice_origin", "in", so_ids],
                            ["state", "=", "posted"],
                            ["move_type", "=", "out_invoice"],
                        ]
                    ],
                    {
                        "fields": [
                            "id",
                            "name",
                            "invoice_origin",
                            "amount_total",
                            "amount_residual",
                            "currency_id",
                            "invoice_date",
                            "move_type",
                            "payment_state",
                        ]
                    },
                )
                for inv in invoices:
                    so = str(inv.get("invoice_origin", "")).strip()
                    if not so:
                        continue
                    invoices_by_so.setdefault(so, []).append(inv)
                    invoice_ids_all.append(int(inv["id"]))
                    inv_id_to_so[int(inv["id"])] = so
                    inv_name = str(inv.get("name", "")).strip()
                    if inv_name:
                        inv_name_to_so[inv_name] = so

                # 2. Fetch out_refund (Credit Notes) matching SO name, invoice name, or
                # reversed_entry_id
                inv_names = list(inv_name_to_so.keys())
                nc_domain = [["state", "=", "posted"], ["move_type", "=", "out_refund"]]
                sub_domain = []
                if so_ids:
                    sub_domain.append(["invoice_origin", "in", so_ids])
                if inv_names:
                    sub_domain.append(["invoice_origin", "in", inv_names])
                    sub_domain.append(["ref", "in", inv_names])
                if invoice_ids_all:
                    sub_domain.append(["reversed_entry_id", "in", invoice_ids_all])

                if sub_domain:
                    if len(sub_domain) == 1:
                        full_nc_domain = sub_domain + nc_domain
                    else:
                        # Dominio Odoo en notación prefija: '|' * (n-1) antepone las n
                        # cláusulas para hacer OR entre ellas, luego se ANDan con nc_domain.
                        full_nc_domain = ["|"] * (len(sub_domain) - 1) + sub_domain + nc_domain

                    ncs = execute(
                        "account.move",
                        "search_read",
                        [full_nc_domain],
                        {
                            "fields": [
                                "id",
                                "name",
                                "invoice_origin",
                                "amount_total",
                                "amount_residual",
                                "currency_id",
                                "invoice_date",
                                "move_type",
                                "payment_state",
                                "reversed_entry_id",
                                "ref",
                            ]
                        },
                    )
                    for nc in ncs:
                        so = None
                        orig = str(nc.get("invoice_origin", "")).strip()
                        ref = str(nc.get("ref", "")).strip()
                        rev_raw = nc.get("reversed_entry_id")
                        rev_id = rev_raw[0] if isinstance(rev_raw, list | tuple) else None

                        if orig in invoices_by_so:
                            so = orig
                        elif orig in inv_name_to_so:
                            so = inv_name_to_so[orig]
                        elif ref in inv_name_to_so:
                            so = inv_name_to_so[ref]
                        elif rev_id and rev_id in inv_id_to_so:
                            so = inv_id_to_so[rev_id]

                        if so:
                            ncs_by_so.setdefault(so, []).append(nc)
        except Exception as e:
            logger.warning("Error al consultar facturas Odoo en get_reporte_saldos: %s", e)

        # Una SO sale del reporte de CxC solo si TODAS sus facturas out_invoice
        # ya estan pagadas/en proceso de pago (si queda alguna sin pagar, se
        # mantiene visible).
        for so_name, inv_list in invoices_by_so.items():
            estados = [str(i.get("payment_state", "")) for i in inv_list]
            if estados and all(ps in ("paid", "in_payment") for ps in estados):
                so_pagada_en_odoo.add(so_name)

        # ── Descuentos en líneas de FACTURAS (account.move.line.discount) ──────
        # Captura dos formas de descuento: (a) el campo `discount` (%) de la línea,
        # y (b) una línea aparte del producto "Descuento" con `price_subtotal` negativo
        # (patrón usado en Lubrikca en vez de/además del campo discount).
        inv_line_discounts_by_so: dict[str, float] = {}
        inv_line_discount_detail_by_so: dict[str, str] = {}
        if execute and invoice_ids_all:
            try:
                inv_lines = execute(
                    "account.move.line",
                    "search_read",
                    [
                        [
                            ["move_id", "in", invoice_ids_all],
                            ["display_type", "in", ["product", False]],
                            "|",
                            ["discount", ">", 0],
                            "&",
                            ["product_id.name", "ilike", "descuento"],
                            ["price_subtotal", "<", 0],
                        ]
                    ],
                    {
                        "fields": [
                            "move_id",
                            "name",
                            "quantity",
                            "price_unit",
                            "discount",
                            "price_subtotal",
                            "currency_id",
                        ]
                    },
                )
                for il in inv_lines:
                    move_raw = il.get("move_id")
                    move_id = (
                        move_raw[0] if isinstance(move_raw, list | tuple) else int(move_raw or 0)
                    )
                    so_name = inv_id_to_so.get(move_id, "")
                    if not so_name:
                        continue
                    disc_pct_field = float(il.get("discount") or 0)
                    if disc_pct_field > 0:
                        qty = float(il.get("quantity") or 0)
                        price = float(il.get("price_unit") or 0)
                        disc_monto = qty * price * (disc_pct_field / 100.0)
                        new_frag = f"{str(il.get('name') or 'línea')[:40]}: {disc_pct_field:.1f}%"
                    else:
                        # Línea aparte de producto "Descuento" (price_subtotal negativo).
                        disc_monto = abs(float(il.get("price_subtotal") or 0))
                        new_frag = f"{str(il.get('name') or 'Descuento')[:40]}: ${disc_monto:.2f}"
                    inv_line_discounts_by_so[so_name] = (
                        inv_line_discounts_by_so.get(so_name, 0.0) + disc_monto
                    )
                    detail_existing = inv_line_discount_detail_by_so.get(so_name, "")
                    inv_line_discount_detail_by_so[so_name] = (
                        (detail_existing + "; " + new_frag).lstrip("; ")
                        if detail_existing
                        else new_frag
                    )
            except Exception as e_il:
                logger.warning("Error al consultar account.move.line discounts: %s", e_il)

        # ── Descuentos en líneas de la ORDEN (sale.order.line.discount) ─────────
        # Misma lógica de dos formas de descuento que en facturas (ver arriba).
        sol_discounts_by_so: dict[str, float] = {}
        sol_discount_detail_by_so: dict[str, str] = {}
        if execute and so_ids_names:
            try:
                sol_lines = execute(
                    "sale.order.line",
                    "search_read",
                    [
                        [
                            ["order_id.name", "in", so_ids_names],
                            "|",
                            ["discount", ">", 0],
                            "&",
                            ["product_id.name", "ilike", "descuento"],
                            ["price_subtotal", "<", 0],
                        ]
                    ],
                    {
                        "fields": [
                            "order_id",
                            "name",
                            "product_uom_qty",
                            "price_unit",
                            "discount",
                            "price_subtotal",
                        ]
                    },
                )
                for sol in sol_lines:
                    order_raw = sol.get("order_id")
                    so_name = (
                        order_raw[1]
                        if isinstance(order_raw, list | tuple) and len(order_raw) > 1
                        else str(order_raw or "")
                    )
                    if not so_name:
                        continue
                    disc_pct_field = float(sol.get("discount") or 0)
                    if disc_pct_field > 0:
                        qty = float(sol.get("product_uom_qty") or 0)
                        price = float(sol.get("price_unit") or 0)
                        disc_monto = qty * price * (disc_pct_field / 100.0)
                        new_frag = f"{str(sol.get('name') or 'línea')[:40]}: {disc_pct_field:.1f}%"
                    else:
                        # Línea aparte de producto "Descuento" (price_subtotal negativo).
                        disc_monto = abs(float(sol.get("price_subtotal") or 0))
                        new_frag = f"{str(sol.get('name') or 'Descuento')[:40]}: ${disc_monto:.2f}"
                    sol_discounts_by_so[so_name] = (
                        sol_discounts_by_so.get(so_name, 0.0) + disc_monto
                    )
                    detail_existing = sol_discount_detail_by_so.get(so_name, "")
                    sol_discount_detail_by_so[so_name] = (
                        (detail_existing + "; " + new_frag).lstrip("; ")
                        if detail_existing
                        else new_frag
                    )
            except Exception as e_sol:
                logger.warning("Error al consultar sale.order.line discounts: %s", e_sol)

        # ─── ABONOS DESDE ODOO CONSULTANDO account.payment DIRECTAMENTE ───────────
        # Consulta los pagos reales reconciliados en account.payment:
        # - Importe pagado en la moneda original (amount, currency_id)
        # - Fecha del pago (date)
        # - Si es USD -> 1:1 ($1 pagado = $1 abonado).
        # - Si es VES -> Se convierte a la tasa BCV/Binance a la fecha del pago (date).
        payments_by_so: dict[str, dict] = {}
        if execute and invoice_ids_all:
            try:
                payments_raw = execute(
                    "account.payment",
                    "search_read",
                    [
                        [
                            ["reconciled_invoice_ids", "in", invoice_ids_all],
                            ["state", "in", PAGO_ESTADOS_CONFIRMADOS],
                        ]
                    ],
                    {"fields": ["id", "amount", "currency_id", "date", "reconciled_invoice_ids"]},
                )
                for p in payments_raw:
                    p_amt = Decimal(str(p.get("amount") or "0"))
                    p_curr_raw = p.get("currency_id")
                    p_curr = (
                        p_curr_raw[1]
                        if isinstance(p_curr_raw, list | tuple) and len(p_curr_raw) > 1
                        else "USD"
                    )
                    p_date = str(p.get("date") or "")[:10]

                    rec_invs = p.get("reconciled_invoice_ids", [])
                    matched_sos = set()
                    for r_id in rec_invs:
                        so_m = inv_id_to_so.get(int(r_id))
                        if so_m:
                            matched_sos.add(so_m)

                    # NOTA: aqui hubo una heuristica que intentaba distinguir
                    # Notas de Credito de abonos en efectivo buscando palabras
                    # clave ("nota"/"credito"/"nc"/"refund"/"reversion") en el
                    # nombre del diario o del pago. Se elimino: el substring
                    # "nc" hacia match con cualquier diario que contuviera
                    # "banco" (baNCo), "bancamiga" (baNCamiga), "banesco"
                    # (baNCesco... y demas), "binance" (biNANCe) -- es decir,
                    # con la mayoria de los diarios bancarios reales -- asi
                    # que pagos normales (ej. PBAMI/2026/00283, un abono real
                    # via Banco Bancamiga) se mostraban como Notas de Credito.
                    # Verificado en vivo contra Odoo: ninguna Nota de Credito
                    # real (account.move move_type=out_refund, diario "Notas
                    # de credito de clientes") aparece jamas como
                    # account.payment -- las NC reales ya se detectan de
                    # forma confiable en el bloque de arriba (paso 2, via
                    # out_refund). Todo lo que llega aqui es, por definicion
                    # de Odoo, un pago real.
                    for so_m in matched_sos:
                        p_info = payments_by_so.setdefault(
                            so_m,
                            {
                                "abono_bcv": Decimal("0"),
                                "abono_binance": Decimal("0"),
                                "latest_date": None,
                            },
                        )
                        if p_curr == "USD":
                            p_bcv = p_amt
                            p_bin = p_amt
                        else:
                            rate_bcv = rates_map.get(p_date, last_bcv_val)
                            p_bcv = (
                                p_amt / Decimal(str(rate_bcv))
                                if rate_bcv and float(rate_bcv) > 0
                                else Decimal("0")
                            )
                            p_bin = p_bcv

                        p_info["abono_bcv"] += p_bcv
                        p_info["abono_binance"] += p_bin
                        if p_date and (not p_info["latest_date"] or p_date > p_info["latest_date"]):
                            p_info["latest_date"] = p_date
            except Exception as e_pay:
                logger.warning("Error al consultar account.payment directo: %s", e_pay)

        for so_name, inv_list in invoices_by_so.items():
            existing = pagos_by_so.get(so_name, {})
            if existing.get("tiene_vinc_manual", False):
                continue

            p_direct = payments_by_so.get(so_name)
            if p_direct and p_direct["abono_bcv"] > Decimal("0"):
                total_paid_bcv = p_direct["abono_bcv"]
                total_paid_binance = p_direct["abono_binance"]
                latest_inv_date = p_direct["latest_date"]
            else:
                total_paid_bcv = Decimal("0")
                total_paid_binance = Decimal("0")
                latest_inv_date = None
                o_obj = next((o for o in ordenes if o.so_id == so_name), None)
                order_usd_total = o_obj.monto_total if o_obj else Decimal("0")

                for inv in inv_list:
                    tot = Decimal(str(inv.get("amount_total") or "0"))
                    res = Decimal(str(inv.get("amount_residual") or "0"))
                    paid_inv = max(Decimal("0"), tot - res)

                    curr = inv.get("currency_id")
                    c_name = curr[1] if isinstance(curr, list | tuple) and len(curr) > 1 else "USD"
                    inv_dt = str(inv.get("invoice_date") or "")[:10]

                    if c_name == "VES":
                        if order_usd_total > Decimal("0") and tot > Decimal("0"):
                            effective_rate = tot / order_usd_total
                        else:
                            rate_val = rates_map.get(inv_dt, last_bcv_val)
                            effective_rate = (
                                Decimal(str(rate_val))
                                if rate_val and float(rate_val) > 0
                                else Decimal("1")
                            )
                        paid_inv_usd = (
                            paid_inv / effective_rate
                            if effective_rate > Decimal("0")
                            else Decimal("0")
                        )
                    else:
                        paid_inv_usd = paid_inv

                    total_paid_bcv += paid_inv_usd
                    total_paid_binance += paid_inv_usd
                    if inv_dt and (not latest_inv_date or inv_dt > latest_inv_date):
                        latest_inv_date = inv_dt

            if total_paid_bcv > Decimal("0"):
                if so_name not in pagos_by_so:
                    pagos_by_so[so_name] = {
                        "abono_bcv": total_paid_bcv,
                        "abono_binance": total_paid_binance,
                        "ultimo_abono": latest_inv_date,
                        "desde_odoo": True,
                        "tiene_vinc_manual": False,
                    }
                else:
                    pagos_by_so[so_name]["abono_bcv"] = max(
                        Decimal(str(pagos_by_so[so_name].get("abono_bcv", "0"))), total_paid_bcv
                    )
                    pagos_by_so[so_name]["abono_binance"] = max(
                        Decimal(str(pagos_by_so[so_name].get("abono_binance", "0"))),
                        total_paid_binance,
                    )
                    pagos_by_so[so_name]["desde_odoo"] = True
                    if latest_inv_date:
                        curr_last = pagos_by_so[so_name].get("ultimo_abono")
                        if not curr_last or latest_inv_date > curr_last:
                            pagos_by_so[so_name]["ultimo_abono"] = latest_inv_date

        # Read historical audit price lists from Google Sheets (ListasPreciosHistoricas)
        hist_rows = repo._g.read_rows("ListasPreciosHistoricas")
        hist_map = {}
        for r in hist_rows:
            code = str(r.get("codigo", "")).strip()
            if code:
                with contextlib.suppress(Exception):
                    hist_map[code] = {
                        "nombre": r.get("producto_nombre", ""),
                        "usd": Decimal(str(r.get("precio_usd", "0") or "0")),
                        "eur": Decimal(str(r.get("precio_bcv_euro", "0") or "0")),
                    }

        cutoff_historical = date(2026, 3, 12)
        reporte = []
        saldo_minimo_items = []  # Tarea 3: facturadas con saldo residual <= $1
        vendedores_set = set()

        def empty_kpi():
            return {"deudor_bcv": 0.0, "desc_bcv": 0.0, "desc_usd": 0.0, "factura_odoo": 0.0}

        kpi_total_general = empty_kpi()
        kpi_total_vencido = empty_kpi()
        kpi_vigentes = empty_kpi()
        kpi_1_30 = empty_kpi()
        kpi_31_60 = empty_kpi()
        kpi_61_90 = empty_kpi()
        kpi_mas_90 = empty_kpi()

        today_date = date.today()

        # Setup engine objects for on-the-fly calculation when order is not in BandejaFacturacion
        from cxc.engine.discounts import EngineInputs, calcular_factura
        from cxc.engine.price_resolver import PriceResolver
        from cxc.odoo.price import OdooPriceResolver
        from cxc.sheets import serde

        engine_cfg_obj = config.engine
        pricelist_ids_map = {
            engine_cfg_obj.lista_usd: int(usd_ids[0])
            if usd_ids and str(usd_ids[0]).isdigit()
            else 4,
            engine_cfg_obj.lista_bcv: int(ves_ids[0])
            if ves_ids and str(ves_ids[0]).isdigit()
            else 5,
        }
        price_resolver_engine = OdooPriceResolver(execute, pricelist_ids_map) if execute else None

        # Pre-fetch all collections once outside loop to eliminate N+1 I/O overhead
        all_lines_map = {}
        for l_row in repo._g.read_rows("LineasOrden"):
            so_id_key = l_row.get("so_id", "")
            if so_id_key:
                all_lines_map.setdefault(so_id_key, []).append(serde.linea_from_row(l_row))

        class FastPriceResolver(PriceResolver):
            def __init__(self, lines_map, fallback_resolver=None):
                self._prices = {}
                for lines in lines_map.values():
                    for line in lines:
                        if line.producto and line.precio_unitario is not None:
                            p_str = str(line.producto).strip()
                            d_pu = line.precio_unitario
                            self._prices[(p_str, "5")] = d_pu
                            self._prices[(p_str, "Precio USD Pago VES")] = d_pu
                            self._prices[(p_str, "4")] = d_pu
                            self._prices[(p_str, "Precio USD")] = d_pu
                self._fallback = fallback_resolver

            def precio(self, producto: str, lista: str, fecha: date | None = None) -> Decimal:
                p_str = str(producto).strip()
                if (p_str, str(lista)) in self._prices:
                    return self._prices[(p_str, str(lista))]
                if (p_str, "5") in self._prices:
                    return self._prices[(p_str, "5")]
                if self._fallback:
                    try:
                        return self._fallback.precio(producto, lista, fecha)
                    except Exception:
                        pass
                return Decimal("0")

            def volumen(self, producto: str) -> Decimal:
                if self._fallback:
                    try:
                        return self._fallback.volumen(producto)
                    except Exception:
                        pass
                return Decimal("0")

        fast_resolver = FastPriceResolver(all_lines_map, price_resolver_engine)

        all_desc_mc = repo.descuentos_marca_categoria()
        all_desc_vol = repo.descuentos_volumen()
        all_reg_rec = repo.reglas_recurrencia()
        all_desc_bcv = repo.descuento_bcv_completo()
        all_promo_1st = repo.promociones_primera_compra()
        all_feriados = repo.feriados()
        all_exclusiones = repo.exclusiones()
        all_desc_recompra = repo.descuentos_recompra()
        all_desc_diferencial = repo.descuentos_diferencial_cambiario()

        # Import audit logic
        from cxc.engine.discount_audit import (
            auditar_descuento_factura,
            auditar_descuento_orden,
            auditar_nota_credito,
        )

        recon_cfg = config.reconciliation
        tol_round = recon_cfg.tolerance_rounding
        tol_red = recon_cfg.tolerance_red

        # Load existing audit rows to avoid duplicate appends on every cache refresh
        try:
            existing_audit_rows = repo.all_auditoria() if hasattr(repo, "all_auditoria") else []
        except Exception:
            existing_audit_rows = []
        # Key: (so_id, tipo_auditoria) — only append if not already recorded today
        from datetime import date as _date

        _today_str = _date.today().isoformat()
        existing_audit_keys: set[tuple[str, str]] = {
            (r.get("so_id", ""), r.get("tipo_auditoria", ""))
            for r in existing_audit_rows
            if str(r.get("timestamp_audit", ""))[:10] == _today_str
        }

        new_audit_rows: list[dict] = []
        for o in ordenes:
            live_state = so_odoo_data.get(o.so_id, {}).get("state")
            entrega_valida = o.so_id in picking_delivery_map and o.so_id not in picking_return_set
            if orden_excluida(o, live_state=live_state, entrega_valida=entrega_valida):
                continue

            # Tarea 2: orden facturada cuya factura Odoo ya esta 'Pagada' o
            # 'En proceso de pago' -> ya no es cuenta por cobrar, sale del
            # reporte general (sigue disponible via /api/auditoria).
            if o.facturada and o.so_id in so_pagada_en_odoo:
                continue

            order_lines = lines_by_so.get(o.so_id, [])
            odoo_info = so_odoo_data.get(o.so_id, {})

            # Compute actual net delivered subtotal per product line
            # (cantidad_entregada * precio_unitario)
            if order_lines:
                monto_entregado_neto_usd = sum(
                    max(
                        Decimal("0"),
                        Decimal(
                            str(
                                ln.get("cantidad_entregada")
                                if ln.get("cantidad_entregada") not in (None, "", "None")
                                else ln.get("cantidad", "0")
                            )
                        ),
                    )
                    * Decimal(str(ln.get("precio_unitario", "0")))
                    for ln in order_lines
                )
            else:
                st_fallback = odoo_info.get("state") or getattr(o, "estado_orden", "sale")
                monto_entregado_neto_usd = (
                    o.monto_total if st_fallback not in ["cancel", "draft"] else Decimal("0")
                )

            # REGLA DE AUDITORÍA POR CANTIDADES ENTREGADAS Y DEVOLUCIONES (SIN HARDCODING):
            # Si el valor de mercancía efectivamente despachada y retenida por el cliente es 0
            # (sin despachar o devuelta a almacén 100%), no genera saldo deudor por cobrar.
            if monto_entregado_neto_usd <= Decimal("0"):
                continue

            client_name = clientes_map.get(o.cliente_id, f"Cliente ID: {o.cliente_id}")
            vendedor = odoo_info.get("vendedor", "Sin Vendedor")
            vendedores_set.add(vendedor)

            p_data = pagos_by_so.get(
                o.so_id,
                {"abono_bcv": Decimal("0"), "abono_binance": Decimal("0"), "ultimo_abono": None},
            )
            abono_bcv = float(p_data["abono_bcv"])
            abono_binance = float(p_data["abono_binance"])
            fecha_ultimo_abono = p_data["ultimo_abono"]
            # Indica si el abono proviene de reconciliación automática de Odoo
            # (sin vinculación manual en Sheets)
            pago_desde_odoo = bool(p_data.get("desde_odoo", False)) and not bool(
                p_data.get("tiene_vinc_manual", False)
            )

            subtotal = monto_entregado_neto_usd

            lista_id_str = str(o.lista_precios or "").strip()
            is_historical = (
                not lista_id_str or lista_id_str in ("0", "None", "") or o.fecha < cutoff_historical
            )

            # Compute projected USD subtotal and total using UI candidate USD pricelists
            # or Historical List
            total_proyectado_usd = Decimal("0.0")
            for ln in order_lines:
                qty = max(
                    Decimal("0"),
                    Decimal(
                        str(
                            ln.get("cantidad_entregada")
                            if ln.get("cantidad_entregada") not in (None, "", "None")
                            else ln.get("cantidad", "0")
                        )
                    ),
                )
                prod_raw = ln.get("producto", "")
                pt_id = extract_product_tmpl_id(prod_raw)

                if is_historical:
                    code_key = str(pt_id) if pt_id else prod_raw.strip()
                    hist_info = hist_map.get(code_key)
                    if hist_info and hist_info["usd"] > Decimal("0"):
                        price_usd = hist_info["usd"]
                    else:
                        price_usd = Decimal(str(ln.get("precio_unitario", "0")))
                else:
                    eff_price = (
                        resolve_effective_pricelist_price(pt_id, o.fecha, usd_ids, rules_usd)
                        if pt_id
                        else None
                    )
                    price_usd = (
                        eff_price
                        if eff_price is not None
                        else Decimal(str(ln.get("precio_unitario", "0")))
                    )

                total_proyectado_usd += qty * price_usd

            if is_historical:
                lista_name = (
                    "Lista Histórica Auditoría"
                    if o.fecha < cutoff_historical
                    else "Lista Histórica (Sin Lista)"
                )
                monto_total_proyectado_usd = (
                    float(total_proyectado_usd)
                    if total_proyectado_usd > Decimal("0")
                    else float(o.monto_total)
                )
            elif not lista_id_str or lista_id_str in ("0", "None"):
                lista_name = "Sin Lista (Odoo)"
                monto_total_proyectado_usd = float(o.monto_total)
            elif lista_id_str in [str(x) for x in usd_ids]:
                lista_name = f"Lista USD (#{lista_id_str})"
                monto_total_proyectado_usd = float(o.monto_total)
            elif lista_id_str in [str(x) for x in ves_ids]:
                lista_name = f"Precio VES (#{lista_id_str})"
                monto_total_proyectado_usd = (
                    float(total_proyectado_usd)
                    if total_proyectado_usd > Decimal("0")
                    else float(o.monto_total)
                )
            else:
                lista_name = f"Lista #{lista_id_str}"
                monto_total_proyectado_usd = (
                    float(total_proyectado_usd)
                    if total_proyectado_usd > Decimal("0")
                    else float(o.monto_total)
                )

            # Calculate Odoo Invoice residual balance for posted invoices (converted to USD if VES)
            inv_list = invoices_by_so.get(o.so_id, [])
            if inv_list:
                tot_res_usd = 0.0
                inv_names_list = []
                for inv in inv_list:
                    inv_names_list.append(str(inv.get("name", "")))
                    res_val = float(inv.get("amount_residual", 0.0))
                    curr = inv.get("currency_id")
                    c_name = curr[1] if isinstance(curr, list | tuple) and len(curr) > 1 else "USD"
                    inv_dt = str(inv.get("invoice_date") or o.fecha.isoformat())[:10]
                    rate = rates_map.get(inv_dt, last_bcv_val)
                    if c_name == "VES" and rate > 0:
                        tot_res_usd += res_val / rate
                    else:
                        tot_res_usd += res_val
                saldo_factura_odoo = max(0.0, float(tot_res_usd))
                factura_odoo_nombre = ", ".join(inv_names_list)
            else:
                saldo_factura_odoo = None
                factura_odoo_nombre = "Sin Factura"

            # Engine calculation data
            b = bandeja_map.get(o.so_id)
            if not b and fast_resolver:
                try:
                    inputs = EngineInputs(
                        orden=o,
                        lineas=all_lines_map.get(o.so_id, []),
                        abonos=[],
                        descuentos=all_desc_mc,
                        descuentos_volumen=all_desc_vol,
                        reglas_recurrencia=all_reg_rec,
                        descuento_bcv_diario=all_desc_bcv,
                        promociones_primera_compra=all_promo_1st,
                        feriados_tabla=all_feriados,
                        price_resolver=fast_resolver,
                        engine_config=engine_cfg_obj,
                        fecha_calculo=o.fecha,
                        all_ordenes=ordenes,
                        exclusiones=all_exclusiones,
                        descuentos_recompra=all_desc_recompra,
                        descuentos_diferencial=all_desc_diferencial,
                    )
                    b = calcular_factura(inputs)
                except Exception as e_calc:
                    logger.warning("Error evaluando motor dinámico para %s: %s", o.so_id, e_calc)

            if b:
                total_descuentos_monto = float(b.total_descuentos + b.ncs_calculadas)
                total_con_descuentos = float(b.total_motor)
                descuentos_desglose = []
                for d in b.descuentos_detalle:
                    descuentos_desglose.append(
                        {"origen": d.origen, "descripcion": d.descripcion, "monto": float(d.monto)}
                    )
                if b.ncs_calculadas > Decimal("0") and not any(
                    d["origen"] == "primera_compra" for d in descuentos_desglose
                ):
                    descuentos_desglose.append(
                        {
                            "origen": "primera_compra",
                            "descripcion": "Obsequio / Promo Primera Compra",
                            "monto": float(b.ncs_calculadas),
                        }
                    )
            else:
                total_descuentos_monto = 0.0
                total_con_descuentos = float(o.monto_total)
                descuentos_desglose = []

            # ── Notas de Crédito reales de Odoo para esta orden ──────────────────
            nc_list_odoo = ncs_by_so.get(o.so_id, [])
            ncs_odoo_monto_usd = 0.0
            ncs_odoo_nombres: list[str] = []
            for nc in nc_list_odoo:
                nc_tot = float(nc.get("amount_total") or 0)
                nc_curr = nc.get("currency_id")
                nc_c_name = (
                    nc_curr[1] if isinstance(nc_curr, list | tuple) and len(nc_curr) > 1 else "USD"
                )
                nc_dt = str(nc.get("invoice_date") or o.fecha.isoformat())[:10]
                nc_rate = rates_map.get(nc_dt, last_bcv_val)
                if nc_c_name == "VES" and nc_rate > 0:
                    ncs_odoo_monto_usd += nc_tot / nc_rate
                else:
                    ncs_odoo_monto_usd += nc_tot
                ncs_odoo_nombres.append(str(nc.get("name", "")))

            # ── Descuentos en línea de la orden Odoo ─────────────────────────────
            desc_orden_odoo_monto = sol_discounts_by_so.get(o.so_id, 0.0)
            desc_orden_odoo_detalle = sol_discount_detail_by_so.get(o.so_id, "")

            # ── Descuentos en línea de facturas Odoo ─────────────────────────────
            desc_factura_odoo_monto = inv_line_discounts_by_so.get(o.so_id, 0.0)
            desc_factura_odoo_detalle = inv_line_discount_detail_by_so.get(o.so_id, "")

            # ── Debt columns (including NCs from Odoo) ────────────────────────────
            monto_orig = float(o.monto_total)
            saldo_deudor_bcv = max(0.0, monto_orig - abono_bcv)
            saldo_deudor_lista_usd = max(0.0, monto_total_proyectado_usd - abono_binance)
            # NCs reducen el saldo con descuento (son hechos contables reales)
            saldo_con_descuento_bcv = max(
                0.0, saldo_deudor_bcv - total_descuentos_monto - ncs_odoo_monto_usd
            )
            saldo_con_descuento_lista_usd = max(
                0.0, saldo_deudor_lista_usd - total_descuentos_monto - ncs_odoo_monto_usd
            )

            # Venta bruta teórica: lo que la orden DEBIÓ sumar con el precio
            # correcto de lista y SIN ningún descuento (b.precio_base_calculado,
            # ignora el precio que realmente quedó en la línea de Odoo). Sirve
            # para separar "cuánto perdí por vender a un precio equivocado" de
            # "cuánto di en descuentos válidos" -- monto_orig ya trae ambos
            # efectos mezclados. precio_base_calculado es SIN IVA (viene del
            # price_resolver, igual que price_unit en Odoo); monto_orig
            # (amount_total) SÍ trae IVA -- hay que igualar la base antes de
            # comparar o la diferencia siempre marca ~16% de "pérdida" aunque
            # el precio esté correcto.
            venta_bruta_teorica = float(b.precio_base_calculado) if b else monto_orig
            venta_bruta_teorica_con_iva = venta_bruta_teorica * (1 + float(config.engine.iva_rate))
            diferencia_precio_lista = round(venta_bruta_teorica_con_iva - monto_orig, 2)

            # Tarea 3, umbral de cierre: orden ya facturada cuyo saldo con
            # descuentos (BCV o USD lista, cualquiera de las dos) es <= $1 ->
            # se oculta del reporte general y pasa a la tabla de "pendientes
            # por cerrar (saldo <= $1)". No aplica a ordenes sin facturar
            # (esas se manejan por la Tarea 3.1 / bandeja de facturacion).
            if o.facturada and (
                saldo_con_descuento_bcv <= 1.0 or saldo_con_descuento_lista_usd <= 1.0
            ):
                saldo_minimo_items.append(
                    {
                        "so_id": o.so_id,
                        "cliente_nombre": clientes_map.get(
                            o.cliente_id, f"Cliente ID: {o.cliente_id}"
                        ),
                        "vendedor": odoo_info.get("vendedor", "Sin Vendedor"),
                        "factura_id": o.factura_id or "N/A",
                        "saldo_con_descuento_bcv": round(saldo_con_descuento_bcv, 2),
                        "saldo_con_descuento_lista_usd": round(saldo_con_descuento_lista_usd, 2),
                        "saldo_factura_odoo": saldo_factura_odoo,
                    }
                )
                continue

            # ── Auditoría de descuentos: motor vs Odoo ────────────────────────────
            motor_desc_usd = Decimal(str(total_descuentos_monto))
            motor_ncs_usd = Decimal(str(b.ncs_calculadas if b else 0))

            audit_orden = auditar_descuento_orden(
                so_id=o.so_id,
                motor_total_descuentos=motor_desc_usd,
                odoo_descuento_aplicado=Decimal(str(desc_orden_odoo_monto)),
                tolerance_rounding=tol_round,
                tolerance_red=tol_red,
                detalle_odoo=desc_orden_odoo_detalle,
                detalle_motor="; ".join(
                    f"{d['descripcion']}: ${d['monto']:.2f}" for d in descuentos_desglose
                ),
            )
            audit_factura = auditar_descuento_factura(
                so_id=o.so_id,
                motor_total_descuentos=motor_desc_usd,
                odoo_descuento_factura=Decimal(str(desc_factura_odoo_monto)),
                tolerance_rounding=tol_round,
                tolerance_red=tol_red,
                detalle_odoo=desc_factura_odoo_detalle,
                detalle_motor="; ".join(
                    f"{d['descripcion']}: ${d['monto']:.2f}" for d in descuentos_desglose
                ),
            )
            audit_nc = auditar_nota_credito(
                so_id=o.so_id,
                motor_ncs_calculadas=motor_ncs_usd,
                odoo_nc_monto=Decimal(str(ncs_odoo_monto_usd)),
                tolerance_rounding=tol_round,
                tolerance_red=tol_red,
                detalle_odoo=", ".join(ncs_odoo_nombres) if ncs_odoo_nombres else "Sin NC",
                detalle_motor=f"NCs motor: ${float(motor_ncs_usd):.2f}",
            )

            # Collect audit rows in memory (persisted in 1 single batch call outside loop)
            _now_iso = datetime.now().isoformat()
            for _ar in [audit_orden, audit_factura, audit_nc]:
                if _ar.enviar_a_bandeja:
                    _key = (o.so_id, _ar.tipo.value)
                    if _key not in existing_audit_keys:
                        new_audit_rows.append(
                            {
                                "audit_id": f"{o.so_id}_{_ar.tipo.value}_{_today_str}",
                                "so_id": o.so_id,
                                "tipo_auditoria": _ar.tipo.value,
                                "motor_calcula_usd": round(float(_ar.motor_calcula_usd), 4),
                                "odoo_registrado_usd": round(float(_ar.odoo_registrado_usd), 4),
                                "diferencia_usd": round(float(_ar.diferencia_usd), 4),
                                "detalle_odoo": _ar.detalle_odoo,
                                "detalle_motor": _ar.detalle_motor,
                                "estado": "pendiente",
                                "revisado_por": "",
                                "timestamp_audit": _now_iso,
                            }
                        )
                        existing_audit_keys.add(_key)

            # Build audit summary for the report row
            has_any_discrepancy = any(
                a.enviar_a_bandeja for a in [audit_orden, audit_factura, audit_nc]
            )
            audit_descuentos_summary = {
                "tiene_discrepancia": has_any_discrepancy,
                "estado_orden": audit_orden.estado.value,
                "estado_factura": audit_factura.estado.value,
                "estado_nc": audit_nc.estado.value,
                "motor_calcula_usd": float(motor_desc_usd),
                "odoo_orden_usd": desc_orden_odoo_monto,
                "odoo_factura_usd": desc_factura_odoo_monto,
                "diferencia_orden": float(audit_orden.diferencia_usd),
                "diferencia_factura": float(audit_factura.diferencia_usd),
                "diferencia_nc": float(audit_nc.diferencia_usd),
                "descuento_adicional": float(audit_orden.descuento_adicional_a_aplicar),
            }

            # Dates & aging calculation
            fecha_delivery = picking_delivery_map.get(o.so_id)
            if not fecha_delivery:
                fecha_delivery = o.fecha.isoformat()

            term_name = odoo_info.get("payment_term_name") or "Contado"
            dias_credito = parse_term_days(term_name)

            try:
                dt_del = datetime.strptime(fecha_delivery[:10], "%Y-%m-%d").date()
            except Exception:
                dt_del = o.fecha
            dt_venc = dt_del + timedelta(days=dias_credito)
            fecha_vencimiento = dt_venc.isoformat()

            dias_vencido = 0
            if today_date > dt_venc:
                dias_vencido = (today_date - dt_venc).days

            # Accumulate Aging KPIs with 4 distinct sub-balances per bucket
            s_inv = saldo_factura_odoo if saldo_factura_odoo is not None else 0.0
            if saldo_deudor_bcv > 0.05 or saldo_con_descuento_lista_usd > 0.05 or s_inv > 0.05:
                # 1. Total General por Cobrar (Always accumulate)
                kpi_total_general["deudor_bcv"] += saldo_deudor_bcv
                kpi_total_general["desc_bcv"] += saldo_con_descuento_bcv
                kpi_total_general["desc_usd"] += saldo_con_descuento_lista_usd
                kpi_total_general["factura_odoo"] += s_inv

                if dias_vencido <= 0:
                    kpi_vigentes["deudor_bcv"] += saldo_deudor_bcv
                    kpi_vigentes["desc_bcv"] += saldo_con_descuento_bcv
                    kpi_vigentes["desc_usd"] += saldo_con_descuento_lista_usd
                    kpi_vigentes["factura_odoo"] += s_inv
                else:
                    # 2. Total Vencido General (All overdue orders)
                    kpi_total_vencido["deudor_bcv"] += saldo_deudor_bcv
                    kpi_total_vencido["desc_bcv"] += saldo_con_descuento_bcv
                    kpi_total_vencido["desc_usd"] += saldo_con_descuento_lista_usd
                    kpi_total_vencido["factura_odoo"] += s_inv

                    if 1 <= dias_vencido <= 30:
                        kpi_1_30["deudor_bcv"] += saldo_deudor_bcv
                        kpi_1_30["desc_bcv"] += saldo_con_descuento_bcv
                        kpi_1_30["desc_usd"] += saldo_con_descuento_lista_usd
                        kpi_1_30["factura_odoo"] += s_inv
                    elif 31 <= dias_vencido <= 60:
                        kpi_31_60["deudor_bcv"] += saldo_deudor_bcv
                        kpi_31_60["desc_bcv"] += saldo_con_descuento_bcv
                        kpi_31_60["desc_usd"] += saldo_con_descuento_lista_usd
                        kpi_31_60["factura_odoo"] += s_inv
                    elif 61 <= dias_vencido <= 90:
                        kpi_61_90["deudor_bcv"] += saldo_deudor_bcv
                        kpi_61_90["desc_bcv"] += saldo_con_descuento_bcv
                        kpi_61_90["desc_usd"] += saldo_con_descuento_lista_usd
                        kpi_61_90["factura_odoo"] += s_inv
                    else:
                        kpi_mas_90["deudor_bcv"] += saldo_deudor_bcv
                        kpi_mas_90["desc_bcv"] += saldo_con_descuento_bcv
                        kpi_mas_90["desc_usd"] += saldo_con_descuento_lista_usd
                        kpi_mas_90["factura_odoo"] += s_inv

            conc = concs.get(o.so_id)

            reporte.append(
                {
                    "so_id": o.so_id,
                    "cliente_nombre": client_name,
                    "vendedor": vendedor,
                    "fecha": o.fecha.isoformat(),
                    "fecha_entrega": fecha_delivery[:10],
                    "terminos_pago": term_name,
                    "dias_credito": dias_credito,
                    "fecha_vencimiento": fecha_vencimiento,
                    "dias_vencido": dias_vencido,
                    "fecha_ultimo_abono": fecha_ultimo_abono,
                    "lista_precios": lista_name,
                    "lista_origen": lista_name,
                    "subtotal": float(subtotal),
                    "monto_total": monto_orig,
                    "monto_odoo": monto_orig,
                    "monto_total_proyectado_usd": monto_total_proyectado_usd,
                    "abono_usd_bcv": abono_bcv,
                    "abono_usd_binance": abono_binance,
                    "monto_pagado": abono_bcv,
                    "saldo_deudor": saldo_deudor_bcv,
                    "saldo_deudor_bcv": saldo_deudor_bcv,
                    "saldo_deudor_lista_usd": saldo_deudor_lista_usd,
                    "total_con_descuentos": total_con_descuentos,
                    "total_descuentos_monto": total_descuentos_monto,
                    "venta_bruta_teorica": venta_bruta_teorica,
                    "diferencia_precio_lista": diferencia_precio_lista,
                    "saldo_deudor_con_descuentos": saldo_con_descuento_bcv,
                    "saldo_con_descuento_bcv": saldo_con_descuento_bcv,
                    "saldo_con_descuento_lista_usd": saldo_con_descuento_lista_usd,
                    "saldo_factura_odoo": saldo_factura_odoo,
                    "factura_odoo_nombre": factura_odoo_nombre,
                    "descuentos_desglose": descuentos_desglose,
                    "facturada": o.facturada,
                    # Tarea 4, Caso B ("Listo para Cierre"): SOLO ordenes SIN
                    # factura cuyo saldo segun el motor ya es 0 (pagada, lista
                    # para que Administracion facture). El otro escenario de
                    # "Listo para Cierre" (facturada y pagada segun el motor pero
                    # no en Odoo -> falta aplicar NC) nunca llega a esta lista:
                    # ya sale del reporte general por el umbral de la Tarea 3.
                    "candidata_a_cierre": (not o.facturada)
                    and (saldo_con_descuento_bcv <= 0.05 or saldo_con_descuento_lista_usd <= 0.05),
                    "pago_desde_odoo": pago_desde_odoo,
                    "descuentos_odoo_orden": {
                        "monto_usd": round(desc_orden_odoo_monto, 2),
                        "detalle": desc_orden_odoo_detalle,
                        "pct_sobre_total": round(desc_orden_odoo_monto / monto_orig * 100, 2)
                        if monto_orig > 0
                        else 0.0,
                    },
                    "descuentos_odoo_factura": {
                        "monto_usd": round(desc_factura_odoo_monto, 2),
                        "detalle": desc_factura_odoo_detalle,
                    },
                    "ncs_odoo": {
                        "monto_usd": round(ncs_odoo_monto_usd, 2),
                        "nombres": ncs_odoo_nombres,
                        "auditoria_estado": audit_nc.estado.value,
                    },
                    "auditoria_descuentos": audit_descuentos_summary,
                    "reconciliacion": {"resultado": conc.resultado.value if conc else "pendiente"}
                    if conc
                    else None,
                }
            )

        # Single batch write for new audit rows to avoid Google Sheets API rate limits
        if new_audit_rows and hasattr(repo, "append_auditoria_rows"):
            try:
                repo.append_auditoria_rows(new_audit_rows)
            except Exception as e_aud:
                logger.warning("Error guardando lote de auditoría: %s", e_aud)

        # Orden mas reciente primero (por numero de SO, creciente con el tiempo
        # en Odoo) -- sin esto, las ordenes nuevas quedaban al final de una
        # lista de cientos de filas y parecian "no haber entrado".
        def _so_num(item: dict) -> int:
            digits = re.sub(r"[^\d]", "", str(item.get("so_id", "")))
            return int(digits) if digits else 0

        reporte.sort(key=_so_num, reverse=True)

        res = {
            "kpis": {
                "total_general": kpi_total_general,
                "total_vencido": kpi_total_vencido,
                "vigentes": kpi_vigentes,
                "vencidas_1_30": kpi_1_30,
                "vencidas_31_60": kpi_31_60,
                "vencidas_61_90": kpi_61_90,
                "vencidas_mas_90": kpi_mas_90,
            },
            "vendedores": sorted(vendedores_set),
            "items": reporte,
            "saldo_minimo_pendientes": saldo_minimo_items,
        }
        _REPORTE_SALDOS_CACHE["data"] = res
        _REPORTE_SALDOS_CACHE["timestamp"] = time.time()
        return res
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/config/tasas")
async def get_config_tasas():
    try:
        repo = get_repo()
        # Read the last 15 raw rows from SerieTasas
        filas = repo._g.read_rows("SerieTasas")[-15:]
        tasas = []
        for f in reversed(filas):
            tbcv = float(parse_decimal_safe(f.get("tasa_bcv", "0")))
            tbin = float(parse_decimal_safe(f.get("tasa_binance", "0")))
            diff_bs = tbin - tbcv
            diff_pct = (diff_bs / tbin * 100) if tbin > 0 else 0.0
            tasas.append(
                {
                    "timestamp": f.get("timestamp", ""),
                    "tasa_bcv": tbcv,
                    "tasa_binance": tbin,
                    "diferencia_bs": round(diff_bs, 2),
                    "diferencia_pct": round(diff_pct, 2),
                    "fuente": f.get("fuente", ""),
                }
            )
        return tasas
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/tasas")
async def post_config_tasas(req: TasaRequest):
    try:
        repo = get_repo()
        from cxc.models import SerieTasa

        tasa = SerieTasa(
            timestamp=datetime.now(),
            tasa_bcv=Decimal(str(req.tasa_bcv)),
            tasa_binance=Decimal(str(req.tasa_binance)),
            fuente="Carga Manual Web",
            es_heredada=False,
            capturada_ok=True,
        )
        repo.append_serie_tasa(tasa)
        return {"status": "success", "message": "Tasa manual registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/config/feriados")
async def get_config_feriados():
    try:
        repo = get_repo()
        feriados = repo.feriados()
        return [
            {"fecha": f.fecha.isoformat(), "descripcion": f.descripcion, "tipo": f.tipo.value}
            for f in feriados
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/feriados")
async def post_config_feriados(req: FeriadoRequest):
    try:
        repo = get_repo()
        from cxc.models import Feriado, TipoFeriado
        from cxc.sheets import gateway as g
        from cxc.sheets import serde

        feriado = Feriado(
            fecha=date.fromisoformat(req.fecha),
            descripcion=req.descripcion,
            tipo=TipoFeriado.NACIONAL,
        )
        repo._g.append_row(g.T_FERIADOS, serde.feriado_to_row(feriado))
        return {"status": "success", "message": "Feriado registrado con éxito."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/config/descuentos-marca")
async def get_config_descuentos_marca():
    try:
        repo = get_repo()
        rules = repo.descuentos_marca_categoria()
        return [
            {
                "regla_id": r.regla_id,
                "marca": r.marca,
                "categoria": r.categoria,
                "tipo_descuento": r.tipo_descuento,
                "porcentaje": float(r.porcentaje),
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "listas_aplicables": r.listas_aplicables,
                "activo": r.activo,
            }
            for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/descuentos-marca")
async def post_config_descuentos_marca(req: DescuentoMarcaRequest):
    try:
        repo = get_repo()
        import uuid

        from cxc.models import DescuentoMarcaCategoria
        from cxc.sheets import gateway as g
        from cxc.sheets import serde

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        # Check date overlap with active rules of same type, brand, category, list
        existing = repo.descuentos_marca_categoria()
        for r in existing:
            if (
                r.activo
                and r.tipo_descuento == req.tipo_descuento
                and r.marca == req.marca
                and r.categoria == req.categoria
            ):
                lists_overlap = (
                    r.listas_aplicables == "*"
                    or req.listas_aplicables == "*"
                    or r.listas_aplicables == req.listas_aplicables
                )
                if lists_overlap:
                    h1 = v_hasta if v_hasta is not None else date(9999, 12, 31)
                    h2 = r.vigencia_hasta if r.vigencia_hasta is not None else date(9999, 12, 31)
                    if max(v_desde, r.vigencia_desde) <= min(h1, h2):
                        r_hasta = r.vigencia_hasta or "siempre"
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Conflicto: ya existe la regla activa {r.regla_id} "
                                f"({r.vigencia_desde} a {r_hasta}) para esta marca/categoría/lista."
                            ),
                        )

        regla_id = f"REG_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoMarcaCategoria(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            tipo_descuento=req.tipo_descuento,
            porcentaje=Decimal(str(req.porcentaje)),
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            listas_aplicables=req.listas_aplicables,
            activo=True,
        )
        repo._g.append_row(g.T_DESCUENTOS, serde.descuento_to_row(rule))
        return {"status": "success", "message": "Regla de descuento registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/config/listas-precio")
@app.get("/api/odoo/listas-precio")
async def get_config_listas_precio():
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)

        # IMPORTANTE: pasar context={'active_test': False} para que Odoo devuelva
        # las listas archivadas con sus nombres ACTUALES (sin este contexto, Odoo
        # las filtra aunque el dominio las incluya explícitamente).
        pricelists = execute(
            "product.pricelist",
            "search_read",
            [[["active", "in", [True, False]]]],
            {
                "fields": ["id", "name", "currency_id", "active"],
                "context": {"active_test": False},
                "order": "id asc",
            },
        )

        # Fetch items/rules for these pricelists
        list_ids = [pl["id"] for pl in pricelists]
        items = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "in", list_ids]]],
            {
                "fields": [
                    "pricelist_id",
                    "fixed_price",
                    "percent_price",
                    "date_start",
                    "date_end",
                    "product_tmpl_id",
                ],
                "context": {"active_test": False},
            },
        )

        items_by_list: dict[int, list] = {}
        for item in items:
            pl_id = (
                item["pricelist_id"][0]
                if isinstance(item["pricelist_id"], list | tuple)
                else item["pricelist_id"]
            )
            items_by_list.setdefault(pl_id, []).append(item)

        resultado = []
        for pl in pricelists:
            pl_items = items_by_list.get(pl["id"], [])
            rules = []
            dates_start = []
            dates_end = []
            for item in pl_items:
                prod = item.get("product_tmpl_id")
                prod_name = (
                    prod[1] if isinstance(prod, list) and len(prod) > 1 else "Todos los productos"
                )
                ds = item.get("date_start") or None
                de = item.get("date_end") or None
                if ds:
                    dates_start.append(str(ds)[:10])
                if de:
                    dates_end.append(str(de)[:10])
                rules.append(
                    {
                        "producto": prod_name,
                        "precio_fijo": float(item.get("fixed_price") or 0.0),
                        "descuento_porcentaje": float(item.get("percent_price") or 0.0),
                        "fecha_inicio": str(ds)[:10] if ds else "N/A",
                        "fecha_fin": str(de)[:10] if de else "N/A",
                    }
                )

            resultado.append(
                {
                    "id": pl["id"],
                    "name": pl["name"],
                    "moneda": pl["currency_id"][1]
                    if isinstance(pl["currency_id"], list | tuple) and len(pl["currency_id"]) > 1
                    else "USD",
                    "active": pl["active"],
                    "reglas": rules,
                    # Fechas mínima/máxima de las reglas para mostrar rango de vigencia real
                    "fecha_desde": min(dates_start) if dates_start else "N/A",
                    "fecha_hasta": max(dates_end) if dates_end else "N/A",
                }
            )

        from fastapi.responses import JSONResponse

        return JSONResponse(
            content=resultado,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/config/meta")
async def get_config_meta():
    try:
        repo = get_repo()
        rows = repo._g.read_rows("_Meta")
        meta = {}
        for r in rows:
            k = r.get("key")
            if k:
                meta[k] = r.get("value", "")
        # Defaults
        if "cash_window_business_days" not in meta:
            meta["cash_window_business_days"] = "3"
        if "descuento_recompra" not in meta:
            meta["descuento_recompra"] = "0.05"
        return meta
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/meta")
async def post_config_meta(req: MetaRequest):
    try:
        repo = get_repo()
        repo._g.upsert_row(
            "_Meta",
            "key",
            {"key": "cash_window_business_days", "value": str(req.cash_window_business_days)},
        )
        repo._g.upsert_row(
            "_Meta", "key", {"key": "descuento_recompra", "value": str(req.descuento_recompra)}
        )

        # Sync to ReglasRecurrencia sheet for RECOMPRA rule
        rules = repo._g.read_rows("ReglasRecurrencia")
        recompra_exists = False
        for r in rules:
            if r.get("condicion") == "recompra":
                recompra_exists = True
                r["porcentaje"] = str(req.descuento_recompra)
                repo._g.upsert_row("ReglasRecurrencia", "regla_id", r)
                break
        if not recompra_exists:
            repo._g.append_row(
                "ReglasRecurrencia",
                {
                    "regla_id": "REG_RECOMPRA",
                    "condicion": "recompra",
                    "porcentaje": str(req.descuento_recompra),
                    "vigencia_desde": date.today().isoformat(),
                    "vigencia_hasta": "",
                    "activo": "TRUE",
                },
            )

        return {"status": "success", "message": "Ajustes globales actualizados correctamente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


MAPEO_LISTAS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "secrets", "listas_precio_mapeo.json"
)

# In-process cache for pricelist mapping — survives page refreshes within same process lifetime
_PRICELIST_MAPEO_CACHE: dict[str, list[str]] = {}


def _load_mapeo_from_json() -> tuple[list[str], list[str]] | None:
    """Lee mapeo desde el archivo JSON local. Retorna None si no existe o falla."""
    try:
        if os.path.exists(MAPEO_LISTAS_FILE):
            with open(MAPEO_LISTAS_FILE, encoding="utf-8") as f:
                j_data = json.load(f)
                usd = [str(x) for x in j_data.get("valid_pricelists_usd", [])]
                ves = [str(x) for x in j_data.get("valid_pricelists_ves", [])]
                if usd:
                    return usd, ves
    except Exception:
        pass
    return None


def _save_mapeo_to_json(usd_list: list[str], ves_list: list[str]) -> None:
    """Persiste el mapeo en el archivo JSON local."""
    try:
        os.makedirs(os.path.dirname(MAPEO_LISTAS_FILE), exist_ok=True)
        with open(MAPEO_LISTAS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"valid_pricelists_usd": usd_list, "valid_pricelists_ves": ves_list}, f, indent=2
            )
    except Exception:
        pass


def get_valid_pricelists_usd_and_ves(repo=None) -> tuple[list[str], list[str]]:
    """Obtiene las listas de precios USD y VES configuradas.
    Orden de prioridad:
    1. In-process memory cache (más rápido, persiste entre requests del mismo proceso)
    2. Archivo JSON local (persiste entre requests, no entre deploys en Railway)
    3. Google Sheets _Meta (más lento pero persistente entre deploys)
    4. Variables de entorno (fallback final)
    """
    global _PRICELIST_MAPEO_CACHE
    # 1. In-process memory cache
    if _PRICELIST_MAPEO_CACHE.get("usd") and _PRICELIST_MAPEO_CACHE.get("ves"):
        return _PRICELIST_MAPEO_CACHE["usd"], _PRICELIST_MAPEO_CACHE["ves"]

    # 2. JSON file
    json_result = _load_mapeo_from_json()
    if json_result:
        usd_list, ves_list = json_result
        _PRICELIST_MAPEO_CACHE["usd"] = usd_list
        _PRICELIST_MAPEO_CACHE["ves"] = ves_list
        return usd_list, ves_list

    # 3. Google Sheets _Meta
    try:
        if repo is None:
            repo = get_repo()
        usd_str = repo._g.get_meta("valid_pricelists_usd")
        ves_str = repo._g.get_meta("valid_pricelists_ves")

        usd_list = [x.strip() for x in usd_str.split(",") if x.strip()] if usd_str else []
        ves_list = [x.strip() for x in ves_str.split(",") if x.strip()] if ves_str else []

        if usd_list:
            _PRICELIST_MAPEO_CACHE["usd"] = usd_list
            _PRICELIST_MAPEO_CACHE["ves"] = ves_list or [os.environ.get("ODOO_PRICELIST_BCV", "5")]
            # Also write to JSON so next cold start loads instantly
            _save_mapeo_to_json(usd_list, _PRICELIST_MAPEO_CACHE["ves"])
            return _PRICELIST_MAPEO_CACHE["usd"], _PRICELIST_MAPEO_CACHE["ves"]
    except Exception:
        pass

    # 4. Environment variable fallback
    usd_env = os.environ.get("ODOO_PRICELIST_USD", "4")
    ves_env = os.environ.get("ODOO_PRICELIST_BCV", "5")
    return [x.strip() for x in usd_env.split(",") if x.strip()], [
        x.strip() for x in ves_env.split(",") if x.strip()
    ]


@app.get("/api/config/listas-precio-mapeo")
async def get_config_listas_precio_mapeo():
    try:
        repo = get_repo()
        usd_list, ves_list = get_valid_pricelists_usd_and_ves(repo)
        return {"valid_pricelists_usd": usd_list, "valid_pricelists_ves": ves_list}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/listas-precio-mapeo")
async def post_config_listas_precio_mapeo(req: PricelistMapRequest):
    global _PRICELIST_MAPEO_CACHE
    try:
        usd_list = [str(x) for x in req.valid_pricelists_usd] if req.valid_pricelists_usd else ["4"]
        ves_list = [str(x) for x in req.valid_pricelists_ves] if req.valid_pricelists_ves else ["5"]

        # 1. Update in-process memory cache immediately (fastest — no I/O required)
        _PRICELIST_MAPEO_CACHE["usd"] = usd_list
        _PRICELIST_MAPEO_CACHE["ves"] = ves_list

        # 2. Write to JSON file (persists across requests, lost on Railway deploy)
        _save_mapeo_to_json(usd_list, ves_list)

        # 3. Write to Google Sheets _Meta (true persistent storage across deploys)
        try:
            repo = get_repo()
            repo._g.set_meta("valid_pricelists_usd", ",".join(usd_list))
            repo._g.set_meta("valid_pricelists_ves", ",".join(ves_list))
        except Exception as sheets_err:
            logger.warning("No se pudo guardar mapeo en Google Sheets: %s", sheets_err)

        return {
            "status": "success",
            "message": "Mapeo de listas de precios actualizado correctamente.",
            "valid_pricelists_usd": usd_list,
            "valid_pricelists_ves": ves_list,
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/eliminar-descuento")
async def post_eliminar_descuento(req: EliminarDescuentoRequest):
    try:
        repo = get_repo()
        deleted = repo._g.delete_row(req.tabla, "regla_id", req.regla_id)
        if hasattr(repo._g, "_read_cache"):
            repo._g._read_cache.pop(req.tabla, None)
        if not deleted:
            raise HTTPException(
                status_code=404, detail="Regla no encontrada o no se pudo eliminar."
            )
        return {"status": "success", "message": f"Regla {req.regla_id} eliminada permanentemente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


_SALDOS_REALES_CACHE: dict[str, Any] = {"data": None, "timestamp": 0.0}
_SALDOS_REALES_CACHE_TTL = 60.0


async def _get_saldos_reales_por_so() -> dict[str, float] | None:
    """``so_id`` -> saldo real pendiente, EXACTAMENTE el mismo cálculo que

    ``/api/reporte-saldos`` (Vinculaciones + pago directo de Odoo como
    fallback + NCs reales + descuentos del motor + zonificación por
    cantidad entregada/devuelta + exclusión por ``payment_state`` + piso de
    $1) -- a diferencia del cálculo naive de sugerencias
    (``monto_total - Vinculaciones``), que no resta nada de eso y por eso
    podía sugerir aplicar un pago a una orden que en realidad ya está
    saldada.

    Reusa ``get_reporte_saldos()`` tal cual en vez de duplicar su lógica
    (evita mantener dos copias de un cálculo financiero de ~1000 líneas).
    Esa función recalcula SIEMPRE desde Odoo (su propio cache interno tiene
    TTL 0 -- deliberado para que el reporte principal muestre datos
    siempre frescas), así que se envuelve acá con un cache propio de 60s
    para que cargar la tabla de sugerencias repetidas veces no dispare esa
    misma batería completa de queries a Odoo en cada carga.

    Una orden que no aparece en ninguna de las dos listas del reporte
    (``items`` ni ``saldo_minimo_pendientes``) se trata como saldo 0 -- el
    reporte ya la excluyó por alguna razón real (cancelada, sin entrega
    neta, totalmente pagada), y sugerencias no debe ofrecerla tampoco.

    Devuelve ``None`` (no ``{}``) si ``get_reporte_saldos()`` mismo falla
    (Odoo caído, error de datos) -- quien llama debe caer al cálculo naive
    anterior en ese caso, no tratar "no pude calcular" como "todo saldo 0"
    (eso vaciaría la bandeja de sugerencias enteras ante cualquier falla
    transitoria del reporte).
    """
    import time

    now_ts = time.time()
    cached = _SALDOS_REALES_CACHE["data"]
    if cached is not None and now_ts - float(_SALDOS_REALES_CACHE["timestamp"]) < (
        _SALDOS_REALES_CACHE_TTL
    ):
        return cached  # type: ignore[no-any-return]

    try:
        reporte = await get_reporte_saldos()
    except Exception as e_reporte:
        logger.warning(
            "No se pudo calcular saldos reales (get_reporte_saldos) para sugerencias: %s",
            e_reporte,
        )
        return None

    saldos: dict[str, float] = {}
    for item in reporte.get("items", []):
        saldos[item["so_id"]] = item["saldo_con_descuento_bcv"]
    for item in reporte.get("saldo_minimo_pendientes", []):
        saldos[item["so_id"]] = item["saldo_con_descuento_bcv"]

    _SALDOS_REALES_CACHE["data"] = saldos
    _SALDOS_REALES_CACHE["timestamp"] = now_ts
    return saldos


def _detectar_pagos_duplicados(pagos_rows: list[dict[str, str]]) -> dict[str, list[str]]:
    """``pago_id`` -> lista de otros ``pago_id`` con cliente, monto, moneda,

    método de pago y fecha IDÉNTICOS -- posible duplicado (ej. el mismo
    pago cargado dos veces en Odoo, o un banco que reporta la misma
    transacción dos veces).

    Compara contra TODO el universo de pagos, incluidos los ya vinculados o
    conciliados -- no solo los pendientes: el caso real es que el pago
    "original" ya esté aplicado, y el que entra de nuevo (todavía sin
    asociar) sea el sospechoso. Comparar solo contra pendientes no
    detectaría ese caso, el más común de un duplicado real.
    """
    grupos: dict[tuple[str, Decimal, str, str, str], list[str]] = {}
    for p in pagos_rows:
        pid = str(p.get("pago_id", "")).strip()
        if not pid:
            continue
        cliente_id = str(p.get("cliente_id", "")).strip()
        monto = parse_decimal_safe(p.get("monto", "0")).quantize(Decimal("0.01"))
        moneda = str(p.get("moneda", "") or "").upper().strip()
        metodo = str(p.get("metodo_pago", "") or "").strip()
        fecha = str(p.get("fecha_pago") or p.get("fecha") or "")[:10]
        key = (cliente_id, monto, moneda, metodo, fecha)
        grupos.setdefault(key, []).append(pid)

    duplicados: dict[str, list[str]] = {}
    for pids in grupos.values():
        if len(pids) > 1:
            for pid in pids:
                duplicados[pid] = [otro for otro in pids if otro != pid]
    return duplicados


def leer_pagos_huerfanos_cerrados(repo: Any) -> dict[str, dict[str, str]]:
    """``pago_id`` -> detalle de cierre (``motivo``, ``cerrado_por``,
    ``timestamp_cierre``) para pagos huérfanos marcados "a favor de la
    empresa" (ver ``POST /api/conciliaciones/cerrar-pago-huerfano``).

    Único lector compartido de ``PagosHuerfanosCerrados`` -- antes solo se
    usaba para excluir estos pagos de "pendientes" (un ``set`` de ids); esto
    además expone el detalle para mostrarlo en su propia bandeja.
    """
    return {
        str(r.get("pago_id", "")).strip(): r
        for r in repo._g.read_rows("PagosHuerfanosCerrados")
        if r.get("pago_id")
    }


@app.get("/api/conciliaciones/sugerencias")
async def get_conciliaciones_sugerencias(cxc_session: str | None = Cookie(default=None)):
    """Pagos pendientes por asociar, con orden sugerida (FIFO).

    Ya no es la fuente principal de la UI -- absorbida por
    ``GET /api/cobranza/pagos`` (``get_cobranza_pagos_unificado``), que
    llama esta función directamente. Se conserva como ruta pública además
    de función interna reusable: sigue siendo un endpoint válido y
    probado, con cobertura de tests que documentan bugs reales corregidos.
    """
    try:
        repo = get_repo()
        user = get_current_user_from_cookie(cxc_session)

        pagos_rows = repo._g.read_rows("Pagos")
        vincs = repo.all_vinculaciones()
        ordenes = repo.all_ordenes()
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {str(r.get("cliente_id", "")): r.get("nombre", "") for r in clientes_rows}
        tasas_rows = repo._g.read_rows("SerieTasas")
        tasas_historicas_rows = repo._g.read_rows("TasasHistoricasAuditoria")
        pagos_duplicados = _detectar_pagos_duplicados(pagos_rows)
        # Pagos huérfanos que un humano ya cerró "a favor de la empresa"
        # (ver POST /api/conciliaciones/cerrar-pago-huerfano) -- no deben
        # seguir apareciendo como pendientes.
        pagos_huerfanos_cerrados = set(leer_pagos_huerfanos_cerrados(repo).keys())

        # Live Odoo batch verification for reconciled payments and cancelled orders
        reconciled_pagos_set: set[str] = set()
        so_states_map = {}
        so_pagada_en_odoo: set[str] = set()
        entrega_valida_set: set[str] = set()
        odoo_pago_info: dict[str, dict[str, Any]] = {}
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            if execute:
                p_ids_str = [
                    str(p.get("pago_id", "")).strip() for p in pagos_rows if p.get("pago_id")
                ]
                reconciled_pagos_set = get_reconciled_pago_ids_odoo(execute, p_ids_str)

                # tax_today ("Tasa") y amount_ref ("Importe referencia") son
                # campos propios de este Odoo: la tasa BCV exacta que se
                # aplicó a ESE pago puntual y su equivalente USD ya
                # calculado por Odoo -- confirmado en vivo que coinciden con
                # lo que muestra la ficha del pago. Se prefieren sobre
                # cualquier tasa adivinada por cercanía de SerieTasas (regla
                # general: Odoo siempre prevalece). "name" es el número de
                # pago (ej. PBAMI/2026/00009) para ubicarlo en Odoo.
                p_ids_int = [int(pid) for pid in p_ids_str if pid.isdigit()]
                if p_ids_int:
                    pago_recs = execute(
                        "account.payment",
                        "search_read",
                        [[["id", "in", p_ids_int]]],
                        {"fields": ["id", "name", "tax_today", "amount_ref"]},
                    )
                    odoo_pago_info = {str(r["id"]): r for r in pago_recs}

                so_names = [o.so_id for o in ordenes]
                if so_names:
                    odoo_sos = execute(
                        "sale.order",
                        "search_read",
                        [[["name", "in", so_names]]],
                        {"fields": ["name", "state"]},
                    )
                    for os_item in odoo_sos:
                        sname = str(os_item.get("name", "")).strip()
                        if sname:
                            so_states_map[sname] = str(os_item.get("state", "")).strip().lower()

                    # SO "pagada" = el residual de TODAS sus out_invoice suma
                    # 0 (usando amount_residual_usd, el campo firmado en USD
                    # de Odoo -- NUNCA recalcular la conversión nosotros
                    # mismos con una tasa BCV propia, que puede diferir de la
                    # que Odoo aplicó a esa factura puntual). Una orden ya
                    # facturada sigue siendo un destino valido para sugerir
                    # un pago mientras su factura tenga saldo pendiente
                    # (residual > 0), sin importar el payment_state.
                    invoices = execute(
                        "account.move",
                        "search_read",
                        [
                            [
                                ["invoice_origin", "in", so_names],
                                ["state", "=", "posted"],
                                ["move_type", "=", "out_invoice"],
                            ]
                        ],
                        {"fields": ["invoice_origin", "amount_residual_usd"]},
                    )
                    residual_por_so: dict[str, Decimal] = {}
                    for inv in invoices:
                        so = str(inv.get("invoice_origin", "")).strip()
                        if so:
                            residual_por_so[so] = residual_por_so.get(
                                so, Decimal("0")
                            ) + parse_decimal_safe(str(inv.get("amount_residual_usd") or "0"))
                    for so, residual in residual_por_so.items():
                        if residual <= Decimal("0.05"):
                            so_pagada_en_odoo.add(so)

                    entrega_valida_set = get_live_delivered_not_returned(so_names, execute=execute)
        except Exception as e_odoo:
            logger.warning("Error consultando Odoo en get_conciliaciones_sugerencias: %s", e_odoo)

        linked_pago = {}
        linked_so = {}
        for v in vincs:
            linked_pago[v.pago_id] = linked_pago.get(v.pago_id, Decimal("0")) + v.monto_aplicado
            linked_so[v.so_id] = linked_so.get(v.so_id, Decimal("0")) + v.monto_aplicado

        unallocated_pagos = []
        for p in pagos_rows:
            pid = str(p.get("pago_id", "")).strip()
            if not pid or pid in reconciled_pagos_set or pid in pagos_huerfanos_cerrados:
                continue
            fecha_pago = str(p.get("fecha_pago") or p.get("fecha") or "")[:10]
            try:
                # Mediodía por defecto (no medianoche): la tasa varía por
                # hora y el pago normalmente se recibe en horario laboral --
                # mismo criterio que el resto del sistema (formulario de
                # vinculación manual). El usuario puede ajustarlo antes de
                # confirmar una vinculación puntual.
                fecha_dt = (
                    datetime.strptime(f"{fecha_pago} 12:00:00", "%Y-%m-%d %H:%M:%S")
                    if fecha_pago
                    else datetime.now()
                )
            except ValueError:
                fecha_dt = datetime.now()
            bcv_rate, binance_rate = get_rate_for_datetime(fecha_dt, tasas_rows)

            moneda = str(p.get("moneda", "USD") or "USD").upper().strip()
            monto_orig_raw = parse_decimal_safe(p.get("monto", "0"))

            # Odoo prevalece sobre cualquier tasa adivinada localmente: para un
            # pago en VES, tax_today es la tasa BCV EXACTA que Odoo aplicó a
            # ESE pago puntual (no la más cercana en el tiempo de SerieTasas),
            # y amount_ref es el equivalente USD que Odoo ya calculó con ella
            # -- confirmado en vivo que ambos coinciden con la ficha del pago
            # en Odoo. Binance no existe en Odoo: se busca el promedio del día
            # EXACTO en TasasHistoricasAuditoria (sin caer a otro día).
            odoo_info = odoo_pago_info.get(pid)
            numero_pago_odoo = odoo_info.get("name") if odoo_info else None
            monto_orig_usd_odoo: Decimal | None = None
            if moneda == "VES" and odoo_info:
                tax_today = parse_decimal_safe(str(odoo_info.get("tax_today") or "0"))
                if tax_today > Decimal("0"):
                    bcv_rate = tax_today
                    amount_ref = parse_decimal_safe(str(odoo_info.get("amount_ref") or "0"))
                    if amount_ref > Decimal("0"):
                        monto_orig_usd_odoo = amount_ref
            binance_del_dia = get_binance_rate_for_date(fecha_dt.date(), tasas_historicas_rows)
            # Guardia de plausibilidad: Binance y BCV son ambas tasas VES/USD del
            # mismo día, con una brecha de mercado normalmente < 100%. Si el dato
            # histórico sembrado está corrupto (ej. bug de locale que borra el punto
            # decimal), aparece muy fuera de ese rango -- se descarta y se conserva
            # el fallback de SerieTasas en vez de aplicar una tasa disparatada.
            if binance_del_dia is not None and bcv_rate > Decimal("0"):
                ratio = binance_del_dia / bcv_rate
                if Decimal("0.5") <= ratio <= Decimal("3"):
                    binance_rate = binance_del_dia

            # monto_vinculado (Vinculacion.monto_aplicado) siempre esta en USD
            # (la moneda de las ordenes) -- nunca restar directamente un saldo
            # en VES contra esto; hay que convertir el monto original primero.
            monto_orig_usd = (
                monto_orig_usd_odoo
                if monto_orig_usd_odoo is not None
                else pago_monto_usd(monto_orig_raw, moneda, bcv_rate)
            )
            monto_vinculado_usd = linked_pago.get(pid, Decimal("0"))
            saldo_usd = monto_orig_usd - monto_vinculado_usd

            if saldo_usd > Decimal("0.05"):
                vendedor = p.get("vendedor") or "Sin Vendedor"
                cliente_id = str(p.get("cliente_id", "")).strip()
                cliente_nombre = (
                    p.get("cliente_nombre")
                    or clientes_map.get(cliente_id)
                    or f"Cliente {cliente_id}"
                )

                unallocated_pagos.append(
                    {
                        "pago_id": pid,
                        "numero_pago_odoo": numero_pago_odoo,
                        "fecha_pago": fecha_pago,
                        "cliente_id": cliente_id,
                        "cliente_nombre": cliente_nombre,
                        "monto_original_raw": monto_orig_raw,
                        "monto_original_usd": monto_orig_usd,
                        "saldo_pendiente_usd": saldo_usd,
                        "moneda": moneda,
                        "tasa_bcv": bcv_rate,
                        "tasa_binance": binance_rate,
                        "vendedor": vendedor,
                        "posible_duplicado": pid in pagos_duplicados,
                        "duplicado_de": pagos_duplicados.get(pid, []),
                    }
                )

        saldos_reales = await _get_saldos_reales_por_so()

        open_orders_by_client = {}
        for o in ordenes:
            if orden_excluida(
                o,
                live_state=so_states_map.get(o.so_id),
                entrega_valida=o.so_id in entrega_valida_set,
            ):
                continue
            # Destino valido: NO pagada segun el motor (saldo local pendiente)
            # -- sin facturar o facturada pero con su factura Odoo aun sin
            # marcar como pagada/en proceso de pago.
            if o.facturada and o.so_id in so_pagada_en_odoo:
                continue

            if saldos_reales is not None:
                # Saldo real -- el mismo que muestra /api/reporte-saldos para
                # esta orden (resta pagos directos de Odoo, NCs y descuentos
                # del motor, no solo Vinculaciones locales). Una orden
                # ausente de saldos_reales ya está saldada/excluida según
                # ese reporte -- no se ofrece como destino.
                saldo_real = saldos_reales.get(o.so_id)
                if saldo_real is None or saldo_real <= 0.05:
                    continue
                saldo = Decimal(str(saldo_real))
            else:
                # get_reporte_saldos falló -- cae al cálculo naive anterior
                # en vez de vaciar la bandeja de sugerencias por completo.
                pagado = linked_so.get(o.so_id, Decimal("0"))
                saldo = o.monto_total - pagado
                if saldo <= Decimal("0.05"):
                    continue

            if o.cliente_id not in open_orders_by_client:
                open_orders_by_client[o.cliente_id] = []
            open_orders_by_client[o.cliente_id].append(
                {
                    "so_id": o.so_id,
                    "fecha": o.fecha,
                    "monto_total": o.monto_total,
                    "saldo_pendiente": saldo,
                    "vendedor": o.vendedor_email,
                }
            )

        for cid in open_orders_by_client:
            open_orders_by_client[cid].sort(key=lambda x: x["fecha"])

        def visible_to_user(vendedor: str) -> bool:
            if not (user and user["rol"] == "ventas"):
                return True
            u_name = (user["nombre"] or user["email"]).strip().lower()
            return (
                vendedor.strip().lower() == u_name
                or user["email"].strip().lower() in vendedor.lower()
            )

        sugerencias = []
        for p in unallocated_pagos:
            cid = p["cliente_id"]
            client_orders = open_orders_by_client.get(cid, [])
            bcv_rate, binance_rate, moneda_p = p["tasa_bcv"], p["tasa_binance"], p["moneda"]

            def saldo_fields(
                restante_usd_bcv: Decimal,
                moneda_r: str = moneda_p,
                bcv_r: Decimal = bcv_rate,
                binance_r: Decimal = binance_rate,
            ) -> dict:
                # Ambas referencias del MISMO residual -- el pago en VES no
                # tiene una tasa "oficial" unica para el usuario, y la que
                # aplique al vincular puede ajustarse (hora) antes de
                # confirmar; mostrar las dos evita que una parezca faltante.
                # Argumentos default (no closure) para no atar esta función
                # a la variable de loop mutable de la siguiente iteración.
                return {
                    "saldo_pago": float(restante_usd_bcv),
                    "saldo_pago_binance": float(
                        usd_bcv_to_binance(restante_usd_bcv, moneda_r, bcv_r, binance_r)
                    ),
                    "saldo_pago_original": float(
                        restante_usd_bcv * bcv_r if moneda_r == "VES" else restante_usd_bcv
                    ),
                }

            base_item = {
                "pago_id": p["pago_id"],
                "numero_pago_odoo": p["numero_pago_odoo"],
                "pago_fecha": p["fecha_pago"],
                "cliente_id": cid,
                "cliente_nombre": p["cliente_nombre"],
                "monto_pago": float(p["monto_original_usd"]),
                "monto_pago_binance": float(
                    usd_bcv_to_binance(p["monto_original_usd"], moneda_p, bcv_rate, binance_rate)
                ),
                "monto_pago_original": float(p["monto_original_raw"]),
                "moneda_pago": p["moneda"],
                "tasa_bcv": float(bcv_rate),
                "tasa_binance": float(binance_rate),
                "posible_duplicado": p["posible_duplicado"],
                "duplicado_de": p["duplicado_de"],
            }

            monto_pago_restante = p["saldo_pendiente_usd"]
            for o in client_orders:
                if monto_pago_restante <= Decimal("0.05"):
                    break
                if o["saldo_pendiente"] <= Decimal("0.05"):
                    continue

                monto_aplicar = min(monto_pago_restante, o["saldo_pendiente"])

                sug_id = f"SUG_{p['pago_id']}_{o['so_id']}"
                item = {
                    **base_item,
                    # Residual del pago justo ANTES de aplicar esta sugerencia
                    # -- si un mismo pago cubre varias órdenes, cada fila
                    # muestra cuanto le quedaba disponible en ese momento, no
                    # el total original constante.
                    **saldo_fields(monto_pago_restante),
                    "sugerencia_id": sug_id,
                    "so_id": o["so_id"],
                    "so_fecha": o["fecha"].isoformat()
                    if hasattr(o["fecha"], "isoformat")
                    else str(o["fecha"]),
                    "so_monto_total": float(o["monto_total"]),
                    "so_saldo_pendiente": float(o["saldo_pendiente"]),
                    "monto_sugerido": float(monto_aplicar),
                    "vendedor": p["vendedor"] or o["vendedor"],
                }

                if not visible_to_user(item["vendedor"]):
                    continue

                sugerencias.append(item)
                monto_pago_restante -= monto_aplicar
                o["saldo_pendiente"] -= monto_aplicar

            # Pago sin (mas) ordenes abiertas del mismo cliente que cubrir --
            # se sigue mostrando (sin sugerencia, con el residual que
            # realmente le queda) para poder vincularse manualmente, en vez
            # de desaparecer silenciosamente de la vista.
            if monto_pago_restante > Decimal("0.05") and visible_to_user(p["vendedor"]):
                sugerencias.append(
                    {
                        **base_item,
                        **saldo_fields(monto_pago_restante),
                        "sugerencia_id": f"SUG_{p['pago_id']}_SIN_ORDEN",
                        "so_id": None,
                        "so_fecha": None,
                        "so_monto_total": None,
                        "so_saldo_pendiente": None,
                        "monto_sugerido": 0.0,
                        "vendedor": p["vendedor"],
                    }
                )

        # Pago más antiguo primero -- cola de trabajo: los pendientes más
        # atrasados se resuelven antes. sort() es estable, así que las
        # filas de "reparto" de un mismo pago (misma pago_fecha) mantienen
        # su orden FIFO relativo entre sí.
        sugerencias.sort(key=lambda item: item["pago_fecha"] or "")

        return sugerencias
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


class CerrarPagoHuerfanoRequest(BaseModel):
    pago_id: str
    motivo: str = "Sin orden abierta del cliente -- cerrado a favor de la empresa"


@app.post("/api/conciliaciones/cerrar-pago-huerfano")
async def post_cerrar_pago_huerfano(
    req: CerrarPagoHuerfanoRequest, cxc_session: str | None = Cookie(default=None)
):
    """Marca localmente un pago huérfano (sin orden abierta del cliente que

    cubrir) como resuelto/a favor de la empresa -- deja de aparecer en
    "Pagos Pendientes por Asociar". Solo marca local: NO crea ningún
    asiento contable ni ajuste en Odoo; eso lo hace un humano por fuera si
    corresponde. Reversible: quitar la fila de la pestaña
    PagosHuerfanosCerrados hace que el pago vuelva a aparecer.
    """
    try:
        repo = get_repo()
        user = get_current_user_from_cookie(cxc_session)
        cerrado_por = (user["nombre"] or user["email"]) if user else "Desconocido"
        repo._g.upsert_row(
            "PagosHuerfanosCerrados",
            "pago_id",
            {
                "pago_id": req.pago_id,
                "motivo": req.motivo,
                "cerrado_por": cerrado_por,
                "timestamp_cierre": datetime.now().isoformat(),
            },
        )
        return {
            "status": "success",
            "message": f"Pago {req.pago_id} cerrado a favor de la empresa.",
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/bandeja")
async def get_bandeja_facturacion():
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        bandeja_rows = repo.all_bandeja()
        bandeja_map = {b.so_id: b for b in bandeja_rows}
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {str(r.get("cliente_id", "")): r for r in clientes_rows}
        vincs = repo.all_vinculaciones()

        # Batch query live Odoo state
        so_odoo_states = {}
        entrega_valida_set: set[str] = set()
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            if execute:
                so_names = [o.so_id for o in ordenes]
                if so_names:
                    so_recs = execute(
                        "sale.order",
                        "search_read",
                        [[["name", "in", so_names]]],
                        {"fields": ["name", "state"]},
                    )
                    for s in so_recs:
                        sname = str(s.get("name", "")).strip()
                        if sname:
                            so_odoo_states[sname] = str(s.get("state", "")).strip().lower()
                    entrega_valida_set = get_live_delivered_not_returned(so_names, execute=execute)
        except Exception as e_odoo:
            logger.warning("Error consultando Odoo en get_bandeja: %s", e_odoo)

        pagos_by_so = {}
        for v in vincs:
            eq = (
                v.equiv_usd_binance
                if v.equiv_usd_binance is not None
                else (v.equiv_usd_bcv if v.equiv_usd_bcv is not None else v.monto_aplicado)
            )
            pagos_by_so[v.so_id] = pagos_by_so.get(v.so_id, Decimal("0")) + eq

        ordenes_por_facturar = []
        notas_credito_pendientes = []
        iva_pendiente_agentes = []

        for o in ordenes:
            if orden_excluida(
                o,
                live_state=so_odoo_states.get(o.so_id),
                entrega_valida=o.so_id in entrega_valida_set,
            ):
                continue

            c_info = clientes_map.get(str(o.cliente_id), {})
            c_name = c_info.get("nombre") or f"Cliente {o.cliente_id}"
            wh_agent = bool(c_info.get("wh_iva_agent")) or (
                str(c_info.get("agente_retencion", "")).upper() == "TRUE"
            )
            wh_rate = (
                float(parse_decimal_safe(str(c_info.get("wh_iva_rate", "75"))))
                if c_info.get("wh_iva_rate")
                else 75.0
            )

            b = bandeja_map.get(o.so_id)
            abono = float(pagos_by_so.get(o.so_id, Decimal("0")))
            monto_orig = float(o.monto_total)

            tot_motor = float(b.total_motor) if b else monto_orig
            desc_monto = float(b.total_descuentos + b.ncs_calculadas) if b else 0.0
            desc_pct = (
                (desc_monto / monto_orig * 100.0) if (monto_orig > 0 and desc_monto > 0) else 0.0
            )

            # Bandeja 1: ordenes SIN factura listas para facturar.
            # - Cliente NO agente de retencion: debe estar pagada al 100%
            #   segun el motor (reglas estandar).
            # - Cliente SI agente de retencion de IVA: le basta con haber
            #   pagado el Subtotal (monto sin IVA); lo que falta es
            #   exactamente la porcion de IVA que retiene (no paga esa
            #   porcion en efectivo, entrega comprobante de retencion mas
            #   adelante -- ver Bandeja 3, que aplica una vez facturada).
            # IVA Venezuela = 16%; se estima el subtotal despejando el total
            # que calculo el motor (tot_motor, ya con descuentos aplicados).
            subtotal_neto_motor = tot_motor / 1.16
            iva_estimado_motor = tot_motor - subtotal_neto_motor
            saldo_motor = tot_motor - abono
            if wh_agent:
                listo_para_facturar = saldo_motor <= iva_estimado_motor + 0.05
            else:
                listo_para_facturar = saldo_motor <= 0.05

            if not o.facturada and listo_para_facturar:
                ordenes_por_facturar.append(
                    {
                        "so_id": o.so_id,
                        "cliente_nombre": c_name,
                        "wh_iva_agent": wh_agent,
                        "wh_iva_rate": wh_rate,
                        "fecha": o.fecha.isoformat()
                        if hasattr(o.fecha, "isoformat")
                        else str(o.fecha),
                        "monto_pagado": abono,
                        "subtotal_neto": round(subtotal_neto_motor, 2),
                        "iva_estimado": round(iva_estimado_motor, 2),
                        "total_motor": tot_motor,
                        "saldo_pendiente": round(saldo_motor, 2),
                        "descuento_aplicar_monto": desc_monto,
                        "descuento_aplicar_pct": desc_pct,
                        "precio_base": monto_orig,
                    }
                )
            elif o.facturada:
                # Nota: el bloque de arriba (not facturada) ya se sale con su
                # propio "if"; este "elif o.facturada" evita que ordenes SIN
                # factura pero con saldo pendiente (> $0.05) caigan aqui por
                # error -- NC pendiente y retencion de IVA solo aplican a
                # ordenes ya facturadas en Odoo.
                nc_calc = float(b.ncs_calculadas) if b else 0.0
                if nc_calc > 0.01:
                    # El concepto real (obsequio de producto vs. % de
                    # descuento de primera compra) vive en el detalle que ya
                    # calculó el motor -- se usa en vez de un texto generico.
                    detalles_b = b.descuentos_detalle if b else []
                    detalle_nc = next(
                        (d for d in detalles_b if d.origen == "primera_compra"), None
                    )
                    concepto = (
                        detalle_nc.descripcion
                        if detalle_nc
                        else "Obsequio / Descuento de primera compra no aplicado en factura"
                    )
                    notas_credito_pendientes.append(
                        {
                            "so_id": o.so_id,
                            "cliente_nombre": c_name,
                            "factura_id": o.factura_id or "Odoo",
                            "monto_pagado": abono,
                            "nc_monto": nc_calc,
                            "nc_porcentaje": (nc_calc / monto_orig * 100.0)
                            if monto_orig > 0
                            else 0.0,
                            "concepto": concepto,
                        }
                    )

                # Tarea 5: retencion de IVA. El cliente-agente de retencion no
                # paga en efectivo la porcion de IVA que retiene (wh_rate% del
                # IVA); en su lugar entrega un comprobante de retencion. El
                # saldo que el motor ve pendiente (tot_motor - abono, ya con
                # descuentos aplicados) es "normal" si cabe dentro de esa
                # porcion retenida -- no es que el cliente deba mas, es que
                # falta el comprobante. IVA Venezuela = 16%; se estima el
                # subtotal despejando el total del MOTOR (tot_motor, ya con
                # descuentos aplicados) -- no el monto bruto original: si la
                # orden tuvo descuentos ya reflejados en la factura real, el
                # 16% debe calcularse sobre lo efectivamente facturado, no
                # sobre el precio de lista previo al descuento.
                if wh_agent:
                    subtotal_est = tot_motor / 1.16
                    iva_total_est = tot_motor - subtotal_est
                    iva_retenido_est = iva_total_est * (wh_rate / 100.0)
                    saldo_pendiente_motor = tot_motor - abono
                    if 0 <= saldo_pendiente_motor <= iva_retenido_est + 0.05:
                        iva_pendiente_agentes.append(
                            {
                                "so_id": o.so_id,
                                "cliente_nombre": c_name,
                                "factura_id": o.factura_id or "Odoo",
                                "monto_factura": tot_motor,
                                "wh_iva_rate": wh_rate,
                                "base_cobrada": round(subtotal_est, 2),
                                "iva_total_estimado": round(iva_total_est, 2),
                                "retencion_iva_est": round(iva_retenido_est, 2),
                                "monto_iva_retenido_est": round(iva_retenido_est, 2),
                                "monto_pagado": abono,
                                "saldo_pendiente": round(saldo_pendiente_motor, 2),
                                "estado_comprobante": "Pendiente Comprobante IVA",
                                "estado": "Pendiente Comprobante IVA",
                            }
                        )

        return {
            "ordenes_por_facturar": ordenes_por_facturar,
            "notas_credito_pendientes": notas_credito_pendientes,
            "iva_pendiente_agentes": iva_pendiente_agentes,
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/vincular-masivo")
async def post_vincular_masivo(req: VincularMasivoRequest, background_tasks: BackgroundTasks):
    try:
        repo = get_repo()
        last_tasa = repo.last_serie_tasa()
        tasa_bcv = last_tasa.tasa_bcv if last_tasa else Decimal("36.5")
        tasa_binance = last_tasa.tasa_binance if last_tasa else Decimal("38.0")

        processed = 0
        so_ids_affected = set()

        for item in req.items:
            pago = repo.get_pago(item.pago_id)
            if not pago:
                continue

            monto_dec = Decimal(str(item.monto_aplicado))
            if monto_dec <= Decimal("0"):
                continue

            if pago.moneda == "USD":
                equiv_usd_bcv = monto_dec
                equiv_usd_binance = monto_dec
                equiv_ves_bcv = monto_dec * tasa_bcv
                equiv_ves_binance = monto_dec * tasa_binance
            else:
                equiv_usd_bcv = monto_dec / tasa_bcv
                equiv_usd_binance = monto_dec / tasa_binance
                equiv_ves_bcv = monto_dec
                equiv_ves_binance = monto_dec

            vinc_id = f"VINC_{item.pago_id}_{item.so_id}"
            vinc = Vinculacion(
                vinc_id=vinc_id,
                pago_id=item.pago_id,
                so_id=item.so_id,
                monto_aplicado=monto_dec,
                hora_pago_confirmada=datetime.combine(pago.fecha_pago, datetime.min.time()),
                tasa_bcv_aplicada=tasa_bcv,
                tasa_binance_aplicada=tasa_binance,
                es_tasa_heredada=False,
                equiv_usd_bcv=equiv_usd_bcv,
                equiv_usd_binance=equiv_usd_binance,
                equiv_ves_bcv=equiv_ves_bcv,
                equiv_ves_binance=equiv_ves_binance,
                confirmado_por="Aprobador Masivo FIFO",
                timestamp_registro=datetime.now(),
                estado=EstadoVinculacion.PENDIENTE,
                moneda_abono=Moneda(pago.moneda),
                tipo_tasa_abono=TipoTasa.BCV,
            )

            repo.update_vinculacion(vinc)
            processed += 1
            so_ids_affected.add(item.so_id)

        for so_id in so_ids_affected:
            background_tasks.add_task(recalculate_all, so_id)

        return {
            "status": "success",
            "message": f"Se procesaron {processed} vinculaciones exitosamente.",
            "procesados": processed,
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


def _get_vinculacion_or_404(repo: Any, vinc_id: str) -> Vinculacion:
    vinc = next((v for v in repo.all_vinculaciones() if v.vinc_id == vinc_id), None)
    if not vinc:
        raise HTTPException(status_code=404, detail=f"Vinculación {vinc_id} no encontrada.")
    return vinc


@app.post("/api/vinculacion/{vinc_id}/tasa-binance")
async def post_editar_tasa_binance(
    vinc_id: str, req: TasaBinanceEditRequest, background_tasks: BackgroundTasks
):
    try:
        repo = get_repo()
        vinc = _get_vinculacion_or_404(repo, vinc_id)
        nueva_tasa = Decimal(str(req.tasa_binance))
        if nueva_tasa <= Decimal("0"):
            raise HTTPException(status_code=400, detail="La tasa Binance debe ser positiva.")

        # Validación estricta: no puede superar el máximo ni caer bajo el
        # mínimo capturado ese día en SerieTasas.
        fecha = vinc.hora_pago_confirmada.date()
        dia_rows = repo.serie_tasas_del_dia(fecha)
        binance_vals = [r.tasa_binance for r in dia_rows if r.tasa_binance and r.tasa_binance > 0]
        if binance_vals:
            minimo, maximo = min(binance_vals), max(binance_vals)
            if nueva_tasa < minimo or nueva_tasa > maximo:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"La tasa Binance ({nueva_tasa}) debe estar entre {minimo} y "
                        f"{maximo} -- rango capturado el {fecha.isoformat()}."
                    ),
                )

        vinc.tasa_binance_aplicada = nueva_tasa
        if vinc.moneda_abono == Moneda.USD:
            vinc.equiv_usd_binance = vinc.monto_aplicado
            vinc.equiv_ves_binance = vinc.monto_aplicado * nueva_tasa
        else:
            vinc.equiv_usd_binance = vinc.monto_aplicado / nueva_tasa
            vinc.equiv_ves_binance = vinc.monto_aplicado

        repo.update_vinculacion(vinc)
        background_tasks.add_task(recalculate_all, vinc.so_id)

        return {
            "status": "success",
            "vinc_id": vinc_id,
            "tasa_binance_aplicada": float(nueva_tasa),
            "equiv_usd_binance": float(vinc.equiv_usd_binance),
            "equiv_ves_binance": float(vinc.equiv_ves_binance),
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/vinculacion/{vinc_id}/tasa-bcv-tipo")
async def post_cambiar_tipo_tasa_bcv(
    vinc_id: str, req: TasaBcvVarianteRequest, background_tasks: BackgroundTasks
):
    try:
        variante = req.variante.strip().upper()
        if variante not in ("USD", "EUR"):
            raise HTTPException(
                status_code=400, detail="La variante de tasa BCV debe ser 'USD' o 'EUR'."
            )

        repo = get_repo()
        vinc = _get_vinculacion_or_404(repo, vinc_id)
        tasas_rows = repo._g.read_rows("SerieTasas")

        if variante == "EUR":
            tasa_bcv_nueva = get_bcv_euro_rate_for_datetime(vinc.hora_pago_confirmada, tasas_rows)
            if tasa_bcv_nueva is None:
                raise HTTPException(
                    status_code=400,
                    detail="No hay tasa BCV-EUR capturada en SerieTasas para usar esta variante.",
                )
        else:
            tasa_bcv_nueva, _ = get_rate_for_datetime(vinc.hora_pago_confirmada, tasas_rows)

        if tasa_bcv_nueva <= Decimal("0"):
            raise HTTPException(
                status_code=400, detail=f"Tasa BCV-{variante} inválida (<= 0)."
            )

        vinc.bcv_variante = variante
        vinc.tasa_bcv_aplicada = tasa_bcv_nueva
        if vinc.moneda_abono == Moneda.USD:
            vinc.equiv_usd_bcv = vinc.monto_aplicado
            vinc.equiv_ves_bcv = vinc.monto_aplicado * tasa_bcv_nueva
        else:
            vinc.equiv_usd_bcv = vinc.monto_aplicado / tasa_bcv_nueva
            vinc.equiv_ves_bcv = vinc.monto_aplicado

        repo.update_vinculacion(vinc)
        background_tasks.add_task(recalculate_all, vinc.so_id)

        return {
            "status": "success",
            "vinc_id": vinc_id,
            "bcv_variante": variante,
            "tasa_bcv_aplicada": float(tasa_bcv_nueva),
            "equiv_usd_bcv": float(vinc.equiv_usd_bcv),
            "equiv_ves_bcv": float(vinc.equiv_ves_bcv),
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/odoo/marcas")
async def get_odoo_marcas():
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        brands = execute("product.brand", "search_read", [[]], {"fields": ["name"]})
        return [b["name"] for b in brands]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/odoo/categorias")
async def get_odoo_categorias():
    return ["Comercial", "Industrial"]


@app.get("/api/config/tasa-referencia")
async def get_tasa_referencia(fecha: str, hora: str):
    try:
        dt_str = f"{fecha} {hora}:00"
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        bcv, binance = get_rate_for_datetime(dt)
        return {"tasa_bcv": float(bcv), "tasa_binance": float(binance)}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/config/tasas/sync-odoo")
async def post_sync_odoo_rates():
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)

        # Fetch rates since 2026-01-01
        rates = execute(
            "res.currency.rate",
            "search_read",
            [[["name", ">=", "2026-01-01"], ["currency_id", "=", 1]]],
            {"fields": ["name", "inverse_company_rate"]},
        )

        repo = get_repo()
        existing_rows = repo._g.read_rows("SerieTasas")
        existing_dates = set()
        for r in existing_rows:
            ts = r.get("timestamp", "")
            if ts:
                existing_dates.add(ts.split(" ")[0].split("T")[0])

        from cxc.models import SerieTasa
        from cxc.sheets import gateway as g
        from cxc.sheets import serde

        rates.sort(key=lambda x: x["name"])
        added_count = 0
        for rate in rates:
            date_str = rate["name"]
            if date_str not in existing_dates:
                ts = datetime.combine(
                    date.fromisoformat(date_str), datetime.min.time().replace(hour=8)
                )
                val = rate.get("inverse_company_rate")
                bcv = Decimal(str(val)) if val else Decimal("1.0")
                binance = bcv * Decimal("1.05")

                tasa = SerieTasa(
                    timestamp=ts,
                    tasa_bcv=bcv,
                    tasa_binance=binance,
                    fuente="Odoo Sync",
                    es_heredada=False,
                    capturada_ok=True,
                )
                repo.append_serie_tasa(tasa)
                added_count += 1

        # Automated Holidays Detection Heuristics
        # Days where BCV didn't publish rates (except weekends and Mondays to avoid bank holidays)
        from datetime import timedelta

        start_date = date(2026, 1, 1)
        end_date = date.today() - timedelta(days=1)

        odoo_rate_dates = {r["name"] for r in rates}
        existing_feriados = {f.fecha for f in repo.feriados()}
        detected_feriados_count = 0

        current = start_date
        while current <= end_date:
            wday = current.weekday()
            if wday not in (0, 5, 6):  # Tuesday to Friday
                date_str = current.isoformat()
                if date_str not in odoo_rate_dates and current not in existing_feriados:
                    from cxc.models import Feriado, TipoFeriado

                    feriado = Feriado(
                        fecha=current,
                        descripcion="Feriado detectado por BCV (sin tasa)",
                        tipo=TipoFeriado.NACIONAL,
                    )
                    repo._g.append_row(g.T_FERIADOS, serde.feriado_to_row(feriado))
                    detected_feriados_count += 1
            current += timedelta(days=1)

        msg = f"Sincronizados {added_count} registros de tasas desde Odoo."
        if detected_feriados_count > 0:
            msg += f" Detectados {detected_feriados_count} nuevos feriados automáticos."

        return {"status": "success", "message": msg}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/odoo/productos")
async def get_odoo_productos():
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)

        # Fetch active products with product_volume (litros)
        prods = execute(
            "product.template",
            "search_read",
            [[["sale_ok", "=", True], ["active", "=", True]]],
            {
                "fields": [
                    "id",
                    "name",
                    "default_code",
                    "list_price",
                    "list_price_usd",
                    "product_volume",
                ],
                "limit": 100,
            },
        )

        usd_lists, ves_lists = get_valid_pricelists_usd_and_ves()
        cand_usd_ids = [int(x) for x in usd_lists if str(x).isdigit()]
        cand_ves_ids = [int(x) for x in ves_lists if str(x).isdigit()]
        all_cand_ids = cand_usd_ids + cand_ves_ids

        # Query rules directly from product.pricelist.item to get exact raw pricing entered
        rules = (
            execute(
                "product.pricelist.item",
                "search_read",
                [[["pricelist_id", "in", all_cand_ids], ["compute_price", "=", "fixed"]]],
                {"fields": ["pricelist_id", "product_tmpl_id", "fixed_price"]},
            )
            if all_cand_ids
            else []
        )

        prices_usd = {}
        prices_ves = {}
        for r in rules:
            pl_id = r.get("pricelist_id")
            p_id = pl_id[0] if isinstance(pl_id, list) else pl_id
            prod_tmpl_id = r.get("product_tmpl_id")
            pt_id = prod_tmpl_id[0] if isinstance(prod_tmpl_id, list) else prod_tmpl_id

            if pt_id:
                if p_id in cand_usd_ids:
                    prices_usd[pt_id] = float(r.get("fixed_price") or 0.0)
                elif p_id in cand_ves_ids:
                    prices_ves[pt_id] = float(r.get("fixed_price") or 0.0)

        resultado = []
        for p in prods:
            pid = p["id"]
            resultado.append(
                {
                    "id": pid,
                    "nombre": p["name"],
                    "ref_interna": p.get("default_code") or "N/A",
                    "precio_publico": float(p.get("list_price") or 0.0),
                    # Sin regla en la pricelist -- fallback a "Precio de venta $"
                    # (list_price_usd), NUNCA list_price (esa está en VES).
                    "precio_usd": prices_usd.get(pid, float(p.get("list_price_usd") or 0.0)),
                    "precio_ves_usd": prices_ves.get(pid, 0.0),
                    "litros": float(p.get("product_volume") or 0.0),
                }
            )
        return resultado
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/odoo/clientes-auditoria")
async def get_odoo_clientes_auditoria():
    try:
        current_year_month = datetime.now().strftime("%Y-%m")
        execute = None
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
        except Exception as e_conn:
            logger.warning(
                "No se pudo conectar a Odoo para clientes-auditoria, usando fallback Sheets: %s",
                e_conn,
            )

        stats = {}
        partners_data = []

        if execute:
            partners = execute(
                "res.partner",
                "search_read",
                [[["customer_rank", ">", 0]]],
                {"fields": ["id", "name", "create_date"]},
            )
            orders = execute(
                "sale.order",
                "search_read",
                [[["state", "in", ["sale", "done"]]]],
                {"fields": ["id", "name", "partner_id", "date_order"]},
            )
            so_ids = [o["id"] for o in orders]
            lines = []
            if so_ids:
                with contextlib.suppress(Exception):
                    lines = execute(
                        "sale.order.line",
                        "search_read",
                        [[["order_id", "in", so_ids]]],
                        {"fields": ["order_id", "product_uom_qty", "qty_delivered", "product_id"]},
                    )

            product_ids = list(
                {ln["product_id"][0] for ln in lines if isinstance(ln.get("product_id"), list)}
            )
            product_map = {}
            if product_ids:
                try:
                    prods = execute(
                        "product.product",
                        "search_read",
                        [[["id", "in", product_ids]]],
                        {"fields": ["id", "volume", "weight", "brand_id"]},
                    )
                    for p in prods:
                        b_info = p.get("brand_id")
                        b_name = b_info[1] if isinstance(b_info, list) else ""
                        vol = parse_decimal_safe(p.get("volume") or "0")
                        if vol == Decimal("0"):
                            vol = parse_decimal_safe(p.get("weight") or "1.0")
                        product_map[p["id"]] = {"brand": b_name, "volume": vol}
                except Exception:
                    pass

            so_partner_map = {}
            for o in orders:
                pid_info = o.get("partner_id")
                if isinstance(pid_info, list) and len(pid_info) > 0:
                    pid = str(pid_info[0])
                    so_partner_map[o["id"]] = pid
                    date_str = str(o.get("date_order") or "")

                    s = stats.setdefault(
                        pid,
                        {
                            "count": 0,
                            "count_mes": 0,
                            "litros_global": Decimal("0"),
                            "litros_sinoco": Decimal("0"),
                            "last_date": "",
                        },
                    )
                    s["count"] += 1
                    if date_str and date_str.startswith(current_year_month):
                        s["count_mes"] += 1
                    if date_str and (not s["last_date"] or date_str > s["last_date"]):
                        s["last_date"] = date_str

            for ln in lines:
                so_id = ln["order_id"][0] if isinstance(ln.get("order_id"), list) else None
                pid = so_partner_map.get(so_id)
                if pid and pid in stats:
                    p_info = ln.get("product_id")
                    p_id = p_info[0] if isinstance(p_info, list) else None
                    if p_id in product_map:
                        brand = product_map[p_id]["brand"]
                        vol = product_map[p_id]["volume"]
                        qty = parse_decimal_safe(
                            ln.get("qty_delivered") or ln.get("product_uom_qty") or "0"
                        )
                        total_l = qty * vol
                        if "GLOBAL" in brand.upper():
                            stats[pid]["litros_global"] += total_l
                        elif "SINOCO" in brand.upper():
                            stats[pid]["litros_sinoco"] += total_l

            for p in partners:
                partners_data.append(
                    {
                        "id": str(p["id"]),
                        "name": p["name"],
                        "create_date": str(p.get("create_date") or "").split(" ")[0] or "N/A",
                    }
                )

        else:
            repo = get_repo()
            clientes_rows = repo._g.read_rows("Clientes")
            ordenes = repo.all_ordenes()
            lineas = repo._g.read_rows("LineasOrden")

            for c in clientes_rows:
                partners_data.append(
                    {
                        "id": c.get("cliente_id", ""),
                        "name": c.get("nombre", ""),
                        "create_date": "N/A",
                    }
                )

            so_partner_map = {}
            for o in ordenes:
                if orden_excluida(o):
                    continue
                pid = str(o.cliente_id)
                so_partner_map[o.so_id] = pid
                date_str = o.fecha.isoformat()

                s = stats.setdefault(
                    pid,
                    {
                        "count": 0,
                        "count_mes": 0,
                        "litros_global": Decimal("0"),
                        "litros_sinoco": Decimal("0"),
                        "last_date": "",
                    },
                )
                s["count"] += 1
                if date_str.startswith(current_year_month):
                    s["count_mes"] += 1
                if not s["last_date"] or date_str > s["last_date"]:
                    s["last_date"] = date_str

            for ln in lineas:
                so_id = ln.get("so_id")
                pid = so_partner_map.get(so_id)
                if pid and pid in stats:
                    brand = str(ln.get("marca", "")).upper()
                    qty = parse_decimal_safe(
                        ln.get("cantidad_entregada") or ln.get("cantidad") or "0"
                    )
                    if "GLOBAL" in brand:
                        stats[pid]["litros_global"] += qty
                    elif "SINOCO" in brand:
                        stats[pid]["litros_sinoco"] += qty

        resultado = []
        for p in partners_data:
            pid = p["id"]
            p_stats = stats.get(
                pid,
                {
                    "count": 0,
                    "count_mes": 0,
                    "litros_global": Decimal("0"),
                    "litros_sinoco": Decimal("0"),
                    "last_date": "N/A",
                },
            )
            resultado.append(
                {
                    "id": pid,
                    "nombre": p["name"],
                    "fecha_creacion": p["create_date"],
                    "ventas_cantidad": p_stats["count"],
                    "ventas_mes_actual": p_stats["count_mes"],
                    "litros_global": float(p_stats["litros_global"]),
                    "litros_sinoco": float(p_stats["litros_sinoco"]),
                    "fecha_ultima_venta": p_stats["last_date"].split(" ")[0]
                    if p_stats["last_date"] != "N/A"
                    else "N/A",
                }
            )
        return resultado
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/config/promociones")
async def get_config_promociones():
    try:
        repo = get_repo()
        promos = repo.promociones_primera_compra()
        return [
            {
                "regla_id": p.regla_id,
                "tipo_beneficio": p.tipo_beneficio,
                "productos": p.productos,
                "valor": str(p.valor),
                "compra_minima": str(p.compra_minima),
                "descuento_fallback": str(getattr(p, "descuento_fallback", "0")),
                "regalo_tipo": p.regalo_tipo,
                "categorias_aplica": getattr(p, "categorias_aplica", "Comercial"),
                "solo_primera_compra": getattr(p, "solo_primera_compra", False),
                "vigencia_desde": p.vigencia_desde.isoformat(),
                "vigencia_hasta": p.vigencia_hasta.isoformat() if p.vigencia_hasta else None,
                "activo": p.activo,
            }
            for p in promos
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/promociones")
async def post_config_promociones(req: PromocionRequest):
    try:
        repo = get_repo()
        import uuid

        from cxc.models import PromocionPrimeraCompra
        from cxc.sheets import gateway as g
        from cxc.sheets import serde

        v_desde = date.fromisoformat(req.vigencia_desde)
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        # Check date overlap with active first purchase promos
        existing = repo.promociones_primera_compra()
        for r in existing:
            if r.activo and r.solo_primera_compra == req.solo_primera_compra:
                h1 = v_hasta if v_hasta is not None else date(9999, 12, 31)
                h2 = r.vigencia_hasta if r.vigencia_hasta is not None else date(9999, 12, 31)
                if max(v_desde, r.vigencia_desde) <= min(h1, h2):
                    r_hasta = r.vigencia_hasta or "siempre"
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Conflicto: ya existe la promoción activa {r.regla_id} "
                            f"({r.vigencia_desde} a {r_hasta})."
                        ),
                    )

        regla_id = f"PROMO_{uuid.uuid4().hex[:8].upper()}"

        promo = PromocionPrimeraCompra(
            regla_id=regla_id,
            tipo_beneficio=req.tipo_beneficio,
            productos=req.productos,
            valor=Decimal(str(req.valor)),
            compra_minima=Decimal(str(req.compra_minima)),
            regalo_tipo=req.regalo_tipo,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            descuento_fallback=Decimal(str(req.descuento_fallback)),
            categorias_aplica=req.categorias_aplica,
            solo_primera_compra=req.solo_primera_compra,
            activo=req.activo,
        )
        row = serde.promocion_to_row(promo)
        repo._g.append_row(g.T_PROMO_PRIMERA, row)
        if hasattr(repo._g, "_read_cache"):
            repo._g._read_cache.pop(g.T_PROMO_PRIMERA, None)
        return {"status": "success", "message": "Promoción registrada correctamente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/config/exclusiones")
async def get_config_exclusiones():
    try:
        repo = get_repo()
        excls = repo.exclusiones()
        return [
            {"regla_tipo_a": e.regla_tipo_a, "regla_tipo_b": e.regla_tipo_b, "activo": e.activo}
            for e in excls
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/exclusiones")
async def post_config_exclusiones(req: ExclusionRequest):
    try:
        repo = get_repo()
        from cxc.models import ExclusionRegla

        rule = ExclusionRegla(
            regla_tipo_a=req.regla_tipo_a, regla_tipo_b=req.regla_tipo_b, activo=req.activo
        )
        repo.save_exclusion(rule)
        return {"status": "success", "message": "Exclusión registrada correctamente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/config/descuentos-volumen")
async def get_config_descuentos_volumen():
    try:
        repo = get_repo()
        rules = repo.descuentos_volumen()
        return [
            {
                "regla_id": r.regla_id,
                "marca": r.marca,
                "categoria": r.categoria,
                "litros_minimo": float(r.litros_minimo),
                "porcentaje": float(r.porcentaje),
                "tipo_evaluacion": getattr(r, "tipo_evaluacion", "orden"),
                "dias_evaluacion": getattr(r, "dias_evaluacion", 30),
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "listas_aplicables": r.listas_aplicables,
                "activo": r.activo,
            }
            for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/descuentos-volumen")
async def post_config_descuentos_volumen(req: DescuentoVolumenRequest):
    try:
        repo = get_repo()
        import uuid

        from cxc.models import DescuentoVolumen
        from cxc.sheets import serde

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"VOL_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoVolumen(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            litros_minimo=Decimal(str(req.litros_minimo)),
            porcentaje=Decimal(str(req.porcentaje)),
            tipo_evaluacion=req.tipo_evaluacion,
            dias_evaluacion=req.dias_evaluacion,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            listas_aplicables=req.listas_aplicables,
            activo=True,
        )
        repo._g.append_row("DescuentosVolumen", serde.desc_volumen_to_row(rule))
        return {"status": "success", "message": "Regla de descuento por volumen registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Unified Discount Rules Endpoint ---
@app.get("/api/reglas-descuento")
async def get_todas_reglas_descuento():
    try:
        repo = get_repo()
        if hasattr(repo._g, "_read_cache"):
            repo._g._read_cache.clear()
        todas = []

        # 1. Recompra
        for r in repo.descuentos_recompra():
            todas.append(
                {
                    "tabla": "DescuentosRecompra",
                    "tipo_regla": "recurrencia",
                    "tipo_nombre": "Recompra / Recurrencia",
                    "regla_id": r.regla_id,
                    "marca": getattr(r, "marca", "GLOBAL OIL"),
                    "categoria": getattr(r, "categoria", "CAJA"),
                    "min_cantidad": float(getattr(r, "min_cantidad", getattr(r, "min_cajas", 2))),
                    "max_cantidad": float(getattr(r, "max_cantidad", getattr(r, "max_cajas", 4))),
                    "unidad_medida": getattr(r, "unidad_medida", "CAJAS"),
                    "tipo_beneficio": getattr(r, "tipo_beneficio", "descuento"),
                    "porcentaje": float(r.porcentaje),
                    "listas_aplicables": getattr(r, "listas_aplicables", "*"),
                    "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                    "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                    "campos_especiales": {
                        "max_usos_mes": r.max_usos_mes,
                        "dias_ventana": r.dias_ventana,
                    },
                    "activo": r.activo,
                }
            )

        # 2. Pronto Pago
        for r in repo.descuentos_pronto_pago():
            todas.append(
                {
                    "tabla": "DescuentosProntoPago",
                    "tipo_regla": "contado",
                    "tipo_nombre": "Pronto Pago / Contado",
                    "regla_id": r.regla_id,
                    "marca": r.marca,
                    "categoria": r.categoria,
                    "min_cantidad": float(getattr(r, "min_cantidad", 0)),
                    "max_cantidad": float(getattr(r, "max_cantidad", 999999)),
                    "unidad_medida": getattr(r, "unidad_medida", "USD"),
                    "tipo_beneficio": getattr(r, "tipo_beneficio", "descuento"),
                    "porcentaje": float(r.porcentaje),
                    "listas_aplicables": r.listas_aplicables,
                    "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                    "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                    "campos_especiales": {
                        "dias_gracia": r.dias_gracia,
                        "monedas_aplicables": r.monedas_aplicables,
                    },
                    "activo": r.activo,
                }
            )

        # 3. Volumen
        for r in repo.descuentos_volumen():
            min_q = getattr(r, "min_cantidad", None)
            if min_q is None or float(min_q) == 0:
                min_q = getattr(r, "litros_minimo", 0)
            u_med = str(getattr(r, "unidad_medida", "") or "").strip()
            if not u_med or u_med == "None":
                u_med = (
                    "LITROS"
                    if (
                        float(r.litros_minimo) > 0
                        and (getattr(r, "min_cantidad", None) is None or float(r.min_cantidad) == 0)
                    )
                    else "CAJAS"
                )
            todas.append(
                {
                    "tabla": "DescuentosVolumen",
                    "tipo_regla": "volumen",
                    "tipo_nombre": "Descuento por Volumen",
                    "regla_id": r.regla_id,
                    "marca": r.marca,
                    "categoria": r.categoria,
                    "min_cantidad": float(min_q),
                    "max_cantidad": float(getattr(r, "max_cantidad", 999999)),
                    "unidad_medida": u_med,
                    "porcentaje": float(r.porcentaje),
                    "listas_aplicables": r.listas_aplicables,
                    "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                    "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                    "campos_especiales": {
                        "tipo_evaluacion": r.tipo_evaluacion,
                        "dias_evaluacion": r.dias_evaluacion,
                    },
                    "activo": r.activo,
                }
            )

        # 4. Primera Compra
        for r in repo.promociones_primera_compra():
            todas.append(
                {
                    "tabla": "PromocionPrimeraCompra",
                    "tipo_regla": "primera_compra",
                    "tipo_nombre": "Promoción Primera Compra",
                    "regla_id": r.regla_id,
                    "marca": getattr(r, "marca", "GLOBAL OIL"),
                    "categoria": getattr(
                        r, "categoria", getattr(r, "categorias_aplica", "Comercial")
                    ),
                    "min_cantidad": float(
                        getattr(r, "min_cantidad", getattr(r, "compra_minima", 3))
                    ),
                    "max_cantidad": float(getattr(r, "max_cantidad", 999999)),
                    "unidad_medida": getattr(r, "unidad_medida", "CAJAS"),
                    "tipo_beneficio": r.tipo_beneficio,
                    "porcentaje": float(r.valor)
                    if r.tipo_beneficio == "porcentaje"
                    else float(getattr(r, "descuento_fallback", 0.02)),
                    "listas_aplicables": getattr(r, "listas_aplicables", "*"),
                    "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                    "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                    "campos_especiales": {
                        "productos": r.productos,
                        "regalo_tipo": r.regalo_tipo,
                        "descuento_fallback": float(getattr(r, "descuento_fallback", 0)),
                    },
                    "activo": r.activo,
                }
            )

        # 5. Producto / Marca / Categoría Promo
        for r in repo.descuentos_producto():
            todas.append(
                {
                    "tabla": "DescuentosProducto",
                    "tipo_regla": "producto",
                    "tipo_nombre": "Promoción por Producto",
                    "regla_id": r.regla_id,
                    "marca": r.marca,
                    "categoria": r.categoria,
                    "min_cantidad": float(getattr(r, "min_cantidad", 0)),
                    "max_cantidad": float(getattr(r, "max_cantidad", 999999)),
                    "unidad_medida": getattr(r, "unidad_medida", "CAJAS"),
                    "tipo_beneficio": getattr(r, "tipo_beneficio", "descuento"),
                    "porcentaje": float(r.porcentaje),
                    "listas_aplicables": r.listas_aplicables,
                    "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                    "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                    "campos_especiales": {
                        "productos": r.productos,
                        "monedas_aplicables": r.monedas_aplicables,
                    },
                    "activo": r.activo,
                }
            )

        # 6. Diferencial Cambiario
        for r in repo.descuentos_diferencial_cambiario():
            todas.append(
                {
                    "tabla": "DescuentosDiferencialCambiario",
                    "tipo_regla": "diferencial_cambiario",
                    "tipo_nombre": "Diferencial Cambiario",
                    "regla_id": r.regla_id,
                    "marca": getattr(r, "marca", "*"),
                    "categoria": getattr(r, "categoria", "*"),
                    "min_cantidad": float(getattr(r, "min_cantidad", 0)),
                    "max_cantidad": float(getattr(r, "max_cantidad", 999999)),
                    "unidad_medida": getattr(r, "unidad_medida", "USD"),
                    "tipo_beneficio": getattr(r, "tipo_beneficio", "descuento"),
                    "porcentaje": float(r.porcentaje_fijo),
                    "listas_aplicables": r.listas_aplicables,
                    "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                    "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                    "campos_especiales": {
                        "nombre": r.nombre,
                        "tipo_diferencial": r.tipo_diferencial,
                        "tipo_calculo": r.tipo_calculo,
                        "monedas_aplicables": r.monedas_aplicables,
                    },
                    "activo": r.activo,
                }
            )

        return todas
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Pronto Pago Endpoints ---
@app.get("/api/config/descuentos-pronto-pago")
async def get_config_pronto_pago():
    try:
        repo = get_repo()
        rules = repo.descuentos_pronto_pago()
        return [
            {
                "regla_id": r.regla_id,
                "marca": r.marca,
                "categoria": r.categoria,
                "dias_gracia": r.dias_gracia,
                "porcentaje": float(r.porcentaje),
                "monedas_aplicables": r.monedas_aplicables,
                "listas_aplicables": r.listas_aplicables,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo,
            }
            for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/descuentos-pronto-pago")
async def post_config_pronto_pago(req: ProntoPagoRequest):
    try:
        repo = get_repo()
        import uuid

        from cxc.models import DescuentoProntoPago
        from cxc.sheets import serde

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"PP_{uuid.uuid4().hex[:8].upper()}"
        min_q = Decimal(str(req.min_cantidad or 0))
        max_q = Decimal(str(req.max_cantidad or 999999))
        rule = DescuentoProntoPago(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            dias_gracia=req.dias_gracia,
            min_cantidad=min_q,
            max_cantidad=max_q,
            unidad_medida=req.unidad_medida or "CAJAS",
            tipo_beneficio=req.tipo_beneficio or "descuento",
            porcentaje=Decimal(str(req.porcentaje)),
            monedas_aplicables=req.monedas_aplicables,
            listas_aplicables=req.listas_aplicables,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo,
        )
        repo._g.append_row("DescuentosProntoPago", serde.pronto_pago_to_row(rule))
        if hasattr(repo._g, "_read_cache"):
            repo._g._read_cache.pop("DescuentosProntoPago", None)
        return {"status": "success", "message": "Regla de descuento por pronto pago registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Volumen Endpoints ---
@app.get("/api/config/descuentos-volumen")
async def get_config_volumen():
    try:
        repo = get_repo()
        if hasattr(repo._g, "_read_cache"):
            repo._g._read_cache.clear()
        rules = repo.descuentos_volumen()
        res = []
        for r in rules:
            min_q = (
                r.min_cantidad
                if (getattr(r, "min_cantidad", None) is not None and float(r.min_cantidad) > 0)
                else getattr(r, "litros_minimo", 0)
            )
            u_med = str(getattr(r, "unidad_medida", "") or "").strip()
            if not u_med:
                u_med = (
                    "LITROS" if (float(r.litros_minimo) > 0 and float(min_q) == 0) else "UNIDADES"
                )
            res.append(
                {
                    "regla_id": r.regla_id,
                    "marca": r.marca,
                    "categoria": r.categoria,
                    "litros_minimo": float(r.litros_minimo),
                    "min_cantidad": float(min_q),
                    "max_cantidad": float(getattr(r, "max_cantidad", 999999)),
                    "unidad_medida": u_med,
                    "porcentaje": float(r.porcentaje),
                    "tipo_evaluacion": r.tipo_evaluacion,
                    "dias_evaluacion": r.dias_evaluacion,
                    "listas_aplicables": r.listas_aplicables,
                    "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                    "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                    "activo": r.activo,
                }
            )
        return res
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/descuentos-volumen")
async def post_config_volumen(req: VolumenRequest):
    try:
        repo = get_repo()
        import uuid

        from cxc.models import DescuentoVolumen
        from cxc.sheets import serde

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"VOL_{uuid.uuid4().hex[:8].upper()}"
        min_q = (
            Decimal(str(req.min_cantidad))
            if req.min_cantidad is not None
            else Decimal(str(req.litros_minimo))
        )
        rule = DescuentoVolumen(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            litros_minimo=Decimal(str(req.litros_minimo)),
            min_cantidad=min_q,
            max_cantidad=Decimal(str(req.max_cantidad)),
            unidad_medida=req.unidad_medida,
            porcentaje=Decimal(str(req.porcentaje)),
            tipo_evaluacion=req.tipo_evaluacion,
            dias_evaluacion=req.dias_evaluacion,
            listas_aplicables=req.listas_aplicables,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo,
        )
        repo._g.append_row("DescuentosVolumen", serde.desc_volumen_to_row(rule))
        if hasattr(repo._g, "_read_cache"):
            repo._g._read_cache.pop("DescuentosVolumen", None)
        return {"status": "success", "message": "Regla de descuento por volumen registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Recompra Endpoints ---
@app.get("/api/config/descuentos-recompra")
async def get_config_recompra():
    try:
        repo = get_repo()
        rules = repo.descuentos_recompra()
        return [
            {
                "regla_id": r.regla_id,
                "marca": getattr(r, "marca", "GLOBAL OIL"),
                "categoria": getattr(r, "categoria", "CAJA"),
                "porcentaje": float(r.porcentaje),
                "max_usos_mes": r.max_usos_mes,
                "dias_ventana": r.dias_ventana,
                "min_cajas": getattr(r, "min_cajas", 2),
                "max_cajas": getattr(r, "max_cajas", 4),
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo,
            }
            for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/descuentos-recompra")
async def post_config_recompra(req: RecompraRequest):
    try:
        repo = get_repo()
        import uuid

        from cxc.models import DescuentoRecompra
        from cxc.sheets import serde

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"REC_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoRecompra(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            porcentaje=Decimal(str(req.porcentaje)),
            max_usos_mes=req.max_usos_mes,
            dias_ventana=req.dias_ventana,
            min_cajas=req.min_cajas,
            max_cajas=req.max_cajas,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo,
        )
        repo._g.append_row("DescuentosRecompra", serde.recompra_to_row(rule))
        if hasattr(repo._g, "_read_cache"):
            repo._g._read_cache.pop("DescuentosRecompra", None)
        return {"status": "success", "message": "Regla de descuento por recompra registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Producto Promo Endpoints ---
@app.get("/api/config/descuentos-producto")
async def get_config_producto():
    try:
        repo = get_repo()
        rules = repo.descuentos_producto()
        return [
            {
                "regla_id": r.regla_id,
                "productos": r.productos,
                "marca": r.marca,
                "categoria": r.categoria,
                "porcentaje": float(r.porcentaje),
                "monedas_aplicables": r.monedas_aplicables,
                "listas_aplicables": r.listas_aplicables,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo,
            }
            for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/descuentos-producto")
async def post_config_producto(req: ProductoPromoRequest):
    try:
        repo = get_repo()
        import uuid

        from cxc.models import DescuentoProducto
        from cxc.sheets import serde

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"PROD_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoProducto(
            regla_id=regla_id,
            productos=req.productos,
            marca=req.marca,
            categoria=req.categoria,
            porcentaje=Decimal(str(req.porcentaje)),
            monedas_aplicables=req.monedas_aplicables,
            listas_aplicables=req.listas_aplicables,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo,
        )
        repo._g.append_row("DescuentosProducto", serde.producto_to_row(rule))
        if hasattr(repo._g, "_read_cache"):
            repo._g._read_cache.pop("DescuentosProducto", None)
        return {"status": "success", "message": "Regla de descuento por producto registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Diferencial Cambiario Endpoints ---
@app.get("/api/config/descuentos-diferencial-cambiario")
async def get_config_diferencial():
    try:
        repo = get_repo()
        rules = repo.descuentos_diferencial_cambiario()
        return [
            {
                "regla_id": r.regla_id,
                "nombre": r.nombre,
                "tipo_diferencial": r.tipo_diferencial,
                "tipo_calculo": r.tipo_calculo,
                "porcentaje_fijo": float(r.porcentaje_fijo),
                "monedas_aplicables": r.monedas_aplicables,
                "listas_aplicables": r.listas_aplicables,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo,
            }
            for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/descuentos-diferencial-cambiario")
async def post_config_diferencial(req: DiferencialCambiarioRequest):
    try:
        repo = get_repo()
        import uuid

        from cxc.models import DescuentoDiferencialCambiario
        from cxc.sheets import serde

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"DIF_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoDiferencialCambiario(
            regla_id=regla_id,
            nombre=req.nombre,
            tipo_diferencial=req.tipo_diferencial,
            tipo_calculo=req.tipo_calculo,
            porcentaje_fijo=Decimal(str(req.porcentaje_fijo)),
            monedas_aplicables=req.monedas_aplicables,
            listas_aplicables=req.listas_aplicables,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo,
        )
        repo._g.append_row("DescuentosDiferencialCambiario", serde.diferencial_to_row(rule))
        if hasattr(repo._g, "_read_cache"):
            repo._g._read_cache.pop("DescuentosDiferencialCambiario", None)
        return {"status": "success", "message": "Regla de diferencial cambiario registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Toggle Rule Active Endpoint ---
@app.post("/api/config/toggle-descuento")
async def post_toggle_descuento(req: ToggleDescuentoRequest):
    try:
        repo = get_repo()

        TABLE_CANDIDATES = {
            "DescuentosProntoPago": ["DescuentosProntoPago", "DescuentosMarcaCategoria"],
            "DescuentosRecompra": ["DescuentosRecompra", "ReglasRecurrencia"],
            "DescuentosProducto": ["DescuentosProducto", "PromocionesPrimeraCompra"],
            "DescuentosDiferencialCambiario": [
                "DescuentosDiferencialCambiario",
                "DescuentoBCVCompleto",
            ],
        }

        candidate_names = TABLE_CANDIDATES.get(
            req.tabla,
            [
                req.tabla,
                "DescuentosProntoPago",
                "DescuentosMarcaCategoria",
                "DescuentosRecompra",
                "DescuentosProducto",
                "DescuentosDiferencialCambiario",
            ],
        )
        target_id_str = str(req.regla_id).strip()

        # Handle GspreadGateway (Real Google Sheets)
        if hasattr(repo._g, "_sh"):
            for w_name in candidate_names:
                try:
                    ws = repo._g._ws(w_name)
                    values = ws.get_all_values()
                    if not values or len(values) < 2:
                        continue

                    headers = [str(h).strip().lower() for h in values[0]]
                    activo_col_idx = None
                    for idx, h in enumerate(headers):
                        if h in ("activo", "active", "estado"):
                            activo_col_idx = idx + 1
                            break

                    if not activo_col_idx:
                        continue

                    for r_idx, row in enumerate(values[1:], start=2):
                        row_str_values = [str(val).strip() for val in row]
                        if target_id_str in row_str_values:
                            ws.update_cell(r_idx, activo_col_idx, "TRUE" if req.activo else "FALSE")
                            if hasattr(repo._g, "invalidate_cache"):
                                repo._g.invalidate_cache(w_name)
                            logger.info(
                                "Regla %s actualizada a %s en '%s', fila %d",
                                target_id_str,
                                req.activo,
                                w_name,
                                r_idx,
                            )
                            estado_str = "Activo" if req.activo else "Inactivo"
                            return {
                                "status": "success",
                                "message": (
                                    f"Estado de la regla {target_id_str} actualizado a "
                                    f"{estado_str} en '{w_name}'."
                                ),
                            }
                except Exception as inner_e:
                    logger.warning(
                        "Error buscando regla %s en '%s': %s", target_id_str, w_name, inner_e
                    )
                    continue
        else:
            # InMemorySheetGateway fallback for tests
            for t_name in candidate_names:
                rows = repo._g.read_rows(t_name)
                for r in rows:
                    r_id = str(r.get("regla_id") or r.get("id") or "")
                    if r_id == target_id_str:
                        r["activo"] = "TRUE" if req.activo else "FALSE"
                        repo._g.upsert_row(t_name, "regla_id", r)
                        estado_str = "Activo" if req.activo else "Inactivo"
                        msg = f"Estado de la regla {target_id_str} actualizado a {estado_str}."
                        return {"status": "success", "message": msg}

        # If not found in Sheet, handle Default Fallback Rules by persisting them into Google Sheets
        DEFAULT_RULES_SEED = {
            "DIF_35_VES": (
                "DescuentosDiferencialCambiario",
                {
                    "regla_id": "DIF_35_VES",
                    "nombre": "35% Fijo VES a USD",
                    "tipo_diferencial": "fijo_35_ves_usd",
                    "tipo_calculo": "fijo",
                    "porcentaje_fijo": "0.35",
                    "unidad_medida": "USD",
                    "monedas_aplicables": "USD",
                    "listas_aplicables": "LISTAS_VES",
                    "vigencia_desde": date.today().isoformat(),
                    "vigencia_hasta": "",
                    "activo": "TRUE" if req.activo else "FALSE",
                },
            ),
            "DIF_EQUIPARAR": (
                "DescuentosDiferencialCambiario",
                {
                    "regla_id": "DIF_EQUIPARAR",
                    "nombre": "Equiparar Binance N/C",
                    "tipo_diferencial": "equiparar_binance",
                    "tipo_calculo": "variable",
                    "porcentaje_fijo": "0",
                    "unidad_medida": "USD",
                    "monedas_aplicables": "*",
                    "listas_aplicables": "LISTAS_VES",
                    "vigencia_desde": date.today().isoformat(),
                    "vigencia_hasta": "",
                    "activo": "TRUE" if req.activo else "FALSE",
                },
            ),
            "DIF_BRECHA_CIERRE": (
                "DescuentosDiferencialCambiario",
                {
                    "regla_id": "DIF_BRECHA_CIERRE",
                    "nombre": "Brecha BCV vs Binance Cierre",
                    "tipo_diferencial": "diferencial_bcv_binance",
                    "tipo_calculo": "variable",
                    "porcentaje_fijo": "0",
                    "unidad_medida": "USD",
                    "monedas_aplicables": "*",
                    "listas_aplicables": "LISTAS_VES",
                    "vigencia_desde": date.today().isoformat(),
                    "vigencia_hasta": "",
                    "activo": "TRUE" if req.activo else "FALSE",
                },
            ),
        }

        if target_id_str in DEFAULT_RULES_SEED:
            target_table, seed_row = DEFAULT_RULES_SEED[target_id_str]
            repo._g.append_row(target_table, seed_row)
            estado_str = "Activo" if req.activo else "Inactivo"
            msg = f"Regla por defecto {target_id_str} registrada y actualizada a {estado_str}."
            return {"status": "success", "message": msg}

        raise HTTPException(
            status_code=404, detail=f"Regla '{target_id_str}' no encontrada en Google Sheets."
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Rate Averages Endpoint ---
@app.get("/api/config/tasas-promedios")
async def get_tasas_promedios():
    try:
        repo = get_repo()
        rows = repo._g.read_rows("SerieTasas")
        today_str = date.today().isoformat()

        rates_today = [r for r in rows if r.get("timestamp", "").startswith(today_str)]
        target_rows = rates_today if rates_today else rows[-24:]

        manana_near_9 = []
        manana_all = []
        tarde_near_13 = []
        tarde_all = []
        diario = []
        last_bcv = Decimal("0")
        last_binance = Decimal("0")

        # Find most recent valid Binance & BCV rates from all rows
        for r in reversed(rows):
            try:
                tb_val = Decimal(str(r.get("tasa_binance", "0")))
                if tb_val > Decimal("0") and last_binance <= Decimal("0"):
                    last_binance = tb_val
                tbcv_val = Decimal(str(r.get("tasa_bcv", "0")))
                if tbcv_val > Decimal("0") and last_bcv <= Decimal("0"):
                    last_bcv = tbcv_val
                if last_binance > Decimal("0") and last_bcv > Decimal("0"):
                    break
            except Exception:
                pass

        for r in target_rows:
            try:
                tb = Decimal(str(r.get("tasa_binance", "0")))
                if tb > Decimal("0"):
                    diario.append(tb)
                    ts_str = str(r.get("timestamp", "00:00"))
                    time_part = ts_str.split("T")[-1].split(" ")[-1]
                    ts_hour = int(time_part.split(":")[0])

                    if 6 <= ts_hour <= 9:
                        manana_near_9.append(tb)
                    elif 10 <= ts_hour <= 13:
                        tarde_near_13.append(tb)

                    if ts_hour < 12:
                        manana_all.append(tb)
                    else:
                        tarde_all.append(tb)
            except Exception:
                pass

        m_list = manana_near_9 if manana_near_9 else manana_all
        t_list = tarde_near_13 if tarde_near_13 else tarde_all

        avg_m = float(sum(m_list) / Decimal(len(m_list))) if m_list else None
        avg_t = float(sum(t_list) / Decimal(len(t_list))) if t_list else None
        avg_d = float(sum(diario) / Decimal(len(diario))) if diario else None

        diff_pct = 0.0
        if avg_d and last_bcv > Decimal("0"):
            diff_pct = float(((Decimal(str(avg_d)) - last_bcv) / Decimal(str(avg_d))) * 100)

        return {
            "fecha": today_str,
            "tasa_bcv_actual": float(last_bcv),
            "tasa_binance_vigente": float(last_binance),
            "tasa_binance_manana": round(avg_m, 2) if avg_m else None,
            "tasa_binance_tarde": round(avg_t, 2) if avg_t else None,
            "tasa_binance_diario": round(avg_d, 2) if avg_d else None,
            "diferencial_bcv_binance_pct": round(diff_pct, 2),
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/pagos-historial")
async def get_pagos_historial():
    """Pagos vinculados localmente + conciliados directo en Odoo.

    Ya no es la fuente principal de la UI -- absorbida por
    ``GET /api/cobranza/pagos`` (``get_cobranza_pagos_unificado``), que
    llama esta función directamente. Se conserva como ruta pública además
    de función interna reusable: sigue siendo un endpoint válido y
    probado, con cobertura de tests que documentan bugs reales corregidos.
    """
    try:
        repo = get_repo()
        vincs = repo.all_vinculaciones()
        pagos_rows = repo._g.read_rows("Pagos")
        pagos_map = {r.get("pago_id"): r for r in pagos_rows}
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {r.get("cliente_id"): r.get("nombre", "") for r in clientes_rows}
        ordenes_map = {o.so_id: o for o in repo.all_ordenes()}
        tasas_rows = repo._g.read_rows("SerieTasas")

        # Total vinculado por orden (todas las vinculaciones, no solo la de
        # esta fila) -- para poder mostrar si la orden destino de una
        # vinculación manual todavía tiene saldo pendiente (residual).
        linked_so_total: dict[str, Decimal] = {}
        for v in vincs:
            linked_so_total[v.so_id] = linked_so_total.get(v.so_id, Decimal("0")) + v.monto_aplicado

        historial = []
        vinculados_pago_ids: set[str] = set()
        for v in vincs:
            vinculados_pago_ids.add(v.pago_id)
            p_data = pagos_map.get(v.pago_id, {})
            cid = p_data.get("cliente_id", "")
            c_name = clientes_map.get(cid, f"Cliente ID: {cid}")
            o = ordenes_map.get(v.so_id)
            factura_id = o.factura_id if o and o.factura_id else "N/A"
            residual_orden = (
                max(Decimal("0"), o.monto_total - linked_so_total.get(v.so_id, Decimal("0")))
                if o
                else Decimal("0")
            )

            # Monto original del pago (no solo lo aplicado en esta
            # vinculación puntual) -- en USD, para poder verificar a ojo si
            # el residual mostrado cuadra contra el importe firmado del pago.
            monto_pago_usd = float(v.monto_aplicado)
            if p_data:
                fecha_p = str(p_data.get("fecha_pago") or p_data.get("fecha") or "")[:10]
                try:
                    fecha_p_dt = (
                        datetime.strptime(fecha_p, "%Y-%m-%d") if fecha_p else datetime.now()
                    )
                except ValueError:
                    fecha_p_dt = datetime.now()
                bcv_p, _ = get_rate_for_datetime(fecha_p_dt, tasas_rows)
                monto_pago_usd = float(
                    pago_monto_usd(
                        parse_decimal_safe(p_data.get("monto", "0")),
                        str(p_data.get("moneda", "USD") or "USD").upper().strip(),
                        bcv_p,
                    )
                )

            historial.append(
                {
                    "vinc_id": v.vinc_id,
                    "pago_id": v.pago_id,
                    "cliente_nombre": c_name,
                    "fecha_pago": v.hora_pago_confirmada.strftime("%Y-%m-%d")
                    if v.hora_pago_confirmada
                    else "",
                    "monto_pago_usd": monto_pago_usd,
                    "monto_aplicado": float(v.monto_aplicado),
                    "moneda": v.moneda_abono.value if v.moneda_abono else "USD",
                    "so_id": v.so_id,
                    "factura_id": factura_id,
                    "facturas": [],
                    "residual_pago_usd": 0.0,
                    "residual_facturas_usd": float(residual_orden),
                    "confirmado_por": v.confirmado_por or "Sistema",
                    "estado": v.estado.value,
                    "origen": "Sistema (vinculación manual)",
                    "tasa_bcv": float(v.tasa_bcv_aplicada) if v.tasa_bcv_aplicada else None,
                    "tasa_binance": float(v.tasa_binance_aplicada)
                    if v.tasa_binance_aplicada
                    else None,
                    "bcv_variante": v.bcv_variante or "USD",
                    "editable": True,
                    # Campos adicionales para /api/cobranza/pagos (unificado) --
                    # no se usaban antes en esta tabla, se agregan sin tocar
                    # los ya existentes.
                    "metodo_pago_id": str(p_data.get("metodo_pago", "") or "").strip(),
                    "monto_original": float(parse_decimal_safe(p_data.get("monto", "0"))),
                    "vendedor_email": (
                        p_data.get("vendedor_email") or (o.vendedor_email if o else "")
                    ),
                }
            )

        # Pagos reconciliados directamente en Odoo (via factura) que NUNCA
        # pasaron por una Vinculacion de este sistema -- antes invisibles en
        # esta tabla, aunque ya estaban "conciliados" desde el punto de vista
        # de Odoo. Una fila POR PAGO (no por orden, un pago puede reconciliar
        # facturas de varias órdenes) con TODAS sus facturas asociadas, monto
        # conciliado y residual. Best-effort: si Odoo no responde, se muestra
        # solo lo que ya se tenía localmente.
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            if execute:
                for p in get_live_pagos_conciliados(execute):
                    pago_id = p["pago_id"]
                    if pago_id in vinculados_pago_ids:
                        continue  # ya vino por la Vinculacion local, no duplicar
                    facturas = p["facturas"]
                    factura_id = (
                        ", ".join(f["factura_id"] for f in facturas) if facturas else "N/A"
                    )
                    so_id = ", ".join(p["so_ids"]) if p["so_ids"] else ""
                    c_name = p["cliente_nombre"] or clientes_map.get(
                        p["cliente_id"], f"Cliente ID: {p['cliente_id']}"
                    )
                    historial.append(
                        {
                            "vinc_id": None,
                            "pago_id": pago_id,
                            "cliente_nombre": c_name,
                            "fecha_pago": p["fecha_pago"],
                            "monto_pago_usd": p["monto_ref_usd"],
                            "monto_aplicado": p["monto_conciliado_usd"],
                            "moneda": p["moneda"],
                            "so_id": so_id,
                            "factura_id": factura_id,
                            "facturas": facturas,
                            "residual_pago_usd": p["residual_pago_usd"],
                            "residual_facturas_usd": p["residual_facturas_usd"],
                            "confirmado_por": "Odoo",
                            "estado": "CONCILIADO",
                            "origen": "Odoo (automático vía factura)",
                            "tasa_bcv": None,
                            "tasa_binance": None,
                            "bcv_variante": "USD",
                            "editable": False,
                            "metodo_pago_id": "",
                            "metodo_pago_nombre": p.get("metodo_pago") or "",
                            "monto_original": p["monto_original"],
                            "vendedor_email": p.get("vendedor_email") or "",
                        }
                    )
        except Exception as e_odoo:
            logger.warning("Error consultando pagos reconciliados en Odoo: %s", e_odoo)

        return historial
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/cobranza/pagos")
async def get_cobranza_pagos_unificado(cxc_session: str | None = Cookie(default=None)):
    """Vista unificada de pagos: reemplaza las 4 tablas históricas

    ("Pagos Pendientes por Asociar", "Mapa de Conciliación", "Pagos
    Conciliados" y "Cobranza") con un solo esquema de campos.

    NO reimplementa el cálculo financiero (FIFO, saldo real de orden,
    tasas por pago) -- reusa ``get_conciliaciones_sugerencias`` (pendientes)
    y ``get_pagos_historial`` (vinculados localmente + conciliados directo
    en Odoo) tal cual, y les agrega lo que falta para el esquema común:
    método de pago (nombre), vendedor validado contra la orden, tasa
    BCV-EUR, trazabilidad de re-vinculación por Odoo, estado de recepción
    de recibo, y los pagos huérfanos cerrados a favor de la empresa (antes
    invisibles fuera del filtro de exclusión de "pendientes").
    """
    try:
        repo = get_repo()
        user = get_current_user_from_cookie(cxc_session)

        sugerencias = await get_conciliaciones_sugerencias(cxc_session)
        historial = await get_pagos_historial()
        cerrados_detalle = leer_pagos_huerfanos_cerrados(repo)

        pagos_rows = repo._g.read_rows("Pagos")
        pagos_by_id = {str(p.get("pago_id", "")).strip(): p for p in pagos_rows}
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map_obj = {
            str(r.get("cliente_id", "")): Cliente(
                cliente_id=str(r.get("cliente_id", "")),
                nombre=r.get("nombre", ""),
                vendedor_email=r.get("vendedor_email", ""),
            )
            for r in clientes_rows
        }
        clientes_nombre_map = {
            str(r.get("cliente_id", "")): r.get("nombre", "") for r in clientes_rows
        }
        ordenes_map = {o.so_id: o for o in repo.all_ordenes()}
        tasas_historicas_rows = repo._g.read_rows("TasasHistoricasAuditoria")

        # Trazabilidad: pagos re-vinculados automáticamente porque Odoo los
        # reconcilió contra una orden distinta a la Vinculación local (ver
        # _resincronizar_vinculaciones_con_odoo, corre en cada sync). Se
        # SURFACEA acá -- la corrección automática y su auditoría ya existen.
        reasignados_por_pago: dict[str, dict[str, str]] = {}
        if hasattr(repo, "all_auditoria"):
            try:
                for row in repo.all_auditoria():
                    if row.get("tipo_auditoria") == "vinculacion_revinculada_por_odoo":
                        pid = str(row.get("pago_id", "")).strip()
                        if pid:
                            reasignados_por_pago[pid] = row
            except Exception as e_aud:
                logger.warning("Error leyendo BandejaAuditoria en /api/cobranza/pagos: %s", e_aud)

        metodo_pago_map: dict[int, str] = {}
        odoo_tax_today_map: dict[str, dict[str, Any]] = {}
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            if execute:
                metodo_pago_map = resolve_metodo_pago_nombre(execute)
                # tax_today para los pagos conciliados directo en Odoo (nunca
                # pasaron por Vinculacion local) -- el historial no trae esta
                # tasa para esos, y el usuario pidió mostrar SIEMPRE las 3
                # tasas, sea cual sea el estado del pago.
                ids_sin_tasa = [
                    int(h["pago_id"])
                    for h in historial
                    if h.get("vinc_id") is None and str(h["pago_id"]).isdigit()
                ]
                if ids_sin_tasa:
                    recs = execute(
                        "account.payment",
                        "search_read",
                        [[["id", "in", ids_sin_tasa]]],
                        {"fields": ["id", "tax_today"]},
                    )
                    odoo_tax_today_map = {str(r["id"]): r for r in recs}
        except Exception as e_odoo:
            logger.warning(
                "Error resolviendo métodos de pago/tasas en /api/cobranza/pagos: %s", e_odoo
            )

        def visible(vendedor: str) -> bool:
            if not (user and user["rol"] == "ventas"):
                return True
            u_name = (user["nombre"] or user["email"]).strip().lower()
            return (
                vendedor.strip().lower() == u_name
                or user["email"].strip().lower() in vendedor.lower()
            )

        def comun(pid: str, cliente_id: str, so_id: str | None, fecha_pago: str) -> dict[str, Any]:
            p_row = pagos_by_id.get(pid, {})
            metodo_id = str(p_row.get("metodo_pago", "") or "").strip()
            metodo_nombre = (
                (metodo_pago_map.get(int(metodo_id)) if metodo_id.isdigit() else None)
                or metodo_id
                or None
            )

            vendedor, mismatch = resolve_vendedor_validado(
                cliente_id, so_id, clientes_map_obj, ordenes_map
            )

            fecha_dt = None
            try:
                if fecha_pago:
                    fecha_dt = datetime.strptime(str(fecha_pago)[:10], "%Y-%m-%d").date()
            except ValueError:
                fecha_dt = None
            tasa_eur = get_eur_rate_for_date(fecha_dt, tasas_historicas_rows) if fecha_dt else None

            reasignado = reasignados_por_pago.get(pid)

            return {
                "metodo_pago": metodo_nombre,
                "vendedor": vendedor,
                "vendedor_mismatch": mismatch,
                "tasa_bcv_eur": float(tasa_eur) if tasa_eur is not None else None,
                "reasignado_por_odoo": reasignado is not None,
                "reasignado_detalle": reasignado.get("detalle_odoo") if reasignado else None,
                "recibido": p_row.get("recibido") == "TRUE",
                "numero_recibido": p_row.get("numero_recibido") or None,
                "fecha_recibido": p_row.get("fecha_recibido") or None,
                "recibido_por": p_row.get("recibido_por") or None,
            }

        def monto_eur(pid: str, tasa_eur: float | None) -> float | None:
            # USD es 1:1 en las 3 tasas (BCV, Binance, EUR) -- no depende de
            # conocer la tasa del día, igual que ``pago_monto_usd``.
            p_row = pagos_by_id.get(pid)
            if p_row:
                monto_raw = parse_decimal_safe(p_row.get("monto", "0"))
                moneda = str(p_row.get("moneda", "USD") or "USD").upper().strip()
            else:
                # Pago conciliado directo en Odoo, nunca sincronizado local --
                # se usa el monto original ya traído por get_live_pagos_conciliados
                # (ver el campo "monto_original" agregado a /api/pagos-historial).
                for h in historial:
                    if h["pago_id"] == pid and h.get("vinc_id") is None:
                        monto_raw = Decimal(str(h.get("monto_original", 0)))
                        moneda = str(h.get("moneda", "USD") or "USD").upper().strip()
                        break
                else:
                    return None
            if moneda == "USD":
                return float(monto_raw)
            if tasa_eur is None or tasa_eur <= 0:
                return None
            return float(monto_raw / Decimal(str(tasa_eur)))

        unificados: list[dict[str, Any]] = []

        # 1) Pendientes -- ya vienen con reparto FIFO resuelto.
        for item in sugerencias:
            pid = item["pago_id"]
            extra = comun(pid, item["cliente_id"], item.get("so_id"), item["pago_fecha"])
            if not visible(extra["vendedor"]):
                continue
            unificados.append(
                {
                    "pago_id": pid,
                    "numero_pago_odoo": item.get("numero_pago_odoo"),
                    "pago_fecha": item["pago_fecha"],
                    "cliente_id": item["cliente_id"],
                    "cliente_nombre": item["cliente_nombre"],
                    "monto_pago_original": item["monto_pago_original"],
                    "moneda_pago": item["moneda_pago"],
                    "tasa_bcv": item["tasa_bcv"],
                    "tasa_binance": item["tasa_binance"],
                    "monto_pago_bcv_usd": item["monto_pago"],
                    "monto_pago_binance_usd": item["monto_pago_binance"],
                    "monto_pago_eur": monto_eur(pid, extra["tasa_bcv_eur"]),
                    "monto_aplicado": 0.0,
                    "monto_por_aplicar": item["saldo_pago"],
                    "so_saldo_pendiente": item.get("so_saldo_pendiente"),
                    "factura_saldo_odoo": None,
                    "so_id": item.get("so_id"),
                    "factura_id": None,
                    "facturas": [],
                    "estado": "pendiente",
                    "origen": "Sistema (sugerencia, aún sin confirmar)",
                    "confirmado_por": None,
                    "posible_duplicado": item["posible_duplicado"],
                    "duplicado_de": item["duplicado_de"],
                    "sugerencia_id": item["sugerencia_id"],
                    "vinc_id": None,
                    "monto_sugerido": item.get("monto_sugerido"),
                    "puede_vincular": True,
                    "puede_cerrar_huerfano": item.get("so_id") is None,
                    "puede_editar_tasas": False,
                    "puede_marcar_recibido": not extra["recibido"],
                    **extra,
                }
            )

        # 2) Vinculados localmente / conciliados directo en Odoo.
        for item in historial:
            pid = item["pago_id"]
            p_row_hist = pagos_by_id.get(pid)
            cliente_id = str(p_row_hist.get("cliente_id", "")).strip() if p_row_hist else ""
            extra = comun(pid, cliente_id, item.get("so_id"), item["fecha_pago"])
            if not cliente_id:
                # Pago Odoo-directo nunca sincronizado local -- no hay Cliente
                # local contra el que validar; se usa el vendedor que ya trae
                # get_live_pagos_conciliados (vendedor_email) tal cual.
                extra["vendedor"] = item.get("vendedor_email") or extra["vendedor"]
            if not visible(extra["vendedor"]):
                continue

            estado_vinc = str(item.get("estado", "")).lower()
            estado_unificado = (
                "conciliado_odoo" if estado_vinc == "conciliado" else "vinculado_local"
            )
            if item.get("vinc_id") is None:
                estado_unificado = "conciliado_odoo"

            tasa_bcv = item.get("tasa_bcv")
            tasa_binance = item.get("tasa_binance")
            if tasa_bcv is None and pid in odoo_tax_today_map:
                tt = parse_decimal_safe(str(odoo_tax_today_map[pid].get("tax_today") or "0"))
                if tt > Decimal("0"):
                    tasa_bcv = float(tt)

            monto_original_raw = item.get("monto_original")
            monto_bin_usd = None
            if monto_original_raw is not None:
                moneda_item = str(item.get("moneda", "USD") or "USD").upper().strip()
                if moneda_item == "USD":
                    monto_bin_usd = float(monto_original_raw)
                elif tasa_binance:
                    monto_bin_usd = float(
                        Decimal(str(monto_original_raw)) / Decimal(str(tasa_binance))
                    )

            unificados.append(
                {
                    "pago_id": pid,
                    "numero_pago_odoo": None,
                    "pago_fecha": item["fecha_pago"],
                    "cliente_id": cliente_id,
                    "cliente_nombre": item["cliente_nombre"],
                    "monto_pago_original": monto_original_raw,
                    "moneda_pago": item.get("moneda"),
                    "tasa_bcv": tasa_bcv,
                    "tasa_binance": tasa_binance,
                    "monto_pago_bcv_usd": item["monto_pago_usd"],
                    "monto_pago_binance_usd": monto_bin_usd,
                    "monto_pago_eur": monto_eur(pid, extra["tasa_bcv_eur"]),
                    "monto_aplicado": item["monto_aplicado"],
                    "monto_por_aplicar": item["residual_pago_usd"],
                    "so_saldo_pendiente": None,
                    "factura_saldo_odoo": item["residual_facturas_usd"],
                    "so_id": item.get("so_id") or None,
                    "factura_id": item.get("factura_id"),
                    "facturas": item.get("facturas", []),
                    "estado": estado_unificado,
                    "origen": item["origen"],
                    "confirmado_por": item["confirmado_por"],
                    "posible_duplicado": False,
                    "duplicado_de": [],
                    "sugerencia_id": None,
                    "vinc_id": item.get("vinc_id"),
                    "monto_sugerido": None,
                    "bcv_variante": item.get("bcv_variante", "USD"),
                    "puede_vincular": False,
                    "puede_cerrar_huerfano": False,
                    "puede_editar_tasas": bool(item.get("editable")),
                    "puede_marcar_recibido": not extra["recibido"],
                    **extra,
                }
            )

        # 3) Cerrados a favor de la empresa -- antes solo excluían de
        # "pendientes", nunca tenían su propia bandeja/vista.
        for pid, detalle in cerrados_detalle.items():
            p_row = pagos_by_id.get(pid, {})
            cliente_id = str(p_row.get("cliente_id", "")).strip()
            cliente_nombre = clientes_nombre_map.get(cliente_id, f"Cliente {cliente_id}")
            fecha_cierre = str(p_row.get("fecha_pago") or p_row.get("fecha") or "")
            extra = comun(pid, cliente_id, None, fecha_cierre)
            if not visible(extra["vendedor"]):
                continue
            monto_raw = parse_decimal_safe(p_row.get("monto", "0"))
            unificados.append(
                {
                    "pago_id": pid,
                    "numero_pago_odoo": None,
                    "pago_fecha": str(p_row.get("fecha_pago") or p_row.get("fecha") or "")[:10],
                    "cliente_id": cliente_id,
                    "cliente_nombre": cliente_nombre,
                    "monto_pago_original": float(monto_raw),
                    "moneda_pago": p_row.get("moneda"),
                    "tasa_bcv": None,
                    "tasa_binance": None,
                    "monto_pago_bcv_usd": None,
                    "monto_pago_binance_usd": None,
                    "monto_pago_eur": monto_eur(pid, extra["tasa_bcv_eur"]),
                    "monto_aplicado": 0.0,
                    "monto_por_aplicar": float(monto_raw),
                    "so_saldo_pendiente": None,
                    "factura_saldo_odoo": None,
                    "so_id": None,
                    "factura_id": None,
                    "facturas": [],
                    "estado": "cerrado_empresa",
                    "origen": "Sistema (cerrado a favor de la empresa)",
                    "confirmado_por": detalle.get("cerrado_por"),
                    "posible_duplicado": False,
                    "duplicado_de": [],
                    "sugerencia_id": None,
                    "vinc_id": None,
                    "monto_sugerido": None,
                    "cerrado_motivo": detalle.get("motivo"),
                    "cerrado_por": detalle.get("cerrado_por"),
                    "cerrado_timestamp": detalle.get("timestamp_cierre"),
                    "puede_vincular": False,
                    "puede_cerrar_huerfano": False,
                    "puede_editar_tasas": False,
                    "puede_marcar_recibido": not extra["recibido"],
                    **extra,
                }
            )

        unificados.sort(key=lambda r: r["pago_fecha"] or "")
        return unificados
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/tasas-historicas")
async def get_tasas_historicas():
    try:
        repo = get_repo()
        rows = repo._g.read_rows("TasasHistoricasAuditoria")
        return {"items": rows, "count": len(rows)}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/reglas-descuento")
async def get_reglas_descuento():
    try:
        repo = get_repo()
        primera = repo._g.read_rows("PromocionPrimeraCompra")
        recompra = repo._g.read_rows("DescuentosRecompra")
        pronto_pago = repo._g.read_rows("DescuentosProntoPago")
        volumen = repo._g.read_rows("DescuentosVolumen")
        producto = repo._g.read_rows("DescuentosProducto")
        diferencial = repo._g.read_rows("DescuentosDiferencialCambiario")

        return {
            "primera_compra": primera,
            "recompra": recompra,
            "pronto_pago": pronto_pago,
            "volumen": volumen,
            "producto": producto,
            "diferencial_cambiario": diferencial,
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/auditoria")
async def get_auditoria():
    try:
        repo = get_repo()
        # Bug preexistente (Tarea 6): esta funcion usaba `cutoff_historical` y
        # `hist_map` sin definirlas nunca en su propio scope (solo existian
        # como variables locales de get_reporte_saldos) -- /api/auditoria
        # siempre tiraba NameError y devolvia 500. Se replican aqui con la
        # misma logica.
        cutoff_historical = date(2026, 3, 12)
        hist_rows = repo._g.read_rows("ListasPreciosHistoricas")
        hist_map: dict[str, dict[str, Any]] = {}
        for _hr in hist_rows:
            _code = str(_hr.get("codigo", "")).strip()
            if _code:
                with contextlib.suppress(Exception):
                    hist_map[_code] = {
                        "nombre": _hr.get("producto_nombre", ""),
                        "usd": Decimal(str(_hr.get("precio_usd", "0") or "0")),
                        "eur": Decimal(str(_hr.get("precio_bcv_euro", "0") or "0")),
                    }

        ordenes = repo.all_ordenes()
        lines_rows = repo._g.read_rows("LineasOrden")
        bandeja_rows = repo.all_bandeja()
        bandeja_map = {b.so_id: b for b in bandeja_rows}
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {r.get("cliente_id"): r.get("nombre", "") for r in clientes_rows}

        # Load UI configured pricelists (USD & VES) from _Meta
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        usd_ids, ves_ids = get_ui_pricelist_ids(repo)
        all_candidate_ids = list(set(usd_ids + ves_ids))

        rules_all = (
            execute(
                "product.pricelist.item",
                "search_read",
                [[["pricelist_id", "in", all_candidate_ids], ["compute_price", "=", "fixed"]]],
                {
                    "fields": [
                        "pricelist_id",
                        "product_tmpl_id",
                        "fixed_price",
                        "date_start",
                        "date_end",
                    ]
                },
            )
            if execute
            else []
        )

        # Load accepted anomalies from Google Sheets
        anomalias_aceptadas_rows = repo._g.read_rows("AnomaliasAceptadas")
        aceptadas_map = {r.get("anomalia_id"): r for r in anomalias_aceptadas_rows}
        lines_by_so = {}
        for r in lines_rows:
            so = r.get("so_id", "")
            if so:
                lines_by_so.setdefault(so, []).append(r)

        # Fetch posted invoices from Odoo in batch for audit comparison
        so_ids = [o.so_id for o in ordenes]
        invoices_by_so = {}
        so_pagada_en_odoo: set[str] = set()
        try:
            invoices = (
                execute(
                    "account.move",
                    "search_read",
                    [
                        [
                            ["invoice_origin", "in", so_ids],
                            ["state", "=", "posted"],
                            ["move_type", "in", ["out_invoice", "out_refund"]],
                        ]
                    ],
                    {
                        "fields": [
                            "id",
                            "name",
                            "invoice_origin",
                            "amount_total",
                            "amount_residual",
                            "currency_id",
                            "invoice_date",
                            "move_type",
                            "payment_state",
                        ]
                    },
                )
                if execute
                else []
            )
            for inv in invoices:
                so = str(inv.get("invoice_origin", "")).strip()
                if so:
                    invoices_by_so.setdefault(so, []).append(inv)

            # SO "pagada" = todas sus out_invoice estan payment_state paid/in_payment
            # (misma regla que /api/reporte-saldos, Tarea 2).
            estados_por_so: dict[str, list[str]] = {}
            for inv in invoices:
                if str(inv.get("move_type")) != "out_invoice":
                    continue
                so = str(inv.get("invoice_origin", "")).strip()
                if so:
                    estados_por_so.setdefault(so, []).append(str(inv.get("payment_state", "")))
            for so, estados in estados_por_so.items():
                if estados and all(ps in ("paid", "in_payment") for ps in estados):
                    so_pagada_en_odoo.add(so)
        except Exception as e:
            logger.warning("Error al consultar facturas Odoo en get_auditoria: %s", e)

        # Read rates series to convert VES invoice residual to USD
        tasas_rows = repo._g.read_rows("SerieTasas")
        rates_map = {}
        for r in tasas_rows:
            ts = str(r.get("timestamp", ""))[:10]
            tbcv = r.get("tasa_bcv")
            if ts and tbcv:
                with contextlib.suppress(Exception):
                    rates_map[ts] = float(tbcv)
        last_bcv_val = list(rates_map.values())[-1] if rates_map else 742.23

        # Load payments map by SO for net debt comparison
        vincs = repo.all_vinculaciones()
        pagos_by_so = {}
        for v in vincs:
            if v.estado == EstadoVinculacion.CONCILIADO:
                pagos_by_so[v.so_id] = pagos_by_so.get(v.so_id, 0.0) + float(v.monto_aplicado)

        # Estado EN VIVO de cada orden -- el espejo local (estado_orden) puede
        # quedar desactualizado si una orden se cancela en Odoo y el sync
        # incremental no la vuelve a traer (ventana delta vencida, downtime,
        # etc.). Ver get_reporte_diario / get_resumen / get_reporte_saldos
        # para el mismo fix.
        so_states_map: dict[str, str] = {}
        entrega_valida_set: set[str] = set()
        if execute and so_ids:
            try:
                so_recs_live = execute(
                    "sale.order",
                    "search_read",
                    [[["name", "in", so_ids]]],
                    {"fields": ["name", "state"]},
                )
                for s in so_recs_live:
                    sname = str(s.get("name", "")).strip()
                    if sname:
                        so_states_map[sname] = str(s.get("state", "")).strip().lower()
                entrega_valida_set = get_live_delivered_not_returned(so_ids, execute=execute)
            except Exception as e:
                logger.warning("Error consultando estado en vivo en get_auditoria: %s", e)

        # Productos realmente despachados (stock.move.line de las entregas
        # ALM/OUT en estado "done") por orden -- para el Check 5 más abajo:
        # detectar si se entregó un producto distinto o adicional al pedido.
        delivered_products_by_so: dict[str, set[int]] = {}
        if execute and so_ids:
            try:
                so_pick_recs = execute(
                    "sale.order",
                    "search_read",
                    [[["name", "in", so_ids]]],
                    {"fields": ["name", "picking_ids"]},
                )
                picking_to_so: dict[int, str] = {}
                for s in so_pick_recs:
                    sname = str(s.get("name", "")).strip()
                    for pid in s.get("picking_ids") or []:
                        picking_to_so[pid] = sname
                if picking_to_so:
                    done_out_pickings = execute(
                        "stock.picking",
                        "search_read",
                        [
                            [
                                ["id", "in", list(picking_to_so.keys())],
                                ["state", "=", "done"],
                                ["picking_type_code", "=", "outgoing"],
                            ]
                        ],
                        {"fields": ["id"]},
                    )
                    done_ids = [p["id"] for p in done_out_pickings]
                    if done_ids:
                        move_lines = execute(
                            "stock.move.line",
                            "search_read",
                            [[["picking_id", "in", done_ids]]],
                            {"fields": ["picking_id", "product_id"]},
                        )
                        for ml in move_lines:
                            pid_info = ml.get("picking_id")
                            p_id = pid_info[0] if isinstance(pid_info, list | tuple) else None
                            so_name = picking_to_so.get(p_id) if p_id else None
                            prod_info = ml.get("product_id")
                            prod_id = (
                                prod_info[0] if isinstance(prod_info, list | tuple) else None
                            )
                            if so_name and prod_id:
                                delivered_products_by_so.setdefault(so_name, set()).add(prod_id)
            except Exception as e_ml:
                logger.warning("Error consultando productos entregados en get_auditoria: %s", e_ml)

        operaciones_conformes = []
        raw_discrepancias = []
        discrepancias_facturas_odoo = []

        for o in ordenes:
            if orden_excluida(
                o,
                live_state=so_states_map.get(o.so_id),
                entrega_valida=o.so_id in entrega_valida_set,
            ):
                continue
            c_name = clientes_map.get(o.cliente_id, f"Cliente ID: {o.cliente_id}")
            b = bandeja_map.get(o.so_id)
            so_lines = lines_by_so.get(o.so_id, [])

            has_discrepancy = False
            lista_id_str = str(o.lista_precios or "").strip()
            is_ves = lista_id_str in [str(x) for x in ves_ids]
            candidate_list_ids = ves_ids if is_ves else usd_ids
            pricelist_label = (
                f"Lista VES (#{lista_id_str})" if is_ves else f"Lista USD (#{lista_id_str})"
            )
            is_historical = (
                not lista_id_str or lista_id_str in ("0", "None", "") or o.fecha < cutoff_historical
            )

            # Check 1: Unit prices vs correct official pricelist or Historical List
            for ln in so_lines:
                qty = parse_decimal_safe(ln.get("cantidad", "0"))
                qty_delivered = parse_decimal_safe(
                    ln.get("cantidad_entregada", ln.get("qty_delivered", "0"))
                )
                price_order = parse_decimal_safe(ln.get("precio_unitario", "0"))

                # Skip returned/non-delivered/zero-qty lines (returns with price=0 or qty=0)
                if (
                    qty <= Decimal("0")
                    or price_order <= Decimal("0")
                    or qty_delivered <= Decimal("0")
                ):
                    continue

                pt_id = extract_product_tmpl_id(ln.get("producto", ""))
                if is_historical:
                    code_key = str(pt_id) if pt_id else str(ln.get("producto", "")).strip()
                    hist_info = hist_map.get(code_key)
                    price_official = (
                        hist_info["usd"] if hist_info and hist_info["usd"] > Decimal("0") else None
                    )
                    cur_label = (
                        "Lista Histórica Auditoría (Pre-12-Mar)"
                        if o.fecha < cutoff_historical
                        else "Lista Histórica Auditoría (Sin Lista)"
                    )
                else:
                    price_official = (
                        resolve_effective_pricelist_price(
                            pt_id, o.fecha, candidate_list_ids, rules_all
                        )
                        if pt_id
                        else None
                    )
                    cur_label = pricelist_label

                if price_official is not None and price_order < price_official - Decimal("0.01"):
                    has_discrepancy = True
                    diff_unit = price_official - price_order
                    diff_monto = float(diff_unit * qty)
                    diff_pct = (
                        float((diff_unit / price_official) * 100) if price_official > 0 else 0.0
                    )
                    detalle_precio = (
                        f"Producto ID {pt_id}: Precio orden (${float(price_order):.2f}) < "
                        f"{cur_label} (${float(price_official):.2f}) "
                        f"[Entregado: {float(qty_delivered):.0f} und]"
                    )
                    raw_discrepancias.append(
                        {
                            "so_id": o.so_id,
                            "factura_id": o.factura_id or "N/A",
                            "cliente_nombre": c_name,
                            "vendedor": o.vendedor_email or "N/A",
                            "tipo": "Precio Inferior a Lista",
                            "detalle": detalle_precio,
                            "esperado": float(price_official * qty),
                            "actual": float(price_order * qty),
                            "diferencia_monto": round(diff_monto, 2),
                            "diferencia_porcentaje": round(diff_pct, 2),
                        }
                    )

                # Check 2: Manual unapproved line discounts
                disc = parse_decimal_safe(ln.get("descuento", "0"))
                if disc > Decimal("0") and (
                    not b
                    or (
                        b
                        and b.total_descuentos == Decimal("0")
                        and b.ncs_calculadas == Decimal("0")
                    )
                ):
                    has_discrepancy = True
                    disc_monto = float((price_order * qty) * (disc / Decimal("100")))
                    detalle_disc = (
                        f"Descuento manual del {float(disc):.1f}% en línea sin regla activa "
                        f"[Entregado: {float(qty_delivered):.0f} und]"
                    )
                    raw_discrepancias.append(
                        {
                            "so_id": o.so_id,
                            "factura_id": o.factura_id or "N/A",
                            "cliente_nombre": c_name,
                            "vendedor": o.vendedor_email or "N/A",
                            "tipo": "Descuento Manual No Explicado",
                            "detalle": detalle_disc,
                            "esperado": float(price_order * qty),
                            "actual": float((price_order * qty) - Decimal(str(disc_monto))),
                            "diferencia_monto": round(disc_monto, 2),
                            "diferencia_porcentaje": float(disc),
                        }
                    )

            # Check 3: Sub-facturación / Sobre-facturación / Orden vs Factura
            if o.facturada and o.monto_facturado:
                net_expected = b.total_motor if b else o.monto_total
                diff_inv = o.monto_facturado - net_expected
                if abs(diff_inv) > Decimal("0.05"):
                    has_discrepancy = True
                    tipo_str = "Sobre-facturación" if diff_inv > Decimal("0") else "Sub-facturación"
                    pct_inv = (
                        float((abs(diff_inv) / net_expected) * 100) if net_expected > 0 else 0.0
                    )
                    detalle_inv = (
                        f"Factura Odoo (${float(o.monto_facturado):.2f}) no coincide con "
                        f"Neto Orden Esperado (${float(net_expected):.2f})"
                    )
                    raw_discrepancias.append(
                        {
                            "so_id": o.so_id,
                            "factura_id": o.factura_id or "N/A",
                            "cliente_nombre": c_name,
                            "vendedor": o.vendedor_email or "N/A",
                            "tipo": tipo_str,
                            "detalle": detalle_inv,
                            "esperado": float(net_expected),
                            "actual": float(o.monto_facturado),
                            "diferencia_monto": round(float(abs(diff_inv)), 2),
                            "diferencia_porcentaje": round(pct_inv, 2),
                        }
                    )

            # Check 4: Discrepancia entre Saldo Deudor CxC vs Saldo Residual Factura Odoo
            inv_list = invoices_by_so.get(o.so_id, [])
            if inv_list:
                tot_res_usd = 0.0
                inv_names_list = []
                for inv in inv_list:
                    inv_names_list.append(str(inv.get("name", "")))
                    res_val = float(inv.get("amount_residual", 0.0))
                    curr = inv.get("currency_id")
                    c_name_inv = (
                        curr[1] if isinstance(curr, list | tuple) and len(curr) > 1 else "USD"
                    )
                    inv_dt = str(inv.get("invoice_date") or o.fecha.isoformat())[:10]
                    rate = rates_map.get(inv_dt, last_bcv_val)
                    if c_name_inv == "VES" and rate > 0:
                        tot_res_usd += res_val / rate
                    else:
                        tot_res_usd += res_val

                saldo_factura_odoo = max(0.0, float(tot_res_usd))
                factura_nombre = ", ".join(inv_names_list)
                abono_conc = pagos_by_so.get(o.so_id, 0.0)
                saldo_cxc = max(0.0, float(o.monto_total) - abono_conc)

                diff_cxc_inv = abs(saldo_cxc - saldo_factura_odoo)
                if diff_cxc_inv > 0.50:
                    discrepancias_facturas_odoo.append(
                        {
                            "so_id": o.so_id,
                            "factura_id": factura_nombre,
                            "cliente_nombre": c_name,
                            "vendedor": o.vendedor_email or "Sin Vendedor",
                            "fecha": o.fecha.isoformat(),
                            "saldo_cxc": round(saldo_cxc, 2),
                            "saldo_factura_odoo": round(saldo_factura_odoo, 2),
                            "diferencia": round(diff_cxc_inv, 2),
                            "causa_probable": (
                                "Abono / Pago registrado en CxC pero pendiente de aplicar en Odoo"
                                if saldo_factura_odoo > saldo_cxc
                                else "Diferencia por retenciones o ajustes en factura Odoo"
                            ),
                        }
                    )

            # Check 5: producto entregado (ALM/OUT) que no estaba en la orden
            # -- el cliente recibió algo distinto o adicional a lo pedido.
            delivered_set = delivered_products_by_so.get(o.so_id)
            if delivered_set:
                expected_set: set[int] = set()
                for ln in so_lines:
                    with contextlib.suppress(Exception):
                        pid = int(ln.get("producto") or 0)
                        if pid:
                            expected_set.add(pid)
                extra_products = delivered_set - expected_set
                if extra_products:
                    has_discrepancy = True
                    extra_ids = ", ".join(str(pid) for pid in sorted(extra_products))
                    raw_discrepancias.append(
                        {
                            "so_id": o.so_id,
                            "factura_id": o.factura_id or "N/A",
                            "cliente_nombre": c_name,
                            "vendedor": o.vendedor_email or "N/A",
                            "tipo": "Producto Entregado No Coincide con la Orden",
                            "detalle": (
                                f"Entrega ALM/OUT incluye producto(s) [Odoo ID: {extra_ids}] "
                                "que no están en las líneas de la orden"
                            ),
                            "esperado": 0.0,
                            "actual": 0.0,
                            "diferencia_monto": 0.0,
                            "diferencia_porcentaje": 0.0,
                        }
                    )

            # Tarea 2: la tabla de confirmacion es para lo ya PAGADO en Odoo
            # (paid/in_payment) y sin discrepancias -- no solo facturado.
            if not has_discrepancy and o.facturada and o.so_id in so_pagada_en_odoo:
                neto = float(b.total_motor) if b else float(o.monto_total)
                desc_tot = float(b.total_descuentos + b.ncs_calculadas) if b else 0.0
                operaciones_conformes.append(
                    {
                        "so_id": o.so_id,
                        "factura_id": o.factura_id or "N/A",
                        "cliente_nombre": c_name,
                        "fecha": o.fecha.isoformat(),
                        "monto_original": float(o.monto_total),
                        "descuentos_aplicados": desc_tot,
                        "monto_neto_conciliado": neto,
                        "estado": "Conforme 100% (Pagada)",
                    }
                )

        # Separate into pending discrepancies and accepted anomalies
        discrepancias_pendientes = []
        anomalias_aceptadas = []

        for item in raw_discrepancias:
            tipo_clean = item["tipo"].replace(" ", "_").upper()
            anomalia_id = f"ANOM_{item['so_id']}_{tipo_clean}_{item['factura_id']}"
            item["anomalia_id"] = anomalia_id

            if anomalia_id in aceptadas_map:
                ac_rec = aceptadas_map[anomalia_id]
                item["motivo_aceptacion"] = ac_rec.get("motivo_aceptacion", "Revisado y Aceptado")
                item["aprobado_por"] = ac_rec.get("aprobado_por", "Dirección")
                item["timestamp_aprobacion"] = ac_rec.get("timestamp_aprobacion", "")
                anomalias_aceptadas.append(item)
            else:
                discrepancias_pendientes.append(item)

        return {
            "operaciones_conformes": operaciones_conformes,
            "discrepancias": discrepancias_pendientes,
            "discrepancias_facturas_odoo": discrepancias_facturas_odoo,
            "anomalias_aceptadas": anomalias_aceptadas,
            "resumen_auditoria": {
                "total_conformes": len(operaciones_conformes),
                "total_discrepancias": len(discrepancias_pendientes),
                "total_aceptadas": len(anomalias_aceptadas),
                "monto_discrepancia_total": round(
                    sum(d["diferencia_monto"] for d in discrepancias_pendientes), 2
                ),
            },
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/ventas")
async def get_ventas(
    vendedor: str | None = None,
    cxc_session: str | None = Cookie(default=None),
):
    """Reporte "Ventas": venta bruta/neta teórica vs real, por orden.

    Venta bruta teórica = precio de lista correcto, SIN descuentos
    (``BandejaFacturacion.precio_base_calculado``). Venta neta teórica =
    bruta menos los descuentos que el motor calcula (``total_motor``) --
    incluye los que solo se pueden calcular una vez que hay pagos
    registrados (contado/BCV-completo se computan por abono real, ver
    ``engine/discounts.py``), así que se actualiza sola cuando el runner
    recalcula tras un nuevo pago. Ambas son SIN impuestos; se les aplica
    ``config.engine.iva_rate`` (+ IGTF si está activo) para las columnas
    "+ Impuestos".

    Venta bruta real = ``amount_untaxed`` de la orden en Odoo. Venta neta
    real = ``monto_total`` (``amount_total``, YA con impuestos -- es el
    valor que usa Cuentas por Cobrar).

    Total facturado: de las facturas "posted" ligadas por ``invoice_origin``,
    en equivalente USD vía los campos ``*_signed_usd`` de Odoo. Neto = con
    impuestos menos las notas de crédito (``out_refund``) aplicadas.

    Alerta: solo si la orden YA tiene factura y lo realmente facturado neto
    quedó por debajo de lo que el motor dice que debió facturarse neto (ya
    con impuestos) -- eso es un faltante real de facturación o un descuento
    indebido. El resto (diferencias explicadas por descuentos válidos,
    redondeo, u órdenes aún sin facturar) es informativo y subsanable.
    """
    try:
        repo = get_repo()
        user = get_current_user_from_cookie(cxc_session)
        config = AppConfig.from_env()
        iva_rate = float(config.engine.iva_rate)
        igtf_rate = float(config.engine.igtf_rate) if config.engine.igtf_activo else 0.0

        ordenes = repo.all_ordenes()
        bandeja_map = {b.so_id: b for b in repo.all_bandeja()}
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {str(r.get("cliente_id", "")): r.get("nombre", "") for r in clientes_rows}

        so_names = [o.so_id for o in ordenes]
        execute = None
        try:
            execute = _connect(config.odoo)
        except Exception as e_conn:
            logger.warning("No se pudo conectar a Odoo en get_ventas: %s", e_conn)

        so_states_map: dict[str, str] = {}
        so_untaxed_map: dict[str, float] = {}
        entrega_valida_set: set[str] = set()
        facturado_antes_imp_map: dict[str, float] = {}
        facturado_con_imp_map: dict[str, float] = {}
        nc_con_imp_map: dict[str, float] = {}

        if execute and so_names:
            try:
                so_recs = execute(
                    "sale.order",
                    "search_read",
                    [[["name", "in", so_names]]],
                    {"fields": ["name", "state", "amount_untaxed"]},
                )
                for s in so_recs:
                    sname = str(s.get("name", "")).strip()
                    if sname:
                        so_states_map[sname] = str(s.get("state", "")).strip().lower()
                        so_untaxed_map[sname] = float(s.get("amount_untaxed") or 0.0)

                entrega_valida_set = get_live_delivered_not_returned(so_names, execute=execute)

                invoices = execute(
                    "account.move",
                    "search_read",
                    [
                        [
                            ["invoice_origin", "in", so_names],
                            ["state", "=", "posted"],
                            ["move_type", "in", ["out_invoice", "out_refund"]],
                        ]
                    ],
                    {
                        "fields": [
                            "invoice_origin",
                            "move_type",
                            "amount_untaxed_signed_usd",
                            "amount_total_signed_usd",
                        ]
                    },
                )
                for inv in invoices:
                    so = str(inv.get("invoice_origin", "")).strip()
                    if not so:
                        continue
                    con_imp = abs(float(inv.get("amount_total_signed_usd") or 0.0))
                    antes_imp = abs(float(inv.get("amount_untaxed_signed_usd") or 0.0))
                    if str(inv.get("move_type")) == "out_refund":
                        nc_con_imp_map[so] = nc_con_imp_map.get(so, 0.0) + con_imp
                    else:
                        facturado_con_imp_map[so] = facturado_con_imp_map.get(so, 0.0) + con_imp
                        facturado_antes_imp_map[so] = (
                            facturado_antes_imp_map.get(so, 0.0) + antes_imp
                        )
            except Exception as e_odoo:
                logger.warning("Error consultando Odoo en get_ventas: %s", e_odoo)

        items = []
        total_alertas = 0
        for o in ordenes:
            live_state = so_states_map.get(o.so_id)
            entrega_valida = o.so_id in entrega_valida_set
            if orden_excluida(o, live_state=live_state, entrega_valida=entrega_valida):
                continue
            if vendedor and vendedor.strip().lower() != str(o.vendedor_email or "").strip().lower():
                continue
            if user and user["rol"] == "ventas":
                u_name = (user["nombre"] or user["email"]).strip().lower()
                v_email = str(o.vendedor_email or "").strip().lower()
                if v_email != u_name and user["email"].strip().lower() not in v_email:
                    continue

            b = bandeja_map.get(o.so_id)
            monto_orig = float(o.monto_total)  # amount_total Odoo: YA con impuestos

            venta_bruta_real = so_untaxed_map.get(o.so_id)
            if venta_bruta_real is None:
                # Sin conexión a Odoo: estimar el subtotal a partir del total con IVA.
                venta_bruta_real = monto_orig / (1 + iva_rate) if iva_rate > -1 else monto_orig
            venta_neta_real = monto_orig

            # Sin cálculo del motor aún (bandeja no recalculada) -- mejor
            # estimación disponible es el subtotal real, sin descuento conocido.
            venta_bruta_teorica = float(b.precio_base_calculado) if b else venta_bruta_real
            venta_neta_teorica = float(b.total_motor) if b else venta_bruta_teorica

            venta_bruta_teorica_iva = venta_bruta_teorica * (1 + iva_rate)
            venta_neta_teorica_impuestos = venta_neta_teorica * (1 + iva_rate + igtf_rate)

            total_facturado_antes_impuestos = facturado_antes_imp_map.get(o.so_id, 0.0)
            total_facturado_con_impuestos = facturado_con_imp_map.get(o.so_id, 0.0)
            total_nc_aplicada = nc_con_imp_map.get(o.so_id, 0.0)
            total_facturado_neto = total_facturado_con_impuestos - total_nc_aplicada
            tiene_factura = total_facturado_con_impuestos > 0.005

            if tiene_factura:
                diferencia = round(venta_bruta_teorica_iva - total_facturado_neto, 2)
            else:
                # Sin factura todavía, "facturado neto" es 0 -- comparar
                # contra eso daría una "diferencia" falsa igual a toda la
                # venta bruta teórica. Antes de facturar, la única cifra
                # real disponible es la propia orden en Odoo: comparar la
                # venta neta teórica (con impuestos) contra el neto real de
                # la orden.
                diferencia = round(venta_neta_teorica_impuestos - venta_neta_real, 2)

            alerta = tiene_factura and (total_facturado_neto < venta_neta_teorica_impuestos - 0.05)
            if alerta:
                total_alertas += 1

            items.append(
                {
                    "so_id": o.so_id,
                    "cliente_nombre": clientes_map.get(o.cliente_id, f"Cliente ID: {o.cliente_id}"),
                    "vendedor": o.vendedor_email or "Sin Vendedor",
                    "fecha": o.fecha.isoformat(),
                    "facturada": o.facturada,
                    "venta_bruta_teorica": round(venta_bruta_teorica, 2),
                    "venta_bruta_teorica_iva": round(venta_bruta_teorica_iva, 2),
                    "venta_neta_teorica": round(venta_neta_teorica, 2),
                    "venta_neta_teorica_impuestos": round(venta_neta_teorica_impuestos, 2),
                    "venta_bruta_real": round(venta_bruta_real, 2),
                    "venta_neta_real": round(venta_neta_real, 2),
                    "total_facturado_antes_impuestos": round(total_facturado_antes_impuestos, 2),
                    "total_facturado_con_impuestos": round(total_facturado_con_impuestos, 2),
                    "total_facturado_neto": round(total_facturado_neto, 2),
                    "diferencia": diferencia,
                    "alerta": alerta,
                }
            )

        items.sort(key=lambda it: str(it["so_id"]), reverse=True)

        return {
            "items": items,
            "kpis": {
                "total_ordenes": len(items),
                "total_alertas": total_alertas,
                "iva_rate": iva_rate,
                "igtf_rate": igtf_rate,
                "igtf_activo": config.engine.igtf_activo,
                "subtotal_real_total": round(sum(i["venta_bruta_real"] for i in items), 2),
                "venta_bruta_teorica_total": round(sum(i["venta_bruta_teorica"] for i in items), 2),
                "venta_bruta_teorica_iva_total": round(
                    sum(i["venta_bruta_teorica_iva"] for i in items), 2
                ),
                "venta_neta_teorica_total": round(sum(i["venta_neta_teorica"] for i in items), 2),
                "venta_neta_real_total": round(sum(i["venta_neta_real"] for i in items), 2),
                "total_facturado_neto_total": round(
                    sum(i["total_facturado_neto"] for i in items), 2
                ),
            },
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


class AceptarAnomaliaRequest(BaseModel):
    anomalia_id: str
    so_id: str
    factura_id: str = "N/A"
    tipo_anomalia: str
    motivo_aceptacion: str = "Revisado y Aceptado en Auditoría"
    aprobado_por: str = "Dirección / Auditor"


@app.post("/api/auditoria/aceptar-anomalia")
async def post_aceptar_anomalia(req: AceptarAnomaliaRequest):
    try:
        repo = get_repo()
        row = {
            "anomalia_id": req.anomalia_id,
            "so_id": req.so_id,
            "factura_id": req.factura_id,
            "tipo_anomalia": req.tipo_anomalia,
            "motivo_aceptacion": req.motivo_aceptacion,
            "aprobado_por": req.aprobado_por,
            "timestamp_aprobacion": datetime.now().isoformat(),
        }
        repo._g.append_row("AnomaliasAceptadas", row)
        return {
            "status": "success",
            "message": "Anomalía aceptada y movida al historial de revisiones.",
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


class MarcarRecibidoRequest(BaseModel):
    pago_ids: list[str]
    recibido_por: str = "Administración"


@app.post("/api/cobranza/marcar-recibido")
async def post_marcar_recibido(
    req: MarcarRecibidoRequest, cxc_session: str | None = Cookie(default=None)
):
    try:
        user = get_current_user_from_cookie(cxc_session)
        recibido_por = req.recibido_por or (user["nombre"] if user else "Administración")
        repo = get_repo()
        pagos_rows = repo._g.read_rows("Pagos")

        now = datetime.now()
        recibo_num = f"REC-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}"

        target_pago_ids = set(req.pago_ids)
        pagos_actualizados = []

        for r in pagos_rows:
            pid = str(r.get("pago_id", "")).strip()
            if pid in target_pago_ids:
                r["recibido"] = "TRUE"
                r["numero_recibido"] = recibo_num
                r["fecha_recibido"] = now.isoformat()[:19]
                r["recibido_por"] = recibido_por
                repo._g.upsert_row("Pagos", "pago_id", r)
                pagos_actualizados.append(r)

        return {
            "status": "success",
            "numero_recibido": recibo_num,
            "fecha_recibido": now.strftime("%Y-%m-%d %H:%M"),
            "recibido_por": recibido_por,
            "pagos": pagos_actualizados,
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


def _periodo_bounds(hoy: date) -> dict[str, str]:
    """Fechas de inicio (YYYY-MM-DD) de mes/trimestre/año en curso, para acumulados."""
    mes_inicio = hoy.replace(day=1)
    trimestre_mes = ((hoy.month - 1) // 3) * 3 + 1
    trimestre_inicio = hoy.replace(month=trimestre_mes, day=1)
    anio_inicio = hoy.replace(month=1, day=1)
    return {
        "hoy": hoy.isoformat(),
        "mes": mes_inicio.isoformat(),
        "trimestre": trimestre_inicio.isoformat(),
        "anio": anio_inicio.isoformat(),
    }


@app.get("/api/reporte/diario")
async def get_reporte_diario(
    vendedor: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
):
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        lineas = repo._g.read_rows("LineasOrden")
        pagos = repo._g.read_rows("Pagos")
        tasas_rows = repo._g.read_rows("SerieTasas")

        vendedor_f = (vendedor or "").strip().lower()
        desde_f = (fecha_desde or "")[:10]
        hasta_f = (fecha_hasta or "")[:10]

        def in_range(fecha_key: str) -> bool:
            if desde_f and fecha_key < desde_f:
                return False
            return not (hasta_f and fecha_key > hasta_f)

        prod_litros_map = {}
        journal_name_map: dict[int, str] = {}
        so_states_map: dict[str, str] = {}
        entrega_valida_set: set[str] = set()
        execute = None
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            if execute:
                prods = execute(
                    "product.product",
                    "search_read",
                    [],
                    {"fields": ["id", "default_code", "name", "volume", "weight"]},
                )
                for p in prods:
                    pid = p.get("id")
                    vol = parse_decimal_safe(p.get("volume") or "0")
                    if vol == Decimal("0"):
                        vol = parse_decimal_safe(p.get("weight") or "1.0")
                    prod_litros_map[pid] = vol

                journals = execute("account.journal", "search_read", [], {"fields": ["id", "name"]})
                journal_name_map = {int(j["id"]): str(j.get("name") or "") for j in journals}

                # Estado EN VIVO de cada orden -- el espejo local (estado_orden)
                # puede quedar desactualizado si una orden se cancela en Odoo
                # y el sync incremental no la vuelve a traer (ventana delta
                # vencida, downtime, etc.). Verificado en vivo: S00162
                # ($161,679.06) aparecía como "sale" en el espejo pero está
                # "cancel" en Odoo, inflando "Ventas del Año" en ese monto.
                so_names = [o.so_id for o in ordenes]
                if so_names:
                    so_recs = execute(
                        "sale.order",
                        "search_read",
                        [[["name", "in", so_names]]],
                        {"fields": ["name", "state"]},
                    )
                    for s in so_recs:
                        sname = str(s.get("name", "")).strip()
                        if sname:
                            so_states_map[sname] = str(s.get("state", "")).strip().lower()
                    entrega_valida_set = get_live_delivered_not_returned(so_names, execute=execute)
        except Exception as e_p:
            logger.warning("Error leyendo catálogos de Odoo (litros/métodos de pago): %s", e_p)

        # Órdenes validas para este reporte: excluidas por regla global
        # (usando el estado EN VIVO de Odoo si se pudo consultar, con
        # fallback al espejo local), y filtradas por vendedor si se pidió.
        ordenes_validas = {}
        for o in ordenes:
            if orden_excluida(
                o,
                live_state=so_states_map.get(o.so_id),
                entrega_valida=o.so_id in entrega_valida_set,
            ):
                continue
            if vendedor_f and vendedor_f != str(o.vendedor_email or "").strip().lower():
                continue
            ordenes_validas[o.so_id] = o

        # 1. Ventas por Día (USD -- Monto Bruto de la Orden -- y Litros)
        ventas_por_dia = {}
        for o in ordenes_validas.values():
            fecha_key = o.fecha.isoformat()[:10]
            if not in_range(fecha_key):
                continue
            if fecha_key not in ventas_por_dia:
                ventas_por_dia[fecha_key] = {
                    "fecha": fecha_key,
                    "total_usd": Decimal("0"),
                    "litros_totales": Decimal("0"),
                    "ordenes_count": 0,
                }
            ventas_por_dia[fecha_key]["total_usd"] += o.monto_total
            ventas_por_dia[fecha_key]["ordenes_count"] += 1

        # Litros: se usa sale.report de Odoo (mismo "Volumen (L)" que Odoo
        # muestra en su propio pivot "Análisis de Ventas") para las órdenes
        # que Odoo reconoce ahí -- evita 2 bugs reales encontrados
        # comparando contra Odoo en vivo:
        #  (a) "cantidad_entregada" en la hoja local se guarda como texto
        #      "0" (nunca vacío) para líneas aún no despachadas, así que
        #      "cantidad_entregada or cantidad" NUNCA caía al pedido (el
        #      "0" ya es truthy como string) -- subestimaba litros en
        #      ~2.600 L verificado en vivo (79.201 vs 81.823 L reales).
        #  (b) el volumen de un producto puede diferir entre el valor
        #      ACTUAL en product.product.volume y el que Odoo capturó en
        #      su reporte al momento de la orden (ej. "GRASA MULTIPLE
        #      Ep200 PAILA": nuestro campo decía 15.9 L, sale.report decía
        #      18.92 L) -- leer sale.report directamente elimina también
        #      ese desfase.
        # sale.report NO incluye órdenes canceladas (ni con la excepción de
        # negocio cancelada+entregada) -- para esas (y como red de
        # seguridad si Odoo no responde para alguna orden puntual) se cae
        # al cálculo local, ya con el bug (a) corregido.
        litros_por_so: dict[str, Decimal] = {}
        if execute:
            try:
                so_names_validas = list(ordenes_validas.keys())
                if so_names_validas:
                    sr_rows = execute(
                        "sale.report",
                        "search_read",
                        [[["name", "in", so_names_validas]]],
                        {"fields": ["name", "product_volume"]},
                    )
                    for r in sr_rows:
                        sname = str(r.get("name", "")).strip()
                        if sname:
                            litros_por_so[sname] = litros_por_so.get(
                                sname, Decimal("0")
                            ) + parse_decimal_safe(str(r.get("product_volume") or "0"))
            except Exception as e_sr:
                logger.warning("Error consultando sale.report en get_reporte_diario: %s", e_sr)

        lineas_por_so: dict[str, list[dict]] = {}
        for ln in lineas:
            so_id = ln.get("so_id")
            if so_id:
                lineas_por_so.setdefault(so_id, []).append(ln)

        for so_id, o_match in ordenes_validas.items():
            fk = o_match.fecha.isoformat()[:10]
            if fk not in ventas_por_dia:
                continue
            if so_id in litros_por_so:
                ventas_por_dia[fk]["litros_totales"] += litros_por_so[so_id]
                continue
            # Fallback local (orden no encontrada en sale.report: excepción
            # cancelada+entregada, u Odoo no disponible).
            for ln in lineas_por_so.get(so_id, []):
                prod_id = int(ln.get("producto") or 0)
                qty_entregada = parse_decimal_safe(ln.get("cantidad_entregada") or "0")
                qty = (
                    qty_entregada
                    if qty_entregada > 0
                    else parse_decimal_safe(ln.get("cantidad") or "0")
                )
                l_per_unit = prod_litros_map.get(prod_id, Decimal("1.0"))
                ventas_por_dia[fk]["litros_totales"] += qty * l_per_unit

        # 2. Cobranza por Día (Desglosada por Moneda y Método) -- espejo EXACTO
        # de Odoo. Se consulta LIVE account.payment (cliente, inbound,
        # confirmado) en vez de la hoja local "Pagos": esa hoja solo
        # sincroniza pagos is_reconciled=False (changed_pagos() en
        # odoo/client.py -- existe para sugerir vinculaciones manuales, NO
        # para totalizar cobranza), así que en cuanto Odoo reconcilia un pago
        # contra una factura el sync deja de traerlo. Verificado en vivo: de
        # 882 pagos confirmados en Odoo, 673 (76%) ya estaban reconciliados y
        # el total de cobranza del dashboard quedaba ~$16,562 por debajo del
        # real (ver get_live_pagos_confirmados). Si Odoo no responde, cae a
        # la hoja local (degradado pero funcional).
        cobranza_por_dia: dict[str, dict[str, Any]] = {}

        def _acumular_pago(
            fecha_key: str, monto: Decimal, eq_usd: Decimal, moneda: str, metodo: str
        ) -> None:
            if fecha_key not in cobranza_por_dia:
                cobranza_por_dia[fecha_key] = {
                    "fecha": fecha_key,
                    "total_eq_bcv": Decimal("0"),
                    "total_eq_binance": Decimal("0"),
                    "por_moneda": {},
                    "por_metodo": {},
                    "ves_monto": Decimal("0"),
                    "ves_eq_usd": Decimal("0"),
                }
            dia = cobranza_por_dia[fecha_key]
            dia["total_eq_bcv"] += eq_usd
            dia["total_eq_binance"] += eq_usd
            dia["por_moneda"][moneda] = dia["por_moneda"].get(moneda, Decimal("0")) + monto
            dia["por_metodo"][metodo] = dia["por_metodo"].get(metodo, Decimal("0")) + eq_usd
            if moneda != "USD":
                dia["ves_monto"] += monto
                dia["ves_eq_usd"] += eq_usd

        cobranza_desde_odoo = False
        if execute:
            try:
                for p in get_live_pagos_confirmados(execute):
                    vendedor_email = str(p.get("vendedor_email") or "").strip().lower()
                    if vendedor_f and vendedor_f != vendedor_email:
                        continue
                    fecha_key = str(p.get("date") or "")[:10] or date.today().isoformat()
                    if not in_range(fecha_key):
                        continue
                    monto = parse_decimal_safe(str(p.get("amount") or "0"))
                    eq_usd = parse_decimal_safe(str(p.get("amount_ref") or "0"))
                    curr_info = p.get("currency_id")
                    moneda = (
                        curr_info[1]
                        if isinstance(curr_info, list | tuple) and len(curr_info) > 1
                        else "USD"
                    )
                    journal_info = p.get("journal_id")
                    metodo = (
                        journal_info[1]
                        if isinstance(journal_info, list | tuple) and len(journal_info) > 1
                        else "Efectivo"
                    )
                    _acumular_pago(fecha_key, monto, eq_usd, moneda, metodo)
                cobranza_desde_odoo = True
            except Exception as e_pagos:
                logger.warning(
                    "Error consultando pagos en vivo en get_reporte_diario, "
                    "usando hoja local degradada: %s",
                    e_pagos,
                )
                cobranza_por_dia = {}

        if not cobranza_desde_odoo:
            for p in pagos:
                if vendedor_f and vendedor_f != str(p.get("vendedor_email", "")).strip().lower():
                    continue
                fecha_key = str(p.get("fecha_pago", ""))[:10] or date.today().isoformat()
                if not in_range(fecha_key):
                    continue
                monto = parse_decimal_safe(p.get("monto", "0"))
                moneda = p.get("moneda", "VES")
                metodo_raw = str(p.get("metodo_pago") or p.get("forma_pago") or "").strip()
                metodo = (
                    journal_name_map.get(int(metodo_raw)) if metodo_raw.isdigit() else None
                ) or metodo_raw or "Efectivo"
                try:
                    fecha_dt = datetime.strptime(fecha_key, "%Y-%m-%d")
                except ValueError:
                    fecha_dt = datetime.now()
                bcv_rate, _binance_rate = get_rate_for_datetime(fecha_dt, tasas_rows)
                eq_usd = (
                    monto
                    if moneda == "USD"
                    else (monto / bcv_rate if bcv_rate > 0 else Decimal("0"))
                )
                _acumular_pago(fecha_key, monto, eq_usd, moneda, metodo)

        ventas_list = [
            {
                "fecha": k,
                "total_usd": float(v["total_usd"]),
                "litros_totales": float(v["litros_totales"]),
                "ordenes_count": v["ordenes_count"],
            }
            for k, v in sorted(ventas_por_dia.items(), reverse=True)
        ]

        cobranza_list = [
            {
                "fecha": k,
                "total_eq_bcv": float(v["total_eq_bcv"]),
                "total_eq_binance": float(v["total_eq_binance"]),
                "por_moneda": {m: float(val) for m, val in v["por_moneda"].items()},
                "por_metodo": {m: float(val) for m, val in v["por_metodo"].items()},
                "ves_monto": float(v["ves_monto"]),
                "ves_eq_usd": float(v["ves_eq_usd"]),
            }
            for k, v in sorted(cobranza_por_dia.items(), reverse=True)
        ]

        # 3. Acumulados (Hoy / Mes / Trimestre / Año) para las tarjetas del Dashboard
        bounds = _periodo_bounds(date.today())

        def suma_ventas_desde(inicio: str) -> dict:
            en_rango = [v for k, v in ventas_por_dia.items() if k >= inicio]
            total_usd = sum((v["total_usd"] for v in en_rango), Decimal("0"))
            litros = sum((v["litros_totales"] for v in en_rango), Decimal("0"))
            cnt = sum(v["ordenes_count"] for v in en_rango)
            return {"total_usd": float(total_usd), "litros": float(litros), "ordenes_count": cnt}

        def _merge_cobranza(en_rango: list[dict]) -> dict:
            total_bcv = sum((v["total_eq_bcv"] for v in en_rango), Decimal("0"))
            total_binance = sum((v["total_eq_binance"] for v in en_rango), Decimal("0"))
            ves_monto = sum((v["ves_monto"] for v in en_rango), Decimal("0"))
            ves_eq_usd = sum((v["ves_eq_usd"] for v in en_rango), Decimal("0"))
            por_metodo: dict[str, Decimal] = {}
            for v in en_rango:
                for metodo, monto_m in v["por_metodo"].items():
                    por_metodo[metodo] = por_metodo.get(metodo, Decimal("0")) + monto_m
            return {
                "total_eq_bcv": float(total_bcv),
                "total_eq_binance": float(total_binance),
                "ves_monto": float(ves_monto),
                "ves_eq_usd": float(ves_eq_usd),
                "por_metodo": {m: float(val) for m, val in por_metodo.items()},
            }

        def suma_cobranza_desde(inicio: str) -> dict:
            en_rango = [v for k, v in cobranza_por_dia.items() if k >= inicio]
            return _merge_cobranza(en_rango)

        def suma_ventas_dia(dia: str) -> dict:
            v = ventas_por_dia.get(dia)
            if not v:
                return {"total_usd": 0.0, "litros": 0.0, "ordenes_count": 0}
            return {
                "total_usd": float(v["total_usd"]),
                "litros": float(v["litros_totales"]),
                "ordenes_count": v["ordenes_count"],
            }

        def suma_cobranza_dia(dia: str) -> dict:
            v = cobranza_por_dia.get(dia)
            return _merge_cobranza([v] if v else [])

        resumen = {
            "ventas": {
                "hoy": suma_ventas_dia(bounds["hoy"]),
                "mes": suma_ventas_desde(bounds["mes"]),
                "trimestre": suma_ventas_desde(bounds["trimestre"]),
                "anio": suma_ventas_desde(bounds["anio"]),
            },
            "cobranza": {
                "hoy": suma_cobranza_dia(bounds["hoy"]),
                "mes": suma_cobranza_desde(bounds["mes"]),
                "trimestre": suma_cobranza_desde(bounds["trimestre"]),
                "anio": suma_cobranza_desde(bounds["anio"]),
            },
        }

        vendedores = sorted(
            {
                str(o.vendedor_email)
                for o in ordenes
                if o.vendedor_email
                and not orden_excluida(
                    o,
                    live_state=so_states_map.get(o.so_id),
                    entrega_valida=o.so_id in entrega_valida_set,
                )
            }
        )

        return {
            "ventas_diarias": ventas_list,
            "cobranza_diaria": cobranza_list,
            "resumen": resumen,
            "vendedores": vendedores,
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e
