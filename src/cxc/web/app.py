import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import time
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
from cxc.engine.cxc_routing import BandejaDestino, clasificar_estado_cxc
from cxc.engine.equivalents import (
    calcular_equivalentes,
    valor_pagado_bcv_usd,
    valor_pagado_binance_usd,
)
from cxc.engine.historical_pricing import es_orden_historica
from cxc.engine.runner import EngineRunner
from cxc.models import (
    Cliente,
    EstadoVinculacion,
    Moneda,
    OrdenVenta,
    TipoTasa,
    Vinculacion,
    set_marca_fallback,
)
from cxc.odoo.client import PAGO_ESTADOS_CONFIRMADOS, OdooXmlRpcReader, _connect
from cxc.odoo.price import FallbackFichaConfig, OdooPriceResolver
from cxc.reconciliation.reconcile import OdooFacturasReader, Reconciler
from cxc.repositories import Repository
from cxc.sheets import serde
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
        (live_state if live_state is not None else str(getattr(o, "estado_orden", "sale") or ""))
        .strip()
        .lower()
    )
    if st not in ESTADOS_ORDEN_EXCLUIDOS:
        return False
    return not (st in ("cancel", "cancelled") and entrega_valida)


# Caché por-orden de estado en vivo (agosto 2026, plan de reducción de
# llamadas a Odoo) -- get_resumen, get_auditoria y el reporte diario piden
# el estado en vivo de conjuntos de órdenes que se SOLAPAN (muchas órdenes
# aparecen en más de una página) pero rara vez coinciden como lista
# completa, así que cachear por el nombre de SO individual (no por el
# conjunto completo pedido) es lo que realmente aprovecha el solapamiento.
# TTL deliberadamente corto (45s, MUY por debajo de la ventana de 48h que
# causó el bug real de la orden cancelada de $161,679.06 que motivó este
# chequeo en vivo) -- reduce llamadas duplicadas cuando varias páginas
# cargan casi al mismo tiempo, sin resucitar el riesgo de estado stale.
_SO_STATE_CACHE: dict[str, tuple[str, float]] = {}
_SO_STATE_CACHE_TTL = 45.0


def get_live_so_states(so_names: list[str]) -> dict[str, str]:
    """Estado EN VIVO de cada sale.order en Odoo, para usar con orden_excluida.

    El espejo local (estado_orden) puede quedar desactualizado si una orden
    se cancela/revierte en Odoo y el sync incremental no la vuelve a traer
    (ventana delta de 48h vencida, downtime del servidor, etc.) -- verificado
    en vivo: una orden cancelada de $161,679.06 seguía contando como venta
    confirmada porque el espejo nunca se refrescó. Best-effort: si Odoo no
    responde para las órdenes sin caché fresca, esas quedan fuera del dict
    devuelto (el llamador, ``orden_excluida``, cae a su estado local SOLO
    para esas -- no para el resto, a diferencia de un fallo total antes).
    """
    if not so_names:
        return {}
    now_ts = time.time()
    result: dict[str, str] = {}
    faltantes: list[str] = []
    for name in so_names:
        cached = _SO_STATE_CACHE.get(name)
        if cached is not None and now_ts - cached[1] < _SO_STATE_CACHE_TTL:
            result[name] = cached[0]
        else:
            faltantes.append(name)
    if not faltantes:
        return result
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        if not execute:
            return result
        so_recs = execute(
            "sale.order",
            "search_read",
            [[["name", "in", faltantes]]],
            {"fields": ["name", "state"]},
        )
        for s in so_recs:
            name = str(s.get("name", "")).strip()
            if not name:
                continue
            state = str(s.get("state", "")).strip().lower()
            result[name] = state
            _SO_STATE_CACHE[name] = (state, now_ts)
        return result
    except Exception as e:
        logger.warning("Error consultando estado en vivo de órdenes en Odoo: %s", e)
        return result


# Caché por-orden de entregas (agosto 2026, mismo patrón y misma
# justificación que _SO_STATE_CACHE -- ver su comentario): cada valor es
# (es_entrega_valida, fecha_entrega o None, timestamp). TTL corto (45s),
# suficiente para absorber varias páginas cargando casi al mismo tiempo sin
# arriesgar datos de entrega desactualizados.
_ENTREGA_CACHE: dict[str, tuple[bool, str | None, float]] = {}
_ENTREGA_CACHE_TTL = 45.0


def get_live_entregas_info(
    so_names: list[str], execute: Any = None
) -> tuple[set[str], dict[str, str]]:
    """(delivered_not_returned, fecha_entrega_map) -- una sola consulta a

    stock.picking para ambas cosas. ``fecha_entrega_map`` es la fecha
    (YYYY-MM-DD) de la entrega ALM/OUT más reciente por orden, mismo
    criterio que ya usa el reporte de CxC (``get_reporte_saldos``); se
    llena para TODA orden con una entrega saliente, sin importar si
    terminó devuelta (a diferencia del set ``delivered_not_returned``, que
    sí excluye devueltas). Acepta un `execute` ya conectado para reusar la
    conexión del llamador; si no se provee, abre una propia. Best-effort:
    ante cualquier error devuelve lo que ya se pudo resolver desde caché
    (nunca menos que antes de intentar).
    """
    if not so_names:
        return set(), {}
    now_ts = time.time()
    delivered: set[str] = set()
    fecha_entrega_map: dict[str, str] = {}
    faltantes: list[str] = []
    for name in so_names:
        cached = _ENTREGA_CACHE.get(name)
        if cached is not None and now_ts - cached[2] < _ENTREGA_CACHE_TTL:
            es_valida, fecha, _ts = cached
            if es_valida:
                delivered.add(name)
            if fecha:
                fecha_entrega_map[name] = fecha
        else:
            faltantes.append(name)
    if not faltantes:
        return delivered, fecha_entrega_map

    if execute is None:
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
        except Exception as e:
            logger.warning("Error conectando a Odoo en get_live_entregas_info: %s", e)
            return delivered, fecha_entrega_map
    if not execute:
        return delivered, fecha_entrega_map
    try:
        so_records = execute(
            "sale.order",
            "search_read",
            [[["name", "in", faltantes]]],
            {"fields": ["name", "picking_ids"]},
        )
        picking_to_so: dict[int, str] = {}
        for s in so_records:
            sname = str(s.get("name", "")).strip()
            p_ids = s.get("picking_ids") or []
            if sname and isinstance(p_ids, list | tuple):
                for pid in p_ids:
                    picking_to_so[pid] = sname

        # Faltantes sin ningún picking (nunca despachadas): se cachean como
        # "sin entrega" para no volver a pedirlas en cada llamada dentro del
        # TTL -- mismo resultado final que la versión sin caché (quedaban
        # fuera de `delivered` y de `fecha_entrega_map`).
        con_picking = set(picking_to_so.values())
        for name in faltantes:
            if name not in con_picking:
                _ENTREGA_CACHE[name] = (False, None, now_ts)
        if not picking_to_so:
            return delivered, fecha_entrega_map

        pickings = execute(
            "stock.picking",
            "search_read",
            [[["id", "in", list(picking_to_so.keys())], ["state", "=", "done"]]],
            {"fields": ["id", "picking_type_code", "return_id", "date_done"]},
        )
        delivered_faltantes: set[str] = set()
        returned_faltantes: set[str] = set()
        fecha_faltantes: dict[str, str] = {}
        for p in pickings:
            so_name = picking_to_so.get(p["id"])
            if not so_name:
                continue
            if bool(p.get("return_id")) or str(p.get("picking_type_code")) == "incoming":
                returned_faltantes.add(so_name)
            elif str(p.get("picking_type_code")) == "outgoing":
                delivered_faltantes.add(so_name)
                dt_done = p.get("date_done")
                if dt_done:
                    dt_str = str(dt_done).split(" ")[0]
                    if so_name not in fecha_faltantes or dt_str > fecha_faltantes[so_name]:
                        fecha_faltantes[so_name] = dt_str

        for name in con_picking:
            es_valida = name in delivered_faltantes and name not in returned_faltantes
            fecha = fecha_faltantes.get(name)
            _ENTREGA_CACHE[name] = (es_valida, fecha, now_ts)
            if es_valida:
                delivered.add(name)
            if fecha:
                fecha_entrega_map[name] = fecha
        return delivered, fecha_entrega_map
    except Exception as e:
        logger.warning("Error consultando entregas en get_live_entregas_info: %s", e)
        return delivered, fecha_entrega_map


def get_live_delivered_not_returned(so_names: list[str], execute: Any = None) -> set[str]:
    """SO names con entrega ALM/OUT (stock.picking saliente, estado "done") y SIN devolución.

    Es la excepción de negocio de ESTADOS_ORDEN_EXCLUIDOS: una orden CANCELADA
    en Odoo después de que la mercancía ya salió de almacén sigue siendo una
    venta real -- cancelar la SO no deshace el despacho.
    """
    delivered, _ = get_live_entregas_info(so_names, execute)
    return delivered


def _parse_payment_term_days(t_name: str) -> int:
    """Días de crédito otorgados según el nombre del payment term de Odoo.

    Fuente única (agosto 2026) -- usada por ``get_ventas`` y
    ``_get_reporte_saldos_sync``; antes esta última tenía su propia copia
    idéntica como closure local (``parse_term_days``)."""
    if not t_name:
        return 0
    t_low = t_name.lower().strip()
    if "immediate" in t_low or "contado" in t_low:
        return 0
    m = re.search(r"(\d+)\s*(dias|días|days|day|día)", t_low)
    return int(m.group(1)) if m else 0


# Caché por-partner (agosto 2026, mismo patrón que _SO_STATE_CACHE/
# _ENTREGA_CACHE): get_live_pagos_confirmados y get_live_pagos_conciliados
# -- ambas invocadas típicamente en la MISMA carga de la página de Cobranza
# -- llaman resolve_vendedores_por_partner con conjuntos de partner_ids que
# suelen solaparse mucho (los mismos clientes aparecen en pagos confirmados
# y conciliados). TTL más largo que el de estado/entregas (5 min) porque la
# asignación vendedor-cliente es configuración administrativa, no un
# estado de negocio que deba verse en vivo.
_VENDEDOR_POR_PARTNER_CACHE: dict[int, tuple[str, float]] = {}
_VENDEDOR_POR_PARTNER_CACHE_TTL = 300.0


def resolve_vendedores_por_partner(execute: Any, partner_ids: set[int]) -> dict[int, str]:
    """Email del vendedor (res.users.login) asignado a cada partner (res.partner.user_id).

    Mismo criterio que ``OdooXmlRpcReader._vendedor_por_partner`` (odoo/client.py),
    reimplementado aquí sobre el ``execute`` crudo para no depender de esa clase.
    """
    if not partner_ids:
        return {}
    now_ts = time.time()
    result: dict[int, str] = {}
    faltantes: list[int] = []
    for pid in partner_ids:
        cached = _VENDEDOR_POR_PARTNER_CACHE.get(pid)
        if cached is not None and now_ts - cached[1] < _VENDEDOR_POR_PARTNER_CACHE_TTL:
            result[pid] = cached[0]
        else:
            faltantes.append(pid)
    if not faltantes:
        return result
    try:
        partners = execute(
            "res.partner", "read", [faltantes], {"fields": ["id", "user_id"]}
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
        partners_con_dato = {int(p["id"]) for p in partners}
        for p in partners:
            u = p.get("user_id")
            uid = u[0] if isinstance(u, list | tuple) and u else None
            login = logins.get(int(uid), "") if uid else ""
            pid = int(p["id"])
            result[pid] = login
            _VENDEDOR_POR_PARTNER_CACHE[pid] = (login, now_ts)
        # Partners pedidos pero que Odoo no devolvió (id inexistente/archivado
        # sin acceso): cachear "" para no volver a pedirlos en cada llamada.
        for pid in faltantes:
            if pid not in partners_con_dato:
                result[pid] = ""
                _VENDEDOR_POR_PARTNER_CACHE[pid] = ("", now_ts)
        return result
    except Exception as e:
        logger.warning("Error resolviendo vendedores por partner: %s", e)
        return result


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

    Bug real (orden S00010 y 113 pagos más, ~12% del total en producción):
    ``is_reconciled`` en el DOMINIO excluía pagos que SÍ están vinculados
    (``reconciled_invoice_ids`` poblado, factura con ``amount_residual=0``
    en Odoo) pero cuyo ``is_reconciled`` calculado da ``False`` -- Odoo
    distingue "conciliado con extracto bancario" (lo que mueve
    ``is_reconciled``) de "aplicado a factura vía Registrar Pago"
    (``payment_state='in_payment'``, lo que realmente nos interesa acá).
    Se filtra ahora por ``reconciled_invoice_ids`` no vacío en Python (no
    hay forma robusta de expresar "many2many no vacío" en el dominio XML-RPC
    de esta versión de Odoo -- se probó y falla con
    ``TypeError: 'NotImplementedType' object is not iterable``).
    """
    pagos_todos = execute(
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
    pagos = [p for p in pagos_todos if p.get("reconciled_invoice_ids")]
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


def _pagos_bcv_binance_por_orden(
    execute: Any,
    invoice_ids_all: list[int],
    inv_id_to_so: dict[int, str],
    es_historica_map: dict[str, bool],
    tasas_rows: list[dict],
    hist_rows: list[dict],
    facturado_con_imp_por_so: dict[str, float] | None = None,
) -> dict[str, dict[str, float]]:
    """Monto pagado por orden convertido a USD con la tasa BCV y con la tasa

    Binance del DÍA DEL PAGO, cada una por separado (a diferencia de
    ``_pagos_odoo_por_orden``/``_pagos_por_so_desde_cobranza``, que hoy dan
    el mismo número para ambas rutas -- limitación conocida, documentada,
    NO corregida ahí a propósito porque ``/api/reporte-saldos`` se va a
    sustituir). Pedido explícito del usuario para las columnas "Monto
    pagado BCV"/"Monto pagado USD" de Ventas.

    Si la orden cae en la ventana de la Lista Histórica de Auditoría
    (Euro), la ruta BCV usa la tasa BCV-Euro del día (mismo criterio que
    ``resolver_tasa_bcv_vinculacion`` para Vinculaciones nuevas) -- Binance
    sigue usando la tasa Binance normal, el Euro solo sustituye la
    referencia BCV.

    Prorrateo (agosto 2026, pedido explícito del usuario -- hallazgo real
    al construir el reporte de candidatos a cierre de Diferencial
    Cambiario: sin esto, órdenes de clientes con varios pedidos y un pago
    grande mostraban 300-800%+ "pagado"): si un pago reconcilia facturas de
    VARIAS órdenes, el monto se reparte entre ellas proporcional al monto
    facturado (con impuestos) de cada una (``facturado_con_imp_por_so``,
    ya calculado por el llamador -- no hace falta una consulta nueva a
    Odoo). Odoo no expone el monto exacto reconciliado por factura a nivel
    de ``account.payment`` (eso vive en ``account.partial.reconcile``, más
    costoso de consultar); esto es una aproximación razonable y sin config
    nueva. Si ninguna de las órdenes tiene monto facturado (peso 0), se
    reparte equitativo como último recurso. Con una sola orden en ``sos``
    el resultado es idéntico a antes (100% a esa orden). NOTA: esto NO
    cambia ``get_live_pagos_conciliados``/``_pagos_odoo_por_orden`` --
    esas dos siguen sin prorratear, por diseño, documentado ahí.
    """
    result: dict[str, dict[str, float]] = {}
    if not execute or not invoice_ids_all:
        return result
    try:
        payments = execute(
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
    except Exception as e:
        logger.warning("Error consultando account.payment en _pagos_bcv_binance_por_orden: %s", e)
        return result

    for p in payments:
        amt = Decimal(str(p.get("amount") or "0"))
        if amt <= Decimal("0"):
            continue
        curr_raw = p.get("currency_id")
        curr = curr_raw[1] if isinstance(curr_raw, list | tuple) and len(curr_raw) > 1 else "USD"
        fecha_str = str(p.get("date") or "")[:10]
        try:
            fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d") if fecha_str else datetime.now()
        except ValueError:
            fecha_dt = datetime.now()

        sos = {
            inv_id_to_so[int(rid)]
            for rid in (p.get("reconciled_invoice_ids") or [])
            if int(rid) in inv_id_to_so
        }
        if not sos:
            continue

        if curr == "USD":
            monto_bcv = amt
            monto_binance = amt
        else:
            bcv_normal, tasa_binance = get_rate_for_datetime(fecha_dt, tasas_rows)
            tasa_bcv = bcv_normal
            if any(es_historica_map.get(so, False) for so in sos):
                tasa_eur = get_bcv_euro_rate_for_datetime(fecha_dt, tasas_rows)
                if not tasa_eur or tasa_eur <= Decimal("0"):
                    tasa_eur = get_eur_rate_for_date(fecha_dt.date(), hist_rows)
                if tasa_eur and tasa_eur > Decimal("0"):
                    tasa_bcv = tasa_eur
            monto_bcv = amt / tasa_bcv if tasa_bcv > Decimal("0") else Decimal("0")
            monto_binance = amt / tasa_binance if tasa_binance > Decimal("0") else Decimal("0")

        pesos_por_so = facturado_con_imp_por_so or {}
        pesos = {so: max(0.0, pesos_por_so.get(so, 0.0)) for so in sos}
        total_peso = sum(pesos.values())
        if total_peso <= 0.0:
            pesos = dict.fromkeys(sos, 1.0)
            total_peso = float(len(sos))

        for so in sos:
            frac = pesos[so] / total_peso
            entry = result.setdefault(
                so, {"monto_pagado_bcv": 0.0, "monto_pagado_usd_binance": 0.0}
            )
            entry["monto_pagado_bcv"] += float(monto_bcv) * frac
            entry["monto_pagado_usd_binance"] += float(monto_binance) * frac

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

    Fase 0 (plan de arquitectura de pagos, agosto 2026, pedido explícito
    del usuario): en el caso simple, además de corregir el ``so_id`` si
    diverge, esta función es la que PROMUEVE la Vinculación de
    ``PENDIENTE`` a ``CONCILIADO`` -- antes esta rama solo hacía ``continue``
    cuando ya coincidía, sin tocar el estado, así que ninguna Vinculación
    llegaba nunca a ``CONCILIADO`` (motor.runner._abonos() la sigue
    tratando como no confirmada para siempre). Se llama justo antes de
    ``runner.run_all()`` en ``recalculate_all_orders`` -- la promoción
    dispara el recálculo completo de la orden en la misma corrida, así el
    descuento retroactivo (ventana evaluada contra la fecha REAL del
    abono, nunca la de confirmación -- ver ``within_window`` en
    discounts.py) queda reflejado de inmediato, aunque la ventana de pago
    ya haya cerrado en el calendario.

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
            # Ya coincide -- Odoo confirma que la asignación es correcta.
            # Promueve cualquier Vinculación todavía PENDIENTE (manual
            # reciente o auto-FIFO) a CONCILIADO; nada que hacer con las
            # que ya estaban confirmadas.
            for v in vincs_locales:
                if v.estado != EstadoVinculacion.CONCILIADO:
                    vincs_a_actualizar.append(
                        dataclasses_replace(v, estado=EstadoVinculacion.CONCILIADO)
                    )
            continue

        if len(vincs_locales) == 1 and len(so_ids_odoo) == 1:
            v = vincs_locales[0]
            so_id_nuevo = next(iter(so_ids_odoo))
            vincs_a_actualizar.append(
                dataclasses_replace(
                    v, so_id=so_id_nuevo, estado=EstadoVinculacion.CONCILIADO
                )
            )
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


def _detectar_vinculaciones_pendientes_a_revisar(
    repo: Any, dias_umbral_facturada: int = 2
) -> list[dict[str, Any]]:
    """Fase 2 (plan de arquitectura de pagos, agosto 2026, pedido explícito

    del usuario): marca en Auditoría las Vinculaciones ``PENDIENTE`` (Fase
    1, auto-FIFO o manuales) que ya ameritan revisión -- el criterio es
    DISTINTO según si la orden ya está facturada:

    - **Facturada**: umbral configurable (``dias_umbral_facturada``,
      default 2 días) desde que se creó la Vinculación (``timestamp_
      registro``) sin que Odoo la haya confirmado (``_resincronizar_
      vinculaciones_con_odoo`` la habría promovido a ``CONCILIADO``) --
      señal de que la sugerencia FIFO probablemente esté mal.
    - **NO facturada**: sin plazo -- se marca solo cuando el saldo real
      pendiente de la orden (``_get_saldos_reales_por_so_sync``, ya neta
      lo que estas Vinculaciones PENDIENTE aportan) ya llegó a ~0. Ahí SÍ
      hay algo que decidir (facturar); mientras no llegue, esperar sin
      alerta es el comportamiento correcto -- eso es la razón de ser del
      sistema, no una anomalía.

    Deduplicado por día -- no reinserta la misma fila de auditoría en
    cada ciclo de 5 minutos si la condición sigue vigente.
    """
    vincs_pendientes = [
        v for v in repo.all_vinculaciones() if v.estado == EstadoVinculacion.PENDIENTE
    ]
    if not vincs_pendientes:
        return []

    ordenes_map = {o.so_id: o for o in repo.all_ordenes()}
    saldos_reales = _get_saldos_reales_por_so_sync() or {}
    hoy = date.today()

    try:
        existing_audit_rows = repo.all_auditoria()
    except Exception:
        existing_audit_rows = []
    today_str = hoy.isoformat()
    existing_audit_keys = {
        (r.get("so_id", ""), r.get("tipo_auditoria", ""))
        for r in existing_audit_rows
        if str(r.get("timestamp_audit", ""))[:10] == today_str
    }

    revisar: list[dict[str, Any]] = []
    for v in vincs_pendientes:
        o = ordenes_map.get(v.so_id)
        if o is None:
            continue

        if o.facturada:
            creada = v.timestamp_registro
            if creada is None:
                continue
            dias_pendiente = (hoy - creada.date()).days
            if dias_pendiente < dias_umbral_facturada:
                continue
            motivo = (
                f"Vinculación PENDIENTE hace {dias_pendiente} día(s) "
                f"(umbral: {dias_umbral_facturada}) sin confirmar por Odoo -- "
                "orden ya facturada."
            )
        else:
            saldo = saldos_reales.get(v.so_id)
            if saldo is None or saldo > 0.05:
                continue  # aún no cubre el neto -- esperar es lo correcto, sin alerta.
            motivo = (
                "El saldo pendiente por confirmar ya cubre el neto de la orden "
                "(sin facturar todavía) -- lista para facturar."
            )

        revisar.append(
            {
                "vinc_id": v.vinc_id,
                "pago_id": v.pago_id,
                "so_id": v.so_id,
                "facturada": o.facturada,
                "motivo": motivo,
            }
        )

    nuevas_rows = [
        r
        for r in revisar
        if (r["so_id"], "vinculacion_pendiente_revisar") not in existing_audit_keys
    ]
    if nuevas_rows and hasattr(repo, "append_auditoria_rows"):
        ahora = datetime.now()
        audit_rows = [
            {
                "audit_id": f"VINC_STALE_{r['vinc_id']}_{today_str}",
                "so_id": r["so_id"],
                "tipo_auditoria": "vinculacion_pendiente_revisar",
                "motor_calcula_usd": "",
                "odoo_registrado_usd": "",
                "diferencia_usd": "",
                "detalle_odoo": "",
                "detalle_motor": r["motivo"],
                "estado": "pendiente_revision",
                "revisado_por": "",
                "timestamp_audit": ahora.isoformat(),
            }
            for r in nuevas_rows
        ]
        try:
            repo.append_auditoria_rows(audit_rows)
        except Exception as e_aud:
            logger.warning("Error guardando auditoría de Vinculaciones pendientes: %s", e_aud)

    return revisar


def _pagado_confirmado_por_so(vincs: list[Vinculacion]) -> dict[str, Decimal]:
    """Suma de Vinculaciones ``CONCILIADO`` por ``so_id`` -- Fase 0

    (arquitectura de pagos, agosto 2026, pedido explícito del usuario):
    solo lo que Odoo ya confirmó cuenta como "pagado" para decidir si una
    orden está saldada / sale de CxC activa. Una Vinculación ``PENDIENTE``
    (sugerencia FIFO automática de la Fase 1 sin confirmar todavía, o una
    vinculación manual muy reciente) NO cuenta -- mismo criterio que ya
    aplica el motor de descuentos (``EngineRunner._abonos``) desde la
    Fase 0.

    Hallazgo real (pedido explícito del usuario, 2026-08-21): varios
    endpoints (``get_resumen``, ``get_ordenes_pendientes``, ``_get_
    reporte_saldos_sync``, ``get_bandeja_facturacion``, ``_get_ventas_
    sync``) sumaban TODAS las Vinculaciones sin filtrar por estado --
    con la Fase 1 ya corriendo en producción (203 Vinculaciones
    ``PENDIENTE`` creadas automáticamente), esas órdenes se mostraban
    "pagadas" en Ventas/Bandeja/Reporte basándose en una adivinanza sin
    confirmar, mientras el motor (ya corregido) correctamente no les
    otorgaba ningún descuento por la misma razón -- exactamente la
    inconsistencia que la Fase 0 quería evitar.

    NO se usa en todos lados a propósito -- dos excepciones deliberadas,
    documentadas en su propio código: ``_get_conciliaciones_sugerencias_
    sync`` (necesita TODAS, para no volver a sugerir dinero que un pago ya
    tiene reclamado aunque sea de forma provisional) y ``get_auditoria``
    (su chequeo de discrepancias ya documentó por qué filtrar generaba
    falsos positivos con el significado anterior de "pendiente" -- antes
    de la Fase 1, era solo un estado transitorio de segundos/minutos, no
    una adivinanza que puede tardar en confirmarse).
    """
    result: dict[str, Decimal] = {}
    for v in vincs:
        if v.estado != EstadoVinculacion.CONCILIADO:
            continue
        result[v.so_id] = result.get(v.so_id, Decimal("0")) + v.monto_aplicado
    return result


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
    marca_fallback: str = "GLOBAL OIL"
    fallback_industrial_ajuste_pct: float = 0.04


class FilaMapeoRequest(BaseModel):
    moneda: str = ""  # "usd" | "ves" | ""
    categoria: str = ""  # "industrial" | "comercial" | ""
    vigente: bool = False


class PricelistMapeoUnificadoRequest(BaseModel):
    mapeo: dict[str, FilaMapeoRequest] = {}
    historical_pricelist_enabled: bool = True


class VincularMasivoRequest(BaseModel):
    items: list[VinculacionRequest]


class TasaBinanceEditRequest(BaseModel):
    tasa_binance: float
    editado_por: str = ""


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
    requiere_pago_previo: bool = False
    aplica_a: str = "linea"
    descripcion: str = ""


class ExclusionRequest(BaseModel):
    regla_tipo_a: str
    regla_tipo_b: str
    activo: bool = True


class ProntoPagoRequest(BaseModel):
    marca: str = "*"
    categoria: str = "*"
    ventana_pago_tipo: str = "entrega"
    ventana_pago_dias: int = 3
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
    requiere_pago_previo: bool = True
    aplica_a: str = "linea"
    descripcion: str = ""


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
    requiere_pago_previo: bool = False
    aplica_a: str = "linea"
    descripcion: str = ""


class EliminarDescuentoRequest(BaseModel):
    tabla: str
    regla_id: str


def _primer_id_activo(execute: Any, ids: list[int]) -> int | None:
    """Evita que una lista de precios ARCHIVADA en Odoo quede como

    "primaria" solo por aparecer primero en la config de texto (bug real,
    agosto 2026: ``valid_pricelists_usd = "7,8"`` con #7 archivada y con 86
    reglas de precio viejas que YA difieren de las de #8 -- ej. producto
    1063: $92.24 en #7 vs $102.59 en #8 -- el motor calculaba con precios
    obsoletos sin que nadie lo notara). Devuelve el primer id de ``ids``
    que sigue activo en Odoo, preservando el orden dado; si ninguno está
    activo o falla la consulta, cae al primero de la lista original (mismo
    comportamiento que antes, nunca empeora nada).
    """
    if not ids:
        return None
    try:
        activos = execute(
            "product.pricelist",
            "search_read",
            [[["id", "in", ids]]],
            {"fields": ["id", "active"], "context": {"active_test": False}},
        )
        activos_set = {a["id"] for a in activos if a.get("active")}
    except Exception:
        return ids[0]
    for i in ids:
        if i in activos_set:
            return i
    return ids[0]


def get_ui_pricelist_ids(repo) -> tuple[list[int], list[int]]:
    try:
        meta = repo.all_config()

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
    min_cajas: int = 2
    max_cajas: int = 4
    unidad_medida: str | None = "CAJAS"
    tipo_beneficio: str | None = "descuento"
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True
    requiere_pago_previo: bool = False
    aplica_a: str = "linea"
    descripcion: str = ""
    ventana_pago_tipo: str = "vencimiento"
    ventana_pago_dias: int = 3


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
    requiere_pago_previo: bool = False
    aplica_a: str = "linea"
    descripcion: str = ""


class DiferencialCambiarioRequest(BaseModel):
    nombre: str
    tipo_diferencial: str  # 'fijo_35_ves_usd' | 'equiparar_binance' | 'candidato_cierre_factura'
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
    requiere_pago_previo: bool = True
    aplica_a: str = "linea"
    descripcion: str = ""


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
    requiere_pago_previo: bool = False
    aplica_a: str = "linea"
    descripcion: str = ""


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
                raise RuntimeError("REPO_BACKEND=postgres requiere configurar DATABASE_URL")
            _repo_cache = PostgresRepository.from_url(config.database.url)
        else:
            _repo_cache = _fresh_sheets_repo(config)
    return _repo_cache


def _all_lineas_rows(repo) -> list[dict]:
    """LineasOrden del backend activo, como dict de strings (mismo shape
    que ``serde.linea_to_row``)."""
    return [serde.linea_to_row(ln) for ln in repo.all_lineas()]


def _all_pagos_rows(repo) -> list[dict]:
    """Pagos del backend activo, como dict de strings (mismas columnas
    espejo que ``serde.pago_to_row`` -- sin las columnas humanas de
    cobranza, ver ``_all_pagos_rows_con_recibido``)."""
    return [serde.pago_to_row(p) for p in repo.all_pagos()]


def _all_serie_tasas_rows(repo) -> list[dict]:
    """SerieTasas del backend activo, como dict de strings -- mismo formato
    que antes daba ``GspreadGateway.read_rows("SerieTasas")`` -- para no
    tener que tocar el resto del código (parseo con ``.get()``/strptime)
    que las consume, sea cual sea el backend."""
    return [serde.serie_to_row(f) for f in repo.all_serie_tasas()]


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
    """Tasa BCV/Binance más cercana a ``dt``.

    Orden de fuentes (nunca un default hardcodeado -- auditoría de tasas
    históricas, agosto 2026): 1) ``SerieTasas`` (scraper horario, solo
    cubre desde que el cron corre -- en producción, desde 2026-07-25) si
    tiene una captura del MISMO día que ``dt``; 2) si no, cae a
    ``TasasHistoricasAuditoria`` (tabla poblada con la tasa BCV real de
    Odoo día a día desde 2026-02-01, y Binance real donde ``SerieTasas`` sí
    la capturó, o estimada con el diferencial de mercado donde no --
    ``scripts/cargar_tasas_historicas.py``); 3) como ÚLTIMO recurso, si
    ninguna fuente tiene NADA (no debería pasar tras la siembra inicial),
    usa la fila de ``SerieTasas`` más cercana aunque sea de otro día.
    """
    repo = get_repo()
    if rows is None:
        rows = _all_serie_tasas_rows(repo)

    fecha_str = dt.date().isoformat()
    if rows:
        closest_row = _closest_serie_row(dt, rows)
        if closest_row and str(closest_row.get("timestamp", ""))[:10] == fecha_str:
            return parse_decimal_safe(closest_row.get("tasa_bcv")), parse_decimal_safe(
                closest_row.get("tasa_binance")
            )

    try:
        hist_rows = repo.all_tasas_historicas_auditoria()
    except Exception as e_hist:
        logger.warning(
            "Error leyendo TasasHistoricasAuditoria en get_rate_for_datetime: %s", e_hist
        )
        hist_rows = []
    bcv_hist = get_bcv_usd_rate_for_date(dt.date(), hist_rows)
    binance_hist = get_binance_rate_for_date(dt.date(), hist_rows)
    if bcv_hist is not None and binance_hist is not None:
        return bcv_hist, binance_hist

    if rows:
        closest_row = _closest_serie_row(dt, rows)
        if closest_row:
            return parse_decimal_safe(closest_row.get("tasa_bcv")), parse_decimal_safe(
                closest_row.get("tasa_binance")
            )

    logger.warning(
        "Sin ninguna tasa disponible (SerieTasas ni TasasHistoricasAuditoria) para %s -- "
        "revisar que la siembra inicial se haya corrido.",
        fecha_str,
    )
    return Decimal("36.5"), Decimal("38.0")


def get_bcv_usd_rate_for_date(fecha: date, rows: list[dict]) -> Decimal | None:
    """Tasa BCV-USD oficial del día EXACTO `fecha`, desde ``TasasHistoricasAuditoria``.

    Mismo criterio que ``get_binance_rate_for_date``/``get_eur_rate_for_date``
    (lookup por día exacto, sin caer a otro día)."""
    fecha_str = fecha.isoformat()
    for r in rows:
        if str(r.get("fecha", ""))[:10] == fecha_str:
            val = parse_decimal_safe(r.get("tasa_bcv_usd", "0"))
            if val > Decimal("0"):
                return val
    return None


def get_bcv_euro_rate_for_datetime(dt: datetime, rows: list[dict]) -> Decimal | None:
    """Tasa BCV-EUR (SerieTasa.tasa_bcv_euro) más cercana a `dt`, MISMO DÍA.

    None si no hay ninguna fila con esa columna capturada ESE día (huérfana
    en la mayoría de los despliegues -- el scraper la captura pero nada la
    usaba; o simplemente ``dt`` cae fuera de la ventana que cubre el
    scraper en vivo, ver guardia de fecha abajo), o si la fila más cercana
    falla la guardia de plausibilidad (ver abajo) -- en ese caso quien
    llama debe caer al fallback de ``TasasHistoricasAuditoria``
    (``get_eur_rate_for_date``).

    Guardia de fecha (bug real, agosto 2026, encontrado auditando una N/C de
    marzo): sin este chequeo, para cualquier ``dt`` anterior a que el
    scraper empezara a capturar EUR (2026-07-31), "la fila más cercana"
    terminaba siendo la primera fila disponible del scraper -- semanas o
    MESES después de ``dt`` -- y esa fila ganaba silenciosamente sobre el
    fallback correcto de ``TasasHistoricasAuditoria`` (que sí busca por día
    exacto) porque esta función solo devolvía ``None`` si no había NINGUNA
    fila con la columna capturada en TODO el historial, nunca por estar
    lejos en el tiempo. Mismo criterio que ``get_rate_for_datetime`` (línea
    ~1147): la fila más cercana solo cuenta si es del mismo día que ``dt``.

    Guardia de plausibilidad (bug real, agosto 2026): ``OdooBcvClient``
    (fuente del scraper hasta este fix) lee la tasa EUR de
    ``res.currency.rate`` en Odoo, que llevaba congelada desde 2026-07-07
    (casi un mes) mientras BCV-USD sí se actualizaba a diario -- el scraper
    repetía fielmente ese valor viejo hora tras hora en ``SerieTasas`` sin
    ninguna señal de error. Empíricamente EUR/BCV-USD nunca baja de ~1.05
    en los datos reales de esta serie (BCV siempre devalúa más rápido que
    EUR en términos relativos); una fila cuyo ratio cae por debajo de ese
    piso es casi con certeza una tasa EUR estancada, no real.
    """
    candidatas = [r for r in rows if parse_decimal_safe(r.get("tasa_bcv_euro", "0")) > Decimal("0")]
    closest_row = _closest_serie_row(dt, candidatas)
    if not closest_row:
        return None
    if str(closest_row.get("timestamp", ""))[:10] != dt.date().isoformat():
        return None
    tasa_eur = parse_decimal_safe(closest_row.get("tasa_bcv_euro"))
    tasa_bcv_fila = parse_decimal_safe(closest_row.get("tasa_bcv", "0"))
    if tasa_bcv_fila > Decimal("0"):
        ratio = tasa_eur / tasa_bcv_fila
        if ratio < Decimal("1.05"):
            return None
    return tasa_eur


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


def _correr_auditoria_sobre_descuento_diaria(repo) -> None:
    """Envoltorio del daemon (ver ``run_sync_in_background``) para

    ``_detectar_sobre_descuentos_batch`` -- sin esto, un sobre-descuento
    recién expuesto por el vencimiento de una ventana de pago (Contado/
    Recompra, ver docstring de esa función) solo se detectaría si alguien
    abre Reporte de Saldos. Nunca debe tumbar el ciclo del daemon si falla.
    """
    try:
        filas = _detectar_sobre_descuentos_batch(repo)
        if filas and hasattr(repo, "append_auditoria_rows"):
            repo.append_auditoria_rows(filas)
            print(f"FastAPI Daemon: {len(filas)} sobre-descuento(s) nuevo(s) en Auditoría.")
    except Exception as e_aud:
        print(f"Error detectando sobre-descuentos (daemon): {e_aud}", file=sys.stderr)


async def run_sync_in_background():
    """Daemon de sincronización incremental Odoo → Sheets.

    Primera iteración: siempre hace lookback de 7 días para capturar
    órdenes/pagos que llegaron mientras el servidor estuvo caído (downtime).
    Iteraciones siguientes: usa el cursor incremental normal (delta 48h solapado).
    """
    _first_run = True
    _last_daily_recalc_date: date | None = None
    # Catálogo: cambia con poca frecuencia (precios/nombres de producto),
    # no necesita el ciclo de 5 min -- se sincroniza una vez por día
    # calendario, mismo patrón que _last_daily_recalc_date.
    _last_catalogo_sync_date: date | None = None
    while True:
        try:
            config = AppConfig.from_env()
            repo = get_repo()
            reader = OdooXmlRpcReader(config.odoo)

            if _first_run:
                # Hallazgo real (agosto 2026, DB Postgres local vacía): un
                # cliente puede no tener cambios en Odoo en los últimos 7
                # días aunque tenga una orden reciente (write_date de
                # res.partner no se toca al crear una orden) -- con la
                # ventana de 7 días aplicada TAMBIÉN a clientes, una orden
                # podía llegar sin su cliente y violar la FK
                # ordenes_venta.cliente_id (visto en vivo: cliente_id=725
                # ausente). Con cursor nunca fijado (``get_last_sync() is
                # None`` -- primera vez real, no un simple reinicio del
                # server), se hace un sync SIN acotar por fecha (since=None
                # -- ver changed_clientes: ya maneja este caso con
                # active_test=False) para garantizar que todo cliente
                # referenciado exista antes que su(s) orden(es). Con cursor
                # ya fijado (reinicio normal), se mantiene el catch-up de 7
                # días como antes.
                _es_bootstrap_real = repo.get_last_sync() is None
                if _es_bootstrap_real:
                    print("FastAPI Daemon: Primera corrida — DB vacía, sync completo sin acotar.")
                    since_override = None
                else:
                    print(
                        "FastAPI Daemon: Primera corrida — lookback 7 días para recuperar ventas."
                    )
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
                _VENTAS_CACHE["data"] = None
                _VENTAS_CACHE["timestamp"] = 0.0
                print(f"FastAPI Daemon: Primera corrida completada. {total_first} filas.")
                if total_first > 0:
                    # En un hilo aparte: Reconciler hace una llamada XML-RPC a
                    # Odoo POR ORDEN -- con cientos de órdenes puede tardar
                    # minutos. Ejecutarlo inline bloquearía el event loop
                    # entero (incluido el health check de Railway), causando
                    # 502 "Application failed to respond" en el servidor.
                    await asyncio.to_thread(recalculate_all_orders)
                await asyncio.to_thread(_correr_auditoria_sobre_descuento_diaria, repo)
                _last_daily_recalc_date = date.today()
                _first_run = False
            else:
                print("FastAPI Daemon: Iniciando ciclo de sync incremental...")
                sync = IncrementalSync(repo, reader)
                _sync_catalogo_hoy = _last_catalogo_sync_date != date.today()
                result = sync.run(datetime.now(), sync_catalogo=_sync_catalogo_hoy)
                if _sync_catalogo_hoy:
                    _last_catalogo_sync_date = date.today()
                if result.total > 0:
                    _REPORTE_SALDOS_CACHE["data"] = None
                    _REPORTE_SALDOS_CACHE["timestamp"] = 0.0
                    _VENTAS_CACHE["data"] = None
                    _VENTAS_CACHE["timestamp"] = 0.0
                    # Sincronización bidireccional: si Odoo reportó cambios
                    # (ej. un pago editado en monto/fecha/cliente), se
                    # recalculan motor y reconciliación para reflejarlo sin
                    # esperar a que un humano vincule algo manualmente. En
                    # hilo aparte por la misma razón que arriba.
                    await asyncio.to_thread(recalculate_all_orders)
                    await asyncio.to_thread(_correr_auditoria_sobre_descuento_diaria, repo)
                    _last_daily_recalc_date = date.today()
                elif _last_daily_recalc_date != date.today():
                    # Recálculo diario independiente del sync: aunque Odoo no
                    # reporte cambios, descuentos como Recompra dependen de
                    # ventana_pago_vigente(...) evaluada contra date.today()
                    # -- una orden que nadie tocó puede cruzar el borde de su
                    # ventana de un día para otro sin que IncrementalSync ni
                    # una Vinculación manual lo disparen. Se fuerza al menos
                    # una corrida por día calendario para que ese borde no
                    # pase inadvertido.
                    print(
                        "FastAPI Daemon: Recálculo diario (ventanas de pago) sin cambios de sync."
                    )
                    await asyncio.to_thread(recalculate_all_orders)
                    await asyncio.to_thread(_correr_auditoria_sobre_descuento_diaria, repo)
                    _last_daily_recalc_date = date.today()
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
            lineas_borradas = IncrementalSync(repo, reader).reconciliar_lineas_borradas(
                ordenes, lineas
            )
            repo.set_last_sync(datetime.now())
            total = len(clientes) + len(ordenes) + len(lineas) + len(pagos) + lineas_borradas
        else:
            sync = IncrementalSync(repo, reader)
            res = sync.run(datetime.now())
            total = res.total

        _REPORTE_SALDOS_CACHE["data"] = None
        _REPORTE_SALDOS_CACHE["timestamp"] = 0.0
        _VENTAS_CACHE["data"] = None
        _VENTAS_CACHE["timestamp"] = 0.0

        return {
            "status": "ok",
            "total_actualizados": total,
            "mensaje": f"Sincronización completada. {total} registros actualizados.",
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.api_route("/api/backfill/ventas-teoricos", methods=["GET", "POST"])
async def api_backfill_ventas_teoricos(limite: int | None = None):
    """Backfill manual de ``ventas_teoricos`` (Fase 10) -- corre en el

    request (no en el daemon de 5 min) para el llenado inicial masivo de
    órdenes existentes; el daemon (``recalculate_all_orders``) se encarga
    de las nuevas/re-verificaciones con un tope de 50 por ciclo. Puede
    tardar varios minutos contra cientos de órdenes -- pensado para
    correrse una vez (o con ``limite`` para ir en tandas).
    """
    try:
        config = AppConfig.from_env()
        repo = get_repo()
        execute = _connect(config.odoo)
        if not execute:
            raise HTTPException(status_code=503, detail="Sin conexión a Odoo")

        usd_lists, ves_lists = get_valid_pricelists_usd_and_ves(repo)
        usd_ids_int = [int(x) for x in usd_lists if str(x).isdigit()]
        ves_ids_int = [int(x) for x in ves_lists if str(x).isdigit()]
        primary_usd_id = _primer_id_activo(execute, usd_ids_int) or 4
        primary_ves_id = _primer_id_activo(execute, ves_ids_int) or 5
        pricelist_ids = {"USD": primary_usd_id, "BCV": primary_ves_id}
        fallback_pricelist_ids = [int(x) for x in (*usd_lists, *ves_lists) if str(x).isdigit()]
        resolver = OdooPriceResolver(
            execute, pricelist_ids, fallback_pricelist_ids, build_fallback_ficha_config(repo)
        )
        runner = EngineRunner(repo, resolver, config.engine)

        procesadas = await asyncio.to_thread(
            runner.run_teoricos_pendientes, date.today(), limite
        )
        return {
            "status": "ok",
            "ordenes_procesadas": procesadas,
            "mensaje": f"{procesadas} orden(es) calculada(s)/re-verificada(s) en ventas_teoricos.",
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.api_route("/api/backfill/entregas", methods=["GET", "POST"])
async def api_backfill_entregas():
    """Backfill manual de una sola vez del espejo ``entregas``/``lineas_

    entrega`` -- pedido explícito del usuario (agosto 2026): casi todas
    las órdenes ya entregadas en el pasado no tenían ``fecha_entrega``,
    dato que alimenta las reglas de ventana de pago Contado del motor de
    descuentos. Causa raíz confirmada: el sync delta (``IncrementalSync.
    run``) usa un ÚNICO cursor ``last_sync`` compartido por todas las
    entidades -- ``entregas``/``facturas`` se agregaron como espejo
    DESPUÉS de que ese cursor ya llevaba corriendo un tiempo (Fase 0 del
    plan de consolidación de fuentes), así que ``changed_entregas(since)``
    solo capturó despachos con ``write_date`` posterior a ese punto. Un
    ``stock.picking`` ya "done" no vuelve a tocarse en Odoo, así que su
    ``write_date`` quedó viejo para siempre -- nunca calificó para el
    delta (confirmado en producción: 22 de 565 órdenes facturadas tenían
    entrega en el espejo antes de este backfill). Igual patrón que
    ``/api/backfill/ventas-teoricos``: corre bajo demanda con
    ``since=None`` (trae el historial completo vía Odoo, sin límite de
    fecha) y hace upsert -- NO toca el cursor ``last_sync``, así que el
    sync incremental normal sigue exactamente igual después de correr
    esto. Idempotente, se puede repetir sin riesgo.
    """
    try:
        config = AppConfig.from_env()
        repo = get_repo()
        reader = OdooXmlRpcReader(config.odoo)

        entregas = await asyncio.to_thread(reader.changed_entregas, None)
        entregas_lineas = await asyncio.to_thread(reader.changed_entregas_lineas, None)
        repo.upsert_entregas(entregas)
        repo.upsert_entregas_lineas(entregas_lineas)

        _REPORTE_SALDOS_CACHE["data"] = None
        _REPORTE_SALDOS_CACHE["timestamp"] = 0.0
        _VENTAS_CACHE["data"] = None
        _VENTAS_CACHE["timestamp"] = 0.0

        return {
            "status": "ok",
            "entregas": len(entregas),
            "entregas_lineas": len(entregas_lineas),
            "mensaje": (
                f"{len(entregas)} entrega(s) y {len(entregas_lineas)} línea(s) "
                "de entrega sincronizadas desde el historial completo de Odoo."
            ),
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


_SCRAPER_HORA_INICIO = 6  # primera captura del día, 6:00
_SCRAPER_HORA_FIN = 22  # última captura del día, 22:00 (inclusive)


def _now_caracas() -> datetime:
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(hours=4)).replace(tzinfo=None)


def _segundos_hasta_proxima_hora_en_punto(now: datetime) -> float:
    """Segundos hasta el próximo filo de hora (ej. 14:07 -> 52.8 min).

    El scraper se despierta cada hora EN PUNTO para evaluar si está dentro
    de la ventana 6:00-22:00 -- así la primera captura del día cae
    exactamente a las 6:00 y no en un minuto arbitrario relativo al
    arranque del proceso.
    """
    from datetime import timedelta

    proxima = (now.replace(minute=0, second=0, microsecond=0)) + timedelta(hours=1)
    return max((proxima - now).total_seconds(), 60.0)


async def run_scraper_in_background():
    from cxc.alerts import build_alerter
    from cxc.scraper.bcv import BcvClient
    from cxc.scraper.binance import BinanceClient
    from cxc.scraper.rates_scraper import RatesScraper

    while True:
        try:
            now_caracas = _now_caracas()
            if _SCRAPER_HORA_INICIO <= now_caracas.hour <= _SCRAPER_HORA_FIN:
                print(
                    "FastAPI Daemon: Iniciando ciclo de scraping de tasas "
                    f"(BCV y Binance) -- {now_caracas.isoformat()}..."
                )
                config = AppConfig.from_env()
                repo = get_repo()
                # BcvClient hace scraping directo del sitio del BCV (USD y
                # EUR), NO depende de que alguien cargue la tasa en Odoo.
                # Bug real encontrado en auditoría (agosto 2026):
                # OdooBcvClient lee res.currency.rate, y la fila de EUR
                # (currency_id=125) llevaba congelada desde 2026-07-07 --
                # casi un mes sin actualizarse en Odoo, mientras USD sí se
                # mantenía al día -- todo el sistema mostraba/calculaba con
                # esa tasa EUR vieja sin ninguna señal de error. El
                # scraping directo trae ambas tasas frescas cada hora.
                scraper = RatesScraper(
                    repo,
                    BinanceClient(config.binance),
                    BcvClient(config.bcv),
                    build_alerter(config.alert),
                    config.scraper_policy,
                )
                # scraper.run() hace 2-3 llamadas HTTP síncronas (Binance +
                # BCV) que pueden tardar varios segundos -- sin to_thread
                # bloquean el event loop entero (mismo patrón de bug que
                # Fase 1, ver recalculate_all_orders), tumbando /reporte y
                # cualquier otro endpoint mientras el scraper espera red.
                fila = await asyncio.to_thread(scraper.run, now_caracas)
                print(
                    f"FastAPI Daemon: Tasas actualizadas. BCV={fila.tasa_bcv} "
                    f"Binance={fila.tasa_binance}"
                )
            else:
                print(
                    "FastAPI Daemon: fuera de la ventana de captura "
                    f"({_SCRAPER_HORA_INICIO}:00-{_SCRAPER_HORA_FIN}:00) -- "
                    f"hora actual {now_caracas.strftime('%H:%M')}, esperando."
                )
        except Exception as e:
            print(f"Error en daemon de scraping de tasas: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        # Dormir hasta el próximo filo de hora en punto (Caracas) -- ese es
        # el único punto en el que el scraper vuelve a evaluar/capturar,
        # sea que la hora recién corrida haya estado dentro o fuera de la
        # ventana 6:00-22:00.
        await asyncio.sleep(_segundos_hasta_proxima_hora_en_punto(_now_caracas()))


def _aplicar_migraciones_pendientes() -> None:
    """Red de seguridad: corre ``alembic upgrade head`` al arrancar.

    El `Procfile` ya declara ``release: alembic upgrade head``, que Railway
    debería ejecutar en cada deploy antes de levantar el proceso `web` --
    pero si esa fase de release no corre (o falla en silencio, como pasó en
    producción: ver ``UndefinedColumn`` en ``descuentos_pronto_pago.
    requiere_pago_previo``/``bandeja_facturacion.equivalente_lista_usd``),
    el proceso `web` arrancaba de todos modos contra un esquema
    desactualizado. Esto es best-effort y no debe tumbar el arranque: si
    falla (backend Sheets sin Postgres, DB no disponible, permisos), solo
    se loggea -- el resto de la app sigue funcionando igual que antes de
    este cambio.
    """
    config = AppConfig.from_env()
    if config.database.repo_backend != "postgres":
        return
    try:
        from pathlib import Path

        from alembic import command
        from alembic.config import Config as AlembicConfig

        repo_root = Path(__file__).resolve().parents[3]
        alembic_ini = repo_root / "alembic.ini"
        if not alembic_ini.exists():
            logger.warning(
                "alembic.ini no encontrado en %s -- se omite la migración automática de arranque.",
                alembic_ini,
            )
            return
        cfg = AlembicConfig(str(alembic_ini))
        cfg.set_main_option("script_location", str(repo_root / "alembic"))
        command.upgrade(cfg, "head")
        logger.info("Migraciones Alembic verificadas/aplicadas al arrancar (alembic upgrade head).")
    except Exception as e:
        logger.error(
            "No se pudieron aplicar migraciones Alembic al arrancar -- "
            "la app seguirá corriendo, pero el esquema puede estar desactualizado: %s",
            e,
        )


@app.on_event("startup")
async def startup_event():
    # Red de seguridad: si la fase `release` de Railway no corrió (o falló
    # en silencio), esto evita que el proceso `web` sirva requests contra
    # un esquema desactualizado -- ver docs/REDISENO_DESCUENTOS_UNIFICADOS.md,
    # sección "Checklist de migraciones Alembic".
    _aplicar_migraciones_pendientes()
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


@app.get("/inventario", response_class=HTMLResponse)
async def page_inventario(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("inventario", cxc_session)


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
    repo.upsert_usuario_plataforma(u_row)
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
        # 1. Total por cobrar (Orders not invoiced), NETO de lo ya pagado --
        # antes sumaba el monto_total bruto de la orden sin restar
        # Vinculaciones aplicadas, a diferencia de la función casi idéntica
        # /api/ordenes-pendientes/{cliente_id} (mismo archivo) que sí resta
        # correctamente. No se ve hoy en el Dashboard (las tarjetas
        # kpi-cobrables/kpi-sin-asignar/kpi-alertas ya no existen en el
        # HTML), pero se corrige para que quede correcto si se reconecta.
        ordenes = repo.all_ordenes()
        so_names_r = [o.so_id for o in ordenes]
        so_states_map = get_live_so_states(so_names_r)
        # Fase 4/6 (plan de consolidación de fuentes, agosto 2026): entregas
        # desde el espejo, no Odoo en vivo -- ver _entregas_desde_espejo.
        entrega_valida_set, _ = _entregas_desde_espejo(repo, so_names_r)
        vincs = repo.all_vinculaciones()
        # Fase 0: solo Vinculaciones CONCILIADO cuentan como pagado real
        # para "Total por Cobrar" (abajo). La sección 2 ("Pagos sin
        # asignar", más abajo) sí usa `vincs` sin filtrar a propósito --
        # un pago con una Vinculación PENDIENTE ya está reclamado, aunque
        # no esté confirmado, y no debe ofrecerse de nuevo como "sin
        # asignar" (mismo criterio que ya documenta
        # _get_conciliaciones_sugerencias_sync).
        linked_by_so = _pagado_confirmado_por_so(vincs)
        total_por_cobrar = sum(
            max(Decimal("0"), o.monto_total - linked_by_so.get(o.so_id, Decimal("0")))
            for o in ordenes
            if not o.facturada
            and not orden_excluida(
                o,
                live_state=so_states_map.get(o.so_id),
                entrega_valida=o.so_id in entrega_valida_set,
            )
        )

        # 2. Pagos sin asignar (saldo real -- no todo-o-nada -- en USD y VES)
        pagos = _all_pagos_rows(repo)
        linked_amounts: dict[str, Decimal] = {}
        for v in vincs:
            prev = linked_amounts.get(v.pago_id, Decimal("0"))
            linked_amounts[v.pago_id] = prev + v.monto_aplicado
        tasas_rows = _all_serie_tasas_rows(repo)

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
        # Fase 0: solo Vinculaciones CONCILIADO cuentan como pagado real.
        linked_by_so = _pagado_confirmado_por_so(repo.all_vinculaciones())

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
        tasa_bcv_ultima = last_tasa.tasa_bcv if last_tasa else Decimal("36.5")
        tasa_binance = last_tasa.tasa_binance if last_tasa else Decimal("38.0")

        # Fetch payment to get currency
        pago = repo.get_pago(req.pago_id)
        if not pago:
            raise HTTPException(status_code=404, detail="Pago no encontrado.")

        monto_dec = Decimal(str(req.monto_aplicado))
        hora_pago_confirmada = datetime.combine(pago.fecha_pago, datetime.min.time())
        # Tarea 2: orden en la ventana histórica -> tasa BCV-Euro de referencia.
        tasa_bcv, bcv_variante = resolver_tasa_bcv_vinculacion(
            repo, req.so_id, hora_pago_confirmada, tasa_bcv_ultima
        )

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
            hora_pago_confirmada=hora_pago_confirmada,
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
            bcv_variante=bcv_variante,
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
            get_repo() if config.database.repo_backend == "postgres" else _fresh_sheets_repo(config)
        )
        execute = _connect(config.odoo)
        usd_lists, ves_lists = get_valid_pricelists_usd_and_ves(repo)
        usd_ids_int = [int(x) for x in usd_lists if str(x).isdigit()]
        ves_ids_int = [int(x) for x in ves_lists if str(x).isdigit()]
        primary_usd_id = _primer_id_activo(execute, usd_ids_int) or 4
        primary_ves_id = _primer_id_activo(execute, ves_ids_int) or 5
        # "USD"/"BCV": nombres lógicos de fallback (ver engine/discounts.py) --
        # el motor mismo ya resuelve la lista via EngineInputs.valid_usd/
        # valid_ves (Configuración), este dict solo cubre el caso residual
        # de que algo la pase por nombre lógico en vez de id numerico.
        pricelist_ids = {
            "USD": primary_usd_id,
            "BCV": primary_ves_id,
        }
        # Tarea 4: ambas listas están fijadas en USD -- si la pricelist
        # puntual de la orden no tiene item propio para un producto, probar
        # las demás pricelists configuradas antes de asumir precio 0.
        fallback_pricelist_ids = [int(x) for x in (*usd_lists, *ves_lists) if str(x).isdigit()]
        resolver = OdooPriceResolver(
            execute, pricelist_ids, fallback_pricelist_ids, build_fallback_ficha_config(repo)
        )
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

    Fase 1 (plan de arquitectura de pagos, agosto 2026): antes de
    resincronizar con Odoo, corre la confirmación FIFO automática
    (``_auto_vincular_fifo_pendientes``) -- crea Vinculaciones PENDIENTE
    para pagos sin asignar contra las órdenes abiertas más antiguas del
    cliente. El orden importa: si Odoo ya reconcilió alguno de esos pagos
    en el mismo ciclo, el resync que sigue justo después las promueve a
    CONCILIADO de una vez, sin esperar al próximo ciclo.
    """
    try:
        print("Recalculando motor de descuentos y reconciliación (todas las órdenes)...")
        config = AppConfig.from_env()
        repo = (
            get_repo() if config.database.repo_backend == "postgres" else _fresh_sheets_repo(config)
        )
        execute = _connect(config.odoo)

        try:
            n_auto_vinc = _auto_vincular_fifo_pendientes(repo)
            if n_auto_vinc:
                print(f"Auto-FIFO: {n_auto_vinc} vinculación(es) PENDIENTE creada(s).")
        except Exception as e_fifo:
            print(f"Error en auto-vinculación FIFO: {e_fifo}", file=sys.stderr)

        if execute:
            try:
                cambios = _resincronizar_vinculaciones_con_odoo(repo, execute)
                if cambios:
                    print(f"Re-vinculación por Odoo: {len(cambios)} discrepancia(s) revisada(s).")
            except Exception as e_relink:
                print(f"Error re-sincronizando Vinculaciones con Odoo: {e_relink}", file=sys.stderr)

        # Fase 2 (plan de arquitectura de pagos): corre DESPUÉS del resync
        # de Odoo -- así una Vinculación que este mismo ciclo se promovió a
        # CONCILIADO no aparece marcada como "pendiente por revisar".
        try:
            n_revisar = _detectar_vinculaciones_pendientes_a_revisar(repo)
            if n_revisar:
                print(f"Vinculaciones PENDIENTE a revisar: {len(n_revisar)}.")
        except Exception as e_stale:
            print(
                f"Error detectando Vinculaciones pendientes a revisar: {e_stale}",
                file=sys.stderr,
            )

        usd_lists, ves_lists = get_valid_pricelists_usd_and_ves(repo)
        usd_ids_int = [int(x) for x in usd_lists if str(x).isdigit()]
        ves_ids_int = [int(x) for x in ves_lists if str(x).isdigit()]
        primary_usd_id = _primer_id_activo(execute, usd_ids_int) or 4
        primary_ves_id = _primer_id_activo(execute, ves_ids_int) or 5
        # "USD"/"BCV": nombres lógicos de fallback (ver engine/discounts.py) --
        # el motor mismo ya resuelve la lista via EngineInputs.valid_usd/
        # valid_ves (Configuración), este dict solo cubre el caso residual
        # de que algo la pase por nombre lógico en vez de id numerico.
        pricelist_ids = {
            "USD": primary_usd_id,
            "BCV": primary_ves_id,
        }
        # Tarea 4: ambas listas están fijadas en USD -- si la pricelist
        # puntual de la orden no tiene item propio para un producto, probar
        # las demás pricelists configuradas antes de asumir precio 0.
        fallback_pricelist_ids = [int(x) for x in (*usd_lists, *ves_lists) if str(x).isdigit()]
        resolver = OdooPriceResolver(
            execute, pricelist_ids, fallback_pricelist_ids, build_fallback_ficha_config(repo)
        )
        runner = EngineRunner(repo, resolver, config.engine)

        resultados = runner.run_all(date.today())

        # Fase 10: teóricos de Ventas -- a diferencia de run_all, SÍ cubre
        # órdenes ya facturadas (por eso vive aparte de Bandeja). Tope por
        # ciclo (no todas de una) para no golpear Odoo con cientos de
        # órdenes pendientes en una sola corrida de 5 min -- el backfill
        # inicial masivo se dispara aparte via /api/backfill/ventas-teoricos.
        try:
            n_teoricos = runner.run_teoricos_pendientes(date.today(), limite=50)
            if n_teoricos:
                print(f"Teóricos de Ventas: {n_teoricos} orden(es) calculada(s)/re-verificada(s).")
        except Exception as e_teo:
            print(f"Error calculando teóricos de Ventas: {e_teo}", file=sys.stderr)

        facturas = OdooFacturasReader(execute)
        Reconciler(repo, facturas, config.reconciliation).run()
        print(f"Recálculo completo: {len(resultados)} órdenes procesadas.")
    except Exception as e:
        print(f"Error al recalcular todas las órdenes: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


_REPORTE_SALDOS_CACHE: dict[str, Any] = {"data": None, "timestamp": 0.0}
_REPORTE_CACHE_TTL = 0.0
# Guarda de reentrancia -- bug real encontrado agosto 2026: get_reporte_
# saldos llama (para el huérfano-check de Diferencial Cambiario, regla 2)
# a get_conciliaciones_sugerencias -> _get_saldos_reales_por_so -> get_
# reporte_saldos de nuevo. Con _REPORTE_CACHE_TTL=0 (deliberado, para que
# el reporte principal siempre muestre datos frescos) esa llamada anidada
# SIEMPRE ve el cache frío -- nunca alcanza a escribir su propio resultado
# antes de que la copia interna dispare la misma cadena otra vez, causando
# recursión sin límite (RecursionError tras cientos de rondas completas de
# queries a Odoo, o un cuelgue de varios minutos antes de llegar ahí).
_reporte_saldos_computing = False

# Caché corta de /api/ventas (solo vendedor=None) -- ver docstring de
# _get_ventas_sync. TTL corto (no cero como reporte-saldos): Ventas no
# participa hoy en la cadena de recursión reporte_saldos ->
# conciliaciones_sugerencias -> reporte_saldos, así que un TTL >0 aquí es
# seguro; se invalida explícitamente en los mismos puntos donde ya se
# invalida _REPORTE_SALDOS_CACHE (sync incremental) para no arrastrar datos
# viejos más allá de un ciclo de sync.
_VENTAS_CACHE: dict[str, Any] = {"data": None, "timestamp": 0.0}
_VENTAS_CACHE_TTL = 60.0
_ventas_computing = False

# Caché de product.pricelist.item (reglas "fixed") -- Reporte de Saldos y
# Auditoría consultan la MISMA tabla de Odoo con el MISMO filtro
# (compute_price=fixed) y los mismos 5 campos, cada uno con su propio
# conjunto de pricelist_ids (Auditoría pide usd_ids+ves_ids combinados,
# Reporte de Saldos solo usd_ids -- alcances distintos, así que la clave de
# caché es el conjunto EXACTO de ids pedido, nunca se mezcla data de un
# alcance con otro). Las reglas de precio son configuración administrativa
# que cambia con poca frecuencia -- TTL de 5 minutos es seguro.
_PRICELIST_ITEMS_CACHE: dict[tuple[int, ...], dict[str, Any]] = {}
_PRICELIST_ITEMS_CACHE_TTL = 300.0


def _get_pricelist_items_fixed(execute: Any, pricelist_ids: list[int]) -> list[dict[str, Any]]:
    """``product.pricelist.item`` con ``compute_price=fixed`` para

    ``pricelist_ids``, cacheado por 5 minutos y keyed por el conjunto EXACTO
    de ids solicitado -- ver comentario de ``_PRICELIST_ITEMS_CACHE``.
    """
    if not execute or not pricelist_ids:
        return []
    key = tuple(sorted({int(x) for x in pricelist_ids}))
    now_ts = time.time()
    cached = _PRICELIST_ITEMS_CACHE.get(key)
    if cached is not None and now_ts - float(cached["timestamp"]) < _PRICELIST_ITEMS_CACHE_TTL:
        return cached["data"]
    data = execute(
        "product.pricelist.item",
        "search_read",
        [[["pricelist_id", "in", list(key)], ["compute_price", "=", "fixed"]]],
        {"fields": ["pricelist_id", "product_tmpl_id", "fixed_price", "date_start", "date_end"]},
    )
    _PRICELIST_ITEMS_CACHE[key] = {"data": data, "timestamp": now_ts}
    return data


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


def _pagos_odoo_por_orden(
    execute: Any,
    invoice_ids_all: list[int],
    inv_id_to_so: dict[int, str],
    invoices_by_so: dict[str, list[dict]],
    ordenes_map: dict[str, OrdenVenta],
    rates_map: dict[str, float],
    last_bcv_val: float,
) -> dict[str, dict[str, Any]]:
    """Pagos por orden en equivalente USD, calculados directo contra Odoo --

    usado internamente por ``/api/reporte-saldos`` (Cuentas por Cobrar).
    NO depende de la tabla ``Vinculaciones``: en producción real esa tabla
    está vacía (el flujo de pagos vive enteramente en Odoo).

    ``/api/ventas`` usa una fuente distinta para el mismo propósito --
    ``_pagos_por_so_desde_cobranza`` -- que reusa ``get_live_pagos_
    conciliados`` (la función que ya usa ``/api/cobranza/pagos``) en vez de
    esta, para no mantener dos conversiones a USD independientes que
    podrían divergir entre reportes.

    Ruta principal: ``account.payment`` reconciliado contra las facturas de
    la orden (exacto, importe real cobrado). Fallback (sin
    ``account.payment`` reconciliado -- ej. el pago se aplicó directo en la
    factura sin generar un registro de pago separado): ``amount_total -
    amount_residual`` de cada factura, convertido a USD equivalente.

    ATENCIÓN -- limitación heredada del reporte de CxC, no introducida por
    esta extracción: para pagos en VES, "abono_bcv" y "abono_binance" salen
    IGUALES (ambos a tasa BCV) porque Odoo no distingue la ruta de pago
    (BCV oficial vs Binance/P2P) a nivel de ``account.payment``, solo la
    moneda -- se intentó inferir la ruta por el nombre del diario contable
    y se quitó (ver comentario más abajo): daba falsos positivos.
    """
    pagos: dict[str, dict[str, Any]] = {}
    if not execute:
        return pagos

    # ─── ABONOS DESDE ODOO CONSULTANDO account.payment DIRECTAMENTE ───────────
    # Consulta los pagos reales reconciliados en account.payment:
    # - Importe pagado en la moneda original (amount, currency_id)
    # - Fecha del pago (date)
    # - Si es USD -> 1:1 ($1 pagado = $1 abonado).
    # - Si es VES -> Se convierte a la tasa BCV/Binance a la fecha del pago (date).
    payments_by_so: dict[str, dict[str, Any]] = {}
    if invoice_ids_all:
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
                # forma confiable via out_refund. Todo lo que llega aqui
                # es, por definicion de Odoo, un pago real.
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
            logger.warning("Error al consultar account.payment en _pagos_odoo_por_orden: %s", e_pay)

    for so_name, inv_list in invoices_by_so.items():
        p_direct = payments_by_so.get(so_name)
        if p_direct and p_direct["abono_bcv"] > Decimal("0"):
            total_paid_bcv = p_direct["abono_bcv"]
            total_paid_binance = p_direct["abono_binance"]
            latest_inv_date = p_direct["latest_date"]
        else:
            total_paid_bcv = Decimal("0")
            total_paid_binance = Decimal("0")
            latest_inv_date = None
            o_obj = ordenes_map.get(so_name)
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
                        paid_inv / effective_rate if effective_rate > Decimal("0") else Decimal("0")
                    )
                else:
                    paid_inv_usd = paid_inv

                total_paid_bcv += paid_inv_usd
                total_paid_binance += paid_inv_usd
                if inv_dt and (not latest_inv_date or inv_dt > latest_inv_date):
                    latest_inv_date = inv_dt

        if total_paid_bcv > Decimal("0") or total_paid_binance > Decimal("0"):
            pagos[so_name] = {
                "abono_bcv": total_paid_bcv,
                "abono_binance": total_paid_binance,
                "ultimo_abono": latest_inv_date,
            }
    return pagos


def _get_reporte_saldos_sync(refresh: bool = False):
    """Cuerpo síncrono de ``get_reporte_saldos`` (Fase 1 restante, agosto

    2026) -- 100% trabajo síncrono (Odoo XML-RPC + DB), corrido en un hilo
    aparte vía ``asyncio.to_thread`` desde el wrapper público ``async def
    get_reporte_saldos``. Antes corría inline en el event loop: la función
    más pesada de todo el sistema (~1000 líneas, decenas de llamadas a
    Odoo) bloqueaba CUALQUIER otro request (incluido ``/reporte``) durante
    todo su tiempo de cómputo.
    """
    global _reporte_saldos_computing
    import time

    now_ts = time.time()
    if (
        not refresh
        and _REPORTE_SALDOS_CACHE["data"] is not None
        and now_ts - float(_REPORTE_SALDOS_CACHE["timestamp"]) < _REPORTE_CACHE_TTL
    ):
        return _REPORTE_SALDOS_CACHE["data"]
    if _reporte_saldos_computing:
        # Llamada anidada (ver comentario en la declaración del flag) --
        # devuelve el cache aunque esté frío/vacío en vez de recalcular
        # recursivamente.
        return _REPORTE_SALDOS_CACHE["data"] or {
            "items": [],
            "saldo_minimo_pendientes": [],
            "kpis": {},
            "vendedores": [],
        }
    _reporte_saldos_computing = True
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        # Fase 0: solo Vinculaciones CONCILIADO cuentan como pagado real
        # para decidir si una orden está saldada.
        vincs = [v for v in repo.all_vinculaciones() if v.estado == EstadoVinculacion.CONCILIADO]
        concs = {c.so_id: c for c in repo.all_conciliaciones()}

        # Load clients once
        clientes_map = {c.cliente_id: c.nombre for c in repo.all_clientes()}

        execute = None
        config = AppConfig.from_env()
        try:
            execute = _connect(config.odoo)
        except Exception as e_conn:
            logger.warning("No se pudo conectar a Odoo en get_reporte_saldos: %s", e_conn)

        # Query Odoo SOs for seller (user_id), payment terms & estado en vivo
        # (genuinamente mutable -- se queda en vivo, ver docstring del plan
        # de consolidación de fuentes).
        so_ids_names = [o.so_id for o in ordenes]
        so_odoo_data = {}
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
            except Exception as e_so:
                logger.warning("Error consultando sale.order en Odoo: %s", e_so)

        # Fase 4 (plan de consolidación de fuentes, agosto 2026): entregas
        # ahora se leen del espejo en vez de Odoo en vivo -- validado con
        # un parity check contra las 819 órdenes reales sincronizadas.
        # picking_delivery_map/picking_return_set (removidos) tenían el
        # MISMO bug real que ya se encontró y corrigió en
        # get_live_entregas_info (Ventas, Fase 2): un picking interno
        # (transferencia de bodega) con return_id apuntando a otro picking
        # interno se marcaba como devolución de cliente sin serlo -- 5
        # órdenes reales (S00076/S00091/S00098/S00224/S00329) salían con
        # "entrega_valida=False" incorrectamente. changed_entregas ya
        # filtra picking_type_code en el sync, así que el espejo no tiene
        # ese bug. Los otros 2 fallbacks del código en vivo (resolución de
        # so_name vía substring de "origin", y vía cadena de return_id) sí
        # se necesitaron para 4 pickings reales sin sale_id -- no
        # replicados en cxc.odoo.client.map_entrega_espejo todavía; si
        # aparecen en una corrida futura, esas 4 órdenes puntuales
        # perderían su fecha de entrega (no su facturación/pago) hasta que
        # se agregue esa resolución al sync.
        picking_delivery_map: dict[str, str] = {}
        entrega_valida_set: set[str] = set()
        if so_ids_names:
            entrega_valida_set, picking_delivery_map = _entregas_desde_espejo(repo, so_ids_names)

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
        rules_usd = _get_pricelist_items_fixed(execute, usd_ids)

        all_lines = _all_lineas_rows(repo)
        lines_by_so = {}
        for r in all_lines:
            so = r.get("so_id", "")
            if so:
                lines_by_so.setdefault(so, []).append(r)

        bandeja_rows = repo.all_bandeja()
        bandeja_map = {b.so_id: b for b in bandeja_rows}

        # Read discount rules for theoretical evaluation when order is not in BandejaFacturacion
        repo.descuentos_marca_categoria()

        # Read rates series to convert VES invoice residual to USD
        tasas_rows = _all_serie_tasas_rows(repo)
        rates_map = {}
        for r in tasas_rows:
            ts = str(r.get("timestamp", ""))[:10]
            tbcv = r.get("tasa_bcv")
            if ts and tbcv:
                with contextlib.suppress(Exception):
                    rates_map[ts] = float(tbcv)
        last_bcv_val = list(rates_map.values())[-1] if rates_map else 742.23

        # Fase 4 (plan de consolidación de fuentes, agosto 2026): montos e
        # identidad de facturas/NC ahora vienen del espejo Factura (que ya
        # resuelve NC sin invoice_origin propio vía la cadena
        # factura_origen_id -- reemplaza la lógica OR-domain/ref/
        # reversed_entry_id que hacía esto mismo en vivo). Solo
        # amount_residual/payment_state (genuinamente mutables) se piden
        # en vivo, acotados a los ids ya resueltos -- validado con un
        # parity check completo contra las 819 órdenes reales
        # sincronizadas (0 diffs, incluyendo el resultado final de
        # _pagos_odoo_por_orden con ambas fuentes).
        so_ids = [o.so_id for o in ordenes]
        invoices_by_so: dict[str, list[dict]] = {}  # out_invoice only
        ncs_by_so: dict[str, list[dict]] = {}  # out_refund (notas de crédito)
        invoice_ids_all: list[int] = []  # for fetching move lines
        inv_id_to_so: dict[int, str] = {}
        # SOs cuya(s) factura(s) Odoo ya estan "Pagada" (paid) o "En proceso de
        # pago" (in_payment): salen del reporte general de CxC (Tarea 2).
        so_pagada_en_odoo: set[str] = set()

        facturas_dicts = _facturas_dicts_desde_espejo(repo, so_ids)
        ids_para_estado_pago = [d["id"] for d in facturas_dicts if d["id"] is not None]
        estado_pago_map = (
            _estado_pago_facturas_desde_odoo(execute, ids_para_estado_pago) if execute else {}
        )
        for d in facturas_dicts:
            fid = d["id"]
            overlay = estado_pago_map.get(fid, {})
            merged = dict(d)
            merged["amount_residual"] = overlay.get("amount_residual", 0.0)
            merged["payment_state"] = overlay.get("payment_state", "")
            so = merged["invoice_origin"]
            if merged["move_type"] == "out_refund":
                ncs_by_so.setdefault(so, []).append(merged)
            else:
                invoices_by_so.setdefault(so, []).append(merged)
                if fid is not None:
                    invoice_ids_all.append(fid)
                    inv_id_to_so[fid] = so

        # Una SO sale del reporte de CxC solo si TODAS sus facturas out_invoice
        # ya estan pagadas/en proceso de pago (si queda alguna sin pagar, se
        # mantiene visible).
        for so_name, inv_list in invoices_by_so.items():
            estados = [str(i.get("payment_state", "")) for i in inv_list]
            if estados and all(ps in ("paid", "in_payment") for ps in estados):
                so_pagada_en_odoo.add(so_name)

        # Fase 4 (plan de consolidación de fuentes, agosto 2026): descuentos
        # de línea (orden + factura) ahora se leen del espejo -- validado
        # con un parity check completo contra las 819 órdenes reales (0
        # diffs en montos Y en los strings de detalle legible).
        #
        # Bug real encontrado en el camino: el bloque en vivo que esto
        # reemplaza NUNCA aplicaba inv_usd_ratio_map a los descuentos de
        # línea de FACTURA -- a diferencia de la función equivalente ya
        # usada por Ventas (_leer_descuentos_lineas_odoo), que sí lo hace
        # desde la corrección del bug S00010 documentada ahí. Como el
        # campo de salida se llama literalmente "odoo_factura_usd" y
        # alimenta un % de auditoría, mostrar el monto crudo en VES sin
        # convertir es un bug, no una decisión de diseño -- confirmado en
        # vivo: 580 de 582 líneas de descuento de factura reales están en
        # VES, así que este bug afectaba a la inmensa mayoría de los
        # casos, no un edge case. _descuentos_lineas_desde_espejo aplica
        # el ratio igual que Ventas, así que conectar el espejo también
        # corrige esto.
        # inv_usd_ratio_map: mismo cálculo (amount_total_signed_usd/
        # amount_total por factura) que ya usa _facturacion_por_so_desde_espejo
        # -- se reutiliza esa función solo por este dict (lectura del espejo
        # local, no otra llamada a Odoo).
        inv_usd_ratio_map = _facturacion_por_so_desde_espejo(repo, so_ids)["inv_usd_ratio_map"]
        sol_discounts_by_so, inv_line_discounts_by_so, sol_discount_detail_by_so, (
            inv_line_discount_detail_by_so
        ) = _descuentos_lineas_desde_espejo(
            repo, so_ids_names, invoice_ids_all, inv_id_to_so, inv_usd_ratio_map, con_detalle=True
        )

        # ─── ABONOS DESDE ODOO (account.payment reconciliado, fallback a
        # amount_residual de factura) -- ver _pagos_odoo_por_orden. ────────
        ordenes_map = {o.so_id: o for o in ordenes}
        pagos_odoo = _pagos_odoo_por_orden(
            execute,
            invoice_ids_all,
            inv_id_to_so,
            invoices_by_so,
            ordenes_map,
            rates_map,
            last_bcv_val,
        )
        for so_name, p_odoo in pagos_odoo.items():
            existing = pagos_by_so.get(so_name, {})
            if existing.get("tiene_vinc_manual", False):
                continue
            total_paid_bcv = p_odoo["abono_bcv"]
            total_paid_binance = p_odoo["abono_binance"]
            latest_inv_date = p_odoo["ultimo_abono"]

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
        hist_map = _build_hist_map(repo)

        historical_enabled = is_historical_pricelist_enabled(repo)
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

        # "USD"/"BCV": nombres lógicos de fallback (ver engine/discounts.py).
        engine_cfg_obj = config.engine
        pricelist_ids_map = {
            "USD": int(usd_ids[0]) if usd_ids and str(usd_ids[0]).isdigit() else 4,
            "BCV": int(ves_ids[0]) if ves_ids and str(ves_ids[0]).isdigit() else 5,
        }
        _fallback_pl_ids = [int(x) for x in (*usd_ids, *ves_ids) if str(x).isdigit()]
        price_resolver_engine = (
            OdooPriceResolver(
                execute, pricelist_ids_map, _fallback_pl_ids, build_fallback_ficha_config(repo)
            )
            if execute
            else None
        )

        # Pre-fetch all collections once outside loop to eliminate N+1 I/O overhead
        all_lines_map = {}
        for ln in repo.all_lineas():
            if ln.so_id:
                all_lines_map.setdefault(ln.so_id, []).append(ln)

        # Volumen "acumulado": lineas de OTRAS ordenes del mismo cliente
        # (ver engine/discounts.py) -- reutiliza all_lines_map/ordenes_map ya
        # cargados en este loop, sin queries adicionales.
        historial_por_cliente: dict[str, list] = {}
        for so_id_h, lineas_h in all_lines_map.items():
            orden_h = ordenes_map.get(so_id_h)
            if orden_h is None:
                continue
            historial_por_cliente.setdefault(orden_h.cliente_id, []).append((orden_h, lineas_h))

        # Diferencial Cambiario, regla "Equiparar": clientes con algún pago
        # sin aplicar/conciliar en Odoo ("pago huérfano"). Reusa la MISMA
        # fuente que el reporte CxC por Cliente (get_conciliaciones_
        # sugerencias) en vez de reimplementar la detección -- ese endpoint
        # ya excluye los huérfanos cerrados manualmente (pagos_huerfanos_
        # cerrados) y ya dedup por pago_id tomando el saldo MÁXIMO (residual
        # real sin aplicar). Calculado UNA vez por ciclo de caché, no por
        # orden -- ver EngineInputs.cliente_tiene_pagos_huerfanos.
        clientes_con_huerfanos: set[str] = set()
        try:
            sugerencias_huerfanas = _get_conciliaciones_sugerencias_sync(cxc_session=None)
            _pago_saldo_max_h: dict[str, float] = {}
            _pago_cliente_h: dict[str, str] = {}
            for s in sugerencias_huerfanas:
                pid = s.get("pago_id")
                if not pid:
                    continue
                saldo = float(s.get("saldo_pago") or 0.0)
                if saldo > _pago_saldo_max_h.get(pid, 0.0):
                    _pago_saldo_max_h[pid] = saldo
                    _pago_cliente_h[pid] = str(s.get("cliente_id") or "")
            for pid, saldo in _pago_saldo_max_h.items():
                if saldo > 0.05 and _pago_cliente_h.get(pid):
                    clientes_con_huerfanos.add(_pago_cliente_h[pid])
        except Exception as e_huerf:
            logger.warning(
                "No se pudieron calcular pagos huérfanos para Diferencial Cambiario: %s", e_huerf
            )

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

        try:
            marca_fallback_cfg = repo.get_config("marca_fallback")
            if marca_fallback_cfg:
                set_marca_fallback(marca_fallback_cfg)
        except Exception:
            pass

        all_desc_mc = repo.descuentos_marca_categoria()
        all_desc_vol = repo.descuentos_volumen()
        all_reg_rec = repo.reglas_recurrencia()
        all_promo_1st = repo.promociones_primera_compra()
        all_feriados = repo.feriados()
        all_exclusiones = repo.exclusiones()
        all_desc_recompra = repo.descuentos_recompra()
        all_desc_diferencial = repo.descuentos_diferencial_cambiario()
        all_desc_producto = repo.descuentos_producto()

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
            entrega_valida = o.so_id in entrega_valida_set
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
                not lista_id_str
                or lista_id_str in ("0", "None", "")
                or (
                    historical_enabled
                    and HISTORICAL_PRICE_LIST_START <= o.fecha < HISTORICAL_PRICE_LIST_END_EXCLUSIVE
                )
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
                    if HISTORICAL_PRICE_LIST_START <= o.fecha < HISTORICAL_PRICE_LIST_END_EXCLUSIVE
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
                        promociones_primera_compra=all_promo_1st,
                        feriados_tabla=all_feriados,
                        price_resolver=fast_resolver,
                        engine_config=engine_cfg_obj,
                        fecha_calculo=o.fecha,
                        all_ordenes=ordenes,
                        exclusiones=all_exclusiones,
                        descuentos_recompra=all_desc_recompra,
                        descuentos_diferencial=all_desc_diferencial,
                        descuentos_producto=all_desc_producto,
                        valid_usd=[str(x) for x in usd_ids],
                        valid_ves=[str(x) for x in ves_ids],
                        historial_cliente_lineas=[
                            (oh, lh)
                            for oh, lh in historial_por_cliente.get(o.cliente_id, [])
                            if oh.so_id != o.so_id
                        ],
                        cliente_tiene_pagos_huerfanos=o.cliente_id in clientes_con_huerfanos,
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
            # Fase 1 (auditoría del ciclo CxC, agosto 2026): `total_descuentos_
            # monto` es libre de impuesto (base subtotal del motor, igual que
            # `venta_bruta_teorica`/`precio_base_calculado`) -- restarlo
            # directo de `saldo_deudor_*` (que SÍ trae IVA, viene de `monto_
            # orig`/`monto_total_proyectado_usd`) subestima el saldo real:
            # un descuento de $100 sobre el subtotal reduce el total CON IVA
            # en $116, no en $100. Se reaplica el IVA al monto del descuento
            # antes de restarlo -- mismo orden "descuento sobre subtotal,
            # impuesto sobre lo ya descontado" que ya usa `/api/ventas`
            # (`ves_neta_teorica_iva`/`usd_neta_teorica_iva`), sin necesitar
            # trackear aquí un subtotal separado por lista VES/USD. Los NCs
            # de Odoo (`ncs_odoo_monto_usd`) NO llevan este ajuste -- ya son
            # documentos reales con impuesto incluido (`amount_total`).
            descuentos_motor_con_iva = total_descuentos_monto * (1 + float(config.engine.iva_rate))
            saldo_con_descuento_bcv = max(
                0.0, saldo_deudor_bcv - descuentos_motor_con_iva - ncs_odoo_monto_usd
            )
            saldo_con_descuento_lista_usd = max(
                0.0, saldo_deudor_lista_usd - descuentos_motor_con_iva - ncs_odoo_monto_usd
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

            # Fase 1 (auditoría del ciclo CxC): las dos etapas del "descuento
            # teórico pendiente por aplicar" expuestas por separado, para que
            # el dashboard pueda mostrar tanto el subtotal con descuento como
            # el neto con impuestos sobre ese subtotal -- mismo cálculo que
            # ya usa `descuentos_motor_con_iva` arriba, solo que aquí se
            # expone cada etapa en vez de solo el resultado final restado.
            subtotal_teorico_con_descuento = round(
                max(0.0, venta_bruta_teorica - total_descuentos_monto), 2
            )
            neto_teorico_con_descuento_iva = round(
                subtotal_teorico_con_descuento * (1 + float(config.engine.iva_rate)), 2
            )

            # Árbol de enrutamiento de CxC (Sección 5 del Manual) -- ver
            # src/cxc/engine/cxc_routing.py, misma fuente de verdad que
            # /api/ventas y /api/bandeja. Para órdenes YA facturadas,
            # "pagado vs algún teórico" (BS o USD, tolerancia $1 -- igual
            # que el umbral de cierre histórico de este reporte) hace que
            # la orden salga de CxC activa (antes el criterio era idéntico
            # pero sin pasar por la función compartida). El caso NUEVO:
            # pagado vs Factura Real en Odoo pero NO vs ningún teórico ->
            # NO se oculta, permanece visible + se marca para la nueva
            # Bandeja de Auditoría de Precios (sospecha de precio/lista por
            # debajo del estándar).
            clasificacion_cxc = clasificar_estado_cxc(
                so_id=o.so_id,
                facturada=bool(o.facturada),
                teorico_bs_pagado=saldo_con_descuento_bcv <= 1.0,
                teorico_usd_pagado=saldo_con_descuento_lista_usd <= 1.0,
                factura_real_pagada=bool(o.facturada)
                and (saldo_factura_odoo if saldo_factura_odoo is not None else 0.0) <= 1.0,
                nacio_en_lista_usd=(
                    not is_historical and lista_id_str in [str(x) for x in usd_ids]
                ),
            )

            if clasificacion_cxc.sale_de_cxc:
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
                        "bandeja_destino": clasificacion_cxc.bandeja_destino.value
                        if clasificacion_cxc.bandeja_destino
                        else None,
                        "cxc_routing_motivo": clasificacion_cxc.motivo,
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

            # Dates & aging calculation -- misma fórmula que Ventas
            # (_fecha_y_dias_vencido, fuente única de la antigüedad, agosto
            # 2026). picking_delivery_map (Fase 4: ahora viene del espejo,
            # ver _entregas_desde_espejo más arriba) también decide
            # "entrega_valida" más abajo, no solo la antigüedad.
            fecha_delivery = picking_delivery_map.get(o.so_id)
            if not fecha_delivery:
                fecha_delivery = o.fecha.isoformat()

            term_name = odoo_info.get("payment_term_name") or "Contado"
            dias_credito = _parse_payment_term_days(term_name)

            dt_venc, dias_vencido = _fecha_y_dias_vencido(
                {
                    "fecha_entrega": fecha_delivery,
                    "fecha": o.fecha.isoformat(),
                    "dias_credito": dias_credito,
                },
                today_date,
            )
            fecha_vencimiento = dt_venc.isoformat() if dt_venc else o.fecha.isoformat()

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
                    # Fase 1: subtotal con descuento teórico (antes de
                    # impuestos) y neto teórico (ese subtotal + IVA) por
                    # separado -- antes solo se exponía el saldo ya neteado
                    # contra lo pagado, sin las dos etapas visibles.
                    "subtotal_teorico_con_descuento": subtotal_teorico_con_descuento,
                    "neto_teorico_con_descuento_iva": neto_teorico_con_descuento_iva,
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
                    # Fase 2: mismo valor que `audit_descuentos_summary.
                    # descuento_adicional` (`discount_audit.py`,
                    # descuento_adicional_a_aplicar = max(0, motor - odoo))
                    # expuesto también como columna de primer nivel -- antes
                    # solo vivía anidado, sin que el dashboard lo mostrara
                    # como una columna propia.
                    "descuento_pendiente_por_aplicar": round(
                        float(audit_orden.descuento_adicional_a_aplicar), 2
                    ),
                    "reconciliacion": {"resultado": conc.resultado.value if conc else "pendiente"}
                    if conc
                    else None,
                    # Árbol de enrutamiento de CxC -- la orden llegó hasta
                    # aquí porque clasificacion_cxc.sale_de_cxc es False;
                    # bandeja_destino solo puede ser None (caso normal) o
                    # "auditoria_precios" (pagado vs factura real pero no
                    # vs ningún teórico -- permanece visible + se marca).
                    "bandeja_destino": clasificacion_cxc.bandeja_destino.value
                    if clasificacion_cxc.bandeja_destino
                    else None,
                    "cxc_routing_motivo": clasificacion_cxc.motivo,
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
    finally:
        _reporte_saldos_computing = False


@app.get("/api/reporte-saldos")
async def get_reporte_saldos(refresh: bool = False):
    return await asyncio.to_thread(_get_reporte_saldos_sync, refresh)


_CXC_CLIENTE_SALDOS = ["teorico_bs", "teorico_usd", "venta_real", "factura_real"]


def _saldos_4_columnas_item(item: dict[str, Any]) -> dict[str, float | None]:
    """Los 4 saldos pendientes de una orden (mismos campos de ``/api/ventas``),

    en tiempo real -- fuente única de verdad reusada por
    ``/api/reporte-cxc-cliente`` y ``/api/cobranza/pagos`` (el "Saldo Orden
    (CxC)" del modal de detalle de pago). Antes ``/api/cobranza/pagos``
    mostraba un solo saldo blended (``saldo_con_descuento_bcv`` de
    ``get_reporte_saldos``, o un cálculo naive) -- ahora son las mismas 4
    referencias que el resto del sistema ya usa (Teórico Lista BS, Teórico
    Lista USD, Venta Real, Factura Neta Real).
    """
    desc_sistema = float(item.get("descuento_aplicado_sistema") or 0.0)
    pagado_bcv = float(item.get("pagado_teorico_bcv") or 0.0)
    pagado_binance = float(item.get("pagado_teorico_binance") or 0.0)
    pagado_ref = float(item.get("monto_pagado_factura_odoo") or 0.0)

    saldo_teorico_bs = max(0.0, float(item.get("ves_neta_teorica_iva") or 0.0) - pagado_bcv)
    saldo_teorico_usd = max(0.0, float(item.get("usd_neta_teorica_iva") or 0.0) - pagado_binance)
    saldo_venta_real = max(
        0.0, float(item.get("venta_neta_real") or 0.0) - desc_sistema - pagado_ref
    )
    facturada = bool(item.get("facturada"))
    saldo_factura_real = (
        max(0.0, float(item.get("total_facturado_neto") or 0.0) - desc_sistema - pagado_ref)
        if facturada
        else None
    )
    return {
        "teorico_bs": saldo_teorico_bs,
        "teorico_usd": saldo_teorico_usd,
        "venta_real": saldo_venta_real,
        "factura_real": saldo_factura_real,
    }


def _fecha_y_dias_vencido(item: dict[str, Any], today: date) -> tuple[date | None, int]:
    """Fecha de vencimiento y días vencido de una orden (item de Ventas).

    Fuente única de la antigüedad de una orden -- se calcula UNA vez dentro
    de ``_get_ventas_sync`` (columna "Días Vencido" de la página Ventas,
    pedido explícito del usuario, agosto 2026) y todo lo demás lee el
    resultado (``item["dias_vencido"]``/``item["fecha_vencimiento"]``) en
    vez de recalcularlo -- antes ``/api/reporte-cxc-cliente`` tenía su
    propia copia de esta misma fórmula.

    Base: ``fecha_entrega`` (entrega efectiva) o ``fecha`` (de la orden) si
    aún no hay entrega registrada, + ``dias_credito`` reales otorgados.
    """
    fecha_base_raw = item.get("fecha_entrega") or item.get("fecha")
    try:
        dt_base = datetime.strptime(str(fecha_base_raw)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        dt_base = today
    dt_venc = dt_base + timedelta(days=int(item.get("dias_credito") or 0))
    return dt_venc, max(0, (today - dt_venc).days)


@app.get("/api/reporte-cxc-cliente")
async def get_reporte_cxc_cliente(cxc_session: str | None = Cookie(default=None)):
    """Cuentas por Cobrar agrupadas por CLIENTE, estilo "Aged Receivable"

    de Odoo (referencia visual del usuario), pero con las 4 referencias de
    saldo propias del sistema en vez de un único "saldo neto" (un solo
    número mezclaría 4 saldos que pueden ser bien distintos entre sí --
    decisión explícita del usuario tras ver la primera versión con un solo
    saldo neto + buckets de antigüedad). Fila resumen por cliente con los
    4 saldos (Teórico Lista BS, Teórico Lista USD, Venta Real, Factura
    Neta Real), expandible a filas de detalle por documento.

    Fuente única de verdad: ``/api/ventas`` (mismos campos que alimentan
    las columnas de Ventas y el árbol de enrutamiento -- ver
    ``cxc_routing.py``), filtrado por ``sale_de_cxc`` para excluir órdenes
    que ya salieron de CxC activa (Bandeja 1/2). Por cada orden se calcula
    un saldo pendiente independiente por referencia:

    - ``teorico_bs``  = max(0, ``ves_neta_teorica_iva`` - ``pagado_teorico_bcv``)
    - ``teorico_usd`` = max(0, ``usd_neta_teorica_iva`` - ``pagado_teorico_binance``)
    - ``venta_real``  = max(0, ``venta_neta_real`` - desc. sistema - ``monto_pagado_factura_odoo``)
    - ``factura_real``= max(0, ``total_facturado_neto`` - desc. sistema
      - ``monto_pagado_factura_odoo``) (``None`` si la orden aún no está
      facturada -- no hay factura real contra qué comparar).

    Antigüedad (``dias_vencido``) = ``fecha_entrega`` (o ``fecha`` si no
    hay entrega registrada) + ``dias_credito`` real, mismo criterio que
    ``/api/reporte-saldos``; se expone por documento pero ya NO se usa
    para agrupar en buckets a nivel de cliente (reemplazado por las 4
    columnas de saldo).

    Pagos huérfanos (conciliados en Odoo pero SIN orden específica
    asociada -- misma lista que ya calcula
    ``/api/conciliaciones/sugerencias``, dedup por ``pago_id`` tomando el
    saldo MÁXIMO entre sus filas de sugerencia FIFO, el residual real sin
    aplicar) se muestran como documento NEGATIVO por cliente. Bug real
    corregido (agosto 2026): antes se restaba el MISMO valor (la ruta BCV)
    de las 4 columnas por igual, como si el pago fuera USD directo. Ahora
    cada columna resta SU PROPIA ruta: ``teorico_bs``/``teorico_usd``
    restan ``saldo_pago``/``saldo_pago_binance`` (ya calculados por
    separado en ``get_conciliaciones_sugerencias``, con el ajuste BCV-EUR
    para clientes con órdenes históricas); ``venta_real``/``factura_real``
    restan la ruta que corresponde a la lista de nacimiento de la orden a
    la que el FIFO sugirió aplicar el pago (BCV por defecto si no hay
    orden sugerida -- ``so_id is None``).

    Limitación conocida (MVP, documentada para no aparentar más de lo que
    hace): Notas de Crédito y Notas de Débito NO se muestran como
    documentos separados -- ya están netas dentro de
    ``total_facturado_neto``/``venta_neta_real`` de su orden (más
    conservador que duplicarlas como filas aparte, que sumaría el mismo
    efecto dos veces).
    """
    try:
        repo = get_repo()
        ordenes_map = {o.so_id: o for o in repo.all_ordenes()}

        ventas_data = await get_ventas(vendedor=None, cxc_session=cxc_session)
        sugerencias = await get_conciliaciones_sugerencias(cxc_session=cxc_session)
        ventas_items_by_so = {it["so_id"]: it for it in ventas_data["items"]}

        # Dedup por pago_id: el saldo MÁXIMO visto en las filas de
        # sugerencia de ese pago es su residual sin aplicar (las filas
        # subsecuentes del mismo pago muestran el saldo YA descontado de
        # sugerencias anteriores -- ver bucle FIFO en
        # get_conciliaciones_sugerencias). Se toma la fila COMPLETA (no
        # solo ``saldo_pago``) para no mezclar valores de distintas filas.
        pago_saldo_max: dict[str, float] = {}
        pago_info: dict[str, dict[str, Any]] = {}
        for s in sugerencias:
            pid = s.get("pago_id")
            if not pid:
                continue
            saldo = float(s.get("saldo_pago") or 0.0)
            if saldo > pago_saldo_max.get(pid, 0.0):
                pago_saldo_max[pid] = saldo
                pago_info[pid] = s

        clientes: dict[str, dict[str, Any]] = {}

        def _cliente_row(cliente_id: str, cliente_nombre: str) -> dict[str, Any]:
            return clientes.setdefault(
                cliente_id,
                {
                    "cliente_id": cliente_id,
                    "cliente_nombre": cliente_nombre,
                    "saldos": dict.fromkeys(_CXC_CLIENTE_SALDOS, 0.0),
                    "documentos": [],
                    "vendedores": set(),
                    "dias_vencido_max": 0,
                },
            )

        for item in ventas_data["items"]:
            if item.get("sale_de_cxc"):
                continue

            desc_sistema = float(item.get("descuento_aplicado_sistema") or 0.0)
            facturada = bool(item.get("facturada"))
            saldos_orden = _saldos_4_columnas_item(item)
            # Monto original (bruto de referencia, antes de restar lo
            # pagado) por columna -- pedido explícito del usuario, para no
            # mostrar solo el saldo sin poder ver contra qué se comparó.
            montos_originales_orden = {
                "teorico_bs": round(float(item.get("ves_neta_teorica_iva") or 0.0), 2),
                "teorico_usd": round(float(item.get("usd_neta_teorica_iva") or 0.0), 2),
                "venta_real": round(
                    max(0.0, float(item.get("venta_neta_real") or 0.0) - desc_sistema), 2
                ),
                "factura_real": (
                    round(
                        max(0.0, float(item.get("total_facturado_neto") or 0.0) - desc_sistema), 2
                    )
                    if facturada
                    else None
                ),
            }
            if all((v or 0.0) <= 0.05 for v in saldos_orden.values() if v is not None):
                continue

            o = ordenes_map.get(item["so_id"])
            cliente_id = str(o.cliente_id) if o else item["so_id"]
            c = _cliente_row(cliente_id, item["cliente_nombre"])
            # Fuente única: Ventas ya calculó esto (ver _fecha_y_dias_vencido
            # dentro de _get_ventas_sync) -- se lee en vez de recalcular.
            dias_vencido = int(item.get("dias_vencido") or 0)

            if item.get("vendedor"):
                c["vendedores"].add(item["vendedor"])
            c["dias_vencido_max"] = max(c["dias_vencido_max"], dias_vencido)

            for k, v in saldos_orden.items():
                if v is not None:
                    c["saldos"][k] += v

            factura_id = (o.factura_id if o else None) or None
            c["documentos"].append(
                {
                    "tipo": "orden",
                    "so_id": item["so_id"],
                    "factura_id": factura_id if facturada else None,
                    "fecha": item.get("fecha"),
                    "facturada": facturada,
                    "dias_vencido": dias_vencido,
                    "montos_originales": montos_originales_orden,
                    "saldos": {
                        k: (round(v, 2) if v is not None else None) for k, v in saldos_orden.items()
                    },
                    "descripcion": (
                        f"Orden {item['so_id']}"
                        + (f" / Factura {factura_id}" if facturada and factura_id else "")
                        + (" (facturada)" if facturada else " (sin facturar)")
                    ),
                    "bandeja_destino": item.get("bandeja_destino"),
                }
            )

        for pid, saldo in pago_saldo_max.items():
            if saldo <= 0.05:
                continue
            info = pago_info[pid]
            cliente_id = str(info.get("cliente_id") or "")
            c = _cliente_row(cliente_id, info.get("cliente_nombre") or f"Cliente {cliente_id}")

            saldo_bcv_ref = float(info.get("saldo_pago") or 0.0)
            saldo_binance_ref = float(info.get("saldo_pago_binance") or 0.0)
            so_sugerido = info.get("so_id")
            item_sugerido = ventas_items_by_so.get(so_sugerido) if so_sugerido else None
            usa_ref_binance = bool(item_sugerido and item_sugerido.get("nacio_en_lista_usd"))
            saldo_real_ref = saldo_binance_ref if usa_ref_binance else saldo_bcv_ref

            saldos_pago = {
                "teorico_bs": -saldo_bcv_ref,
                "teorico_usd": -saldo_binance_ref,
                "venta_real": -saldo_real_ref,
                "factura_real": -saldo_real_ref,
            }
            for k, v in saldos_pago.items():
                c["saldos"][k] += v
            c["documentos"].append(
                {
                    "tipo": "pago_huerfano",
                    "pago_id": pid,
                    "numero_pago_odoo": info.get("numero_pago_odoo"),
                    "fecha": info.get("pago_fecha"),
                    "dias_vencido": 0,
                    "saldos": {k: round(v, 2) for k, v in saldos_pago.items()},
                    "descripcion": f"Pago sin aplicar ({info.get('moneda_pago') or 'USD'})",
                }
            )

        for c in clientes.values():
            for k in _CXC_CLIENTE_SALDOS:
                c["saldos"][k] = round(c["saldos"][k], 2)
            c["documentos"].sort(key=lambda d: str(d.get("fecha") or ""))
            # Vendedor(es) y antigüedad máxima -- usados por la grilla de
            # priorización de cobro y los filtros de la tabla por cliente
            # (Reporte de Saldos). "saldo_priorizacion" es el mismo valor
            # (máximo de las 4 columnas) que ya determina el orden por
            # defecto de clientes_list, expuesto explícitamente para no
            # duplicar el criterio en el frontend.
            c["vendedor"] = ", ".join(sorted(c.pop("vendedores"))) or "Sin Vendedor"
            c["saldo_priorizacion"] = round(max(c["saldos"].values(), default=0.0), 2)

        clientes_list = sorted(
            clientes.values(), key=lambda c: -max(c["saldos"].values(), default=0.0)
        )

        totales: dict[str, Any] = {}
        for k in _CXC_CLIENTE_SALDOS:
            totales[k] = round(sum(c["saldos"][k] for c in clientes_list), 2)

        return {"clientes": clientes_list, "totales": totales}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/config/tasas")
async def get_config_tasas():
    try:
        repo = get_repo()
        # Read the last 15 raw rows from SerieTasas
        filas = _all_serie_tasas_rows(repo)[-15:]
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

        feriado = Feriado(
            fecha=date.fromisoformat(req.fecha),
            descripcion=req.descripcion,
            tipo=TipoFeriado.NACIONAL,
        )
        repo.append_feriado(feriado)
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
        repo.append_descuento_pronto_pago(rule)
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
        meta = repo.all_config()
        # Defaults
        if "cash_window_business_days" not in meta:
            meta["cash_window_business_days"] = "3"
        if "descuento_recompra" not in meta:
            meta["descuento_recompra"] = "0.05"
        if "marca_fallback" not in meta:
            meta["marca_fallback"] = "GLOBAL OIL"
        if "fallback_industrial_ajuste_pct" not in meta:
            meta["fallback_industrial_ajuste_pct"] = str(_AJUSTE_INDUSTRIAL_PCT_DEFAULT)
        return meta
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/meta")
async def post_config_meta(req: MetaRequest):
    try:
        repo = get_repo()
        repo.set_config("cash_window_business_days", str(req.cash_window_business_days))
        repo.set_config("descuento_recompra", str(req.descuento_recompra))
        repo.set_regla_recurrencia_porcentaje("recompra", Decimal(str(req.descuento_recompra)))
        repo.set_config("marca_fallback", req.marca_fallback or "GLOBAL OIL")
        set_marca_fallback(req.marca_fallback)
        set_ajuste_industrial_pct(Decimal(str(req.fallback_industrial_ajuste_pct)), repo)

        return {"status": "success", "message": "Ajustes globales actualizados correctamente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# Mapeo UNIFICADO de listas de precio (agosto 2026, pedido explícito del
# usuario tras revisar Configuración en vivo): reemplaza los DOS sistemas
# de mapeo que existían por separado (valid_pricelists_usd/_ves, que
# alimenta al motor, y valid_pricelists_industrial_usd/_ves/_comercial_usd/
# _ves, Fase C, solo consulta) -- tenerlos separados significaba dos
# fuentes de verdad para la misma lista de precios (una podía decir
# "válida para USD" sin que la otra supiera si es Industrial o Comercial),
# riesgo real de que se desincronicen. Ahora hay UNA sola estructura por
# lista: {pricelist_id: {"moneda": "usd"|"ves"|"", "categoria":
# "industrial"|"comercial"|"", "vigente": bool}}. "vigente" es NUEVO: si
# en el futuro hay dos listas con la misma categoría+moneda superpuestas
# en el tiempo, marca cuál es "la" que se muestra en Inventario -- el
# motor de descuentos sigue usando TODAS las listas con esa moneda como
# cadena de fallback (moneda, no vigente, es lo que lee
# get_valid_pricelists_usd_and_ves), sin cambio de comportamiento ahí.
PRICELIST_MAPEO_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "secrets", "pricelist_mapeo.json"
)
_PRICELIST_MAPEO_CACHE_UNIFICADO: dict[str, dict[str, Any]] = {}
_CLASIFICACION_KEYS = (
    "industrial_usd",
    "industrial_ves",
    "comercial_usd",
    "comercial_ves",
)


def _load_pricelist_mapeo_from_json() -> dict[str, dict[str, Any]] | None:
    try:
        if os.path.exists(PRICELIST_MAPEO_FILE):
            with open(PRICELIST_MAPEO_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data:
                    return data
    except Exception:
        pass
    return None


def _save_pricelist_mapeo_to_json(mapeo: dict[str, dict[str, Any]]) -> None:
    try:
        os.makedirs(os.path.dirname(PRICELIST_MAPEO_FILE), exist_ok=True)
        with open(PRICELIST_MAPEO_FILE, "w", encoding="utf-8") as f:
            json.dump(mapeo, f, indent=2)
    except Exception:
        pass


def _migrar_mapeo_pricelist_legado(repo) -> dict[str, dict[str, Any]]:
    """Reconstruye el mapeo unificado a partir de las 6 claves de config

    legadas (valid_pricelists_usd/_ves/_industrial_usd/_industrial_ves/
    _comercial_usd/_comercial_ves) -- corre UNA sola vez, solo si el
    mapeo unificado todavía no existe (ver get_pricelist_mapeo). No
    inventa ningún "vigente" (campo nuevo sin equivalente legado).
    """
    mapeo: dict[str, dict[str, Any]] = {}

    def _leer(key: str) -> list[str]:
        val = repo.get_config(key)
        return [x.strip() for x in val.split(",") if x.strip()] if val else []

    def _fila(pid: str) -> dict[str, Any]:
        return mapeo.setdefault(pid, {"moneda": "", "categoria": "", "vigente": False})

    for pid in _leer("valid_pricelists_usd"):
        _fila(pid)["moneda"] = "usd"
    for pid in _leer("valid_pricelists_ves"):
        _fila(pid)["moneda"] = "ves"
    for pid in _leer("valid_pricelists_industrial_usd"):
        _fila(pid)["categoria"] = "industrial"
    for pid in _leer("valid_pricelists_industrial_ves"):
        _fila(pid)["categoria"] = "industrial"
    for pid in _leer("valid_pricelists_comercial_usd"):
        _fila(pid)["categoria"] = "comercial"
    for pid in _leer("valid_pricelists_comercial_ves"):
        _fila(pid)["categoria"] = "comercial"
    return mapeo


def get_pricelist_mapeo(repo=None) -> dict[str, dict[str, Any]]:
    """Mapeo unificado (moneda/categoría/vigente) por pricelist_id.

    Orden de prioridad: memoria de proceso -> archivo JSON local -> config
    persistente (Postgres/Sheets, con migración automática de las 6
    claves legadas la primera vez) -- mismo patrón de 3 niveles que ya
    usaban por separado los dos mapeos que este reemplaza.
    """
    global _PRICELIST_MAPEO_CACHE_UNIFICADO
    if _PRICELIST_MAPEO_CACHE_UNIFICADO:
        return _PRICELIST_MAPEO_CACHE_UNIFICADO

    json_result = _load_pricelist_mapeo_from_json()
    if json_result:
        _PRICELIST_MAPEO_CACHE_UNIFICADO = json_result
        return json_result

    try:
        if repo is None:
            repo = get_repo()
        val = repo.get_config("pricelist_mapeo_unificado")
        if val:
            mapeo = json.loads(val)
            _PRICELIST_MAPEO_CACHE_UNIFICADO = mapeo
            _save_pricelist_mapeo_to_json(mapeo)
            return mapeo
        mapeo = _migrar_mapeo_pricelist_legado(repo)
        if mapeo:
            set_pricelist_mapeo(mapeo, repo)
            return mapeo
    except Exception:
        pass
    return {}


def set_pricelist_mapeo(mapeo: dict[str, dict[str, Any]], repo=None) -> None:
    global _PRICELIST_MAPEO_CACHE_UNIFICADO
    _PRICELIST_MAPEO_CACHE_UNIFICADO = mapeo
    _save_pricelist_mapeo_to_json(mapeo)
    try:
        if repo is None:
            repo = get_repo()
        repo.set_config("pricelist_mapeo_unificado", json.dumps(mapeo))
    except Exception as e:
        logger.warning("No se pudo guardar el mapeo unificado de listas: %s", e)


def get_pricelist_vigente_por_grupo(repo=None) -> dict[str, str | None]:
    """Para cada grupo categoría x moneda, el pricelist_id marcado

    ``vigente: true``, o ``None`` si ninguno lo está todavía. Usado por
    Inventario para la tabla comparativa -- ahí hace falta UN precio por
    grupo, no una lista de candidatos como en el motor de descuentos.
    """
    mapeo = get_pricelist_mapeo(repo)
    result: dict[str, str | None] = {k: None for k in _CLASIFICACION_KEYS}
    for pid, m in mapeo.items():
        cat, mon = m.get("categoria"), m.get("moneda")
        if cat and mon and m.get("vigente"):
            key = f"{cat}_{mon}"
            if key in result:
                result[key] = pid
    return result


_AJUSTE_INDUSTRIAL_PCT_DEFAULT = Decimal("0.04")


def get_ajuste_industrial_pct(repo=None) -> Decimal:
    """% de AUMENTO deseado para el fallback de precio en listas

    industriales (pedido explícito del usuario, agosto 2026) -- ej. 0.04
    para +4%, que ``FallbackFichaConfig.precio_fallback`` traduce al
    divisor correspondiente (÷0.96). Configurable vía ``app_settings``;
    0.04 si nunca se configuró.
    """
    if repo is None:
        repo = get_repo()
    try:
        val = repo.get_config("fallback_industrial_ajuste_pct")
        if val:
            return Decimal(val)
    except Exception as e:
        logger.warning("Error leyendo fallback_industrial_ajuste_pct: %s", e)
    return _AJUSTE_INDUSTRIAL_PCT_DEFAULT


def set_ajuste_industrial_pct(pct: Decimal, repo=None) -> None:
    if repo is None:
        repo = get_repo()
    repo.set_config("fallback_industrial_ajuste_pct", str(pct))


def get_diferencial_fijo_pct(repo=None) -> Decimal:
    """% de la regla de Diferencial Cambiario ``fijo_35_ves_usd`` vigente

    (hoy 35%) -- reusado tal cual por el fallback de precio en listas USD
    (pedido explícito del usuario: "el 35% va atado al descuento que esté
    configurado como vigente... no un valor separado"). Si no hay ninguna
    regla vigente de ese tipo, 0.35 por defecto (mismo valor histórico).
    """
    if repo is None:
        repo = get_repo()
    try:
        reglas = repo.descuentos_diferencial_cambiario()
        regla = next(
            (
                r
                for r in reglas
                if r.tipo_diferencial == "fijo_35_ves_usd"
                and _vigente_diferencial_local(r, date.today())
            ),
            None,
        )
        if regla is not None:
            return Decimal(str(regla.porcentaje_fijo))
    except Exception as e:
        logger.warning("Error leyendo la regla fijo_35_ves_usd vigente: %s", e)
    return Decimal("0.35")


def build_fallback_ficha_config(repo=None) -> FallbackFichaConfig:
    """Arma ``FallbackFichaConfig`` desde la config real -- un solo lugar

    que todos los constructores de ``OdooPriceResolver`` reusan, en vez de
    repetir esta lectura de config 5 veces.
    """
    if repo is None:
        repo = get_repo()
    mapeo = get_pricelist_mapeo(repo)
    moneda_por_lista: dict[int, str] = {}
    categoria_por_lista: dict[int, str] = {}
    for pid_str, m in mapeo.items():
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        if m.get("moneda"):
            moneda_por_lista[pid] = m["moneda"]
        if m.get("categoria"):
            categoria_por_lista[pid] = m["categoria"]
    return FallbackFichaConfig(
        moneda_por_lista=moneda_por_lista,
        categoria_por_lista=categoria_por_lista,
        diferencial_fijo_pct=get_diferencial_fijo_pct(repo),
        ajuste_industrial_pct=get_ajuste_industrial_pct(repo),
    )


# Tarea 2 (auditoria precios/saldos Ventas): ventana de vigencia de la Lista
# Histórica de Auditoría (ListasPreciosHistoricas / VES-BCV-Euro). Extremo
# superior verificado contra datos reales -- S00092 (primera orden con lista
# de Odoo propia y consistente tras la transición) está fechada 2026-03-13,
# por eso el corte es EXCLUSIVO ese día (órdenes hasta 2026-03-12 inclusive
# quedan históricas). Sin cota inferior no hay riesgo práctico hoy (no existen
# órdenes antes de 2026-02-25), pero se define igual para que coincida con el
# rango de negocio documentado (20-2-2026 al 13-3-2026).
HISTORICAL_PRICE_LIST_START = date(2026, 2, 20)
HISTORICAL_PRICE_LIST_END_EXCLUSIVE = date(2026, 3, 13)


def is_historical_pricelist_enabled(repo) -> bool:
    """Selector de Configuración (Tarea 1/2): permite desactivar la
    sustitución por Lista Histórica de Auditoría para órdenes fechadas en la
    ventana, sin afectar el fallback estructural de órdenes sin lista
    asignada (esas siempre necesitan algún precio de referencia). Default
    activo -- preserva el comportamiento preexistente si nadie lo toca.
    """
    try:
        val = repo.get_config("historical_pricelist_enabled")
        return val is None or val.strip().lower() not in ("false", "0", "no")
    except Exception:
        return True


def _build_hist_map(repo) -> dict[str, dict[str, Any]]:
    """Mapa código de producto -> {nombre, usd, eur} desde

    ``ListasPreciosHistoricas`` (Lista Histórica de Auditoría). Fuente
    única (agosto 2026) -- antes ``get_auditoria`` tenía su propia copia
    idéntica de este bucle, con el propio código documentando la
    duplicación como un bug preexistente (Tarea 6: variables que solo
    existían como locales de ``_get_reporte_saldos_sync``, nunca
    replicadas correctamente hasta que alguien las copió a mano)."""
    hist_rows = repo.all_listas_precios_historicas()
    hist_map: dict[str, dict[str, Any]] = {}
    for r in hist_rows:
        code = str(r.get("codigo", "")).strip()
        if code:
            with contextlib.suppress(Exception):
                hist_map[code] = {
                    "nombre": r.get("producto_nombre", ""),
                    "usd": Decimal(str(r.get("precio_usd", "0") or "0")),
                    "eur": Decimal(str(r.get("precio_bcv_euro", "0") or "0")),
                }
    return hist_map


def _facturacion_por_so_desde_espejo(
    repo, so_names: set[str] | list[str]
) -> dict[str, dict[str, dict[str, float]]]:
    """Fase 2 (plan de consolidación de fuentes, agosto 2026) -- réplica de

    la agregación por-SO de facturado/NC/ND que ``_get_ventas_sync`` arma
    hoy con 3 llamadas en vivo a Odoo (consulta principal ``account.move``
    por ``invoice_origin`` + ``_leer_notas_debito_odoo`` por
    ``debit_origin_id`` + ``_leer_notas_credito_odoo`` por
    ``reversed_entry_id``), pero leyendo del espejo ``Factura``
    (``repo.all_facturas()``) en vez de Odoo.

    Conectada a ``_get_ventas_sync`` (agosto 2026) tras validar con un
    parity check completo contra datos reales (819 órdenes sincronizadas,
    0 diffs). Se deja documentado el motivo original de por qué se
    construyó dormida primero: escribir y validar esta lógica ANTES de
    tener Postgres real disponible habría sido arriesgado para una
    página financiera -- con datos reales ya se pudo confirmar paridad
    exacta antes de conectarla.

    Una N/D o N/C sin ``so_id`` propio (Odoo no siempre puebla
    ``invoice_origin`` en esos documentos -- ver docstrings de
    ``_leer_notas_debito_odoo``/``_leer_notas_credito_odoo``) se resuelve
    siguiendo la cadena ``factura_origen_id`` hasta encontrar una factura
    con ``so_id`` propio, replicando lo que hacían esas dos consultas en
    vivo vía ``inv_id_to_so``.

    Retorna, por SO: ``facturado_con_imp``/``facturado_antes_imp`` (solo
    facturas ``out_invoice`` que NO son N/D), ``nc_con_imp`` (N/C,
    ``move_type == "out_refund"``) y ``nd_con_imp`` (N/D,
    ``es_nota_debito``). NO incluye descuentos de línea de factura
    (``_leer_descuentos_lineas_odoo``) -- ese dato no tiene espejo todavía
    (requeriría mirrorear ``account.move.line``, fuera del alcance de esta
    fase); esa función y ``_pagos_bcv_binance_por_orden`` (payment
    reconciliation, dominio de Cobranza) se quedan en vivo, pero ambas
    necesitan ``invoice_ids_all``/``inv_id_to_so``/``inv_usd_ratio_map``
    como input -- también incluidos aquí, replicando el alcance EXACTO
    (más angosto) de la consulta principal en vivo: solo facturas
    ``out_invoice``/``out_refund`` posted con ``so_id`` PROPIO (sin
    resolver vía cadena) -- las N/D nunca entraban ahí en vivo tampoco
    (``_leer_notas_debito_odoo`` es una consulta aparte que nunca
    alimentaba ``invoice_ids_all``), y una N/C resuelta vía
    ``reversed_entry_id`` (sin ``invoice_origin`` propio) tampoco.
    """
    so_set = {str(s) for s in so_names}
    facturas = repo.all_facturas()
    by_id = {f.factura_id: f for f in facturas}

    def _resolver_so(f) -> str | None:
        if f.so_id:
            return f.so_id
        origen_id = f.factura_origen_id
        vistos: set[str] = set()
        while origen_id and origen_id not in vistos:
            vistos.add(origen_id)
            padre = by_id.get(origen_id)
            if padre is None:
                return None
            if padre.so_id:
                return padre.so_id
            origen_id = padre.factura_origen_id
        return None

    facturado_con_imp: dict[str, float] = {}
    facturado_antes_imp: dict[str, float] = {}
    nc_con_imp: dict[str, float] = {}
    nd_con_imp: dict[str, float] = {}
    invoice_ids_all: list[int] = []
    inv_id_to_so: dict[int, str] = {}
    inv_usd_ratio_map: dict[int, float] = {}
    # Fase 3 (plan de arquitectura de pagos, agosto 2026): True si ALGUNA
    # factura out_invoice de la orden ya tiene la retención de IVA
    # confirmada en Odoo (account.move.wh_iva, espejado -- ver
    # Factura.wh_iva_aplicado). Reemplaza la consulta en vivo
    # _wh_iva_aplicado_por_orden.
    wh_iva_aplicado_por_so: dict[str, bool] = {}

    for f in facturas:
        if f.estado != "posted":
            continue
        so = _resolver_so(f)
        if not so or so not in so_set:
            continue
        con_imp = abs(float(f.monto_total_signed_usd))
        antes_imp = abs(float(f.monto_sin_impuestos_signed_usd))
        if f.move_type == "out_refund":
            nc_con_imp[so] = nc_con_imp.get(so, 0.0) + con_imp
        elif f.es_nota_debito:
            nd_con_imp[so] = nd_con_imp.get(so, 0.0) + con_imp
        else:
            facturado_con_imp[so] = facturado_con_imp.get(so, 0.0) + con_imp
            facturado_antes_imp[so] = facturado_antes_imp.get(so, 0.0) + antes_imp
            if f.wh_iva_aplicado:
                wh_iva_aplicado_por_so[so] = True

        if f.so_id and f.move_type in ("out_invoice", "out_refund") and f.factura_id.isdigit():
            fid = int(f.factura_id)
            invoice_ids_all.append(fid)
            inv_id_to_so[fid] = f.so_id
            amount_total_raw = float(f.monto_total)
            inv_usd_ratio_map[fid] = (
                abs(float(f.monto_total_signed_usd)) / amount_total_raw
                if amount_total_raw > 0.005
                else 1.0
            )

    return {
        "facturado_con_imp": facturado_con_imp,
        "facturado_antes_imp": facturado_antes_imp,
        "nc_con_imp": nc_con_imp,
        "nd_con_imp": nd_con_imp,
        "invoice_ids_all": invoice_ids_all,
        "inv_id_to_so": inv_id_to_so,
        "inv_usd_ratio_map": inv_usd_ratio_map,
        "wh_iva_aplicado_por_so": wh_iva_aplicado_por_so,
    }


def _entregas_desde_espejo(
    repo, so_names: set[str] | list[str]
) -> tuple[set[str], dict[str, str]]:
    """Fase 2 (plan de consolidación de fuentes, agosto 2026) -- réplica,

    leyendo del espejo ``Entrega`` (``repo.all_entregas()``), de
    ``get_live_entregas_info``: retorna ``(delivered, fecha_entrega_map)``
    con la MISMA semántica exacta (ver docstring de esa función) --
    ``delivered`` excluye órdenes con alguna devolución aunque también
    tengan un despacho válido; ``fecha_entrega_map`` se puebla para TODA
    orden con un despacho saliente terminado, con o sin devolución
    posterior, usando la fecha más reciente si hay varios.

    NO ESTÁ CONECTADA a ningún endpoint todavía -- mismo motivo que
    ``_facturacion_por_so_desde_espejo``: el espejo de Entregas no ha sido
    validado contra un Postgres real ni poblado por una corrida real del
    sync. El espejo (a diferencia de la consulta en vivo, que filtra
    ``state == "done"`` en Odoo) puede contener pickings en cualquier
    estado -- ``changed_entregas`` no filtra por estado a propósito (ver
    su docstring), así que ese filtro se aplica aquí en vez de en el
    sync.
    """
    so_set = {str(s) for s in so_names}
    entregas = repo.all_entregas()

    delivered_by_so: set[str] = set()
    returned_by_so: set[str] = set()
    fecha_entrega_map: dict[str, str] = {}

    for e in entregas:
        if not e.so_id or e.so_id not in so_set or e.estado != "done":
            continue
        if e.es_devolucion:
            returned_by_so.add(e.so_id)
        elif e.tipo == "outgoing":
            delivered_by_so.add(e.so_id)
            if e.fecha:
                fecha_str = e.fecha.isoformat()
                if e.so_id not in fecha_entrega_map or fecha_str > fecha_entrega_map[e.so_id]:
                    fecha_entrega_map[e.so_id] = fecha_str

    delivered = delivered_by_so - returned_by_so
    return delivered, fecha_entrega_map


def _litros_por_so_desde_espejo(
    repo, lineas_por_so: dict[str, list[Any]]
) -> dict[str, float]:
    """Fase 2 (plan de consolidación de fuentes, agosto 2026) -- réplica,

    leyendo del espejo ``Producto`` (``repo.all_catalogo()``), del cálculo
    de litros por SO que ``_get_ventas_sync`` arma hoy con una consulta en
    vivo a ``product.template.product_volume`` usando ids de
    ``sale.order.line.product_id`` (``product.product``, NO
    ``product.template``).

    Verificado en vivo contra Odoo (agosto 2026, muestra de 3 productos
    reales de este catálogo): ``product.product.volume`` ==
    ``product.template.product_volume`` en todos los casos probados, y
    ``product.product.id`` coincidió con ``product.template.id`` para
    esos mismos productos -- este catálogo no usa variantes, así que la
    consulta en vivo "funciona" hoy por esa coincidencia, no por una
    garantía real de Odoo. El espejo (``Producto.volumen``, sourced de
    ``product.product.volume`` vía ``changed_catalogo``) es un
    reemplazo válido y más correcto -- consulta el modelo correcto
    directamente, sin depender de esa coincidencia de ids.

    NO ESTÁ CONECTADA a ningún endpoint todavía -- mismo motivo que
    ``_facturacion_por_so_desde_espejo``/``_entregas_desde_espejo``: el
    espejo de Catálogo no ha sido poblado por una corrida real del sync
    incremental contra Postgres.
    """
    catalogo = repo.all_catalogo()
    volumen_por_producto = {p.producto_id: float(p.volumen) for p in catalogo}

    litros_por_so: dict[str, float] = {}
    for so_id, lineas in lineas_por_so.items():
        litros_por_so[so_id] = sum(
            float(ln.cantidad) * volumen_por_producto.get(str(ln.producto), 0.0)
            for ln in lineas
        )
    return litros_por_so


def _facturas_dicts_desde_espejo(repo, so_names: set[str] | list[str]) -> list[dict]:
    """Fase 4 (plan de consolidación de fuentes, agosto 2026) -- réplica,

    leyendo del espejo ``Factura``, de la consulta en vivo a
    ``account.move`` que ``_get_reporte_saldos_sync``/``get_auditoria``
    arman hoy para poblar ``invoices_by_so``/``ncs_by_so``. A diferencia
    de ``_facturacion_por_so_desde_espejo`` (que agrega a totales por
    SO), esto devuelve un dict POR FACTURA -- ambos consumidores
    (``_pagos_odoo_por_orden`` en su fallback sin ``account.payment``
    reconciliado, y el cálculo de ``saldo_factura_odoo`` en Reporte de
    Saldos/Auditoría) necesitan el detalle por factura (``amount_total``,
    ``amount_residual``, ``currency_id``, ``invoice_date``), no un
    agregado.

    ``amount_residual``/``payment_state`` son los ÚNICOS 2 campos
    genuinamente mutables de ``account.move`` (cambian con cada pago
    reconciliado) -- el espejo NUNCA los captura a propósito (ver
    docstring de ``Factura`` en ``cxc.models``). Quedan en blanco aquí
    (``amount_residual=None``, ``payment_state=""``); el llamador debe
    sobreescribirlos con ``_estado_pago_facturas_desde_odoo`` (consulta
    EN VIVO acotada solo a los ids ya resueltos por el espejo, no un
    re-scan completo) antes de usarlos.

    NO ESTÁ CONECTADA a ningún endpoint todavía -- mismo motivo que las
    demás piezas dormidas de Fase 2/4: falta validar con un parity check
    contra datos reales antes de sustituir la consulta en vivo.
    """
    so_set = {str(s) for s in so_names}
    facturas = repo.all_facturas()
    by_id = {f.factura_id: f for f in facturas}

    def _resolver_so(f) -> str | None:
        if f.so_id:
            return f.so_id
        origen_id = f.factura_origen_id
        vistos: set[str] = set()
        while origen_id and origen_id not in vistos:
            vistos.add(origen_id)
            padre = by_id.get(origen_id)
            if padre is None:
                return None
            if padre.so_id:
                return padre.so_id
            origen_id = padre.factura_origen_id
        return None

    result: list[dict] = []
    for f in facturas:
        if f.estado != "posted" or f.move_type not in ("out_invoice", "out_refund"):
            continue
        so = _resolver_so(f)
        if not so or so not in so_set:
            continue
        result.append(
            {
                "id": int(f.factura_id) if f.factura_id.isdigit() else None,
                "name": f.numero,
                "invoice_origin": so,
                "amount_total": float(f.monto_total),
                "amount_residual": None,
                "currency_id": [0, f.moneda],
                "invoice_date": f.fecha.isoformat(),
                "move_type": f.move_type,
                "payment_state": "",
            }
        )
    return result


def _estado_pago_facturas_desde_odoo(execute: Any, invoice_ids: list[int]) -> dict[int, dict]:
    """``payment_state``/``amount_residual`` EN VIVO para un conjunto de

    ids ya resueltos por ``_facturas_dicts_desde_espejo`` -- los únicos 2
    campos genuinamente mutables de ``account.move`` que el espejo nunca
    captura. Consulta acotada por id (no un re-scan de
    ``invoice_origin in so_names``), igual de barata que la que ya usa
    ``_pagos_bcv_binance_por_orden``/``_leer_descuentos_lineas_odoo``
    para el mismo conjunto de ids.
    """
    if not execute or not invoice_ids:
        return {}
    try:
        recs = execute(
            "account.move",
            "read",
            [invoice_ids],
            {"fields": ["id", "payment_state", "amount_residual"]},
        )
        return {int(r["id"]): r for r in recs}
    except Exception as e:
        logger.warning("Error consultando estado de pago en vivo: %s", e)
        return {}


def _agregar_fragmento_detalle(detalle: dict[str, str], key: str, frag: str) -> None:
    existente = detalle.get(key, "")
    detalle[key] = (existente + "; " + frag).lstrip("; ") if existente else frag


def _descuentos_lineas_desde_espejo(
    repo,
    so_names: set[str] | list[str],
    invoice_ids: list[int],
    inv_id_to_so: dict[int, str],
    inv_usd_ratio_map: dict[int, float] | None = None,
    con_detalle: bool = False,
) -> tuple[dict[str, float], dict[str, float]] | tuple[
    dict[str, float], dict[str, float], dict[str, str], dict[str, str]
]:
    """Fase 4/5 (plan de consolidación de fuentes, agosto 2026) -- réplica,

    leyendo del espejo (``LineaOrden``/``LineaFactura``), de
    ``_leer_descuentos_lineas_odoo`` (lectura de descuentos ya
    materializados en Odoo, NUNCA calcula ninguno). Misma detección de 2
    patrones que la consulta en vivo: (a) ``discount`` % > 0 en la línea,
    o (b) una línea de producto "Descuento" con ``subtotal`` negativo.

    Para (b), el nombre a chequear es el del PRODUCTO vinculado (vía
    Catálogo), NO el de la línea -- hallazgo real (agosto 2026, orden
    S00003): Odoo auto-genera el descuento por línea como una línea
    separada cuyo NOMBRE PROPIO es "Discount 20.00%" (en inglés,
    independiente del idioma de la UI), mientras el producto vinculado
    ("Descuento ") sí trae la palabra en español -- la consulta en vivo
    original filtraba por ``product_id.name``, nunca por el nombre de la
    línea. Confirmado con un parity check contra las 819 órdenes reales:
    0 diffs una vez corregido para chequear el nombre del producto.

    ``con_detalle=True`` (usado por Reporte de Saldos): además de los
    montos agregados, devuelve un string legible por SO con el detalle
    de cada línea (``"nombre: XX.X%"`` o ``"nombre: $XX.XX"``, unidas
    por "; "), replicando exactamente el formato que esa página arma hoy
    en vivo -- Ventas solo usa los montos, no este detalle.
    """
    catalogo_por_id = {p.producto_id: p.nombre.lower() for p in repo.all_catalogo()}

    def _es_linea_descuento(nombre_linea: str, producto_id: str) -> bool:
        if "descuento" in nombre_linea.lower():
            return True
        return "descuento" in catalogo_por_id.get(producto_id, "")

    so_set = {str(s) for s in so_names}
    desc_orden: dict[str, float] = {}
    desc_orden_detalle: dict[str, str] = {}
    for ln in repo.all_lineas():
        if ln.so_id not in so_set:
            continue
        disc_pct = float(ln.descuento)
        if disc_pct > 0:
            monto = float(ln.cantidad) * float(ln.precio_unitario) * (disc_pct / 100.0)
            frag = f"{(ln.nombre or 'línea')[:40]}: {disc_pct:.1f}%"
        elif float(ln.subtotal) < 0 and _es_linea_descuento(ln.nombre, ln.producto):
            monto = abs(float(ln.subtotal))
            frag = f"{(ln.nombre or 'Descuento')[:40]}: ${monto:.2f}"
        else:
            continue
        desc_orden[ln.so_id] = desc_orden.get(ln.so_id, 0.0) + monto
        if con_detalle:
            _agregar_fragmento_detalle(desc_orden_detalle, ln.so_id, frag)

    invoice_ids_set = set(invoice_ids)
    desc_factura: dict[str, float] = {}
    desc_factura_detalle: dict[str, str] = {}
    for lf in repo.all_lineas_factura():
        if not lf.factura_id.isdigit():
            continue
        fid = int(lf.factura_id)
        if fid not in invoice_ids_set:
            continue
        so_name = inv_id_to_so.get(fid, "")
        if not so_name:
            continue
        disc_pct = float(lf.descuento)
        if disc_pct > 0:
            monto = float(lf.cantidad) * float(lf.precio_unitario) * (disc_pct / 100.0)
            frag = f"{(lf.nombre or 'línea')[:40]}: {disc_pct:.1f}%"
        elif float(lf.subtotal) < 0 and _es_linea_descuento(lf.nombre, lf.producto_id):
            monto = abs(float(lf.subtotal))
            frag = f"{(lf.nombre or 'Descuento')[:40]}: ${monto:.2f}"
        else:
            continue
        ratio = (inv_usd_ratio_map or {}).get(fid, 1.0)
        desc_factura[so_name] = desc_factura.get(so_name, 0.0) + monto * ratio
        if con_detalle:
            _agregar_fragmento_detalle(desc_factura_detalle, so_name, frag)

    if con_detalle:
        return desc_orden, desc_factura, desc_orden_detalle, desc_factura_detalle
    return desc_orden, desc_factura


def _productos_despachados_desde_espejo(
    repo, so_names: set[str] | list[str]
) -> dict[str, set[int]]:
    """Fase 5 (plan de consolidación de fuentes, agosto 2026) -- réplica,

    leyendo del espejo (``Entrega``/``EntregaLinea``), del bloque en vivo
    de ``get_auditoria`` (Check 5: "productos realmente despachados") que
    arma ``delivered_products_by_so`` consultando ``sale.order`` +
    ``stock.picking`` + ``stock.move.line`` en vivo -- usado para detectar
    si se despachó un producto distinto o adicional al pedido.

    Mismo criterio EXACTO que la consulta en vivo: solo pickings
    ``state == "done"`` y ``picking_type_code == "outgoing"`` -- sin
    excluir devoluciones explícitamente (la consulta en vivo tampoco lo
    hace; un picking outgoing con ``return_id`` seteado, si existiera,
    igual contaría aquí).

    NO ESTÁ CONECTADA a ningún endpoint todavía.
    """
    so_set = {str(s) for s in so_names}
    entrega_to_so: dict[str, str] = {
        e.entrega_id: e.so_id
        for e in repo.all_entregas()
        if e.so_id in so_set and e.estado == "done" and e.tipo == "outgoing"
    }
    if not entrega_to_so:
        return {}

    result: dict[str, set[int]] = {}
    for ln in repo.all_entregas_lineas():
        so_name = entrega_to_so.get(ln.entrega_id)
        if not so_name or not ln.producto_id.isdigit():
            continue
        result.setdefault(so_name, set()).add(int(ln.producto_id))
    return result


def orden_en_periodo_historico(repo, orden) -> bool:
    """True si ``orden`` cae en la ventana de la Lista Histórica de Auditoría
    (Tarea 2) y el toggle correspondiente está activo."""
    if orden is None or not isinstance(getattr(orden, "fecha", None), date):
        return False
    try:
        return is_historical_pricelist_enabled(repo) and (
            HISTORICAL_PRICE_LIST_START <= orden.fecha < HISTORICAL_PRICE_LIST_END_EXCLUSIVE
        )
    except Exception:
        return False


def resolver_tasa_bcv_vinculacion(
    repo, so_id: str, hora_pago: datetime, tasa_bcv_default: Decimal
) -> tuple[Decimal, str]:
    """Tasa BCV a aplicar a una Vinculación nueva + su variante ('USD'/'EUR').

    Tarea 2: las órdenes de la ventana histórica (20-Feb al 12-Mar-2026
    inclusive) se pagaron con la tasa BCV-Euro como referencia, no la BCV-USD
    normal -- si no hay tasa BCV-Euro capturada en SerieTasas para esa fecha,
    se cae a la tasa BCV-USD normal (mejor tener algo que bloquear el
    vínculo) y queda con variante 'USD' igual, para no fingir una tasa que no
    existe.
    """
    try:
        orden = repo.get_orden(so_id)
    except Exception:
        orden = None
    if not orden_en_periodo_historico(repo, orden):
        return tasa_bcv_default, "USD"
    try:
        tasas_rows = _all_serie_tasas_rows(repo)
        tasa_eur = get_bcv_euro_rate_for_datetime(hora_pago, tasas_rows)
    except Exception:
        tasa_eur = None
    if tasa_eur and tasa_eur > Decimal("0"):
        return tasa_eur, "EUR"
    return tasa_bcv_default, "USD"


def get_valid_pricelists_usd_and_ves(repo=None) -> tuple[list[str], list[str]]:
    """Listas de precio USD/VES que alimentan el motor -- deriva del

    mapeo unificado (``get_pricelist_mapeo``, campo ``moneda``). Preserva
    la cadena de fallback existente: TODAS las listas con moneda="usd"
    (o "ves") cuentan, sin importar su categoría/vigencia -- eso solo
    afecta qué se muestra en Inventario, no qué usa el motor para
    resolver precios (ver ``_primer_id_activo``/``OdooPriceResolver``).
    """
    mapeo = get_pricelist_mapeo(repo)
    usd_list = [pid for pid, m in mapeo.items() if m.get("moneda") == "usd"]
    ves_list = [pid for pid, m in mapeo.items() if m.get("moneda") == "ves"]
    if usd_list:
        return usd_list, ves_list
    usd_env = os.environ.get("ODOO_PRICELIST_USD", "4")
    ves_env = os.environ.get("ODOO_PRICELIST_BCV", "5")
    return [x.strip() for x in usd_env.split(",") if x.strip()], [
        x.strip() for x in ves_env.split(",") if x.strip()
    ]


def get_pricelist_clasificacion(repo=None) -> dict[str, list[str]]:
    """Listas Industrial/Comercial x USD/VES -- deriva del mapeo unificado

    (campos ``categoria``+``moneda``). Solo de consulta (Inventario), no
    alimenta el motor -- ver docstring de ``get_pricelist_mapeo``.
    """
    mapeo = get_pricelist_mapeo(repo)
    result: dict[str, list[str]] = {k: [] for k in _CLASIFICACION_KEYS}
    for pid, m in mapeo.items():
        cat, mon = m.get("categoria"), m.get("moneda")
        if cat and mon:
            key = f"{cat}_{mon}"
            if key in result:
                result[key].append(pid)
    return result


@app.get("/api/config/pricelist-mapeo")
async def get_config_pricelist_mapeo():
    """Mapeo UNIFICADO (moneda/categoría/vigente por lista) -- reemplaza

    los antiguos /api/config/listas-precio-mapeo y
    /api/config/listas-precio-clasificacion (dos endpoints, dos fuentes
    de verdad para la misma lista de precios -- unificado a pedido
    explícito del usuario, agosto 2026).
    """
    try:
        repo = get_repo()
        return {
            "mapeo": get_pricelist_mapeo(repo),
            "historical_pricelist_enabled": is_historical_pricelist_enabled(repo),
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/pricelist-mapeo")
async def post_config_pricelist_mapeo(req: PricelistMapeoUnificadoRequest):
    try:
        mapeo = {
            str(pid): {
                "moneda": fila.moneda if fila.moneda in ("usd", "ves") else "",
                "categoria": (
                    fila.categoria if fila.categoria in ("industrial", "comercial") else ""
                ),
                "vigente": bool(fila.vigente),
            }
            for pid, fila in req.mapeo.items()
        }
        repo = get_repo()
        set_pricelist_mapeo(mapeo, repo)
        try:
            repo.set_config(
                "historical_pricelist_enabled",
                "true" if req.historical_pricelist_enabled else "false",
            )
        except Exception as cfg_err:
            logger.warning("No se pudo guardar historical_pricelist_enabled: %s", cfg_err)
        return {
            "status": "success",
            "message": "Mapeo de listas de precios actualizado correctamente.",
            "mapeo": mapeo,
            "historical_pricelist_enabled": req.historical_pricelist_enabled,
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/inventario/catalogo")
async def get_inventario_catalogo():
    """Ficha descriptiva de presentaciones (Fase D, plan de Inventario/

    Catálogo, agosto 2026, pedido explícito del usuario) -- código,
    nombre, presentación, litros, peso, unidades por paleta. Todo sale
    del espejo local (``repo.all_catalogo()``), SIN llamadas a Odoo en
    vivo. La presentación se deriva del nombre con la MISMA regex que
    ``OdooXmlRpcReader._productos`` (contenido entre paréntesis al final
    del nombre) -- no se duplica el dato en el espejo, se recalcula acá
    porque es barato y puro.
    """
    try:
        repo = get_repo()
        resultado = []
        for p in repo.all_catalogo():
            m = re.search(r"\(([^)]*)\)\s*$", (p.nombre or "").strip())
            resultado.append(
                {
                    "producto_id": p.producto_id,
                    "codigo": p.codigo,
                    "nombre": p.nombre,
                    "marca": p.marca,
                    "presentacion": m.group(1).strip().upper() if m else "",
                    "litros": float(p.volumen),
                    "peso": float(p.peso),
                    "unidades_por_paleta": float(p.unidades_por_paleta),
                }
            )
        resultado.sort(key=lambda r: r["nombre"])
        return resultado
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/inventario/comparativo")
async def get_inventario_comparativo(categoria: str):
    """Tabla comparativa USD vs VES para una categoría (Industrial o

    Comercial), estilo las sub-pestañas de Auditoría (pedido explícito
    del usuario, agosto 2026) -- reemplaza a ``/api/inventario/listas``
    (mostraba nombres de lista sin precios; esto sí resuelve el precio
    real por producto en la lista "vigente" USD y VES de la categoría,
    CON IVA incluido, que es lo que el usuario pidió ver).

    Usa ``get_pricelist_vigente_por_grupo`` (mapeo unificado) para saber
    CUÁL lista es "la" vigente de cada moneda en esta categoría -- si
    todavía no se marcó ninguna como vigente, ese lado queda vacío
    (``None``) en vez de adivinar.
    """
    try:
        if categoria not in ("industrial", "comercial"):
            raise HTTPException(
                status_code=400, detail="categoria debe ser 'industrial' o 'comercial'"
            )
        repo = get_repo()
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        vigentes = get_pricelist_vigente_por_grupo(repo)
        usd_id_str = vigentes.get(f"{categoria}_usd")
        ves_id_str = vigentes.get(f"{categoria}_ves")
        iva_rate = float(config.engine.iva_rate)

        catalogo = repo.all_catalogo()
        cand_ids = [int(x) for x in (usd_id_str, ves_id_str) if x]

        precios_usd: dict[int, float] = {}
        precios_ves: dict[int, float] = {}
        if execute and cand_ids:
            prod_ids = [int(p.producto_id) for p in catalogo if p.producto_id.isdigit()]
            rules = execute(
                "product.pricelist.item",
                "search_read",
                [
                    [
                        ["pricelist_id", "in", cand_ids],
                        ["product_tmpl_id", "in", prod_ids],
                        ["compute_price", "=", "fixed"],
                    ]
                ],
                {"fields": ["pricelist_id", "product_tmpl_id", "fixed_price"]},
            )
            for r in rules:
                pl_raw = r.get("pricelist_id")
                pl_id = pl_raw[0] if isinstance(pl_raw, list | tuple) else pl_raw
                pt_raw = r.get("product_tmpl_id")
                pt_id = pt_raw[0] if isinstance(pt_raw, list | tuple) else pt_raw
                if not pt_id:
                    continue
                if usd_id_str and pl_id == int(usd_id_str):
                    precios_usd[pt_id] = float(r.get("fixed_price") or 0.0)
                if ves_id_str and pl_id == int(ves_id_str):
                    precios_ves[pt_id] = float(r.get("fixed_price") or 0.0)

        items = []
        for p in catalogo:
            if not p.producto_id.isdigit():
                continue
            pid = int(p.producto_id)
            if pid not in precios_usd and pid not in precios_ves:
                continue
            items.append(
                {
                    "producto_id": p.producto_id,
                    "codigo": p.codigo,
                    "nombre": p.nombre,
                    "precio_usd_con_iva": (
                        round(precios_usd[pid] * (1 + iva_rate), 2) if pid in precios_usd else None
                    ),
                    "precio_ves_con_iva": (
                        round(precios_ves[pid] * (1 + iva_rate), 2) if pid in precios_ves else None
                    ),
                }
            )
        items.sort(key=lambda r: r["nombre"])
        return {
            "categoria": categoria,
            "usd_lista_id": usd_id_str,
            "ves_lista_id": ves_id_str,
            "iva_rate": iva_rate,
            "items": items,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/eliminar-descuento")
async def post_eliminar_descuento(req: EliminarDescuentoRequest):
    try:
        repo = get_repo()
        deleted = repo.delete_regla(req.tabla, req.regla_id)
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


def _get_saldos_reales_por_so_sync() -> dict[str, float] | None:
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
        reporte = _get_reporte_saldos_sync(refresh=False)
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
        for r in repo.all_pagos_huerfanos_cerrados()
        if r.get("pago_id")
    }


def _get_conciliaciones_sugerencias_sync(cxc_session: str | None):
    """Cuerpo síncrono de ``get_conciliaciones_sugerencias`` (Fase 1

    restante, agosto 2026) -- ver docstring de ``_get_reporte_saldos_sync``,
    mismo patrón. Ya no es la fuente principal de la UI -- absorbida por
    ``GET /api/cobranza/pagos`` (``get_cobranza_pagos_unificado``), que
    llama esta función directamente. Se conserva como ruta pública además
    de función interna reusable: sigue siendo un endpoint válido y
    probado, con cobertura de tests que documentan bugs reales corregidos.
    """
    try:
        repo = get_repo()
        user = get_current_user_from_cookie(cxc_session)

        pagos_rows = _all_pagos_rows(repo)
        vincs = repo.all_vinculaciones()
        ordenes = repo.all_ordenes()
        clientes_map = {c.cliente_id: c.nombre for c in repo.all_clientes()}
        tasas_rows = _all_serie_tasas_rows(repo)
        tasas_historicas_rows = repo.all_tasas_historicas_auditoria()
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

        # Bug real (reportado por el usuario, cliente Emprendimiento Tomas
        # Marcano 5): un pago huérfano (aún sin reconciliar en Odoo, así
        # que ``tax_today`` no refleja ningún contexto de orden -- es solo
        # la tasa BCV oficial genérica del día) de un cliente cuyas órdenes
        # abiertas son de la ventana histórica debe convertirse a tasa
        # BCV-EUR, no BCV-USD normal. Se resuelve por CLIENTE (no hay
        # orden específica aún -- un pago huérfano por definición no está
        # cruzado contra ninguna) usando el mismo criterio que ya usa
        # ``_pagos_bcv_binance_por_orden``.
        _historical_enabled_sug = is_historical_pricelist_enabled(repo)
        clientes_con_orden_historica: set[str] = {
            str(o.cliente_id)
            for o in ordenes
            if es_orden_historica(o.fecha, o.lista_precios, _historical_enabled_sug)
        }

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
            cliente_id_pago = str(p.get("cliente_id", "")).strip()
            if moneda == "VES" and odoo_info:
                tax_today = parse_decimal_safe(str(odoo_info.get("tax_today") or "0"))
                if tax_today > Decimal("0"):
                    bcv_rate = tax_today
                    amount_ref = parse_decimal_safe(str(odoo_info.get("amount_ref") or "0"))
                    if amount_ref > Decimal("0"):
                        monto_orig_usd_odoo = amount_ref
            if moneda == "VES" and cliente_id_pago in clientes_con_orden_historica:
                # Pago aún sin reconciliar -- tax_today (si vino) es solo la
                # tasa BCV genérica del día, sin contexto de orden histórica.
                # Se sustituye por BCV-EUR (SerieTasas primero, luego
                # TasasHistoricasAuditoria), invalidando también el
                # amount_ref de Odoo (calculado con la tasa BCV normal).
                tasa_eur_huerfano = get_bcv_euro_rate_for_datetime(fecha_dt, tasas_rows)
                if not tasa_eur_huerfano or tasa_eur_huerfano <= Decimal("0"):
                    tasa_eur_huerfano = get_eur_rate_for_date(
                        fecha_dt.date(), tasas_historicas_rows
                    )
                if tasa_eur_huerfano and tasa_eur_huerfano > Decimal("0"):
                    bcv_rate = tasa_eur_huerfano
                    monto_orig_usd_odoo = None
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
                # "vendedor" (a secas) nunca existió como columna real en
                # Pagos -- Sheets solo tiene "vendedor_email" (ver
                # serde.pago_to_row); usarlo daba "Sin Vendedor" siempre y
                # ese pago quedaba invisible para cualquier usuario con rol
                # "ventas" (visible_to_user más abajo).
                vendedor = p.get("vendedor_email") or p.get("vendedor") or "Sin Vendedor"
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

        saldos_reales = _get_saldos_reales_por_so_sync()

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


@app.get("/api/conciliaciones/sugerencias")
async def get_conciliaciones_sugerencias(cxc_session: str | None = Cookie(default=None)):
    return await asyncio.to_thread(_get_conciliaciones_sugerencias_sync, cxc_session)


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
        repo.upsert_pago_huerfano_cerrado(
            {
                "pago_id": req.pago_id,
                "motivo": req.motivo,
                "cerrado_por": cerrado_por,
                "timestamp_cierre": datetime.now().isoformat(),
            }
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
    """Bandeja 1/2 de Facturación + Bandeja de Auditoría de Precios.

    Fuente única de verdad para pagado-vs-teóricos/facturada: reusa
    ``/api/ventas`` (mismos cálculos, incluye el árbol de enrutamiento de
    CxC -- ver ``cxc_routing.clasificar_estado_cxc`` y la Sección 5 del
    Manual del Proceso Administrativo). Antes esta bandeja recalculaba su
    propio criterio (un único ``saldo_motor`` blend BCV+Binance contra
    ``BandejaFacturacion.total_motor``); ahora entra a Bandeja 1/2 según
    ``item["bandeja_destino"]`` calculado allá, que compara por separado
    el Teórico Lista BS (BCV) y el Teórico Lista USD (Binance).

    - **Bandeja 1** (``facturacion_1``): pagado vs algún teórico, orden
      SIN factura todavía -- lista para facturar en Odoo.
    - **Bandeja 2** (``facturacion_2``): igual pero orden YA facturada.
      Se reparte en dos listas según ``descuento_pendiente_aplicar``
      (Fase 6 de ``/api/ventas``) sea > $0.05 o no:
      ``descuentos_pendientes_aprobar`` (NUEVA, Fase 3 de la auditoría del
      ciclo CxC) -- el cliente ya pagó lo que corresponde con el
      descuento teórico, pero ese descuento no existe todavía como NC en
      Odoo; y ``notas_credito_pendientes`` -- sin descuento pendiente,
      solo diferencial cambiario o pronto pago (la tolerancia normal del
      motor, nada que aprobar).
    - **Bandeja de Auditoría de Precios** (``auditoria_precios``, NUEVA):
      pagado vs la Factura Neta Real en Odoo pero NO vs ningún teórico --
      sospecha de facturación con precio/lista por debajo del estándar
      autorizado. La orden permanece en CxC activa (no sale de ninguna
      bandeja anterior), esta es visibilidad adicional.

    Retención de IVA (clientes agentes de retención): en Bandeja 1 (orden
    SIN facturar todavía), un cliente agente de retención no paga en
    efectivo la porción de IVA que retiene (entrega comprobante en su
    lugar) -- se aplica una tolerancia sobre el teórico correspondiente
    antes de exigir "pagada" (ver ``_tolerancia_retencion`` abajo). Esta
    tolerancia NO aplica a Bandeja 2 ni a Auditoría de Precios (ya
    facturadas -- la retención post-factura la maneja Bandeja 3,
    ``iva_pendiente_agentes``, sin cambios en este endpoint).
    """
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        ordenes_map = {o.so_id: o for o in ordenes}
        bandeja_rows = repo.all_bandeja()
        bandeja_map = {b.so_id: b for b in bandeja_rows}
        clientes_map = {c.cliente_id: c for c in repo.all_clientes()}
        # Fase 0: solo Vinculaciones CONCILIADO cuentan como pagado real
        # para decidir si una orden sale de CxC activa.
        vincs = [v for v in repo.all_vinculaciones() if v.estado == EstadoVinculacion.CONCILIADO]

        pagos_by_so = {}
        for v in vincs:
            eq = (
                v.equiv_usd_binance
                if v.equiv_usd_binance is not None
                else (v.equiv_usd_bcv if v.equiv_usd_bcv is not None else v.monto_aplicado)
            )
            pagos_by_so[v.so_id] = pagos_by_so.get(v.so_id, Decimal("0")) + eq

        # Fase 3: descuentos de sistema ya aprobados (activos), para mostrar
        # el badge "Descuento aprobado" en Bandeja 1 sin llamada aparte.
        descuento_sistema_map = {
            r["so_id"]: r
            for r in repo.all_descuentos_sistema_aprobados()
            if str(r.get("activo", "true")).strip().lower() not in ("false", "0", "no")
        }

        ventas_data = await get_ventas(vendedor=None, cxc_session=None)
        ventas_items = {it["so_id"]: it for it in ventas_data["items"]}

        def _tolerancia_retencion(
            target: float, pagado: float, wh_rate: float
        ) -> bool:
            """True si lo pagado cubre el teórico salvo la porción de IVA
            retenida (agentes de retención no pagan esa porción en efectivo)."""
            if target <= 0.05:
                return True
            subtotal = target / 1.16
            iva_retenido = (target - subtotal) * (wh_rate / 100.0)
            return pagado >= target - iva_retenido - 0.05

        ordenes_por_facturar = []
        notas_credito_pendientes = []
        descuentos_pendientes_aprobar = []
        iva_pendiente_agentes = []
        auditoria_precios = []

        for so_id, item in ventas_items.items():
            o = ordenes_map.get(so_id)
            if o is None:
                continue

            c_info = clientes_map.get(str(o.cliente_id))
            c_name = item["cliente_nombre"]
            wh_agent = bool(c_info.wh_iva_agent) if c_info else False
            wh_rate = float(c_info.wh_iva_rate) if c_info else 75.0

            b = bandeja_map.get(o.so_id)
            abono = float(pagos_by_so.get(o.so_id, Decimal("0")))
            monto_orig = float(o.monto_total)

            tot_motor = float(b.total_motor) if b else monto_orig
            desc_monto = float(b.total_descuentos + b.ncs_calculadas) if b else 0.0
            desc_pct = (
                (desc_monto / monto_orig * 100.0) if (monto_orig > 0 and desc_monto > 0) else 0.0
            )

            teorico_bs_pagado = item["estatus_pago_teorico_ves"] == "pagada"
            teorico_usd_pagado = item["estatus_pago_teorico_usd"] == "pagada"
            factura_real_pagada = item["estatus_pago_real_factura"] == "pagada"

            # Tolerancia de retención de IVA -- solo relevante mientras la
            # orden no está facturada (gate de Bandeja 1); una vez
            # facturada, la retención la maneja Bandeja 3 por separado.
            if wh_agent and not o.facturada:
                if not teorico_bs_pagado:
                    teorico_bs_pagado = _tolerancia_retencion(
                        item.get("ves_neta_teorica_iva") or 0.0,
                        item.get("pagado_teorico_bcv", 0.0),
                        wh_rate,
                    )
                if not teorico_usd_pagado:
                    teorico_usd_pagado = _tolerancia_retencion(
                        item.get("usd_neta_teorica_iva") or 0.0,
                        item.get("pagado_teorico_binance", 0.0),
                        wh_rate,
                    )

            clasificacion = clasificar_estado_cxc(
                so_id=so_id,
                facturada=bool(o.facturada),
                teorico_bs_pagado=teorico_bs_pagado,
                teorico_usd_pagado=teorico_usd_pagado,
                factura_real_pagada=factura_real_pagada,
                nacio_en_lista_usd=bool(item.get("nacio_en_lista_usd")),
            )

            if clasificacion.bandeja_destino == BandejaDestino.AUDITORIA_PRECIOS:
                auditoria_precios.append(
                    {
                        "so_id": o.so_id,
                        "cliente_nombre": c_name,
                        "fecha": o.fecha.isoformat()
                        if hasattr(o.fecha, "isoformat")
                        else str(o.fecha),
                        "ves_neta_teorica_iva": item.get("ves_neta_teorica_iva"),
                        "usd_neta_teorica_iva": item.get("usd_neta_teorica_iva"),
                        "venta_neta_real": item.get("venta_neta_real"),
                        "total_facturado_neto": item.get("total_facturado_neto"),
                        "lista_aplicada_label": item.get("lista_aplicada_label"),
                        "motivo": clasificacion.motivo,
                    }
                )

            if clasificacion.bandeja_destino == BandejaDestino.FACTURACION_1:
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
                        "subtotal_neto": round(tot_motor / 1.16, 2) if tot_motor else 0.0,
                        "iva_estimado": (
                            round(tot_motor - tot_motor / 1.16, 2) if tot_motor else 0.0
                        ),
                        "total_motor": tot_motor,
                        "saldo_pendiente": round(max(0.0, tot_motor - abono), 2),
                        "descuento_aplicar_monto": desc_monto,
                        "descuento_aplicar_pct": desc_pct,
                        # Fase 2 (auditoría del ciclo CxC): a diferencia de
                        # `descuento_aplicar_monto` (lo que el motor calculó
                        # en TOTAL, sin ver si ya se aplicó), esta es la
                        # cifra dinámica de `/api/ventas` ("Fase 6") que ya
                        # neta lo que Odoo/NC/descuento de sistema ya
                        # cubrieron -- lo que realmente falta por aplicar
                        # ahora mismo. Ya estaba calculada en `item`, solo
                        # no se reusaba aquí.
                        "descuento_pendiente_por_aplicar": item.get("descuento_pendiente_aplicar"),
                        "precio_base": monto_orig,
                        "descuento_sistema_aprobado": (
                            round(float(descuento_sistema_map[o.so_id]["monto"]), 2)
                            if o.so_id in descuento_sistema_map
                            else None
                        ),
                        "descuento_sistema_motivo": (
                            descuento_sistema_map[o.so_id]["motivo"]
                            if o.so_id in descuento_sistema_map
                            else None
                        ),
                        "cxc_routing_motivo": clasificacion.motivo,
                    }
                )
            elif o.facturada:
                # Nota: el bloque de arriba (not facturada) ya se sale con su
                # propio "if"; este "elif o.facturada" evita que ordenes SIN
                # factura pero con saldo pendiente (> $0.05) caigan aqui por
                # error -- NC pendiente y retencion de IVA solo aplican a
                # ordenes ya facturadas en Odoo.
                if clasificacion.bandeja_destino == BandejaDestino.FACTURACION_2:
                    # Bandeja 2 (nuevo criterio, Sección 5 del Manual): pagado
                    # vs algún teórico Y ya facturada -- pendiente ajustes
                    # (diferencial cambiario o pronto pago). Antes se usaba
                    # "tiene NC calculada por el motor > 0.01" como criterio
                    # de entrada, un concepto distinto (ver docstring del
                    # endpoint); si el motor sí calculó una NC pendiente, se
                    # sigue mostrando aquí como dato adicional (nc_monto).
                    #
                    # Fase 3 (auditoría del ciclo CxC, agosto 2026): esta
                    # misma condición de entrada mezclaba DOS motivos
                    # distintos bajo un solo texto genérico -- "diferencial
                    # cambiario / pronto pago" (nada por aprobar, es la
                    # tolerancia normal del motor) vs. "el cliente ya pagó
                    # lo que corresponde con el descuento teórico aplicado,
                    # pero ese descuento todavía no existe como NC en Odoo"
                    # (escenarios 1.3-1.7 y sus espejos en USD/lista nativa
                    # del análisis). Se separan en dos listas usando la
                    # misma cifra dinámica de `/api/ventas` ("Fase 6",
                    # `descuento_pendiente_aplicar` -- ya neta lo que Odoo/
                    # NC/sistema ya cubrieron) en vez de crear un cálculo
                    # paralelo.
                    descuento_pend = float(item.get("descuento_pendiente_aplicar") or 0.0)
                    if descuento_pend > 0.05:
                        detalles_b = b.descuentos_detalle if b else []
                        es_diferencial = any(d.origen == "bcv_completo" for d in detalles_b)
                        descuentos_pendientes_aprobar.append(
                            {
                                "so_id": o.so_id,
                                "cliente_nombre": c_name,
                                "factura_id": o.factura_id or "Odoo",
                                "monto_pagado": abono,
                                "descuento_pendiente_aplicar": round(descuento_pend, 2),
                                "descuento_pendiente_pct": round(
                                    descuento_pend / monto_orig * 100.0, 2
                                )
                                if monto_orig > 0
                                else 0.0,
                                "incluye_diferencial_cambiario": es_diferencial,
                                "descuentos_detalle": [
                                    {
                                        "origen": d.origen,
                                        "descripcion": d.descripcion,
                                        "monto": float(d.monto),
                                    }
                                    for d in detalles_b
                                ],
                                "estado": "Pendiente Aprobar Descuento / NC",
                                "cxc_routing_motivo": clasificacion.motivo,
                            }
                        )
                    else:
                        nc_calc = float(b.ncs_calculadas) if b else 0.0
                        detalles_b = b.descuentos_detalle if b else []
                        detalle_nc = next(
                            (d for d in detalles_b if d.origen == "primera_compra"), None
                        )
                        concepto = (
                            detalle_nc.descripcion
                            if detalle_nc
                            else "Pendiente ajuste (diferencial cambiario o pronto pago)"
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
                                "cxc_routing_motivo": clasificacion.motivo,
                            }
                        )

                # Tarea 5: retencion de IVA. El cliente-agente de retencion no
                # paga en efectivo la porcion de IVA que retiene; en su lugar
                # entrega un comprobante de retencion. El saldo que el motor
                # ve pendiente (monto_factura - abono, ya con descuentos
                # aplicados) es "normal" si cabe dentro del IVA total de la
                # factura -- no es que el cliente deba mas, es que falta el
                # comprobante. IVA Venezuela = 16%.
                #
                # Hallazgo real (caso S00851, agosto 2026, pedido explicito
                # del usuario): dos bugs encontrados juntos.
                # 1. `abono` (arriba) sale SOLO de Vinculaciones -- una orden
                #    con pago real reconciliado en Odoo pero sin Vinculacion
                #    manual todavia se veia como "$0 pagado" aca, aunque
                #    Ventas ya resuelve exactamente este mismo caso via
                #    `monto_pagado_factura_odoo` (Vinculacion tiene
                #    precedencia si existe; si no, cae al pago reconciliado
                #    en vivo). Se reusa esa misma fuente unica de verdad aca
                #    en vez de tener una segunda logica de "cuanto se pago"
                #    que puede desincronizarse.
                # 2. El % de retencion real puede variar por documento (no es
                #    fijo por cliente pese a que `wh_iva_rate` es un campo
                #    del cliente) -- verificado en vivo, S00851: el cliente
                #    tiene wh_iva_rate=75% guardado, pero en esa factura
                #    puntual retuvo el 100% del IVA. Comparar el saldo
                #    pendiente contra "75% del IVA" nunca iba a calificar. Se
                #    acepta cualquier saldo pendiente que quepa en el IVA
                #    TOTAL de la factura (0-100% de retencion, sin asumir un
                #    porcentaje fijo) -- `wh_iva_rate`/`retencion_iva_est`
                #    quedan solo como referencia informativa en la UI, ya no
                #    como criterio de entrada a esta bandeja.
                #
                # 3. Salida de la bandeja (pedido explicito del usuario,
                #    mismo dia): la retencion se aplica MANUALMENTE en Odoo
                #    -- una vez procesada, la orden debe dejar de aparecer
                #    sola, sin depender de inferirlo por el saldo. Odoo
                #    expone esto directo en `account.move.wh_iva` (boolean,
                #    "¿Ya se ha retenido esta factura con el IVA?"),
                #    respaldado por el documento `account.wh.iva` (estado
                #    draft/confirmed/done/cancel, ligado via `move_id`) --
                #    verificado en vivo con la factura 9872 de S00851: ya
                #    existe un documento de retencion pero sigue en
                #    "draft" con `wh_iva=False`, confirmando que aun
                #    corresponde mostrarla aqui. `wh_iva_aplicado` se lee
                #    del espejo `Factura` (Fase 3, agosto 2026 -- antes
                #    era una consulta en vivo, `_wh_iva_aplicado_por_orden`,
                #    ya retirada) y ya viene resuelto en `item` desde
                #    Ventas.
                #
                # 4. Alcance de la bandeja (pedido explicito del usuario,
                #    2026-08-20): "todo el que deba el IVA entra a
                #    revision" -- ya NO se exige `wh_agent` (cliente
                #    marcado como agente de retencion) para entrar aqui.
                #    Un cliente SIN ese flag que igual deja pendiente un
                #    saldo que cabe dentro del IVA de su factura no es una
                #    retencion legitima (en Venezuela solo los agentes
                #    designados retienen IVA por ley) -- pero igual amerita
                #    revision manual (puede ser un agente no catalogado
                #    todavia, un error del cliente, o un pago incompleto
                #    real). Se distingue con `es_agente_retencion` para que
                #    la UI no lo etiquete como una retencion normal.
                if not item.get("wh_iva_aplicado"):
                    monto_factura_real = float(item.get("total_facturado_neto") or 0.0) or tot_motor
                    subtotal_est = monto_factura_real / 1.16
                    iva_total_est = monto_factura_real - subtotal_est
                    iva_retenido_est = iva_total_est * (wh_rate / 100.0) if wh_agent else 0.0
                    abono_odoo = float(item.get("monto_pagado_factura_odoo") or 0.0)
                    saldo_pendiente_motor = monto_factura_real - abono_odoo
                    if 0.05 < saldo_pendiente_motor <= iva_total_est + 0.05:
                        iva_pendiente_agentes.append(
                            {
                                "so_id": o.so_id,
                                "cliente_nombre": c_name,
                                "factura_id": o.factura_id or "Odoo",
                                "monto_factura": monto_factura_real,
                                "wh_iva_rate": wh_rate,
                                "es_agente_retencion": wh_agent,
                                "base_cobrada": round(subtotal_est, 2),
                                "iva_total_estimado": round(iva_total_est, 2),
                                "retencion_iva_est": round(iva_retenido_est, 2),
                                "monto_iva_retenido_est": round(iva_retenido_est, 2),
                                "monto_pagado": abono_odoo,
                                "saldo_pendiente": round(saldo_pendiente_motor, 2),
                                "estado_comprobante": (
                                    "Pendiente Comprobante IVA"
                                    if wh_agent
                                    else "Debe IVA -- sin agente de retención registrado"
                                ),
                                "estado": (
                                    "Pendiente Comprobante IVA"
                                    if wh_agent
                                    else "Debe IVA -- sin agente de retención registrado"
                                ),
                            }
                        )

        # Bandeja "Pendientes por Cerrar": reusa el mismo cálculo de
        # /api/reporte-saldos (saldo_minimo_pendientes, misma fuente única
        # de verdad -- ver clasificar_estado_cxc) en vez de recalcularlo
        # aquí; evita una quinta implementación paralela del criterio
        # "ya pagada, falta cerrar en Odoo" que podría divergir con el
        # tiempo (mismo problema que ya se corrigió en get_auditoria).
        # Best-effort: si el cálculo pesado de reporte-saldos falla (Odoo
        # caído, etc.), las otras 4 bandejas de este endpoint no deben
        # romperse por eso -- se muestra esta lista vacía en su lugar.
        try:
            reporte_data = await asyncio.to_thread(_get_reporte_saldos_sync, False)
            pendientes_por_cerrar = reporte_data.get("saldo_minimo_pendientes", [])
        except Exception as e_pc:
            logger.warning("No se pudo cargar 'Pendientes por Cerrar' en /api/bandeja: %s", e_pc)
            pendientes_por_cerrar = []

        return {
            "ordenes_por_facturar": ordenes_por_facturar,
            "notas_credito_pendientes": notas_credito_pendientes,
            # Fase 3 (auditoría del ciclo CxC): bandeja nueva -- el cliente
            # ya pagó lo que corresponde con el descuento teórico aplicado,
            # pero ese descuento todavía no existe como NC en Odoo. Antes
            # esto no tenía destino propio; caía sin distinguirse dentro de
            # `notas_credito_pendientes`.
            "descuentos_pendientes_aprobar": descuentos_pendientes_aprobar,
            "iva_pendiente_agentes": iva_pendiente_agentes,
            "auditoria_precios": auditoria_precios,
            "pendientes_por_cerrar": pendientes_por_cerrar,
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


def _vincular_masivo_sync(
    repo: Any,
    items: list[tuple[str, str, float]],
    confirmado_por: str = "Aprobador Masivo FIFO",
) -> tuple[int, set[str]]:
    """Núcleo de ``/api/vincular-masivo`` -- crea Vinculaciones ``PENDIENTE``

    a partir de una lista de ``(pago_id, so_id, monto_aplicado)``. Extraído
    del endpoint (Fase 1 del plan de arquitectura de pagos, agosto 2026)
    para reusarlo también desde el ciclo del daemon (``_auto_vincular_
    fifo_pendientes``), sin duplicar la lógica de tasas/equivalentes.

    Siempre queda en ``PENDIENTE`` -- gracias a la Fase 0, una Vinculación
    en ese estado NO destraba descuentos con ``requiere_pago_previo`` hasta
    que ``_resincronizar_vinculaciones_con_odoo`` la promueva a
    ``CONCILIADO`` confirmando con Odoo.
    """
    last_tasa = repo.last_serie_tasa()
    tasa_bcv_ultima = last_tasa.tasa_bcv if last_tasa else Decimal("36.5")
    tasa_binance = last_tasa.tasa_binance if last_tasa else Decimal("38.0")

    processed = 0
    so_ids_affected: set[str] = set()

    for pago_id, so_id, monto_aplicado in items:
        pago = repo.get_pago(pago_id)
        if not pago:
            continue

        monto_dec = Decimal(str(monto_aplicado))
        if monto_dec <= Decimal("0"):
            continue

        hora_pago_confirmada = datetime.combine(pago.fecha_pago, datetime.min.time())
        # Tarea 2: orden en la ventana histórica -> tasa BCV-Euro de referencia.
        tasa_bcv, bcv_variante = resolver_tasa_bcv_vinculacion(
            repo, so_id, hora_pago_confirmada, tasa_bcv_ultima
        )

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

        vinc_id = f"VINC_{pago_id}_{so_id}"
        vinc = Vinculacion(
            vinc_id=vinc_id,
            pago_id=pago_id,
            so_id=so_id,
            monto_aplicado=monto_dec,
            hora_pago_confirmada=hora_pago_confirmada,
            tasa_bcv_aplicada=tasa_bcv,
            tasa_binance_aplicada=tasa_binance,
            es_tasa_heredada=False,
            equiv_usd_bcv=equiv_usd_bcv,
            equiv_usd_binance=equiv_usd_binance,
            equiv_ves_bcv=equiv_ves_bcv,
            equiv_ves_binance=equiv_ves_binance,
            confirmado_por=confirmado_por,
            timestamp_registro=datetime.now(),
            estado=EstadoVinculacion.PENDIENTE,
            moneda_abono=Moneda(pago.moneda),
            tipo_tasa_abono=TipoTasa.BCV,
            bcv_variante=bcv_variante,
        )

        repo.update_vinculacion(vinc)
        processed += 1
        so_ids_affected.add(so_id)

    return processed, so_ids_affected


@app.post("/api/vincular-masivo")
async def post_vincular_masivo(req: VincularMasivoRequest, background_tasks: BackgroundTasks):
    try:
        repo = get_repo()
        items = [(it.pago_id, it.so_id, it.monto_aplicado) for it in req.items]
        processed, so_ids_affected = _vincular_masivo_sync(repo, items)

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


def _auto_vincular_fifo_pendientes(repo: Any) -> int:
    """Fase 1 (plan de arquitectura de pagos, agosto 2026, pedido explícito

    del usuario): automatiza la confirmación FIFO que ya existía como
    sugerencia manual (``/api/conciliaciones/sugerencias``) -- esa función
    YA reparte cada pago FIFO por orden más antigua (``open_orders_by_
    client`` ordenado por fecha, ``monto_aplicar = min(monto_pago_restante,
    o["saldo_pendiente"])``, remanente a la siguiente orden), justo el
    criterio que pidió el usuario. Antes esas sugerencias quedaban
    dormidas hasta que un humano entraba a Cobranza y las aprobaba a mano
    -- nunca pasó en producción (0 Vinculaciones). Se corre cada ciclo del
    daemon, creando las Vinculaciones directamente vía ``_vincular_masivo_
    sync`` (mismo núcleo que el botón manual).

    Se excluyen las filas "SIN_ORDEN" (dinero sobrante sin orden abierta
    que cubrir -- no hay nada que vincular) y los pagos marcados como
    posible duplicado (requieren juicio humano, no se auto-vinculan).
    Como quedan en PENDIENTE (Fase 0), una sugerencia equivocada no
    destraba ningún descuento -- solo se confirma cuando Odoo la reconcilia.
    """
    try:
        sugerencias = _get_conciliaciones_sugerencias_sync(None)
    except Exception as e:
        logger.warning("Error obteniendo sugerencias FIFO para auto-vincular: %s", e)
        return 0

    items = [
        (s["pago_id"], s["so_id"], s["monto_sugerido"])
        for s in sugerencias
        if s.get("so_id") and not s.get("posible_duplicado") and s.get("monto_sugerido", 0) > 0.05
    ]
    if not items:
        return 0

    processed, _so_ids = _vincular_masivo_sync(repo, items, confirmado_por="Auto-FIFO (daemon)")
    return processed


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


@app.post("/api/pago/{pago_id}/tasa-binance")
async def post_editar_tasa_binance_pago_pendiente(pago_id: str, req: TasaBinanceEditRequest):
    """Corrige la tasa Binance de un pago AÚN PENDIENTE (sin Vinculación

    real todavía -- el modal de detalle lo muestra como "sugerencia, aún
    sin confirmar"). Pedido explícito del usuario: misma validación que
    ``/api/vinculacion/{vinc_id}/tasa-binance`` (no puede superar el
    máximo ni caer bajo el mínimo capturado ese día en SerieTasas), pero
    sin un ``vinc_id`` real contra qué escribir -- se guarda en
    ``pagos_tasa_binance_override`` (por ``pago_id``), y
    ``get_cobranza_pagos_unificado`` lo aplica al armar la fila de ese
    pago mientras siga pendiente.
    """
    try:
        repo = get_repo()
        nueva_tasa = Decimal(str(req.tasa_binance))
        if nueva_tasa <= Decimal("0"):
            raise HTTPException(status_code=400, detail="La tasa Binance debe ser positiva.")

        pago = next((p for p in repo.all_pagos() if p.pago_id == pago_id), None)
        if pago is None:
            raise HTTPException(status_code=404, detail=f"Pago {pago_id} no encontrado.")

        fecha = pago.fecha_pago.date()
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

        repo.upsert_pago_tasa_binance_override(
            {
                "pago_id": pago_id,
                "tasa_binance": str(nueva_tasa),
                "editado_por": req.editado_por,
                "timestamp_edicion": datetime.now().isoformat(),
            }
        )

        return {
            "status": "success",
            "pago_id": pago_id,
            "tasa_binance_aplicada": float(nueva_tasa),
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
        tasas_rows = _all_serie_tasas_rows(repo)

        if variante == "EUR":
            tasa_bcv_nueva = get_bcv_euro_rate_for_datetime(vinc.hora_pago_confirmada, tasas_rows)
            if tasa_bcv_nueva is None:
                tasa_bcv_nueva = get_eur_rate_for_date(
                    vinc.hora_pago_confirmada.date(), repo.all_tasas_historicas_auditoria()
                )
            if tasa_bcv_nueva is None:
                raise HTTPException(
                    status_code=400,
                    detail="No hay tasa BCV-EUR capturada en SerieTasas para usar esta variante.",
                )
        else:
            tasa_bcv_nueva, _ = get_rate_for_datetime(vinc.hora_pago_confirmada, tasas_rows)

        if tasa_bcv_nueva <= Decimal("0"):
            raise HTTPException(status_code=400, detail=f"Tasa BCV-{variante} inválida (<= 0).")

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
    """Categorías raíz + subcategorías en vivo desde Odoo, con la MISMA lógica

    de reducción que usa el motor para clasificar cada línea
    (``OdooClient._productos``: busca "Comercial"/"Industrial" en el path de
    ``categ_id`` para la raíz, y toma el segmento siguiente como
    subcategoría -- ej. "Comercial/Elite" -> raíz "Comercial", subcategoría
    "Elite"). Filtrado a productos de VENTA (``sale_ok=True``) -- excluye
    categorías de compra/gastos (ej. "Expenses") que nunca aparecen en una
    línea de orden de venta y por tanto nunca deben ofrecerse como opción de
    "categoría aplicable" en una regla de descuento.
    """
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        productos = execute(
            "product.template",
            "search_read",
            [[["sale_ok", "=", True]]],
            {"fields": ["categ_id"]},
        )
        categ_ids = {p["categ_id"][0] for p in productos if p.get("categ_id")}
        if not categ_ids:
            return ["Comercial", "Industrial"]
        categs = execute(
            "product.category", "read", [sorted(categ_ids)], {"fields": ["id", "display_name"]}
        )
        nombres: set[str] = set()
        for c in categs:
            full = c.get("display_name") or ""
            parts = [p.strip() for p in full.split("/") if p.strip()]
            if not parts:
                continue
            if "Comercial" in parts:
                raiz = "Comercial"
            elif "Industrial" in parts:
                raiz = "Industrial"
            else:
                non_all = [p for p in parts if p != "All"]
                raiz = non_all[0] if non_all else parts[0]
            nombres.add(raiz)
            idx = parts.index(raiz) if raiz in parts else -1
            if idx >= 0 and idx + 1 < len(parts):
                nombres.add(parts[idx + 1])
        return sorted(nombres)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


def _categoria_raiz_y_subcategoria(display_name: str) -> tuple[str, str]:
    """(raíz, subcategoría) a partir del path completo de una categ_id de

    Odoo, con la misma lógica de reducción que usa el motor (ver
    OdooClient._productos)."""
    parts = [p.strip() for p in (display_name or "").split("/") if p.strip()]
    if not parts:
        return "", ""
    if "Comercial" in parts:
        raiz = "Comercial"
    elif "Industrial" in parts:
        raiz = "Industrial"
    else:
        non_all = [p for p in parts if p != "All"]
        raiz = non_all[0] if non_all else parts[0]
    idx = parts.index(raiz) if raiz in parts else -1
    sub = parts[idx + 1] if (idx >= 0 and idx + 1 < len(parts)) else ""
    return raiz, sub


@app.get("/api/odoo/categorias-arbol")
async def get_odoo_categorias_arbol():
    """Árbol categoría madre -> subcategorías en vivo desde Odoo (sale_ok),

    para el selector en cascada de los formularios de reglas: primero se
    elige la madre (Comercial/Industrial), luego solo se ofrecen las
    subcategorías reales que existen debajo de esa madre.
    """
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        productos = execute(
            "product.template",
            "search_read",
            [[["sale_ok", "=", True]]],
            {"fields": ["categ_id"]},
        )
        categ_ids = {p["categ_id"][0] for p in productos if p.get("categ_id")}
        if not categ_ids:
            return {"Comercial": [], "Industrial": []}
        categs = execute(
            "product.category", "read", [sorted(categ_ids)], {"fields": ["id", "display_name"]}
        )
        arbol: dict[str, set[str]] = {}
        for c in categs:
            raiz, sub = _categoria_raiz_y_subcategoria(c.get("display_name") or "")
            # Solo las 2 categorías madre reales del negocio -- descarta
            # ruido de categorías default de Odoo mal asignadas (ej. "All").
            if raiz not in ("Comercial", "Industrial"):
                continue
            arbol.setdefault(raiz, set())
            if sub:
                arbol[raiz].add(sub)
        return {madre: sorted(subs) for madre, subs in sorted(arbol.items())}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/odoo/presentaciones")
async def get_odoo_presentaciones():
    """Presentaciones/envases reales en vivo desde Odoo (sale_ok), extraídas

    del NOMBRE del producto (contenido entre paréntesis al final, ej. "...
    (1x6)" -> "1X6"), etiquetadas con su categoría madre/subcategoría para
    que el formulario solo ofrezca las presentaciones que realmente existen
    dentro de la madre/subcategoría elegida (ej. Industrial no debe mostrar
    "1X6", Comercial no debe mostrar "TAMBOR" salvo excepciones reales).
    """
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        productos = execute(
            "product.template",
            "search_read",
            [[["sale_ok", "=", True]]],
            {"fields": ["name", "categ_id"]},
        )
        categ_ids = {p["categ_id"][0] for p in productos if p.get("categ_id")}
        categs = execute(
            "product.category", "read", [sorted(categ_ids)], {"fields": ["id", "display_name"]}
        )
        nombre_categ = {c["id"]: c.get("display_name") or "" for c in categs}

        vistos: set[tuple[str, str, str]] = set()
        resultado = []
        for p in productos:
            c = p.get("categ_id")
            if not c:
                continue
            raiz, sub = _categoria_raiz_y_subcategoria(nombre_categ.get(c[0], ""))
            if raiz not in ("Comercial", "Industrial"):
                continue
            m = re.search(r"\(([^)]*)\)\s*$", str(p.get("name") or "").strip())
            if not m:
                continue
            pres = m.group(1).strip().upper()
            key = (raiz, sub, pres)
            if key in vistos:
                continue
            vistos.add(key)
            resultado.append({"madre": raiz, "subcategoria": sub, "presentacion": pres})
        resultado.sort(key=lambda x: (x["madre"], x["subcategoria"], x["presentacion"]))
        return resultado
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


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
        existing_rows = _all_serie_tasas_rows(repo)
        existing_dates = set()
        for r in existing_rows:
            ts = r.get("timestamp", "")
            if ts:
                existing_dates.add(ts.split(" ")[0].split("T")[0])

        from cxc.models import SerieTasa

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
                    repo.append_feriado(feriado)
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
                # Bug real (agosto 2026): "limit": 100 recortaba el catálogo --
                # el selector de productos de la promoción de obsequio (que
                # reusa esta misma lista) mostraba solo un subconjunto
                # arbitrario, ocultando la mayoría de los SKUs reales.
                "limit": 5000,
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

        # Bug real (agosto 2026, auditoría del mapeo de listas): la columna
        # VES caía a $0.00 fijo cuando ninguna lista candidata VES tenía
        # regla para el producto, a diferencia de la columna USD que sí
        # caía a "Precio de venta $" (list_price_usd). Como la lista "VES"
        # NO son precios en bolívares (son una pricelist EN USD que el
        # mapeo marca como la que corresponde a pagos en VES -- ver
        # get_valid_pricelists_usd_and_ves), el mismo fallback aplica: sin
        # regla fija en ninguna candidata, usar list_price_usd también acá.
        list_price_usd_por_id = {p["id"]: float(p.get("list_price_usd") or 0.0) for p in prods}

        resultado = []
        for p in prods:
            pid = p["id"]
            fallback_usd = list_price_usd_por_id.get(pid, 0.0)
            resultado.append(
                {
                    "id": pid,
                    "nombre": p["name"],
                    "ref_interna": p.get("default_code") or "N/A",
                    "precio_publico": float(p.get("list_price") or 0.0),
                    # Sin regla en la pricelist -- fallback a "Precio de venta $"
                    # (list_price_usd), NUNCA list_price (esa está en VES).
                    "precio_usd": prices_usd.get(pid, fallback_usd),
                    "precio_ves_usd": prices_ves.get(pid, fallback_usd),
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
                        {"fields": ["id", "product_volume", "weight", "brand_id"]},
                    )
                    for p in prods:
                        b_info = p.get("brand_id")
                        b_name = b_info[1] if isinstance(b_info, list) else ""
                        # "product_volume" (litros reales), NO "volume" (campo
                        # genérico de logística de Odoo) -- verificado en vivo
                        # (agosto 2026, producto 1034: volume=6.0 vs
                        # product_volume=5.67, campos genuinamente distintos,
                        # ver map_producto_espejo en cxc.odoo.client).
                        vol = parse_decimal_safe(p.get("product_volume") or "0")
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
            ordenes = repo.all_ordenes()
            lineas = repo.all_lineas()

            for c in repo.all_clientes():
                partners_data.append(
                    {
                        "id": c.cliente_id,
                        "name": c.nombre,
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
                pid = so_partner_map.get(ln.so_id)
                if pid and pid in stats:
                    brand = str(ln.marca or "").upper()
                    qty = ln.cantidad_entregada or ln.cantidad or Decimal("0")
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
                "marca": getattr(p, "marca", "*"),
                "categoria": getattr(p, "categorias_aplica", "Comercial"),
                "listas_aplicables": getattr(p, "listas_aplicables", "*"),
                "max_cantidad": float(getattr(p, "max_cantidad", 999999)),
                "unidad_medida": getattr(p, "unidad_medida", "CAJAS"),
                "valor": str(p.valor),
                "compra_minima": str(p.compra_minima),
                "descuento_fallback": str(getattr(p, "descuento_fallback", "0")),
                "regalo_tipo": p.regalo_tipo,
                "categorias_aplica": getattr(p, "categorias_aplica", "Comercial"),
                "solo_primera_compra": getattr(p, "solo_primera_compra", False),
                "vigencia_desde": p.vigencia_desde.isoformat(),
                "vigencia_hasta": p.vigencia_hasta.isoformat() if p.vigencia_hasta else None,
                "activo": p.activo,
                "requiere_pago_previo": p.requiere_pago_previo,
                "aplica_a": getattr(p, "aplica_a", "linea"),
                "descripcion": getattr(p, "descripcion", ""),
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_promocion_primera_compra(promo)
        return {"status": "success", "message": "Promoción registrada correctamente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.put("/api/config/promociones/{regla_id}")
async def put_config_promociones(regla_id: str, req: PromocionRequest):
    try:
        repo = get_repo()
        from cxc.models import PromocionPrimeraCompra

        existentes = repo.promociones_primera_compra()
        if not any(r.regla_id == regla_id for r in existentes):
            raise HTTPException(status_code=404, detail=f"Regla {regla_id} no existe.")

        v_desde = date.fromisoformat(req.vigencia_desde)
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_promocion_primera_compra(promo)
        return {"status": "success", "message": "Promoción actualizada correctamente."}
    except HTTPException:
        raise
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


    # NOTA: existía una ruta GET/POST /api/config/descuentos-volumen duplicada
    # aquí (mismo path, modelo DescuentoVolumenRequest más viejo/incompleto --
    # le faltaban min_cantidad/max_cantidad/unidad_medida). FastAPI/Starlette
    # resuelve rutas duplicadas por ORDEN DE REGISTRO (la primera gana, no la
    # última) -- esta era la que realmente corría en producción, y por eso
    # esos 3 campos del formulario de Volumen se guardaban pero se
    # descartaban en silencio. Eliminada; queda una sola definición más
    # abajo (junto a Recompra) con el modelo completo `VolumenRequest`.


# --- Unified Discount Rules Endpoint ---
@app.get("/api/reglas-descuento")
async def get_todas_reglas_descuento():
    try:
        repo = get_repo()
        repo.invalidate_cache()
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
                        "ventana_pago_tipo": getattr(r, "ventana_pago_tipo", "vencimiento"),
                        "ventana_pago_dias": getattr(r, "ventana_pago_dias", 3),
                    },
                    "activo": r.activo,
                    "aplica_a": getattr(r, "aplica_a", "linea"),
                    "descripcion": getattr(r, "descripcion", ""),
                }
            )

        # 2. Pronto Pago
        for r in repo.descuentos_marca_categoria():
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
                        "ventana_pago_tipo": getattr(r, "ventana_pago_tipo", "vencimiento"),
                        "ventana_pago_dias": getattr(r, "ventana_pago_dias", 3),
                        "monedas_aplicables": r.monedas_aplicables,
                    },
                    "activo": r.activo,
                    "aplica_a": getattr(r, "aplica_a", "linea"),
                    "descripcion": getattr(r, "descripcion", ""),
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
                    "aplica_a": getattr(r, "aplica_a", "linea"),
                    "descripcion": getattr(r, "descripcion", ""),
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
                    "aplica_a": getattr(r, "aplica_a", "linea"),
                    "descripcion": getattr(r, "descripcion", ""),
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
                    "aplica_a": getattr(r, "aplica_a", "linea"),
                    "descripcion": getattr(r, "descripcion", ""),
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
                    "aplica_a": getattr(r, "aplica_a", "linea"),
                    "descripcion": getattr(r, "descripcion", ""),
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
        rules = repo.descuentos_marca_categoria()
        return [
            {
                "regla_id": r.regla_id,
                "marca": r.marca,
                "categoria": r.categoria,
                "ventana_pago_tipo": getattr(r, "ventana_pago_tipo", "vencimiento"),
                "ventana_pago_dias": getattr(r, "ventana_pago_dias", 3),
                "min_cantidad": float(getattr(r, "min_cantidad", 0)),
                "max_cantidad": float(getattr(r, "max_cantidad", 999999)),
                "unidad_medida": getattr(r, "unidad_medida", "CAJAS"),
                "tipo_beneficio": getattr(r, "tipo_beneficio", "descuento"),
                "porcentaje": float(r.porcentaje),
                "monedas_aplicables": r.monedas_aplicables,
                "listas_aplicables": r.listas_aplicables,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo,
                "requiere_pago_previo": r.requiere_pago_previo,
                "aplica_a": getattr(r, "aplica_a", "linea"),
                "descripcion": getattr(r, "descripcion", ""),
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

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"PP_{uuid.uuid4().hex[:8].upper()}"
        min_q = Decimal(str(req.min_cantidad or 0))
        max_q = Decimal(str(req.max_cantidad or 999999))
        rule = DescuentoProntoPago(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            ventana_pago_tipo=req.ventana_pago_tipo,
            ventana_pago_dias=req.ventana_pago_dias,
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_descuento_pronto_pago(rule)
        return {"status": "success", "message": "Regla de descuento por pronto pago registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.put("/api/config/descuentos-pronto-pago/{regla_id}")
async def put_config_pronto_pago(regla_id: str, req: ProntoPagoRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoProntoPago

        existentes = repo.descuentos_marca_categoria()
        if not any(r.regla_id == regla_id for r in existentes):
            raise HTTPException(status_code=404, detail=f"Regla {regla_id} no existe.")

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None
        min_q = Decimal(str(req.min_cantidad or 0))
        max_q = Decimal(str(req.max_cantidad or 999999))
        rule = DescuentoProntoPago(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            ventana_pago_tipo=req.ventana_pago_tipo,
            ventana_pago_dias=req.ventana_pago_dias,
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_descuento_pronto_pago(rule)
        return {"status": "success", "message": "Regla de pronto pago actualizada."}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Volumen Endpoints ---
@app.get("/api/config/descuentos-volumen")
async def get_config_volumen():
    try:
        repo = get_repo()
        repo.invalidate_cache()
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
                    "requiere_pago_previo": r.requiere_pago_previo,
                    "aplica_a": getattr(r, "aplica_a", "linea"),
                    "descripcion": getattr(r, "descripcion", ""),
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_descuento_volumen(rule)
        return {"status": "success", "message": "Regla de descuento por volumen registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.put("/api/config/descuentos-volumen/{regla_id}")
async def put_config_volumen(regla_id: str, req: VolumenRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoVolumen

        existentes = repo.descuentos_volumen()
        if not any(r.regla_id == regla_id for r in existentes):
            raise HTTPException(status_code=404, detail=f"Regla {regla_id} no existe.")

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_descuento_volumen(rule)
        return {"status": "success", "message": "Regla de volumen actualizada."}
    except HTTPException:
        raise
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
                "min_cajas": getattr(r, "min_cajas", 2),
                "max_cajas": getattr(r, "max_cajas", 4),
                "unidad_medida": getattr(r, "unidad_medida", "CAJAS"),
                "tipo_beneficio": getattr(r, "tipo_beneficio", "descuento"),
                "listas_aplicables": getattr(r, "listas_aplicables", "*"),
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo,
                "requiere_pago_previo": r.requiere_pago_previo,
                "aplica_a": getattr(r, "aplica_a", "linea"),
                "descripcion": getattr(r, "descripcion", ""),
                "ventana_pago_tipo": getattr(r, "ventana_pago_tipo", "vencimiento"),
                "ventana_pago_dias": getattr(r, "ventana_pago_dias", 3),
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

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"REC_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoRecompra(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            porcentaje=Decimal(str(req.porcentaje)),
            min_cajas=req.min_cajas,
            max_cajas=req.max_cajas,
            listas_aplicables=req.listas_aplicables,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo,
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
            ventana_pago_tipo=req.ventana_pago_tipo,
            ventana_pago_dias=req.ventana_pago_dias,
        )
        repo.append_descuento_recompra(rule)
        return {"status": "success", "message": "Regla de descuento por recompra registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.put("/api/config/descuentos-recompra/{regla_id}")
async def put_config_recompra(regla_id: str, req: RecompraRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoRecompra

        existentes = repo.descuentos_recompra()
        if not any(r.regla_id == regla_id for r in existentes):
            raise HTTPException(status_code=404, detail=f"Regla {regla_id} no existe.")

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None
        rule = DescuentoRecompra(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            porcentaje=Decimal(str(req.porcentaje)),
            min_cajas=req.min_cajas,
            max_cajas=req.max_cajas,
            listas_aplicables=req.listas_aplicables,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo,
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
            ventana_pago_tipo=req.ventana_pago_tipo,
            ventana_pago_dias=req.ventana_pago_dias,
        )
        repo.append_descuento_recompra(rule)
        return {"status": "success", "message": "Regla de recompra actualizada."}
    except HTTPException:
        raise
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
                "min_cantidad": float(getattr(r, "min_cantidad", 0)),
                "max_cantidad": float(getattr(r, "max_cantidad", 999999)),
                "unidad_medida": getattr(r, "unidad_medida", "CAJAS"),
                "tipo_beneficio": getattr(r, "tipo_beneficio", "descuento"),
                "porcentaje": float(r.porcentaje),
                "monedas_aplicables": r.monedas_aplicables,
                "listas_aplicables": r.listas_aplicables,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo,
                "requiere_pago_previo": r.requiere_pago_previo,
                "aplica_a": getattr(r, "aplica_a", "linea"),
                "descripcion": getattr(r, "descripcion", ""),
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_descuento_producto(rule)
        return {"status": "success", "message": "Regla de descuento por producto registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.put("/api/config/descuentos-producto/{regla_id}")
async def put_config_producto(regla_id: str, req: ProductoPromoRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoProducto

        existentes = repo.descuentos_producto()
        if not any(r.regla_id == regla_id for r in existentes):
            raise HTTPException(status_code=404, detail=f"Regla {regla_id} no existe.")

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_descuento_producto(rule)
        return {"status": "success", "message": "Regla de producto actualizada."}
    except HTTPException:
        raise
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
                "marca": getattr(r, "marca", "*"),
                "categoria": getattr(r, "categoria", "*"),
                "unidad_medida": getattr(r, "unidad_medida", "USD"),
                "min_cantidad": float(getattr(r, "min_cantidad", 0)),
                "max_cantidad": float(getattr(r, "max_cantidad", 999999)),
                "monedas_aplicables": r.monedas_aplicables,
                "listas_aplicables": r.listas_aplicables,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo,
                "requiere_pago_previo": r.requiere_pago_previo,
                "aplica_a": getattr(r, "aplica_a", "linea"),
                "descripcion": getattr(r, "descripcion", ""),
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_descuento_diferencial_cambiario(rule)
        return {"status": "success", "message": "Regla de diferencial cambiario registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.put("/api/config/descuentos-diferencial-cambiario/{regla_id}")
async def put_config_diferencial(regla_id: str, req: DiferencialCambiarioRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoDiferencialCambiario

        existentes = repo.descuentos_diferencial_cambiario()
        if not any(r.regla_id == regla_id for r in existentes):
            raise HTTPException(status_code=404, detail=f"Regla {regla_id} no existe.")

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None
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
            requiere_pago_previo=req.requiere_pago_previo,
            descripcion=req.descripcion,
            aplica_a=req.aplica_a,
        )
        repo.append_descuento_diferencial_cambiario(rule)
        return {"status": "success", "message": "Regla de diferencial cambiario actualizada."}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


def _vigente_diferencial_local(r: Any, today: date) -> bool:
    desde_ok = r.vigencia_desde is None or r.vigencia_desde <= today
    hasta_ok = r.vigencia_hasta is None or r.vigencia_hasta >= today
    return bool(r.activo) and desde_ok and hasta_ok


def calcular_candidatos_cierre_diferencial(
    reglas_dif: list[Any],
    tasas_rows: list[dict[str, Any]],
    ventas_items: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    """Regla 3 de Diferencial Cambiario (candidatos a cierre de factura),

    pura -- sin tocar el repo/Odoo directamente, para poder testearla sin
    mockear todo el pipeline de ``get_ventas``. Ver docstring del endpoint
    ``GET /api/diferencial/candidatos-cierre`` para la explicación completa
    de la fórmula.
    """
    regla_max = next(
        (
            r
            for r in reglas_dif
            if r.tipo_diferencial == "fijo_35_ves_usd" and _vigente_diferencial_local(r, today)
        ),
        None,
    )
    regla_candidatos = next(
        (
            r
            for r in reglas_dif
            if r.tipo_diferencial == "candidato_cierre_factura"
            and _vigente_diferencial_local(r, today)
        ),
        None,
    )
    if regla_max is None or regla_candidatos is None:
        return {
            "habilitado": False,
            "motivo": (
                "Falta configurar una regla 'fijo_35_ves_usd' y una "
                "'candidato_cierre_factura', ambas vigentes y activas."
            ),
            "diferencial_maximo_pct": None,
            "diferencial_hoy_pct": None,
            "umbral_pct_pagado": None,
            "candidatos": [],
        }

    diferencial_maximo = float(regla_max.porcentaje_fijo)

    diferencial_hoy = 0.0
    if tasas_rows:
        ultima = max(tasas_rows, key=lambda r: r.get("timestamp") or "")
        pct_raw = ultima.get("diferencial_bcv_binance_pct")
        if pct_raw not in (None, ""):
            diferencial_hoy = float(pct_raw) / 100.0

    umbral = max(0.0, diferencial_maximo - diferencial_hoy)
    pct_pagado_minimo = max(0.0, 1.0 - umbral)

    candidatos: list[dict[str, Any]] = []
    for item in ventas_items:
        if item.get("nacio_en_lista_usd"):
            continue
        teorico_ves = float(item.get("ves_neta_teorica_iva") or 0.0)
        if teorico_ves <= 0:
            continue
        pagado_bcv = float(item.get("pagado_teorico_bcv") or 0.0)
        # CAP a 100% -- red de seguridad. "pagado_teorico_bcv" ahora
        # prorratea correctamente cuando un pago reconcilia facturas de
        # varias órdenes (ver _pagos_bcv_binance_por_orden), pero redondeos
        # o pagos que exceden ligeramente el teórico igual podrían dar un
        # poco más de 100% -- el cap evita mostrar eso en el reporte. Por
        # diseño, esto sigue siendo solo un reporte de CANDIDATOS: gerencia
        # verifica en Odoo antes de aprobar cualquier descuento real.
        pct_pagado = min(1.0, pagado_bcv / teorico_ves)
        if pct_pagado >= pct_pagado_minimo:
            candidatos.append(
                {
                    "so_id": item.get("so_id"),
                    "cliente_nombre": item.get("cliente_nombre"),
                    "teorico_ves": round(teorico_ves, 2),
                    "pagado_bcv": round(pagado_bcv, 2),
                    "pct_pagado": round(pct_pagado * 100, 2),
                    "monto_candidato_maximo": round(teorico_ves * umbral, 2),
                }
            )
    candidatos.sort(key=lambda c: -c["pct_pagado"])

    return {
        "habilitado": True,
        "diferencial_maximo_pct": round(diferencial_maximo * 100, 2),
        "diferencial_hoy_pct": round(diferencial_hoy * 100, 2),
        "umbral_pct_pagado": round(pct_pagado_minimo * 100, 2),
        "candidatos": candidatos,
    }


@app.get("/api/diferencial/candidatos-cierre")
async def get_diferencial_candidatos_cierre(cxc_session: str | None = Cookie(default=None)):
    """Regla 3 de Diferencial Cambiario (candidatos a cierre de factura).

    A diferencia de las reglas 1 (fijo) y 2 (equiparar) -- ver bloque "(c)
    Diferencial Cambiario" en ``engine/discounts.py`` -- esta NO es un
    descuento automático del motor: es un reporte de candidatos para que
    gerencia decida manualmente cuánto otorgar, vía ``POST /api/
    facturacion/aprobar-descuento-sistema`` (ya existente, ajusta saldos
    internos de CxC sin tocar Odoo).

    Fórmula (explicada por el usuario, agosto 2026): ``diferencial_hoy`` =
    el spread de mercado BCV-vs-Binance del día (``serie_tasas.
    diferencial_bcv_binance_pct``, ya calculado automáticamente por el
    scraper de tasas -- no hay que registrarlo a mano). ``umbral`` =
    ``diferencial_máximo`` (fila vigente ``fijo_35_ves_usd``) menos
    ``diferencial_hoy``. Toda orden nacida en lista VES con
    ``% pagado del teórico VES >= 100% - umbral`` es candidata.

    Requiere DOS filas de configuración vigentes y activas para no
    devolver vacío: la de ``fijo_35_ves_usd`` (fuente del diferencial
    máximo) y una de ``candidato_cierre_factura`` (interruptor on/off de
    este reporte específico) -- ambas se configuran en Configuración >
    Diferencial Cambiario, igual que las otras reglas.
    """
    try:
        repo = get_repo()
        reglas_dif = repo.descuentos_diferencial_cambiario()
        tasas_rows = _all_serie_tasas_rows(repo)
        ventas_data = await get_ventas(vendedor=None, cxc_session=cxc_session)
        return calcular_candidatos_cierre_diferencial(
            reglas_dif, tasas_rows, ventas_data["items"], date.today()
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Toggle Rule Active Endpoint ---
_REGLA_TABLAS_CONOCIDAS = [
    "DescuentosProntoPago",
    "DescuentosRecompra",
    "DescuentosVolumen",
    "PromocionPrimeraCompra",
    "DescuentosProducto",
    "DescuentosDiferencialCambiario",
]


@app.post("/api/config/toggle-descuento")
async def post_toggle_descuento(req: ToggleDescuentoRequest):
    try:
        repo = get_repo()
        target_id_str = str(req.regla_id).strip()

        # Primero la tabla que mandó el front; si no está ahí (regla_id de
        # otro tipo, o el front no sabía en cuál vivía), se busca en las
        # demás tablas de reglas conocidas.
        candidate_names = [req.tabla, *_REGLA_TABLAS_CONOCIDAS]
        for tabla in dict.fromkeys(candidate_names):  # dedup preservando orden
            if repo.set_regla_activo(tabla, target_id_str, req.activo):
                estado_str = "Activo" if req.activo else "Inactivo"
                return {
                    "status": "success",
                    "message": (
                        f"Estado de la regla {target_id_str} actualizado a "
                        f"{estado_str} en '{tabla}'."
                    ),
                }

        # No encontrada en ninguna tabla real -- puede ser una de las 3
        # reglas de diferencial cambiario "por defecto" (nunca persistidas
        # hasta que alguien las toca por primera vez desde el panel).
        defaults_por_id = {d.regla_id: d for d in repo.descuentos_diferencial_cambiario()}
        default_rule = defaults_por_id.get(target_id_str)
        if default_rule is not None:
            import dataclasses

            repo.append_descuento_diferencial_cambiario(
                dataclasses.replace(default_rule, activo=req.activo)
            )
            estado_str = "Activo" if req.activo else "Inactivo"
            msg = f"Regla por defecto {target_id_str} registrada y actualizada a {estado_str}."
            return {"status": "success", "message": msg}

        raise HTTPException(status_code=404, detail=f"Regla '{target_id_str}' no encontrada.")
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Rate Averages Endpoint ---
@app.get("/api/config/tasas-promedios")
async def get_tasas_promedios():
    """Wrapper async -- ver ``_get_tasas_promedios_sync`` (mismo hallazgo

    que ``get_ventas``, ver su docstring)."""
    return await asyncio.to_thread(_get_tasas_promedios_sync)


def _get_tasas_promedios_sync():
    try:
        repo = get_repo()
        rows = _all_serie_tasas_rows(repo)
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
    """Wrapper async -- ver ``_get_pagos_historial_sync``. Corre el trabajo

    síncrono pesado (Odoo/DB) en un thread aparte para no bloquear el
    event loop (mismo hallazgo que ``get_ventas``, ver su docstring)."""
    return await asyncio.to_thread(_get_pagos_historial_sync)


def _get_pagos_historial_sync():
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
        pagos_rows = _all_pagos_rows(repo)
        pagos_map = {r.get("pago_id"): r for r in pagos_rows}
        clientes_map = {c.cliente_id: c.nombre for c in repo.all_clientes()}
        ordenes_map = {o.so_id: o for o in repo.all_ordenes()}
        tasas_rows = _all_serie_tasas_rows(repo)

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
                    factura_id = ", ".join(f["factura_id"] for f in facturas) if facturas else "N/A"
                    so_id = ", ".join(p["so_ids"]) if p["so_ids"] else ""
                    c_name = p["cliente_nombre"] or clientes_map.get(
                        p["cliente_id"], f"Cliente ID: {p['cliente_id']}"
                    )
                    # Bug real (agosto 2026): para pagos YA reconciliados en
                    # Odoo (la mayoría, una vez pasan unos días), tasa_binance
                    # quedaba hardcodeada en None -- Binance no existe como
                    # campo en Odoo, así que a diferencia de tasa_bcv (que sí
                    # tiene el fallback "tax_today" más abajo, en
                    # get_cobranza_pagos_unificado) esta tasa nunca se
                    # calculaba, dejando el equivalente Binance en blanco
                    # para todo pago ya conciliado (los "pendientes" sí la
                    # calculan bien vía get_rate_for_datetime). Mismo
                    # criterio de fallback (SerieTasas del día ->
                    # TasasHistoricasAuditoria) que ya usa el resto del
                    # sistema.
                    fecha_pago_str = str(p["fecha_pago"] or "")[:10]
                    try:
                        fecha_pago_dt = (
                            datetime.strptime(fecha_pago_str, "%Y-%m-%d")
                            if fecha_pago_str
                            else datetime.now()
                        )
                    except ValueError:
                        fecha_pago_dt = datetime.now()
                    _, tasa_binance_calc = get_rate_for_datetime(fecha_pago_dt, tasas_rows)
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
                            "tasa_binance": (
                                float(tasa_binance_calc)
                                if tasa_binance_calc and tasa_binance_calc > Decimal("0")
                                else None
                            ),
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

        # Las 3 fuentes pesadas (sugerencias/historial/ventas, cada una ya
        # threaded vía asyncio.to_thread) son independientes entre sí --
        # antes se esperaban una tras otra en serie (hallazgo Fase 4/5,
        # agosto 2026); correrlas concurrentemente recorta el tiempo total
        # de este endpoint a ~el máximo de las 3, no la suma.
        async def _fetch_ventas_cobranza() -> dict[str, Any] | None:
            try:
                return await get_ventas(vendedor=None, cxc_session=None)
            except Exception as e_ventas:
                logger.warning(
                    "Error obteniendo /api/ventas para /api/cobranza/pagos: %s", e_ventas
                )
                return None

        sugerencias, historial, ventas_data_cobranza = await asyncio.gather(
            get_conciliaciones_sugerencias(cxc_session),
            get_pagos_historial(),
            _fetch_ventas_cobranza(),
        )
        ventas_by_so: dict[str, dict[str, Any]] = (
            {it["so_id"]: it for it in ventas_data_cobranza["items"]}
            if ventas_data_cobranza is not None
            else {}
        )
        cerrados_detalle = leer_pagos_huerfanos_cerrados(repo)

        pagos_rows = repo.all_pagos_full()
        pagos_by_id = {str(p.get("pago_id", "")).strip(): p for p in pagos_rows}
        # Correcciones manuales de tasa Binance para pagos AÚN pendientes
        # (sin Vinculación real) -- ver POST /api/pago/{pago_id}/tasa-binance.
        binance_override_map = {
            r["pago_id"]: parse_decimal_safe(r.get("tasa_binance", "0"))
            for r in repo.all_pagos_tasa_binance_override()
        }
        clientes = repo.all_clientes()
        clientes_map_obj = {c.cliente_id: c for c in clientes}
        clientes_nombre_map = {c.cliente_id: c.nombre for c in clientes}
        ordenes_map = {o.so_id: o for o in repo.all_ordenes()}
        tasas_historicas_rows = repo.all_tasas_historicas_auditoria()
        tasas_rows_eur = _all_serie_tasas_rows(repo)

        # Fuente única de verdad para "Saldo Orden (CxC)" -- antes venía de
        # un saldo blended (get_reporte_saldos) o un cálculo naive, y para
        # pagos aún pendientes ni siquiera se mostraba la factura/su saldo
        # asociados a la orden sugerida. Ahora se reusan los mismos 4
        # saldos en tiempo real que ya calcula /api/ventas (ver
        # _saldos_4_columnas_item), tanto para pagos pendientes como
        # vinculados. (``ventas_by_so`` ya se calculó arriba, en paralelo
        # con sugerencias/historial.)
        def _saldos_orden_para_reparto(so_id: str | None) -> dict[str, Any]:
            item_ventas = ventas_by_so.get(so_id) if so_id else None
            if item_ventas is None:
                return {
                    "so_saldo_teorico_bs": None,
                    "so_saldo_teorico_usd": None,
                    "so_saldo_pendiente": None,
                    "factura_id_sugerida": None,
                    "factura_saldo_odoo": None,
                }
            saldos = _saldos_4_columnas_item(item_ventas)
            orden_obj = ordenes_map.get(so_id)
            return {
                "so_saldo_teorico_bs": round(saldos["teorico_bs"], 2),
                "so_saldo_teorico_usd": round(saldos["teorico_usd"], 2),
                # "so_saldo_pendiente" (nombre legado, ver Reparto en el
                # modal): saldo Venta Real -- la referencia más cercana al
                # monto real de la orden en Odoo, igual criterio que ya usa
                # saldo_pendiente_cxc de /api/ventas para no-facturadas.
                "so_saldo_pendiente": round(saldos["venta_real"], 2),
                "factura_id_sugerida": orden_obj.factura_id if orden_obj else None,
                "factura_saldo_odoo": (
                    round(saldos["factura_real"], 2) if saldos["factura_real"] is not None else None
                ),
            }

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
            # Bug real (screenshot del usuario, agosto 2026): pagos con fecha
            # posterior al último día sembrado en TasasHistoricasAuditoria
            # (2026-07-30) salían con "EUR: -" porque get_eur_rate_for_date
            # busca SOLO el día exacto en esa tabla, sin fallback -- mismo
            # patrón que ya usa _pagos_bcv_binance_por_orden (línea ~574):
            # primero SerieTasas (scraper, más reciente), luego el histórico.
            tasa_eur = None
            if fecha_dt:
                tasa_eur = get_bcv_euro_rate_for_datetime(
                    datetime.combine(fecha_dt, datetime.min.time()), tasas_rows_eur
                )
                if not tasa_eur or tasa_eur <= Decimal("0"):
                    tasa_eur = get_eur_rate_for_date(fecha_dt, tasas_historicas_rows)

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

            tasa_binance_item = item["tasa_binance"]
            monto_binance_usd_item = item["monto_pago_binance"]
            monto_pago_restante = item["saldo_pago"]
            override_binance = binance_override_map.get(pid)
            if override_binance and override_binance > Decimal("0"):
                tasa_binance_item = float(override_binance)
                moneda_pago_item = str(item["moneda_pago"] or "USD").upper().strip()
                if moneda_pago_item == "USD":
                    monto_binance_usd_item = item["monto_pago_original"]
                else:
                    monto_binance_usd_item = float(
                        Decimal(str(item["monto_pago_original"])) / override_binance
                    )

            saldos_reparto_pend = _saldos_orden_para_reparto(item.get("so_id"))
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
                    "tasa_binance": tasa_binance_item,
                    "monto_pago_bcv_usd": item["monto_pago"],
                    "monto_pago_binance_usd": monto_binance_usd_item,
                    "monto_pago_eur": monto_eur(pid, extra["tasa_bcv_eur"]),
                    "monto_aplicado": 0.0,
                    "monto_por_aplicar": monto_pago_restante,
                    "so_saldo_teorico_bs": saldos_reparto_pend["so_saldo_teorico_bs"],
                    "so_saldo_teorico_usd": saldos_reparto_pend["so_saldo_teorico_usd"],
                    "so_saldo_pendiente": saldos_reparto_pend["so_saldo_pendiente"],
                    "factura_saldo_odoo": saldos_reparto_pend["factura_saldo_odoo"],
                    "so_id": item.get("so_id"),
                    "factura_id": saldos_reparto_pend["factura_id_sugerida"],
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
                    # Sin vinc_id real todavía -- la edición se guarda en
                    # pagos_tasa_binance_override (ver POST /api/pago/
                    # {pago_id}/tasa-binance), no en una Vinculación.
                    "puede_editar_tasas": True,
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

            saldos_reparto_vinc = _saldos_orden_para_reparto(item.get("so_id"))
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
                    "so_saldo_teorico_bs": saldos_reparto_vinc["so_saldo_teorico_bs"],
                    "so_saldo_teorico_usd": saldos_reparto_vinc["so_saldo_teorico_usd"],
                    "so_saldo_pendiente": saldos_reparto_vinc["so_saldo_pendiente"],
                    # Residual REAL de la factura vinculada (Odoo,
                    # amount_residual_usd) -- más preciso que el genérico
                    # de _saldos_orden_para_reparto cuando ya hay factura
                    # específica ligada a este pago.
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
                    "so_saldo_teorico_bs": None,
                    "so_saldo_teorico_usd": None,
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
        rows = repo.all_tasas_historicas_auditoria()
        return {"items": rows, "count": len(rows)}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


    # NOTA (agosto 2026, plan de consolidación de fuentes): existía una
    # segunda ruta GET /api/reglas-descuento aquí (función
    # get_reglas_descuento, shape dict por categoría) -- mismo patrón ya
    # documentado y corregido antes con /api/config/descuentos-volumen.
    # FastAPI/Starlette resuelve rutas duplicadas por ORDEN DE REGISTRO (la
    # primera gana, la de arriba -- get_todas_reglas_descuento, shape lista
    # plana), así que esta segunda definición era 100% inalcanzable: nunca
    # se ejecutó, no la llama nada más en el código ni en tests. El
    # frontend (loadReglasConsolidadas en app.js) ya espera la lista plana
    # de la primera, así que no hay comportamiento real que preservar.
    # Eliminada.


@app.get("/api/auditoria")
async def get_auditoria():
    try:
        repo = get_repo()
        # Bug preexistente (Tarea 6): esta funcion usaba `cutoff_historical` y
        # `hist_map` sin definirlas nunca en su propio scope (solo existian
        # como variables locales de get_reporte_saldos) -- /api/auditoria
        # siempre tiraba NameError y devolvia 500. Fuente única ahora (agosto
        # 2026): _build_hist_map, ya no una copia local del mismo bucle.
        historical_enabled = is_historical_pricelist_enabled(repo)
        hist_map = _build_hist_map(repo)

        ordenes = repo.all_ordenes()
        lines_rows = _all_lineas_rows(repo)
        bandeja_rows = repo.all_bandeja()
        bandeja_map = {b.so_id: b for b in bandeja_rows}
        # Fase 10: teóricos VES/USD viven en su propia tabla (fija, cubre
        # también órdenes facturadas) -- ver ventas_teoricos en db/schema.py.
        teoricos_map = {v.so_id: v for v in repo.all_ventas_teoricos()}
        clientes_map = {c.cliente_id: c.nombre for c in repo.all_clientes()}

        # Load UI configured pricelists (USD & VES) from _Meta
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        usd_ids, ves_ids = get_ui_pricelist_ids(repo)
        all_candidate_ids = list(set(usd_ids + ves_ids))
        rules_all = _get_pricelist_items_fixed(execute, all_candidate_ids)

        # Load accepted anomalies from Google Sheets
        anomalias_aceptadas_rows = repo.all_anomalias_aceptadas()
        aceptadas_map = {r.get("anomalia_id"): r for r in anomalias_aceptadas_rows}
        lines_by_so = {}
        for r in lines_rows:
            so = r.get("so_id", "")
            if so:
                lines_by_so.setdefault(so, []).append(r)

        # Fase 4/5 (plan de consolidación de fuentes, agosto 2026): montos e
        # identidad de facturas/NC ahora vienen del espejo Factura -- mismo
        # helper ya validado para /api/reporte-saldos (parity check, 0
        # diffs). A diferencia de la consulta en vivo original (que solo
        # matcheaba NC vía invoice_origin directo, sin intentar
        # reversed_entry_id), el espejo SÍ resuelve NC sin invoice_origin
        # propio vía la cadena factura_origen_id -- corrige el mismo gap
        # ya encontrado y arreglado en Reporte de Saldos, también aquí.
        # Solo amount_residual/payment_state (mutables) se piden en vivo,
        # acotados a los ids ya resueltos.
        so_ids = [o.so_id for o in ordenes]
        invoices_by_so: dict[str, list[dict]] = {}
        so_pagada_en_odoo: set[str] = set()

        facturas_dicts = _facturas_dicts_desde_espejo(repo, so_ids)
        ids_para_estado_pago = [d["id"] for d in facturas_dicts if d["id"] is not None]
        estado_pago_map = (
            _estado_pago_facturas_desde_odoo(execute, ids_para_estado_pago) if execute else {}
        )
        estados_por_so: dict[str, list[str]] = {}
        for d in facturas_dicts:
            fid = d["id"]
            overlay = estado_pago_map.get(fid, {})
            merged = dict(d)
            merged["amount_residual"] = overlay.get("amount_residual", 0.0)
            merged["payment_state"] = overlay.get("payment_state", "")
            so = merged["invoice_origin"]
            invoices_by_so.setdefault(so, []).append(merged)
            if merged["move_type"] == "out_invoice":
                estados_por_so.setdefault(so, []).append(str(merged["payment_state"]))

        # SO "pagada" = todas sus out_invoice estan payment_state paid/in_payment
        # (misma regla que /api/reporte-saldos, Tarea 2).
        for so, estados in estados_por_so.items():
            if estados and all(ps in ("paid", "in_payment") for ps in estados):
                so_pagada_en_odoo.add(so)

        # Read rates series to convert VES invoice residual to USD
        tasas_rows = _all_serie_tasas_rows(repo)
        rates_map = {}
        for r in tasas_rows:
            ts = str(r.get("timestamp", ""))[:10]
            tbcv = r.get("tasa_bcv")
            if ts and tbcv:
                with contextlib.suppress(Exception):
                    rates_map[ts] = float(tbcv)
        last_bcv_val = list(rates_map.values())[-1] if rates_map else 742.23

        # Load payments map by SO for net debt comparison.
        # Suma TODAS las vinculaciones (sin filtrar por estado), igual que
        # get_resumen/get_bandeja_facturacion/_get_ventas_sync -- filtrar
        # por CONCILIADO aqui generaba falsos positivos en
        # discrepancias_facturas_odoo para vinculaciones aun no marcadas
        # conciliadas (estado transitorio normal de un pago recien vinculado).
        vincs = repo.all_vinculaciones()
        pagos_by_so = {}
        for v in vincs:
            pagos_by_so[v.so_id] = pagos_by_so.get(v.so_id, 0.0) + float(v.monto_aplicado)

        # Estado EN VIVO de cada orden -- el espejo local (estado_orden) puede
        # quedar desactualizado si una orden se cancela en Odoo y el sync
        # incremental no la vuelve a traer (ventana delta vencida, downtime,
        # etc.). Ver get_reporte_diario / get_resumen / get_reporte_saldos
        # para el mismo fix.
        so_states_map: dict[str, str] = {}
        entrega_valida_set: set[str] = set()
        # Fase 4/6: entregas desde el espejo -- no depende de `execute`, más
        # resiliente que antes (funciona aunque Odoo esté caído).
        if so_ids:
            entrega_valida_set, _ = _entregas_desde_espejo(repo, so_ids)
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
            except Exception as e:
                logger.warning("Error consultando estado en vivo en get_auditoria: %s", e)

        # Fase 5 (plan de consolidación de fuentes, agosto 2026): productos
        # realmente despachados (para el Check 5 más abajo: detectar si se
        # entregó un producto distinto o adicional al pedido) ahora se leen
        # del espejo (Entrega/EntregaLinea) -- validado con un parity check
        # completo contra las 819 órdenes reales sincronizadas (0 diffs).
        # No depende de `execute` -- funciona aunque Odoo esté caído.
        delivered_products_by_so: dict[str, set[int]] = _productos_despachados_desde_espejo(
            repo, so_ids
        )

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
                not lista_id_str
                or lista_id_str in ("0", "None", "")
                or (
                    historical_enabled
                    and HISTORICAL_PRICE_LIST_START <= o.fecha < HISTORICAL_PRICE_LIST_END_EXCLUSIVE
                )
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
                    _en_ventana_historica = (
                        HISTORICAL_PRICE_LIST_START <= o.fecha < HISTORICAL_PRICE_LIST_END_EXCLUSIVE
                    )
                    cur_label = (
                        "Lista Histórica Auditoría (Pre-13-Mar)"
                        if _en_ventana_historica
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

        # Tarea 4 (venta bruta teórica derivada, VES/USD): a/b/c del diseño
        # -- Fase 10: lee de ventas_teoricos (tabla fija, cubre también
        # órdenes facturadas), NO de BandejaFacturacion -- y solo aplica
        # impuestos + resta, no recalcula ningún descuento aquí.
        _iva_rate = float(config.engine.iva_rate)
        venta_bruta_teorica_auditoria = []
        for o in ordenes:
            teorico_row = teoricos_map.get(o.so_id)
            if teorico_row is None:
                continue
            b = bandeja_map.get(o.so_id)
            teorico_ves = float(teorico_row.teorico_ves)
            teorico_usd = float(teorico_row.teorico_usd)
            desc_ves = float(teorico_row.descuentos_teorico_ves)
            desc_usd = float(teorico_row.descuentos_teorico_usd)
            ves_bruta_mas_iva = teorico_ves * (1 + _iva_rate)
            ves_neta = teorico_ves - desc_ves
            ves_neta_mas_iva = ves_neta * (1 + _iva_rate)
            usd_bruta_mas_iva = teorico_usd * (1 + _iva_rate)
            usd_neta = teorico_usd - desc_usd
            usd_neta_mas_iva = usd_neta * (1 + _iva_rate)
            venta_bruta_teorica_auditoria.append(
                {
                    "so_id": o.so_id,
                    "lista_ves": {
                        "bruta_teorica": round(teorico_ves, 2),
                        "bruta_teorica_mas_iva": round(ves_bruta_mas_iva, 2),
                        "neta_teorica": round(ves_neta, 2),
                        "neta_teorica_mas_iva": round(ves_neta_mas_iva, 2),
                    },
                    "lista_usd": {
                        "bruta_teorica": round(teorico_usd, 2),
                        "bruta_teorica_mas_iva": round(usd_bruta_mas_iva, 2),
                        "neta_teorica": round(usd_neta, 2),
                        "neta_teorica_mas_iva": round(usd_neta_mas_iva, 2),
                    },
                    "venta_real": {
                        "orden_total": round(float(o.monto_total), 2),
                        "factura_neto": round(float(b.total_motor), 2) if b else None,
                    },
                }
            )

        return {
            "operaciones_conformes": operaciones_conformes,
            "discrepancias": discrepancias_pendientes,
            "discrepancias_facturas_odoo": discrepancias_facturas_odoo,
            "anomalias_aceptadas": anomalias_aceptadas,
            "venta_bruta_teorica_auditoria": venta_bruta_teorica_auditoria,
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


def _leer_descuentos_lineas_odoo(
    execute: Any,
    so_names: list[str],
    invoice_ids: list[int],
    inv_id_to_so: dict[int, str],
    inv_usd_ratio_map: dict[int, float] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Lee los descuentos ya materializados en Odoo por orden y por factura.

    Tarea 3c: no CALCULA ningún descuento -- solo lee lo que Odoo ya tiene
    guardado por línea (campo ``discount`` % en ``sale.order.line``/
    ``account.move.line``, o una línea aparte de producto "Descuento" con
    ``price_subtotal`` negativo, patrón también usado en Lubrikca). Sirve
    para comparar contra lo que el motor dictamina
    (``BandejaFacturacion.total_descuentos``), nunca para sustituirlo.
    """
    desc_orden: dict[str, float] = {}
    desc_factura: dict[str, float] = {}
    if not execute:
        return desc_orden, desc_factura
    try:
        if so_names:
            sol_lines = execute(
                "sale.order.line",
                "search_read",
                [
                    [
                        ["order_id.name", "in", so_names],
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
                disc_pct = float(sol.get("discount") or 0)
                if disc_pct > 0:
                    monto = (
                        float(sol.get("product_uom_qty") or 0)
                        * float(sol.get("price_unit") or 0)
                        * (disc_pct / 100.0)
                    )
                else:
                    monto = abs(float(sol.get("price_subtotal") or 0))
                desc_orden[so_name] = desc_orden.get(so_name, 0.0) + monto
    except Exception as e_sol:
        logger.warning("Error leyendo descuentos de sale.order.line en get_ventas: %s", e_sol)
    try:
        if invoice_ids:
            inv_lines = execute(
                "account.move.line",
                "search_read",
                [
                    [
                        ["move_id", "in", invoice_ids],
                        ["display_type", "in", ["product", False]],
                        "|",
                        ["discount", ">", 0],
                        "&",
                        ["product_id.name", "ilike", "descuento"],
                        ["price_subtotal", "<", 0],
                    ]
                ],
                {"fields": ["move_id", "quantity", "price_unit", "discount", "price_subtotal"]},
            )
            for il in inv_lines:
                move_raw = il.get("move_id")
                move_id = move_raw[0] if isinstance(move_raw, list | tuple) else int(move_raw or 0)
                so_name = inv_id_to_so.get(move_id, "")
                if not so_name:
                    continue
                disc_pct = float(il.get("discount") or 0)
                if disc_pct > 0:
                    monto = (
                        float(il.get("quantity") or 0)
                        * float(il.get("price_unit") or 0)
                        * (disc_pct / 100.0)
                    )
                else:
                    monto = abs(float(il.get("price_subtotal") or 0))
                ratio = (inv_usd_ratio_map or {}).get(move_id, 1.0)
                desc_factura[so_name] = desc_factura.get(so_name, 0.0) + monto * ratio
    except Exception as e_il:
        logger.warning("Error leyendo descuentos de account.move.line en get_ventas: %s", e_il)
    return desc_orden, desc_factura


def _leer_notas_debito_odoo(
    execute: Any, original_invoice_ids: list[int], inv_id_to_so: dict[int, str]
) -> dict[str, float]:
    """Tarea 3f: notas de débito (N/D) atadas a una factura ya emitida.

    A diferencia de las notas de crédito (``move_type == "out_refund"``,
    lógica ya existente y reutilizada tal cual), Odoo no marca las N/D con
    un ``move_type`` propio -- son ``account.move`` con
    ``move_type == "out_invoice"`` y ``debit_origin_id`` apuntando a la
    factura original. Se buscan por ese campo, no por ``invoice_origin``
    (una N/D no necesariamente lo trae).
    """
    nd_by_so: dict[str, float] = {}
    if not execute or not original_invoice_ids:
        return nd_by_so
    try:
        debit_notes = execute(
            "account.move",
            "search_read",
            [
                [
                    ["debit_origin_id", "in", original_invoice_ids],
                    ["state", "=", "posted"],
                    # Bug real (orden S00357 y otras del mismo cliente,
                    # agosto 2026): las notas de débito reales en Odoo 18
                    # tienen move_type="out_debit" (journal dedicado "Notas
                    # de débito clientes"), NUNCA "out_invoice" -- verificado
                    # en vivo, debit_origin_id sí apunta correctamente a la
                    # factura original, pero el filtro de move_type las
                    # excluía todas.
                    ["move_type", "=", "out_debit"],
                ]
            ],
            {"fields": ["debit_origin_id", "amount_total_signed_usd"]},
        )
        for dn in debit_notes:
            origin_raw = dn.get("debit_origin_id")
            origin_id = origin_raw[0] if isinstance(origin_raw, list | tuple) else origin_raw
            so_name = inv_id_to_so.get(origin_id, "") if origin_id else ""
            if not so_name:
                continue
            nd_by_so[so_name] = nd_by_so.get(so_name, 0.0) + abs(
                float(dn.get("amount_total_signed_usd") or 0.0)
            )
    except Exception as e_nd:
        logger.warning("Error leyendo notas de débito en get_ventas: %s", e_nd)
    return nd_by_so


def _leer_notas_credito_odoo(
    execute: Any,
    original_invoice_ids: list[int],
    inv_id_to_so: dict[int, str],
    ids_ya_contados: set[int],
) -> dict[str, float]:
    """Notas de crédito (N/C) atadas a una factura ya emitida, vía

    ``reversed_entry_id`` (apunta a la factura original) -- NO vía
    ``invoice_origin``.

    Bug real (mismo patrón que las notas de débito, ver
    ``_leer_notas_debito_odoo``): el código anterior encontraba N/C
    únicamente dentro de la consulta principal de facturas, filtrada por
    ``invoice_origin in so_names`` -- pero las N/C creadas con el asistente
    normal de Odoo ("Agregar Nota de Crédito") dejan ``invoice_origin``
    vacío; se enlazan a la factura original vía ``reversed_entry_id``.
    Verificado en vivo contra varias N/C reales del sistema.

    ``ids_ya_contados``: ids de account.move que la consulta principal ya
    sumó a ``nc_con_imp_map`` (los pocos casos donde ``invoice_origin`` sí
    viene poblado) -- se excluyen aquí para no contar el mismo documento
    dos veces.
    """
    nc_by_so: dict[str, float] = {}
    if not execute or not original_invoice_ids:
        return nc_by_so
    try:
        credit_notes = execute(
            "account.move",
            "search_read",
            [
                [
                    ["reversed_entry_id", "in", original_invoice_ids],
                    ["state", "=", "posted"],
                    ["move_type", "=", "out_refund"],
                ]
            ],
            {"fields": ["id", "reversed_entry_id", "amount_total_signed_usd"]},
        )
        for cn in credit_notes:
            if int(cn.get("id") or 0) in ids_ya_contados:
                continue
            origin_raw = cn.get("reversed_entry_id")
            origin_id = origin_raw[0] if isinstance(origin_raw, list | tuple) else origin_raw
            so_name = inv_id_to_so.get(origin_id, "") if origin_id else ""
            if not so_name:
                continue
            nc_by_so[so_name] = nc_by_so.get(so_name, 0.0) + abs(
                float(cn.get("amount_total_signed_usd") or 0.0)
            )
    except Exception as e_nc:
        logger.warning("Error leyendo notas de crédito (reversed_entry_id) en get_ventas: %s", e_nc)
    return nc_by_so


@app.get("/api/ventas")
async def get_ventas(
    vendedor: str | None = None,
    cxc_session: str | None = Cookie(default=None),
):
    """Wrapper async -- ver ``_get_ventas_sync`` para la lógica real.

    Corre el trabajo síncrono pesado (Odoo/DB) en un thread aparte
    (``asyncio.to_thread``) para no bloquear el único event loop de uvicorn
    mientras corre. Hallazgo real (agosto 2026): sin esto, este endpoint
    (llamado también internamente por ``get_reporte_cxc_cliente``,
    ``get_cobranza_pagos_unificado`` y el reporte de candidatos a cierre de
    Diferencial Cambiario) podía bloquear ``/reporte`` y el resto del sitio
    por varios minutos cuando su llamada coincidía con el ciclo de
    recálculo en background -- exactamente el patrón que ``recalculate_
    all_orders`` ya evita con este mismo mecanismo.
    """
    return await asyncio.to_thread(_get_ventas_sync, vendedor, cxc_session)


def _get_ventas_sync(
    vendedor: str | None = None,
    cxc_session: str | None = None,
):
    """Reporte "Ventas": comparación teórica VES/USD vs real, por orden.

    Tarea 2 (rediseño de columnas, ver docs/REDISENO_DESCUENTOS_UNIFICADOS.md):
    se eliminaron las columnas genéricas "venta bruta/neta teórica" (y sus
    variantes "+ impuestos") -- toda orden nace en una lista VES o USD
    vigente para su fecha, y el teórico existe justamente para EVIDENCIAR
    la discrepancia entre ambas listas, no para repetir un cálculo genérico
    igual al de la lista aplicada. En su lugar se exponen 8 columnas
    explícitas, ``{ves,usd}_{bruta,neta}_teorica[_iva]``, leídas de la tabla
    ``ventas_teoricos`` (Fase 10 -- punto de comparación FIJO, calculado
    una vez por orden y NO recalculado en cada ciclo, a diferencia de
    ``BandejaFacturacion`` que se recalcula constantemente y explícitamente
    SALTA órdenes ya facturadas -- ver docstring de la tabla en db/schema.py).
    Este endpoint solo aplica impuestos, no recalcula ningún descuento.
    Ambos bloques (VES y USD) coexisten siempre, incluso si la orden nació
    en una sola lista. Si ``ventas_teoricos`` aún no tiene fila para una
    orden (nunca calculado -- ver ``/api/backfill/ventas-teoricos``), los 4
    campos de estatus de pago teórico devuelven ``"sin_datos"``.

    Causa raíz corregida (bug orden 771, antes VES y USD salían idénticos):
    ``_determinar_lista``/``_teoricos_por_lista`` (``engine/discounts.py``)
    usaban ``ENGINE_LISTA_USD``/``ENGINE_LISTA_BCV`` (env vars, valor real
    en producción: pricelist 4 -- inactiva, con los mismos precios que la
    VES id 5 por coincidencia histórica) en vez de la lista USD realmente
    vigente (id 8). Esas variables se eliminaron; ahora se deriva de
    ``EngineInputs.valid_usd``/``valid_ves`` (Configuración, misma fuente
    que ya usa el selector de listas y las reglas por lista).

    "diferencia"/"alerta" (abajo) siguen comparando contra el teórico de la
    LISTA APLICADA (``BandejaFacturacion.precio_base_calculado``/
    ``total_motor`` -- coincide por construcción con el bloque VES o USD
    correspondiente), no contra un genérico aparte.

    Venta bruta real = ``amount_untaxed`` de la orden en Odoo. Venta neta
    real = ``monto_total`` (``amount_total``, YA con impuestos -- es el
    valor que usa Cuentas por Cobrar).

    Total facturado: de las facturas "posted" ligadas por ``invoice_origin``,
    en equivalente USD vía los campos ``*_signed_usd`` de Odoo. Neto = con
    impuestos, menos las notas de crédito (``out_refund``, lógica ya
    existente) más las notas de débito (Tarea 3f/3g: ``move_type ==
    "out_invoice"`` con ``debit_origin_id`` apuntando a la factura original --
    Odoo no las distingue con un ``move_type`` propio).

    Alerta: solo si la orden YA tiene factura y lo realmente facturado neto
    quedó por debajo de lo que el motor dice que debió facturarse neto (ya
    con impuestos) -- eso es un faltante real de facturación o un descuento
    indebido. El resto (diferencias explicadas por descuentos válidos,
    redondeo, u órdenes aún sin facturar) es informativo y subsanable.

    Tarea 3c/3d: ``descuento_aplicado_orden``/``_factura`` leen (no calculan)
    el descuento que Odoo ya tiene guardado por línea; se comparan contra
    ``BandejaFacturacion.total_descuentos`` (lo que dictamina el motor) vía
    ``discount_audit`` (mismas funciones puras que usa ``/api/auditoria``,
    sin duplicar la lógica de comparación). ``descuento_pendiente_aplicar``
    es la parte que el motor exige y Odoo todavía no refleja.

    Tarea 3e/Fase 3: ``descuento_aplicado_sistema`` lee de
    ``descuentos_sistema_aprobados`` (aprobación manual desde la Bandeja 1 de
    Facturación vía ``POST /api/facturacion/aprobar-descuento-sistema`` --
    NUNCA se escribe a Odoo). ``saldo_pendiente_cxc`` es el target real de
    CxC (facturado neto u orden neta real, según corresponda, menos ese
    descuento) que deben usar Cobranza/estatus de pago.

    Fase 4 (estatus de pago): 4 columnas, cada una comparando el equivalente
    USD ya congelado por abono (``engine/equivalents.py::valor_pagado_bcv_usd``/
    ``valor_pagado_binance_usd`` -- NUNCA se compara contra VES directo)
    contra SU PROPIO total de referencia:
    - ``estatus_pago_real_orden``/``_real_factura``: usan BCV o Binance
      según la lista con la que NACIÓ la orden (BCV si VES o Lista Histórica
      de Auditoría -- ``historical_pricing.es_orden_historica`` --, Binance
      si USD), comparado contra ``venta_neta_real``/``total_facturado_neto``
      ya netos del descuento de sistema (Fase 3). ``_real_factura`` es
      ``"sin_factura"`` si la orden aún no tiene factura.
    - ``estatus_pago_teorico_ves``: SIEMPRE BCV vs. ``ves_neta_teorica_iva``.
    - ``estatus_pago_teorico_usd``: SIEMPRE Binance vs. ``usd_neta_teorica_iva``.
    Estados: ``"pagada"``/``"parcial"``/``"sin_pago"``, tolerancia 0.05
    (mismo epsilon que ya usa "alerta").

    Caché corta (``_VENTAS_CACHE``, TTL ``_VENTAS_CACHE_TTL``, sólo para la
    corrida sin filtro de vendedor -- ``vendedor is None``, que es como
    siempre la llaman los consumidores internos: Bandeja, Reporte CxC por
    Cliente, Cobranza): esta función ya es la fuente única para esas 3
    páginas (no llaman a Odoo por su cuenta); a medida que Reporte de
    Saldos/Auditoría/Reporte Diario se migren para consumirla también
    (plan de fases, agosto 2026), sin caché cada carga de página
    multiplicaría las llamadas a Odoo en vez de reducirlas -- por eso esta
    caché es prerrequisito de esa consolidación, no solo una optimización
    suelta. Guarda de reentrada igual a la de ``_get_reporte_saldos_sync``
    (mismo bug real ya encontrado ahí: una llamada anidada durante el
    cálculo debe ver el caché "frío" en vez de disparar la cadena de nuevo).
    """
    global _ventas_computing
    if vendedor is None:
        import time

        now_ts = time.time()
        if (
            _VENTAS_CACHE["data"] is not None
            and now_ts - float(_VENTAS_CACHE["timestamp"]) < _VENTAS_CACHE_TTL
        ):
            return _VENTAS_CACHE["data"]
        if _ventas_computing:
            return _VENTAS_CACHE["data"] or {"items": [], "kpis": {}}
        _ventas_computing = True
    try:
        from cxc.engine.discount_audit import auditar_descuento_factura, auditar_descuento_orden

        repo = get_repo()
        user = get_current_user_from_cookie(cxc_session)
        config = AppConfig.from_env()
        iva_rate = float(config.engine.iva_rate)
        igtf_rate = float(config.engine.igtf_rate) if config.engine.igtf_activo else 0.0
        today_ventas = date.today()

        ordenes = repo.all_ordenes()
        bandeja_map = {b.so_id: b for b in repo.all_bandeja()}
        # Fase 10: los teóricos VES/USD viven en su propia tabla (fija, NO
        # se recalcula cada ciclo y SÍ cubre órdenes facturadas -- ver
        # docstring de ventas_teoricos en db/schema.py), no en Bandeja.
        teoricos_map = {v.so_id: v for v in repo.all_ventas_teoricos()}
        clientes_map = {c.cliente_id: c.nombre for c in repo.all_clientes()}
        # Tarea 3e: descuentos aprobados manualmente desde la Bandeja 1 de
        # Facturación (nunca se escriben a Odoo) -- solo el registro activo
        # más reciente por orden cuenta para efectos de saldo interno.
        descuento_sistema_map: dict[str, dict[str, str]] = {
            r["so_id"]: r
            for r in repo.all_descuentos_sistema_aprobados()
            if str(r.get("activo", "true")).strip().lower() not in ("false", "0", "no")
        }

        # Fase 4 (estatus de pago): equivalentes de pago congelados por
        # abono (ya calculados en engine/equivalents.py -- NUNCA se compara
        # contra VES directo). Agrupados por orden en una sola pasada para
        # no golpear la BD/Sheets una vez por fila.
        #
        # Fase 0 (arquitectura de pagos, agosto 2026, pedido explícito del
        # usuario): solo Vinculaciones CONCILIADO cuentan aquí -- antes
        # esta lista incluía cualquier PENDIENTE (sugerencia FIFO de la
        # Fase 1 sin confirmar por Odoo), así que una orden podía verse
        # "pagada" en Ventas por una adivinanza sin confirmar, mientras el
        # motor de descuentos (ya corregido en la Fase 0) correctamente no
        # le otorgaba ningún descuento por la misma razón. Confirmado en
        # producción: 203 Vinculaciones PENDIENTE creadas por el auto-FIFO,
        # todas afectadas por este mismo hueco antes de este fix.
        vincs_por_so: dict[str, list[Vinculacion]] = {}
        # Comentario del usuario en el artefacto de verificación (agosto
        # 2026): una Vinculación PENDIENTE que cubre el teórico no debe
        # verse igual que "sin pago" -- el dinero ya está ahí (sugerencia
        # FIFO o vínculo manual reciente), solo falta que Odoo lo
        # reconcilie. Se guarda aparte (nunca mezclada con vincs_por_so,
        # que sigue siendo solo CONCILIADO para todo lo que ya dependía de
        # "pago real") para computar un estado intermedio informativo.
        vincs_pendientes_por_so: dict[str, list[Vinculacion]] = {}
        for v in repo.all_vinculaciones():
            if v.estado != EstadoVinculacion.CONCILIADO:
                vincs_pendientes_por_so.setdefault(v.so_id, []).append(v)
                continue
            vincs_por_so.setdefault(v.so_id, []).append(v)

        # Alerta "Revisar" (devolución/entrega de más/cancelada sin
        # devolver): cantidades pedida vs entregada, una sola pasada
        # agrupada por orden (no golpear la BD una vez por fila).
        lineas_por_so: dict[str, list[Any]] = {}
        for ln in repo.all_lineas():
            lineas_por_so.setdefault(ln.so_id, []).append(ln)

        # Reglas de días de crédito máximo por volumen -- SOLO validación en
        # Ventas contra el plazo real que Odoo otorgó (dias_credito_odoo_map,
        # abajo); NO alimentan la fórmula de recompra.
        reglas_credito_vol = [
            r
            for r in repo.all_reglas_dias_credito_volumen()
            if str(r.get("activo", "true")).strip().lower() not in ("false", "0", "no")
        ]

        def _max_dias_credito_por_litros(litros: float) -> int | None:
            candidatos = []
            for r in reglas_credito_vol:
                lit_min = float(r.get("litros_minimo") or 0)
                lit_max_raw = r.get("litros_maximo")
                lit_max = float(lit_max_raw) if lit_max_raw not in (None, "") else None
                if litros >= lit_min and (lit_max is None or litros <= lit_max):
                    candidatos.append(int(r.get("dias_credito_max") or 0))
            return max(candidatos) if candidatos else None

        usd_pricelist_ids, _ves_pricelist_ids = get_ui_pricelist_ids(repo)
        usd_ids_str = {str(x) for x in usd_pricelist_ids}
        historical_enabled = is_historical_pricelist_enabled(repo)
        # Precomputado una vez -- lo usan tanto el bloque de "lista aplicada"
        # más abajo como el nuevo cálculo de Monto pagado BCV/USD (para
        # decidir si la ruta BCV de un pago usa la tasa BCV-Euro histórica).
        es_historica_map = {
            o.so_id: es_orden_historica(o.fecha, o.lista_precios, historical_enabled)
            for o in ordenes
        }

        so_names = [o.so_id for o in ordenes]
        execute = None
        try:
            execute = _connect(config.odoo)
        except Exception as e_conn:
            logger.warning("No se pudo conectar a Odoo en get_ventas: %s", e_conn)

        # Tarea 1 (limpieza Ventas): nombre de cada pricelist para mostrar
        # "#id - Nombre" en las columnas de lista -- active_test False trae
        # también las archivadas (órdenes viejas pueden seguir apuntando a
        # una lista ya inactiva).
        pricelist_name_map: dict[str, str] = {}
        if execute:
            try:
                pls = execute(
                    "product.pricelist",
                    "search_read",
                    [[]],
                    {"fields": ["id", "name"], "context": {"active_test": False}},
                )
                pricelist_name_map = {str(p["id"]): str(p.get("name") or "") for p in pls}
            except Exception as e_pl:
                logger.warning("No se pudieron leer nombres de pricelist en get_ventas: %s", e_pl)

        def _lista_label(lista_id: str | None) -> str | None:
            if not lista_id:
                return None
            nombre = pricelist_name_map.get(str(lista_id))
            return f"#{lista_id} - {nombre}" if nombre else f"#{lista_id}"

        _HISTORICA_TXT = "Lista Histórica de Auditoría (Euro, ref. VES)"

        def _lista_label_hist(lista_id: str | None, es_historica: bool) -> str | None:
            # Tarea "lista histórica": para las órdenes de la ventana
            # histórica (20-Feb a 12-Mar-2026, o sin lista de precios
            # asignada -- ver ``historical_pricing.es_orden_historica``), el
            # precio VES real usado por el motor viene de esta lista de
            # respaldo, no de la pricelist normal (que puede estar vacía o
            # ser irrelevante para esa orden). Antes esto quedaba invisible
            # en Ventas cuando la orden no tenía ``lista_precios`` asignada
            # (el campo salía en blanco, sin explicar por qué el teórico VES
            # sí tenía un valor).
            base = _lista_label(lista_id)
            if not es_historica:
                return base
            return f"{base} + {_HISTORICA_TXT}" if base else _HISTORICA_TXT

        so_states_map: dict[str, str] = {}
        so_untaxed_map: dict[str, float] = {}
        dias_credito_odoo_map: dict[str, int] = {}
        litros_por_so: dict[str, float] = {}
        fecha_entrega_map: dict[str, str] = {}
        entrega_valida_set: set[str] = set()
        facturado_antes_imp_map: dict[str, float] = {}
        facturado_con_imp_map: dict[str, float] = {}
        nc_con_imp_map: dict[str, float] = {}
        nd_con_imp_map: dict[str, float] = {}
        desc_orden_odoo_map: dict[str, float] = {}
        desc_factura_odoo_map: dict[str, float] = {}
        invoice_ids_all: list[int] = []
        inv_id_to_so: dict[int, str] = {}
        pagos_bcv_binance_map: dict[str, dict[str, float]] = {}
        wh_iva_aplicado_map: dict[str, bool] = {}

        # Fase 2/4/5 (plan de consolidación de fuentes, agosto 2026):
        # entregas, litros, facturación (facturado/NC/ND) y descuentos de
        # línea ahora se leen del espejo (Postgres) en vez de Odoo en vivo
        # -- todos validados con datos reales (parity check contra las
        # 819 órdenes sincronizadas). NO dependen de ``execute`` -- se
        # calculan aunque Odoo esté caído, más resiliente que antes. Solo
        # ``_pagos_bcv_binance_por_orden`` (payment reconciliation,
        # dominio de Cobranza) se queda en vivo, alimentada por
        # invoice_ids_all/inv_id_to_so ya resueltos aquí.
        if so_names:
            entrega_valida_set, fecha_entrega_map = _entregas_desde_espejo(repo, so_names)
            litros_por_so = _litros_por_so_desde_espejo(repo, lineas_por_so)

            fact_espejo = _facturacion_por_so_desde_espejo(repo, so_names)
            facturado_con_imp_map = fact_espejo["facturado_con_imp"]
            facturado_antes_imp_map = fact_espejo["facturado_antes_imp"]
            nc_con_imp_map = fact_espejo["nc_con_imp"]
            nd_con_imp_map = fact_espejo["nd_con_imp"]
            invoice_ids_all = fact_espejo["invoice_ids_all"]
            inv_id_to_so = fact_espejo["inv_id_to_so"]
            inv_usd_ratio_map = fact_espejo["inv_usd_ratio_map"]
            # Fase 3 (plan de arquitectura de pagos): antes se consultaba
            # en vivo (_wh_iva_aplicado_por_orden) en el bloque `if
            # execute` de abajo -- ahora viene del espejo Factura, igual de
            # fresco (mismo sync incremental) y sin depender de Odoo en
            # cada carga de página.
            wh_iva_aplicado_map = fact_espejo["wh_iva_aplicado_por_so"]

            desc_orden_odoo_map, desc_factura_odoo_map = _descuentos_lineas_desde_espejo(
                repo, so_names, invoice_ids_all, inv_id_to_so, inv_usd_ratio_map
            )

        if execute and so_names:
            try:
                so_recs = execute(
                    "sale.order",
                    "search_read",
                    [[["name", "in", so_names]]],
                    {"fields": ["name", "state", "amount_untaxed", "payment_term_id"]},
                )
                for s in so_recs:
                    sname = str(s.get("name", "")).strip()
                    if sname:
                        so_states_map[sname] = str(s.get("state", "")).strip().lower()
                        so_untaxed_map[sname] = float(s.get("amount_untaxed") or 0.0)
                        term = s.get("payment_term_id")
                        term_name = (
                            term[1] if isinstance(term, list | tuple) and len(term) > 1 else ""
                        )
                        dias_credito_odoo_map[sname] = _parse_payment_term_days(term_name)

                # Monto pagado BCV/USD (Binance) -- columnas nuevas, cada
                # ruta con SU PROPIA tasa del día del pago (no duplicadas
                # como en _pagos_odoo_por_orden/_pagos_por_so_desde_cobranza).
                tasas_rows_pago = _all_serie_tasas_rows(repo)
                hist_rows_pago = repo.all_tasas_historicas_auditoria()
                pagos_bcv_binance_map = _pagos_bcv_binance_por_orden(
                    execute,
                    invoice_ids_all,
                    inv_id_to_so,
                    es_historica_map,
                    tasas_rows_pago,
                    hist_rows_pago,
                    facturado_con_imp_por_so=facturado_con_imp_map,
                )
            except Exception as e_odoo:
                logger.warning("Error consultando Odoo en get_ventas: %s", e_odoo)

        def _pct(monto: float, base: float) -> float | None:
            return round(monto / base, 4) if base > 0.005 else None

        _EPS_PAGO = 0.05

        def _estado_pago(pagado: float, target: float) -> str:
            if target <= _EPS_PAGO:
                return "pagada"
            if pagado >= target - _EPS_PAGO:
                return "pagada"
            if pagado > _EPS_PAGO:
                return "parcial"
            return "sin_pago"

        def _estado_pago_con_pendiente(
            pagado_confirmado: float,
            pagado_incl_pendiente: float,
            target: float,
            facturada: bool,
        ) -> str:
            """Dos estados intermedios nuevos (pedido del usuario en el

            artefacto de verificación, agosto 2026): una Vinculación
            PENDIENTE (FIFO sugerida o vínculo manual reciente, aún sin
            reconciliar en Odoo) que YA cubre el objetivo no debe verse
            igual que "sin_pago" -- el dinero ya está vinculado, solo falta
            que Odoo lo confirme. Se distingue por texto según si la orden
            ya está facturada (hay una factura real en Odoo esperando esa
            reconciliación) o no (no hay nada que reconciliar todavía en
            Odoo -- lo "pendiente" es, de hecho, la única confirmación que
            existe por ahora, dentro de la app). Nunca cambia el criterio
            de "pagado real" en ningún otro lado del sistema -- ver
            ``vincs_por_so`` (solo CONCILIADO) vs
            ``vincs_pendientes_por_so`` más arriba: los gates de
            descuento, saldo real y salida de CxC siguen usando
            exclusivamente lo confirmado.
            """
            estado = _estado_pago(pagado_confirmado, target)
            if estado == "pagada":
                return estado
            if target > _EPS_PAGO and pagado_incl_pendiente >= target - _EPS_PAGO:
                return "pagada_pendiente_odoo" if facturada else "pagada_temporal_app"
            return estado

        def _sin_datos_teorico(
            teorico_row: Any, bruta: float | None, precio_base: float
        ) -> bool:
            # Fase 10: ventas_teoricos aún sin fila para esta orden (nunca
            # calculado -- ver /api/backfill/ventas-teoricos y el daemon).
            # Extra: incluso con fila, un teórico en 0 mientras el precio
            # base real sí se calculó es sospechoso (ej. producto sin
            # precio en NINGUNA lista/ficha) -- se trata igual como "sin
            # datos" en vez de "nada que pagar".
            if teorico_row is None or bruta is None:
                return True
            return bruta <= 0.005 and precio_base > 0.005

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

            # Alerta "Revisar" -- 3 casos independientes, se combinan en un
            # solo campo con motivos separados por "; " (columna única en
            # Ventas, tooltip lista cuál aplica; ver docs de esta fase).
            revisar_motivos: list[str] = []
            if o.tiene_devolucion:
                revisar_motivos.append("Devolución registrada (total o parcial)")
            lineas_o = lineas_por_so.get(o.so_id, [])
            if lineas_o:
                cant_pedida = sum(float(ln.cantidad) for ln in lineas_o)
                cant_entregada = sum(float(ln.cantidad_entregada) for ln in lineas_o)
                if cant_entregada > cant_pedida + 0.005:
                    revisar_motivos.append(
                        f"Entrega de más ({cant_entregada:.2f} entregado "
                        f"vs {cant_pedida:.2f} pedido)"
                    )
            if live_state in ("cancel", "cancelled") and entrega_valida:
                revisar_motivos.append("Orden cancelada en Odoo, mercancía sin devolver")
            litros_orden = litros_por_so.get(o.so_id, 0.0)
            dias_credito_real = dias_credito_odoo_map.get(o.so_id, 0)
            max_dias_permitido = _max_dias_credito_por_litros(litros_orden)
            if max_dias_permitido is not None and dias_credito_real > max_dias_permitido:
                revisar_motivos.append(
                    f"Días de crédito excede el máximo por volumen "
                    f"({dias_credito_real}d otorgados vs {max_dias_permitido}d "
                    f"máximo para {litros_orden:.0f}L)"
                )
            revisar_motivo = "; ".join(revisar_motivos) if revisar_motivos else None

            b = bandeja_map.get(o.so_id)
            monto_orig = float(o.monto_total)  # amount_total Odoo: YA con impuestos

            venta_bruta_real = so_untaxed_map.get(o.so_id)
            if venta_bruta_real is None:
                # Sin conexión a Odoo: estimar el subtotal a partir del total con IVA.
                venta_bruta_real = monto_orig / (1 + iva_rate) if iva_rate > -1 else monto_orig
            venta_neta_real = monto_orig

            # Sin cálculo del motor aún (bandeja no recalculada) -- mejor
            # estimación disponible es el subtotal real, sin descuento conocido.
            # Estos dos NO se exponen como columnas (Tarea 2: redundantes con
            # el bloque VES/USD explícito de abajo) -- se usan solo para
            # "diferencia"/"alerta" contra la lista realmente aplicada.
            venta_bruta_teorica = float(b.precio_base_calculado) if b else venta_bruta_real
            venta_neta_teorica = float(b.total_motor) if b else venta_bruta_teorica

            venta_bruta_teorica_iva = venta_bruta_teorica * (1 + iva_rate)
            venta_neta_teorica_impuestos = venta_neta_teorica * (1 + iva_rate + igtf_rate)

            # Tarea 2: comparación explícita VES vs USD -- toda orden nace en
            # una lista VES o USD vigente, y el teórico existe para EVIDENCIAR
            # la discrepancia entre ambas, no para repetir un cálculo genérico
            # ya cubierto por "aplicada". Ambos bloques coexisten siempre,
            # incluso si la orden nació en una sola lista (ver bug orden 771:
            # antes salían idénticos por una causa raíz en el motor, ya
            # corregida -- estas columnas son las que hacen visible ese tipo
            # de discrepancia si volviera a ocurrir).
            teorico_row = teoricos_map.get(o.so_id)
            ves_bruta_teorica = float(teorico_row.teorico_ves) if teorico_row else None
            usd_bruta_teorica = float(teorico_row.teorico_usd) if teorico_row else None
            ves_desc_teorico = float(teorico_row.descuentos_teorico_ves) if teorico_row else 0.0
            usd_desc_teorico = float(teorico_row.descuentos_teorico_usd) if teorico_row else 0.0
            ves_neta_teorica = (ves_bruta_teorica - ves_desc_teorico) if teorico_row else None
            usd_neta_teorica = (usd_bruta_teorica - usd_desc_teorico) if teorico_row else None
            ves_neta_teorica_iva = (
                ves_neta_teorica * (1 + iva_rate + igtf_rate)
                if ves_neta_teorica is not None
                else None
            )
            usd_neta_teorica_iva = (
                usd_neta_teorica * (1 + iva_rate + igtf_rate)
                if usd_neta_teorica is not None
                else None
            )

            total_facturado_antes_impuestos = facturado_antes_imp_map.get(o.so_id, 0.0)
            total_facturado_con_impuestos = facturado_con_imp_map.get(o.so_id, 0.0)
            total_nc_aplicada = nc_con_imp_map.get(o.so_id, 0.0)
            # Tarea 3g: facturado en Odoo - N/C (lógica existente) + N/D (nueva).
            total_nd_aplicada = nd_con_imp_map.get(o.so_id, 0.0)
            total_facturado_neto = (
                total_facturado_con_impuestos - total_nc_aplicada + total_nd_aplicada
            )
            # Fase 3 (plan de arquitectura de pagos, agosto 2026, pedido
            # explícito del usuario): la retención de IVA debe comunicarse
            # a Ventas igual que ya hacen NC/ND -- antes `wh_iva_aplicado`
            # solo decidía si la orden salía de la Bandeja 3, sin tocar
            # nunca este saldo; una orden con retención ya confirmada en
            # Odoo se veía "parcialmente pagada" para siempre. Una vez
            # confirmada, el cliente ya no debe en efectivo el IVA (0-100%
            # retenido según el documento -- mismo rango que ya acepta la
            # Bandeja 3, sin asumir un porcentaje fijo), así que el saldo
            # objetivo baja por el IVA estimado completo de la factura.
            iva_retenido_confirmado = 0.0
            if wh_iva_aplicado_map.get(o.so_id) and total_facturado_neto > 0.005:
                iva_retenido_confirmado = total_facturado_neto - (
                    total_facturado_neto / (1 + iva_rate)
                )
                total_facturado_neto = max(0.0, total_facturado_neto - iva_retenido_confirmado)
            tiene_factura = total_facturado_con_impuestos > 0.005

            # Tarea 3c: descuentos ya aplicados en Odoo (orden/factura, columnas
            # separadas) + validación visual contra lo que dictamina el motor.
            motor_total_descuentos = Decimal(str(b.total_descuentos)) if b else Decimal("0")
            descuento_aplicado_orden = desc_orden_odoo_map.get(o.so_id, 0.0)
            descuento_aplicado_factura = desc_factura_odoo_map.get(o.so_id, 0.0)
            audit_orden = auditar_descuento_orden(
                so_id=o.so_id,
                motor_total_descuentos=motor_total_descuentos,
                odoo_descuento_aplicado=Decimal(str(descuento_aplicado_orden)),
            )
            audit_factura = auditar_descuento_factura(
                so_id=o.so_id,
                motor_total_descuentos=motor_total_descuentos,
                odoo_descuento_factura=Decimal(str(descuento_aplicado_factura)),
            )
            # Tarea 3e/Fase 3: descuento aprobado manualmente desde la
            # Bandeja 1 de Facturación -- ajusta el saldo interno de CxC sin
            # tocar Odoo. `saldo_pendiente_cxc` es el target real que debe
            # usar Cobranza/estatus de pago (Fase 4), no venta_neta_real/
            # total_facturado_neto en crudo.
            desc_sistema_row = descuento_sistema_map.get(o.so_id)
            descuento_aplicado_sistema = (
                round(float(desc_sistema_row["monto"]), 2) if desc_sistema_row else 0.0
            )
            base_sistema = total_facturado_neto if tiene_factura else venta_neta_real
            saldo_pendiente_cxc = round(max(0.0, base_sistema - descuento_aplicado_sistema), 2)

            # Fase 6: descuento pendiente por aplicar, DINÁMICO -- se resta
            # todo lo que ya materializa el descuento que el motor exige,
            # sin importar por qué mecanismo se formalizó:
            #   1. Descuento ya aplicado en el DOCUMENTO de referencia --
            #      la factura si ya existe (es la que manda una vez
            #      facturado), si no la orden (única referencia disponible
            #      antes de facturar). NUNCA se suman orden + factura: son
            #      el mismo descuento visto en dos documentos, no dos
            #      descuentos distintos.
            #   2. Notas de crédito ya emitidas (mecanismo alterno para
            #      materializar el mismo descuento sin editar la línea).
            #   3. Descuento de sistema ya aprobado (Fase 3, Bandeja 1).
            # max(0, ...) en cada paso: si Odoo/NC/sistema ya cubren más de
            # lo que el motor exige, no hay pendiente negativo -- ese exceso
            # ya se refleja como discrepancia en descuento_validacion_orden/
            # _factura (validación de que lo aplicado no exceda el cálculo
            # del motor), no se resta de vuelta aquí.
            aplicado_documento = (
                descuento_aplicado_factura if tiene_factura else descuento_aplicado_orden
            )
            pendiente_tras_documento = max(0.0, float(motor_total_descuentos) - aplicado_documento)
            pendiente_tras_nc = max(0.0, pendiente_tras_documento - total_nc_aplicada)
            descuento_pendiente_aplicar = round(
                max(0.0, pendiente_tras_nc - descuento_aplicado_sistema), 2
            )

            # Puntos 5-6 (agosto 2026, aprobado por el usuario): nueva columna
            # en la sección de totales de la orden real -- el subtotal REAL
            # de la orden (``venta_bruta_real``, lo que Odoo tiene hoy en la
            # línea), CON IMPUESTOS, con el descuento TEÓRICO total del
            # motor ya restado (``motor_total_descuentos``, el mismo que
            # exige la validación orden/factura), sin importar si ese
            # descuento ya se materializó en Odoo/N.C./sistema o sigue
            # pendiente. Responde "¿cuánto debería costar esta orden con
            # impuestos si se aplicaran todos los descuentos que el motor
            # calcula?", a diferencia de `venta_neta_real` (lo que Odoo YA
            # tiene aplicado hoy).
            #
            # Tarea 2 (bloqueo por sobre-descuento): si Odoo YA tiene
            # aplicado más descuento del que el motor calcula (orden o
            # factura, ver `_detectar_sobre_descuento_vigente`), restar
            # el teórico aquí encima sería engañoso -- la orden está
            # esperando revisión en Auditoría, no un descuento adicional.
            # En ese caso el subtotal se muestra SIN el descuento teórico
            # (solo + impuestos), igual que `venta_neta_real`.
            bloqueado_por_sobre_descuento = (
                audit_orden.enviar_a_bandeja and audit_orden.diferencia_usd < 0
            ) or (audit_factura.enviar_a_bandeja and audit_factura.diferencia_usd < 0)
            orden_real_subtotal_teoricos_base = (
                venta_bruta_real
                if bloqueado_por_sobre_descuento
                else max(0.0, venta_bruta_real - float(motor_total_descuentos))
            )
            orden_real_subtotal_teoricos = round(
                orden_real_subtotal_teoricos_base * (1 + iva_rate + igtf_rate), 2
            )

            precio_base_calculado = float(b.precio_base_calculado) if b else 0.0

            # Fase 4: estatus de pago -- BCV/Binance según a qué lista
            # corresponde cada columna (ver docstring del endpoint y el
            # plan aprobado: "Equivalentes de pago"). "Real Orden"/"Real
            # Factura" usan la referencia de la lista con la que NACIÓ la
            # orden (BCV si VES o Lista Histórica de Auditoría, Binance si
            # USD); "Teórico VES" siempre BCV, "Teórico USD" siempre Binance.
            # Fase 9: Vinculaciones (fuente teórica) tiene precedencia si la
            # orden tiene alguna registrada; si no (caso real hoy: esa
            # tabla está vacía casi siempre), se usa ``pagos_bcv_binance_map``
            # (``_pagos_bcv_binance_por_orden``, la MISMA fuente que ya
            # alimenta "Monto Pagado BCV"/"USD" en esta tabla) -- ANTES se
            # usaba ``pagos_odoo_map`` (fallback de ``_pagos_por_so_desde_
            # cobranza``), que daba el MISMO número duplicado para BCV y
            # Binance y NUNCA aplicaba el ajuste de tasa BCV-EUR para
            # órdenes de la ventana histórica (bug real reportado por el
            # usuario: un pago en VES se restaba igual de ambos teóricos, y
            # para una orden histórica se restaba a tasa BCV normal en vez
            # de EUR).
            vincs_orden = vincs_por_so.get(o.so_id, [])
            if vincs_orden:
                val_bcv = float(valor_pagado_bcv_usd(vincs_orden))
                val_binance = float(valor_pagado_binance_usd(vincs_orden))
            else:
                p_bcv_binance = pagos_bcv_binance_map.get(o.so_id, {})
                val_bcv = float(p_bcv_binance.get("monto_pagado_bcv", 0.0))
                val_binance = float(p_bcv_binance.get("monto_pagado_usd_binance", 0.0))
            # Comentario del usuario (artefacto de verificación, agosto
            # 2026): además de "confirmado" (CONCILIADO, arriba) se calcula
            # cuánto sumaría si se incluyera lo PENDIENTE (vinculado -- FIFO
            # o manual -- pero aún sin reconciliar en Odoo), SOLO para
            # exponer los 2 estados intermedios nuevos más abajo. Nunca se
            # usa para "target"/saldo real ni para ningún gate de
            # descuento -- eso sigue siendo exclusivamente CONCILIADO.
            vincs_pend_orden = vincs_pendientes_por_so.get(o.so_id, [])
            if vincs_pend_orden:
                val_bcv_incl_pendiente = val_bcv + float(valor_pagado_bcv_usd(vincs_pend_orden))
                val_binance_incl_pendiente = val_binance + float(
                    valor_pagado_binance_usd(vincs_pend_orden)
                )
            else:
                val_bcv_incl_pendiente = val_bcv
                val_binance_incl_pendiente = val_binance
            es_historica_o = es_orden_historica(o.fecha, o.lista_precios, historical_enabled)
            es_lista_usd_nacimiento = (
                str(o.lista_precios) in usd_ids_str and not es_historica_o
            )
            val_ref_nacimiento = val_binance if es_lista_usd_nacimiento else val_bcv
            val_ref_nacimiento_incl_pendiente = (
                val_binance_incl_pendiente if es_lista_usd_nacimiento else val_bcv_incl_pendiente
            )

            target_orden = max(0.0, venta_neta_real - descuento_aplicado_sistema)
            estatus_pago_real_orden = _estado_pago_con_pendiente(
                val_ref_nacimiento, val_ref_nacimiento_incl_pendiente, target_orden, o.facturada
            )

            if tiene_factura:
                target_factura = max(0.0, total_facturado_neto - descuento_aplicado_sistema)
                estatus_pago_real_factura = _estado_pago_con_pendiente(
                    val_ref_nacimiento,
                    val_ref_nacimiento_incl_pendiente,
                    target_factura,
                    o.facturada,
                )
            else:
                estatus_pago_real_factura = "sin_factura"

            # Fase 9 -- bug real (S00696 y similares): antes ambos huecos
            # de datos (sin bandeja, o teórico en 0 con precio base > 0)
            # caían en "pagada"/"sin_pago" indistinguibles de un estado
            # real -- ahora "sin_datos" explícito.
            estatus_pago_teorico_ves = (
                "sin_datos"
                if (
                    _sin_datos_teorico(teorico_row, ves_bruta_teorica, precio_base_calculado)
                    or ves_neta_teorica_iva is None
                )
                else _estado_pago_con_pendiente(
                    val_bcv, val_bcv_incl_pendiente, ves_neta_teorica_iva, o.facturada
                )
            )
            estatus_pago_teorico_usd = (
                "sin_datos"
                if (
                    _sin_datos_teorico(teorico_row, usd_bruta_teorica, precio_base_calculado)
                    or usd_neta_teorica_iva is None
                )
                else _estado_pago_con_pendiente(
                    val_binance, val_binance_incl_pendiente, usd_neta_teorica_iva, o.facturada
                )
            )

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

            # Árbol de enrutamiento de CxC (Sección 5 del Manual): decide si
            # la orden sale de CxC activa y a qué bandeja se enruta, en
            # base a los mismos 3 estatus de pago ya calculados arriba
            # (colapsados a booleano: True solo si el estado es "pagada").
            clasificacion_cxc = clasificar_estado_cxc(
                so_id=o.so_id,
                facturada=bool(o.facturada),
                teorico_bs_pagado=estatus_pago_teorico_ves == "pagada",
                teorico_usd_pagado=estatus_pago_teorico_usd == "pagada",
                factura_real_pagada=estatus_pago_real_factura == "pagada",
                nacio_en_lista_usd=es_lista_usd_nacimiento,
            )

            items.append(
                {
                    "so_id": o.so_id,
                    "cliente_nombre": clientes_map.get(o.cliente_id, f"Cliente ID: {o.cliente_id}"),
                    "vendedor": o.vendedor_email or "Sin Vendedor",
                    "fecha": o.fecha.isoformat(),
                    "facturada": o.facturada,
                    "venta_bruta_real": round(venta_bruta_real, 2),
                    "venta_neta_real": round(venta_neta_real, 2),
                    "total_facturado_antes_impuestos": round(total_facturado_antes_impuestos, 2),
                    "total_facturado_con_impuestos": round(total_facturado_con_impuestos, 2),
                    "total_nc_aplicada": round(total_nc_aplicada, 2),
                    "total_nd_aplicada": round(total_nd_aplicada, 2),
                    "total_facturado_neto": round(total_facturado_neto, 2),
                    # Fase 3: informativo -- cuánto de total_facturado_neto
                    # ya se restó por retención de IVA confirmada en Odoo.
                    "iva_retenido_confirmado": round(iva_retenido_confirmado, 2),
                    "diferencia": diferencia,
                    "alerta": alerta,
                    "revisar_motivo": revisar_motivo,
                    # Días de crédito reales (payment_term de Odoo) y fecha
                    # de entrega efectiva (ALM/OUT, stock.picking saliente
                    # "done") -- mismo criterio que ya usa el reporte de CxC.
                    "dias_credito": dias_credito_odoo_map.get(o.so_id, o.dias_credito or 0),
                    "fecha_entrega": fecha_entrega_map.get(o.so_id),
                    # Total pagado según Odoo (mismo valor que ya usan
                    # estatus_pago_real_orden/_factura internamente --
                    # val_ref_nacimiento, BCV o Binance según la lista de
                    # nacimiento de la orden), expuesto ahora como columna
                    # propia junto a los totales de factura.
                    "monto_pagado_factura_odoo": round(val_ref_nacimiento, 2),
                    # True si Odoo ya marcó la factura como retenida
                    # (``account.move.wh_iva``, en vivo) -- usado por
                    # /api/bandeja para sacar la orden de la bandeja de
                    # retención de IVA una vez el comprobante fue procesado.
                    "wh_iva_aplicado": wh_iva_aplicado_map.get(o.so_id, False),
                    # Montos crudos usados para comparar contra cada
                    # teórico (val_bcv/val_binance -- distintos de
                    # monto_pagado_bcv/_usd, que solo existen una vez
                    # facturada la orden porque se leen de pagos
                    # reconciliados contra la FACTURA en Odoo). Expuestos
                    # para que /api/bandeja pueda aplicar la tolerancia de
                    # retención de IVA sin recalcular la conversión.
                    "pagado_teorico_bcv": round(val_bcv, 2),
                    "pagado_teorico_binance": round(val_binance, 2),
                    # Monto pagado con SU PROPIA tasa por ruta (BCV vs
                    # Binance del día del pago) -- distinto del "abono_bcv"/
                    # "abono_binance" que usa estatus_pago_real_orden/_
                    # factura (esos dan el mismo número para ambas rutas).
                    "monto_pagado_bcv": round(
                        pagos_bcv_binance_map.get(o.so_id, {}).get("monto_pagado_bcv", 0.0), 2
                    ),
                    "monto_pagado_usd": round(
                        pagos_bcv_binance_map.get(o.so_id, {}).get(
                            "monto_pagado_usd_binance", 0.0
                        ),
                        2,
                    ),
                    # Tarea 1: lista con la que nació la orden vs. la que
                    # terminó aplicando el motor (puede diferir por
                    # reselección según método de pago -- ver docstring del
                    # endpoint). Id crudo + "#id - Nombre" para mostrar.
                    "lista_nacimiento": o.lista_precios,
                    "lista_nacimiento_label": _lista_label_hist(o.lista_precios, es_historica_o),
                    # Árbol de enrutamiento: True solo si nació en lista USD
                    # y NO es histórica (ver clasificar_estado_cxc -- una
                    # orden USD exige el Teórico USD específicamente pagado,
                    # no le basta el Teórico BS).
                    "nacio_en_lista_usd": es_lista_usd_nacimiento,
                    "lista_aplicada": b.lista_aplicada if b else o.lista_precios,
                    "lista_aplicada_label": _lista_label_hist(
                        b.lista_aplicada if b else o.lista_precios, es_historica_o
                    ),
                    # Tarea 2: comparación explícita por lista (VES/USD), cada
                    # una con su bruta/neta y su "+ impuestos" -- reemplaza
                    # las columnas genéricas "venta bruta/neta teórica"
                    # (redundantes: toda orden nace en VES o USD, y el
                    # teórico existe para EVIDENCIAR la discrepancia entre
                    # ambas, no para repetir un cálculo genérico).
                    "ves_bruta_teorica": (
                        round(ves_bruta_teorica, 2) if ves_bruta_teorica is not None else None
                    ),
                    "ves_bruta_teorica_iva": (
                        round(ves_bruta_teorica * (1 + iva_rate), 2)
                        if ves_bruta_teorica is not None
                        else None
                    ),
                    "ves_neta_teorica": (
                        round(ves_neta_teorica, 2) if ves_neta_teorica is not None else None
                    ),
                    "ves_neta_teorica_iva": (
                        round(ves_neta_teorica_iva, 2) if ves_neta_teorica_iva is not None else None
                    ),
                    # Fase 6: reemplaza la columna genérica "Desc. Motor" --
                    # el motor calcula un descuento distinto para VES y para
                    # USD (reglas con listas_aplicables distintas por lista),
                    # así que un solo número era ambiguo/incorrecto para
                    # comparar contra cada teórico.
                    "descuento_teorico_ves": round(ves_desc_teorico, 2),
                    "descuento_teorico_ves_pct": _pct(ves_desc_teorico, ves_bruta_teorica or 0.0),
                    "usd_bruta_teorica": (
                        round(usd_bruta_teorica, 2) if usd_bruta_teorica is not None else None
                    ),
                    "usd_bruta_teorica_iva": (
                        round(usd_bruta_teorica * (1 + iva_rate), 2)
                        if usd_bruta_teorica is not None
                        else None
                    ),
                    "usd_neta_teorica": (
                        round(usd_neta_teorica, 2) if usd_neta_teorica is not None else None
                    ),
                    "usd_neta_teorica_iva": (
                        round(usd_neta_teorica_iva, 2) if usd_neta_teorica_iva is not None else None
                    ),
                    "descuento_teorico_usd": round(usd_desc_teorico, 2),
                    "descuento_teorico_usd_pct": _pct(usd_desc_teorico, usd_bruta_teorica or 0.0),
                    # Tarea 3c: descuentos aplicados en Odoo (orden/factura) +
                    # validación visual vs. lo que dictamina el motor. Monto
                    # Y porcentaje en todos los campos de descuento (pedido
                    # explícito del usuario).
                    "descuento_aplicado_orden": round(descuento_aplicado_orden, 2),
                    "descuento_aplicado_orden_pct": _pct(
                        descuento_aplicado_orden, venta_bruta_real
                    ),
                    "descuento_aplicado_factura": round(descuento_aplicado_factura, 2),
                    "descuento_aplicado_factura_pct": _pct(
                        descuento_aplicado_factura, total_facturado_antes_impuestos
                    ),
                    "descuento_motor_total": round(float(motor_total_descuentos), 2),
                    "descuento_motor_total_pct": _pct(
                        float(motor_total_descuentos), precio_base_calculado
                    ),
                    "descuento_validacion_orden": audit_orden.estado.value,
                    "descuento_validacion_factura": audit_factura.estado.value,
                    # Tarea 3d: descuento que el motor exige y aún no está en Odoo.
                    # Puntos 5-6: se muestra en la sección de totales de la
                    # orden real (junto a venta_bruta_real/venta_neta_real en
                    # la UI), no solo al final de la tabla.
                    "descuento_pendiente_aplicar": descuento_pendiente_aplicar,
                    "descuento_pendiente_aplicar_pct": _pct(
                        descuento_pendiente_aplicar, precio_base_calculado
                    ),
                    # Puntos 5-6: venta_bruta_real + impuestos menos TODO lo
                    # que el motor exige de descuento (ver cálculo arriba) --
                    # "cuánto debería costar esta orden si se aplicaran todos
                    # los descuentos teóricos", vive junto a las demás
                    # columnas de totales de la orden real. No resta el
                    # teórico si la orden está bloqueada por sobre-descuento
                    # (ver flag hermano).
                    "orden_real_subtotal_teoricos": orden_real_subtotal_teoricos,
                    "orden_real_subtotal_teoricos_bloqueado": bloqueado_por_sobre_descuento,
                    # Fase 3: descuento aprobado manualmente desde la Bandeja 1
                    # de Facturación (nunca se escribe a Odoo, solo ajusta el
                    # saldo interno de CxC).
                    "descuento_aplicado_sistema": descuento_aplicado_sistema,
                    "descuento_aplicado_sistema_pct": _pct(
                        descuento_aplicado_sistema, venta_bruta_real
                    ),
                    "descuento_aplicado_sistema_motivo": (
                        desc_sistema_row["motivo"] if desc_sistema_row else None
                    ),
                    "saldo_pendiente_cxc": saldo_pendiente_cxc,
                    # Fase 4: estatus de pago -- "pagada"/"parcial"/"sin_pago"
                    # (+ "sin_factura" en real_factura si aún no hay factura).
                    # Ver docstring del endpoint para la selección BCV/Binance
                    # de cada columna.
                    "estatus_pago_real_orden": estatus_pago_real_orden,
                    "estatus_pago_real_factura": estatus_pago_real_factura,
                    "estatus_pago_teorico_ves": estatus_pago_teorico_ves,
                    "estatus_pago_teorico_usd": estatus_pago_teorico_usd,
                    # Árbol de enrutamiento de CxC (Sección 5 del Manual):
                    # ver src/cxc/engine/cxc_routing.py -- fuente única de
                    # verdad para Bandeja 1/2, Reporte de Saldos y la nueva
                    # Bandeja de Auditoría de Precios.
                    "sale_de_cxc": clasificacion_cxc.sale_de_cxc,
                    "bandeja_destino": (
                        clasificacion_cxc.bandeja_destino.value
                        if clasificacion_cxc.bandeja_destino
                        else None
                    ),
                    "cxc_routing_motivo": clasificacion_cxc.motivo,
                }
            )
            # Antigüedad ("Días Vencido" en la UI de Ventas): fuente única
            # ahora -- misma fórmula que ya usaba /api/reporte-cxc-cliente
            # (_dias_vencido_orden), calculada UNA vez aquí en vez de que
            # cada consumidor la recalcule por su cuenta (pedido explícito
            # del usuario, agosto 2026). fecha_entrega/dias_credito ya están
            # en el item recién agregado.
            item_actual = items[-1]
            dt_venc, dias_venc = _fecha_y_dias_vencido(item_actual, today_ventas)
            item_actual["fecha_vencimiento"] = dt_venc.isoformat() if dt_venc else None
            item_actual["dias_vencido"] = dias_venc

        items.sort(key=lambda it: str(it["so_id"]), reverse=True)

        res = {
            "items": items,
            "kpis": {
                "total_ordenes": len(items),
                "total_alertas": total_alertas,
                "iva_rate": iva_rate,
                "igtf_rate": igtf_rate,
                "igtf_activo": config.engine.igtf_activo,
                "subtotal_real_total": round(sum(i["venta_bruta_real"] for i in items), 2),
                # Tarea 2: totales por lista VES/USD (reemplaza los KPIs
                # genéricos "venta bruta/neta teórica").
                "ves_bruta_teorica_total": round(
                    sum(i["ves_bruta_teorica"] or 0 for i in items), 2
                ),
                "ves_neta_teorica_iva_total": round(
                    sum(i["ves_neta_teorica_iva"] or 0 for i in items), 2
                ),
                "usd_bruta_teorica_total": round(
                    sum(i["usd_bruta_teorica"] or 0 for i in items), 2
                ),
                "usd_neta_teorica_iva_total": round(
                    sum(i["usd_neta_teorica_iva"] or 0 for i in items), 2
                ),
                "venta_neta_real_total": round(sum(i["venta_neta_real"] for i in items), 2),
                "total_facturado_neto_total": round(
                    sum(i["total_facturado_neto"] for i in items), 2
                ),
                "total_nc_aplicada_total": round(sum(i["total_nc_aplicada"] for i in items), 2),
                "total_nd_aplicada_total": round(sum(i["total_nd_aplicada"] for i in items), 2),
                "descuento_pendiente_aplicar_total": round(
                    sum(i["descuento_pendiente_aplicar"] for i in items), 2
                ),
            },
        }
        if vendedor is None:
            _VENTAS_CACHE["data"] = res
            _VENTAS_CACHE["timestamp"] = time.time()
        return res
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if vendedor is None:
            _ventas_computing = False


@app.get("/api/ventas/{so_id}/detalle")
async def get_ventas_detalle(so_id: str):
    """Fase 5 (modal de detalle de orden): desglose por línea de producto en

    4 modos -- Real Orden, Real Factura, Teórico VES, Teórico USD. Un solo
    fetch trae los 4 modos; el front re-pinta al cambiar de selector sin
    volver a golpear el backend.

    Real Orden/Real Factura leen (no calculan) lo que Odoo ya tiene por
    línea -- TODAS las líneas de producto, a diferencia de
    ``_leer_descuentos_lineas_odoo`` (que solo trae las que ya tienen
    ``discount > 0``, útil para el total agregado de ``/api/ventas`` pero no
    para un desglose completo).

    Teórico VES/USD usa ``EngineRunner.build_inputs`` (mismo cableo repo ->
    EngineInputs que usa el cálculo real de la bandeja, sin duplicar lógica)
    + ``discounts.lineas_con_precio`` para resolver el precio unitario de
    cada línea contra la lista VES/USD vigente, con el NOMBRE del producto
    (via el mapa id->nombre armado con las líneas reales, ``sale.order.
    line`` ya trae ``[id, nombre]``). El motor no distribuye sus descuentos
    por línea (los calcula por grupo/orden, ver ``engine/discounts.py::
    _calcular_componentes``) -- cada bloque teórico trae un ``conceptos``
    a nivel de orden (``discounts.conceptos_descuento_teorico``, mismos 3
    componentes -- recompra/contado/volumen -- que suma ``BandejaFacturacion.
    descuentos_teorico_ves``/``_usd``) en vez de un desglose por línea.

    Fase 8: cada línea trae también litros (``litros_unitario``/``_total``,
    vía ``product.template.product_volume`` -- mismo campo que usa el motor
    para las reglas de Descuento por Volumen) y el subtotal antes/después
    de descuento (``subtotal_antes_descuento``/``_despues_descuento`` --
    en los teóricos ambos coinciden a propósito, el motor no reparte su
    descuento por línea). Cada bloque también trae ``litros_total``
    agregado.

    También trae ``pagos`` (vinculaciones de la orden con sus equivalentes
    congelados) para el botón "Ver Pagos".
    """
    try:
        repo = get_repo()
        orden = repo.get_orden(so_id)
        if orden is None:
            raise HTTPException(status_code=404, detail=f"Orden {so_id} no encontrada")
        config = AppConfig.from_env()

        clientes_map = {c.cliente_id: c.nombre for c in repo.all_clientes()}
        cliente_nombre = clientes_map.get(orden.cliente_id, f"Cliente ID: {orden.cliente_id}")

        execute = None
        try:
            execute = _connect(config.odoo)
        except Exception as e_conn:
            logger.warning("No se pudo conectar a Odoo en get_ventas_detalle: %s", e_conn)

        pricelist_name_map: dict[str, str] = {}
        if execute:
            try:
                pls = execute(
                    "product.pricelist",
                    "search_read",
                    [[]],
                    {"fields": ["id", "name"], "context": {"active_test": False}},
                )
                pricelist_name_map = {str(p["id"]): str(p.get("name") or "") for p in pls}
            except Exception as e_pl:
                logger.warning(
                    "No se pudieron leer nombres de pricelist en get_ventas_detalle: %s", e_pl
                )

        def _lista_label(lista_id: str | None) -> str | None:
            if not lista_id:
                return None
            nombre = pricelist_name_map.get(str(lista_id))
            return f"#{lista_id} - {nombre}" if nombre else f"#{lista_id}"

        def _nombre_producto(raw: Any) -> str:
            if isinstance(raw, list | tuple) and len(raw) > 1:
                return str(raw[1])
            return str(raw or "")

        def _producto_id(raw: Any) -> str:
            if isinstance(raw, list | tuple) and len(raw) > 0:
                return str(raw[0])
            return str(raw or "")

        # Price resolver construido una sola vez (Fase 8) -- lo usan tanto
        # las líneas reales (litros por línea, vía ``.volumen(producto)``,
        # mismo campo Odoo ``product.template.product_volume`` que usa el
        # motor para las reglas de Descuento por Volumen) como las líneas
        # teóricas (precio unitario por lista, ver más abajo).
        price_resolver: OdooPriceResolver | None = None
        if execute:
            usd_ids_pr, ves_ids_pr = get_ui_pricelist_ids(repo)
            pricelist_ids_map_pr = {
                "USD": int(usd_ids_pr[0]) if usd_ids_pr and str(usd_ids_pr[0]).isdigit() else 4,
                "BCV": int(ves_ids_pr[0]) if ves_ids_pr and str(ves_ids_pr[0]).isdigit() else 5,
            }
            fallback_pl_ids_pr = [
                int(x) for x in (*usd_ids_pr, *ves_ids_pr) if str(x).isdigit()
            ]
            price_resolver = OdooPriceResolver(
                execute,
                pricelist_ids_map_pr,
                fallback_pl_ids_pr,
                build_fallback_ficha_config(repo),
            )

        def _litros(producto_id: str, cantidad: float) -> tuple[float, float]:
            if price_resolver is None or not producto_id:
                return 0.0, 0.0
            try:
                vol_unit = float(price_resolver.volumen(producto_id))
            except Exception:
                vol_unit = 0.0
            return round(vol_unit, 3), round(vol_unit * cantidad, 3)

        # --- Real Orden: sale.order.line COMPLETO (todas las líneas, no
        # solo las que tienen discount > 0 -- a diferencia del agregado que
        # usa /api/ventas). También arma el mapa id -> nombre de producto
        # (Fase 6) que se reusa para las líneas teóricas VES/USD, que solo
        # traen el id crudo (``LineaOrden.producto``, sin nombre).
        lineas_real_orden: list[dict[str, Any]] = []
        producto_nombre_map: dict[str, str] = {}
        if execute:
            try:
                sol = execute(
                    "sale.order.line",
                    "search_read",
                    [[["order_id.name", "=", so_id], ["display_type", "=", False]]],
                    {
                        "fields": [
                            "product_id",
                            "product_uom_qty",
                            "price_unit",
                            "discount",
                            "price_subtotal",
                        ]
                    },
                )
                for line in sol:
                    prod_raw = line.get("product_id")
                    if not prod_raw:
                        continue
                    if isinstance(prod_raw, list | tuple) and len(prod_raw) > 1:
                        producto_nombre_map[str(prod_raw[0])] = str(prod_raw[1])
                    qty = float(line.get("product_uom_qty") or 0)
                    price_unit = float(line.get("price_unit") or 0)
                    disc_pct = float(line.get("discount") or 0)
                    subtotal = float(line.get("price_subtotal") or 0)
                    litros_unit, litros_tot = _litros(_producto_id(prod_raw), qty)
                    lineas_real_orden.append(
                        {
                            "producto": _nombre_producto(prod_raw),
                            "cantidad": qty,
                            "precio_unitario": round(price_unit, 2),
                            "descuento_pct": round(disc_pct, 2),
                            "descuento_monto": round(qty * price_unit * (disc_pct / 100.0), 2),
                            "subtotal_antes_descuento": round(qty * price_unit, 2),
                            "subtotal_despues_descuento": round(subtotal, 2),
                            "subtotal": round(subtotal, 2),
                            "litros_unitario": litros_unit,
                            "litros_total": litros_tot,
                        }
                    )
            except Exception as e_sol:
                logger.warning(
                    "Error leyendo sale.order.line en get_ventas_detalle: %s", e_sol
                )

        # --- Real Factura: account.move.line de facturas posted ligadas por
        # invoice_origin (mismo criterio que el resto del rediseño).
        # Fase 9 -- bug real: facturas en VES (moneda de la compañía)
        # mostraban price_unit/price_subtotal CRUDOS (en bolívares, ej.
        # $27,708.03 por una caja de aceite) porque Odoo no expone un
        # "price_unit en USD" por línea. Se usa la MISMA proporción que ya
        # calcula Odoo a nivel de factura (amount_total_signed_usd /
        # amount_total, su propia conversión con la tasa de la fecha de
        # contabilización) y se aplica a cada línea de esa factura --
        # evita reinventar una tasa BCV propia que podría no coincidir con
        # la que Odoo ya usó para el asiento contable. Puede haber MÁS de
        # una factura por orden (reflejadas todas aquí, agregadas) -- si
        # una orden fue facturada más de una vez, esto no es un error del
        # reporte, es un reflejo fiel de Odoo. ---
        lineas_real_factura: list[dict[str, Any]] = []
        inv_ids: list[int] = []
        if execute:
            try:
                invs = execute(
                    "account.move",
                    "search_read",
                    [
                        [
                            ["invoice_origin", "=", so_id],
                            ["move_type", "=", "out_invoice"],
                            ["state", "=", "posted"],
                        ]
                    ],
                    {"fields": ["id", "amount_total", "amount_total_signed_usd"]},
                )
                inv_ids = [i["id"] for i in invs]
                inv_usd_ratio: dict[int, float] = {}
                for inv in invs:
                    tot = float(inv.get("amount_total") or 0)
                    tot_usd = abs(float(inv.get("amount_total_signed_usd") or 0))
                    inv_usd_ratio[int(inv["id"])] = (tot_usd / tot) if tot > 0.005 else 1.0
                if inv_ids:
                    aml = execute(
                        "account.move.line",
                        "search_read",
                        [
                            [
                                ["move_id", "in", inv_ids],
                                ["display_type", "in", ["product", False]],
                            ]
                        ],
                        {
                            "fields": [
                                "move_id",
                                "product_id",
                                "quantity",
                                "price_unit",
                                "discount",
                                "price_subtotal",
                            ]
                        },
                    )
                    for line in aml:
                        prod_raw_f = line.get("product_id")
                        if not prod_raw_f:
                            continue
                        move_raw = line.get("move_id")
                        move_id = (
                            int(move_raw[0])
                            if isinstance(move_raw, list | tuple)
                            else int(move_raw or 0)
                        )
                        ratio = inv_usd_ratio.get(move_id, 1.0)
                        qty = float(line.get("quantity") or 0)
                        price_unit = float(line.get("price_unit") or 0) * ratio
                        disc_pct = float(line.get("discount") or 0)
                        subtotal = float(line.get("price_subtotal") or 0) * ratio
                        litros_unit, litros_tot = _litros(_producto_id(prod_raw_f), qty)
                        lineas_real_factura.append(
                            {
                                "producto": _nombre_producto(prod_raw_f),
                                "cantidad": qty,
                                "precio_unitario": round(price_unit, 2),
                                "descuento_pct": round(disc_pct, 2),
                                "descuento_monto": round(
                                    qty * price_unit * (disc_pct / 100.0), 2
                                ),
                                "subtotal_antes_descuento": round(qty * price_unit, 2),
                                "subtotal_despues_descuento": round(subtotal, 2),
                                "subtotal": round(subtotal, 2),
                                "litros_unitario": litros_unit,
                                "litros_total": litros_tot,
                            }
                        )
            except Exception as e_aml:
                logger.warning(
                    "Error leyendo account.move.line en get_ventas_detalle: %s", e_aml
                )

        # --- Teórico VES / Teórico USD: EngineRunner.build_inputs +
        # discounts.lineas_con_precio (mismo cableo que el cálculo real).
        # Fase 6: nombre de producto (via producto_nombre_map, armado arriba
        # con las líneas reales de la orden -- mismos ids de producto) +
        # conceptos de descuento por lista (discounts.conceptos_descuento_
        # teorico, MISMOS 3 componentes -- recompra/contado/volumen -- que
        # suma BandejaFacturacion.descuentos_teorico_ves/_usd). Son
        # conceptos a nivel de orden, no por línea (el motor no distribuye
        # sus descuentos por línea, ver docstring del helper). ---
        lineas_teorico_ves: list[dict[str, Any]] = []
        lineas_teorico_usd: list[dict[str, Any]] = []
        conceptos_teorico_ves: list[dict[str, Any]] = []
        conceptos_teorico_usd: list[dict[str, Any]] = []
        lista_ves_id: str | None = None
        lista_usd_id: str | None = None
        if execute and price_resolver is not None:
            try:
                from cxc.engine.discounts import (
                    _lista_usd_activa,
                    _lista_ves_activa,
                    conceptos_descuento_teorico,
                    lineas_con_precio,
                )

                runner = EngineRunner(repo, price_resolver, config.engine)
                inputs = runner.build_inputs(so_id, date.today())
                if inputs is not None:
                    lista_ves_id = _lista_ves_activa(inputs)
                    lista_usd_id = _lista_usd_activa(inputs)
                    for fila in lineas_con_precio(inputs, lista_ves_id):
                        subt = round(float(fila["subtotal"]), 2)
                        lineas_teorico_ves.append(
                            {
                                "producto": producto_nombre_map.get(
                                    str(fila["producto"]), str(fila["producto"])
                                ),
                                "cantidad": float(fila["cantidad"]),
                                "precio_unitario": round(float(fila["precio_unitario"]), 2),
                                # El motor no asigna descuento por línea (se
                                # calcula por grupo/orden) -- antes y después
                                # coinciden a propósito, no es un olvido.
                                "subtotal_antes_descuento": subt,
                                "subtotal_despues_descuento": subt,
                                "subtotal": subt,
                                "litros_unitario": round(float(fila["litros_unitario"]), 3),
                                "litros_total": round(float(fila["litros_total"]), 3),
                            }
                        )
                    for fila in lineas_con_precio(inputs, lista_usd_id):
                        subt = round(float(fila["subtotal"]), 2)
                        lineas_teorico_usd.append(
                            {
                                "producto": producto_nombre_map.get(
                                    str(fila["producto"]), str(fila["producto"])
                                ),
                                "cantidad": float(fila["cantidad"]),
                                "precio_unitario": round(float(fila["precio_unitario"]), 2),
                                "subtotal_antes_descuento": subt,
                                "subtotal_despues_descuento": subt,
                                "subtotal": subt,
                                "litros_unitario": round(float(fila["litros_unitario"]), 3),
                                "litros_total": round(float(fila["litros_total"]), 3),
                            }
                        )
                    conceptos_teorico_ves = [
                        {"concepto": c["concepto"], "monto": round(float(c["monto"]), 2)}
                        for c in conceptos_descuento_teorico(inputs, lista_ves_id, pura_bcv=True)
                    ]
                    conceptos_teorico_usd = [
                        {"concepto": c["concepto"], "monto": round(float(c["monto"]), 2)}
                        for c in conceptos_descuento_teorico(inputs, lista_usd_id, pura_bcv=False)
                    ]
            except Exception as e_motor:
                logger.warning(
                    "Error calculando teóricos por línea en get_ventas_detalle: %s", e_motor
                )

        # --- Pagos aplicados a la orden -- para el botón "Ver Pagos" del
        # modal. Vinculaciones (fuente teórica) primero; si la orden no
        # tiene ninguna (caso real hoy: tabla vacía en producción), se
        # reusa ``get_live_pagos_conciliados`` -- la MISMA función que ya
        # usa ``/api/cobranza/pagos`` -- filtrando por esta orden, en vez
        # de reinventar una consulta a account.payment con conversión
        # propia (esa función ya devuelve el monto conciliado en USD
        # equivalente, via los mismos campos que usa Cobranza). Antes de
        # esto, el modal SIEMPRE decía "sin pagos vinculados" para
        # cualquier orden sin Vinculaciones, aunque sí tuviera pagos reales
        # en Odoo -- mismo bug de fondo que el estatus de pago (Fase 9). ---
        pagos: list[dict[str, Any]] = []
        for v in repo.vinculaciones_de_orden(so_id):
            moneda_abono_str = (
                v.moneda_abono.value if hasattr(v.moneda_abono, "value") else str(v.moneda_abono)
            )
            pagos.append(
                {
                    "fuente": "vinculacion",
                    "vinc_id": v.vinc_id,
                    "pago_id": v.pago_id,
                    "fecha": v.hora_pago_confirmada.isoformat() if v.hora_pago_confirmada else None,
                    "monto_original": round(float(v.monto_aplicado), 2),
                    "moneda_original": moneda_abono_str,
                    "monto_aplicado": round(float(v.monto_aplicado), 2),
                    "moneda_abono": moneda_abono_str,
                    "tipo_tasa_abono": v.tipo_tasa_abono.value
                    if hasattr(v.tipo_tasa_abono, "value")
                    else str(v.tipo_tasa_abono),
                    "tasa_bcv_aplicada": round(float(v.tasa_bcv_aplicada), 4),
                    "tasa_binance_aplicada": round(float(v.tasa_binance_aplicada), 4),
                    "equiv_usd_bcv": round(float(v.equiv_usd_bcv), 2)
                    if v.equiv_usd_bcv is not None
                    else None,
                    "equiv_usd_binance": round(float(v.equiv_usd_binance), 2)
                    if v.equiv_usd_binance is not None
                    else None,
                    "confirmado_por": v.confirmado_por,
                    "estado": v.estado.value if hasattr(v.estado, "value") else str(v.estado),
                }
            )

        if not pagos and execute:
            try:
                tasas_rows_pagos = _all_serie_tasas_rows(repo)
                for pago in get_live_pagos_conciliados(execute):
                    facturas_orden = [f for f in pago["facturas"] if f.get("so_id") == so_id]
                    if not facturas_orden:
                        continue
                    otras_ordenes = sorted(
                        {
                            f["so_id"]
                            for f in pago["facturas"]
                            if f.get("so_id") and f["so_id"] != so_id
                        }
                    )
                    estado = "conciliado (Odoo)"
                    if otras_ordenes:
                        estado += f" -- también cubre: {', '.join(otras_ordenes)}"

                    # Odoo no distingue ruta BCV/Binance por pago -- para
                    # poder comparar contra los mismos dos equivalentes que
                    # ya muestra la rama "vinculacion", se recalculan aquí
                    # con la MISMA función pura del motor
                    # (``calcular_equivalentes``), usando la tasa del día del
                    # pago (``get_rate_for_datetime``, ya usado en el resto
                    # de la app para este propósito) sobre el monto ORIGINAL
                    # del pago (``monto_original``, en su moneda real) -- no
                    # sobre ``monto_conciliado_usd`` (que ya es la conversión
                    # propia de Odoo y serviría de referencia, no de insumo).
                    moneda_str = str(pago.get("moneda") or "USD").upper().strip()
                    moneda_enum = Moneda.USD if "USD" in moneda_str else Moneda.VES
                    fecha_str = str(pago.get("fecha_pago") or "")[:10]
                    try:
                        fecha_dt = (
                            datetime.strptime(fecha_str, "%Y-%m-%d")
                            if fecha_str
                            else datetime.now()
                        )
                    except ValueError:
                        fecha_dt = datetime.now()
                    tasa_bcv_dia, tasa_binance_dia = get_rate_for_datetime(
                        fecha_dt, tasas_rows_pagos
                    )
                    try:
                        eq = calcular_equivalentes(
                            parse_decimal_safe(str(pago.get("monto_original") or "0")),
                            moneda_enum,
                            tasa_bcv_dia,
                            tasa_binance_dia,
                        )
                        equiv_usd_bcv = round(float(eq.equiv_usd_bcv), 2)
                        equiv_usd_binance = round(float(eq.equiv_usd_binance), 2)
                    except (ValueError, ArithmeticError):
                        equiv_usd_bcv = None
                        equiv_usd_binance = None

                    pagos.append(
                        {
                            "fuente": "odoo",
                            "pago_id": pago["pago_id"],
                            "fecha": pago["fecha_pago"],
                            "monto_original": round(float(pago.get("monto_original") or 0.0), 2),
                            "moneda_original": moneda_str,
                            # monto_conciliado_usd ya viene en USD (mismo
                            # cálculo que Cobranza) -- monto TOTAL del pago,
                            # no prorrateado si cubre varias órdenes (ver
                            # "estado" arriba para transparencia). Es la
                            # referencia oficial de Odoo, distinta de los
                            # equivalentes BCV/Binance de abajo (calculados
                            # con nuestras propias tasas del día).
                            "monto_aplicado": round(pago["monto_conciliado_usd"], 2),
                            "moneda_abono": "USD (equiv.)",
                            "tipo_tasa_abono": pago.get("metodo_pago") or "",
                            "tasa_bcv_aplicada": round(float(tasa_bcv_dia), 4),
                            "tasa_binance_aplicada": round(float(tasa_binance_dia), 4),
                            "equiv_usd_bcv": equiv_usd_bcv,
                            "equiv_usd_binance": equiv_usd_binance,
                            "confirmado_por": "",
                            "estado": estado,
                        }
                    )
            except Exception as e_pay:
                logger.warning(
                    "Error leyendo get_live_pagos_conciliados en get_ventas_detalle: %s", e_pay
                )

        pagos.sort(key=lambda p: p["fecha"] or "")

        monedas_originales_pagos = {
            p.get("moneda_original") for p in pagos if p.get("moneda_original")
        }
        pagos_totales = {
            "monto_original": round(sum(p.get("monto_original") or 0.0 for p in pagos), 2),
            "monedas_originales_mixtas": len(monedas_originales_pagos) > 1,
            "monto_aplicado": round(sum(p.get("monto_aplicado") or 0.0 for p in pagos), 2),
            "equiv_usd_bcv": round(sum(p.get("equiv_usd_bcv") or 0.0 for p in pagos), 2),
            "equiv_usd_binance": round(sum(p.get("equiv_usd_binance") or 0.0 for p in pagos), 2),
        }

        return {
            "so_id": so_id,
            "cliente_nombre": cliente_nombre,
            "lista_nacimiento_label": _lista_label(orden.lista_precios),
            "real_orden": {
                "lineas": lineas_real_orden,
                "subtotal": round(sum(line["subtotal"] for line in lineas_real_orden), 2),
                "descuento_total": round(
                    sum(line["descuento_monto"] for line in lineas_real_orden), 2
                ),
                "litros_total": round(
                    sum(line["litros_total"] for line in lineas_real_orden), 3
                ),
            },
            "real_factura": {
                "lineas": lineas_real_factura,
                "subtotal": round(sum(line["subtotal"] for line in lineas_real_factura), 2),
                "descuento_total": round(
                    sum(line["descuento_monto"] for line in lineas_real_factura), 2
                ),
                "litros_total": round(
                    sum(line["litros_total"] for line in lineas_real_factura), 3
                ),
            },
            "teorico_ves": {
                "lista_label": _lista_label(lista_ves_id),
                "lineas": lineas_teorico_ves,
                "subtotal": round(sum(line["subtotal"] for line in lineas_teorico_ves), 2),
                "conceptos": conceptos_teorico_ves,
                "descuento_total": round(
                    sum(c["monto"] for c in conceptos_teorico_ves), 2
                ),
                "litros_total": round(
                    sum(line["litros_total"] for line in lineas_teorico_ves), 3
                ),
            },
            "teorico_usd": {
                "lista_label": _lista_label(lista_usd_id),
                "lineas": lineas_teorico_usd,
                "subtotal": round(sum(line["subtotal"] for line in lineas_teorico_usd), 2),
                "conceptos": conceptos_teorico_usd,
                "descuento_total": round(
                    sum(c["monto"] for c in conceptos_teorico_usd), 2
                ),
                "litros_total": round(
                    sum(line["litros_total"] for line in lineas_teorico_usd), 3
                ),
            },
            "pagos": pagos,
            "pagos_totales": pagos_totales,
        }
    except HTTPException:
        raise
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
        repo.append_anomalia_aceptada(row)
        return {
            "status": "success",
            "message": "Anomalía aceptada y movida al historial de revisiones.",
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


class AprobarDescuentoSistemaRequest(BaseModel):
    so_id: str
    monto: float
    motivo: str = "Descuento aprobado en Bandeja de Facturación"
    aprobado_por: str = "Dirección / Facturación"
    activo: bool = True


def _detectar_sobre_descuentos_batch(repo) -> list[dict]:
    """Agosto 2026 -- versión batch (TODAS las órdenes) de

    ``_detectar_sobre_descuento_vigente``, para el daemon diario (ver
    ``run_sync_in_background``): arma filas de auditoría persistidas
    (mismo shape que ``_get_reporte_saldos_sync``) SOLO para sobre-
    descuentos nuevos, sin llamar a Odoo.

    Por qué existe aparte del chequeo reactivo de
    ``post_aprobar_descuento_sistema``: Contado y Recompra se calculan
    PROVISIONALMENTE mientras su ventana de pago sigue vigente (ver
    ``contado_incluido``/``recompras_activas`` en ``engine/discounts.py``)
    -- eso infla ``Bandeja.total_descuentos`` de forma optimista, lo que
    puede TAPAR un sobre-descuento real: si Odoo ya tiene más descuento
    aplicado del que correspondería, pero el motor todavía cuenta
    provisionalmente a Contado/Recompra como si fueran a confirmarse,
    ``motor_total_descuentos`` puede igualar o superar a Odoo sin que se
    detecte nada. Recién cuando la ventana vence (Contado pasa a
    "denegado", Recompra dejaría de calificar) el total del motor baja a
    su valor CONFIRMADO y el sobre-descuento queda expuesto -- pero para
    entonces nadie está mirando esa orden en particular. El recálculo
    diario (Tarea 1, ``recalculate_all_orders``) ya actualiza
    ``Bandeja.total_descuentos`` a diario reflejando ese vencimiento; esta
    función corre justo después, sobre TODAS las órdenes, para que un
    sobre-descuento recién expuesto por un vencimiento de ventana quede
    visible en Auditoría de Descuentos sin depender de que alguien abra
    Reporte de Saldos.
    """
    from cxc.engine.discount_audit import auditar_descuento_factura, auditar_descuento_orden

    so_ids = [o.so_id for o in repo.all_ordenes()]
    if not so_ids:
        return []
    bandeja_map = {b.so_id: b for b in repo.all_bandeja()}
    espejo_fact = _facturacion_por_so_desde_espejo(repo, so_ids)
    desc_orden_map, desc_factura_map = _descuentos_lineas_desde_espejo(
        repo,
        so_ids,
        espejo_fact["invoice_ids_all"],
        espejo_fact["inv_id_to_so"],
        espejo_fact["inv_usd_ratio_map"],
    )

    try:
        existing_audit_rows = repo.all_auditoria() if hasattr(repo, "all_auditoria") else []
    except Exception:
        existing_audit_rows = []
    _today_str = date.today().isoformat()
    existing_keys: set[tuple[str, str]] = {
        (r.get("so_id", ""), r.get("tipo_auditoria", ""))
        for r in existing_audit_rows
        if str(r.get("timestamp_audit", ""))[:10] == _today_str
    }

    _ahora_iso = datetime.now().isoformat()
    filas: list[dict] = []
    for so_id in so_ids:
        bandeja = bandeja_map.get(so_id)
        motor_total_descuentos = (
            Decimal(str(bandeja.total_descuentos)) if bandeja else Decimal("0")
        )
        audit_orden = auditar_descuento_orden(
            so_id=so_id,
            motor_total_descuentos=motor_total_descuentos,
            odoo_descuento_aplicado=Decimal(str(desc_orden_map.get(so_id, 0.0))),
        )
        audit_factura = auditar_descuento_factura(
            so_id=so_id,
            motor_total_descuentos=motor_total_descuentos,
            odoo_descuento_factura=Decimal(str(desc_factura_map.get(so_id, 0.0))),
        )
        for ar in (audit_orden, audit_factura):
            if not (ar.enviar_a_bandeja and ar.diferencia_usd < 0):
                continue
            key = (so_id, ar.tipo.value)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            filas.append(
                {
                    "audit_id": f"{so_id}_{ar.tipo.value}_{_today_str}",
                    "so_id": so_id,
                    "tipo_auditoria": ar.tipo.value,
                    "motor_calcula_usd": round(float(ar.motor_calcula_usd), 4),
                    "odoo_registrado_usd": round(float(ar.odoo_registrado_usd), 4),
                    "diferencia_usd": round(float(ar.diferencia_usd), 4),
                    "detalle_odoo": ar.detalle_odoo,
                    "detalle_motor": ar.detalle_motor,
                    "estado": "pendiente",
                    "revisado_por": "",
                    "timestamp_audit": _ahora_iso,
                }
            )
    return filas


def _detectar_sobre_descuento_vigente(repo, so_id: str):
    """Recalcula EN VIVO, leyendo solo del espejo (sin llamar a Odoo), si

    esta orden ya tiene MÁS descuento aplicado en Odoo (orden o factura)
    que lo que el motor calcula que le corresponde -- "sobre-descuento".

    Guardia de Tarea 2 (agosto 2026, aprobada por el usuario): un
    sobre-descuento NO bloquea nada más del sistema (Facturación,
    Cobranza, etc. siguen su curso normal), pero SÍ debe bloquear que se
    apruebe otro descuento de sistema adicional sobre esa misma orden
    mientras nadie haya revisado por qué se aplicó ese exceso -- hasta
    que se revise en Auditoría de Descuentos (``estado`` distinto de
    "pendiente" vía ``PATCH /api/auditoria-descuentos/{audit_id}``).

    Se recalcula en vivo (no se lee ``repo.all_auditoria()``) porque esa
    tabla solo se repuebla cuando alguien carga Reporte de Saldos o corre
    el daemon diario (``_detectar_sobre_descuentos_batch``) -- una orden
    recién sobre-descontada en Odoo podría no tener fila ahí todavía.
    Devuelve el primer ``ResultadoAuditoria`` en estado de sobre-descuento
    (orden o factura), o ``None`` si no aplica.
    """
    from cxc.engine.discount_audit import auditar_descuento_factura, auditar_descuento_orden

    orden = repo.get_orden(so_id)
    if orden is None:
        return None
    bandeja = repo.get_bandeja(so_id)
    motor_total_descuentos = Decimal(str(bandeja.total_descuentos)) if bandeja else Decimal("0")

    espejo_fact = _facturacion_por_so_desde_espejo(repo, {so_id})
    desc_orden_map, desc_factura_map = _descuentos_lineas_desde_espejo(
        repo,
        {so_id},
        espejo_fact["invoice_ids_all"],
        espejo_fact["inv_id_to_so"],
        espejo_fact["inv_usd_ratio_map"],
    )

    audit_orden = auditar_descuento_orden(
        so_id=so_id,
        motor_total_descuentos=motor_total_descuentos,
        odoo_descuento_aplicado=Decimal(str(desc_orden_map.get(so_id, 0.0))),
    )
    if audit_orden.enviar_a_bandeja and audit_orden.diferencia_usd < 0:
        return audit_orden

    audit_factura = auditar_descuento_factura(
        so_id=so_id,
        motor_total_descuentos=motor_total_descuentos,
        odoo_descuento_factura=Decimal(str(desc_factura_map.get(so_id, 0.0))),
    )
    if audit_factura.enviar_a_bandeja and audit_factura.diferencia_usd < 0:
        return audit_factura

    return None


@app.post("/api/facturacion/aprobar-descuento-sistema")
async def post_aprobar_descuento_sistema(req: AprobarDescuentoSistemaRequest):
    """Aprueba (o revoca, con ``activo=false``) un descuento manual interno

    para una orden. NUNCA se escribe a Odoo -- solo ajusta los saldos
    internos de CxC que expone ``/api/ventas`` (``descuento_aplicado_sistema``
    y ``saldo_pendiente_cxc``).

    Tarea 2 (agosto 2026): si la orden ya está en estado de sobre-descuento
    (Odoo tiene aplicado más descuento del que el motor calcula), se
    bloquea aprobar un descuento ADICIONAL -- hay que revisar primero en
    Auditoría de Descuentos por qué se aplicó ese exceso. La revocación
    (``activo=false``) siempre se permite: no agrega descuento, solo
    retira uno ya aprobado internamente.
    """
    try:
        repo = get_repo()
        if req.activo:
            sobre_descuento = _detectar_sobre_descuento_vigente(repo, req.so_id)
            if sobre_descuento is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"La orden {req.so_id} ya tiene más descuento aplicado en "
                        f"Odoo (${float(sobre_descuento.odoo_registrado_usd):.2f}) que lo "
                        f"que el motor calcula "
                        f"(${float(sobre_descuento.motor_calcula_usd):.2f}). "
                        "No se puede aprobar otro descuento de sistema hasta revisar "
                        "el caso en Auditoría de Descuentos."
                    ),
                )
        row = {
            "so_id": req.so_id,
            "monto": str(req.monto),
            "motivo": req.motivo,
            "aprobado_por": req.aprobado_por,
            "timestamp_aprobacion": datetime.now().isoformat(),
            "activo": "true" if req.activo else "false",
        }
        repo.upsert_descuento_sistema_aprobado(row)
        return {
            "status": "success",
            "message": "Descuento de sistema aprobado correctamente."
            if req.activo
            else "Descuento de sistema revocado.",
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


class ReglaDiasCreditoVolumenRequest(BaseModel):
    regla_id: str
    litros_minimo: float = 0.0
    litros_maximo: float | None = None
    dias_credito_max: int
    descripcion: str = ""
    activo: bool = True


@app.get("/api/config/dias-credito-volumen")
async def get_config_dias_credito_volumen():
    """Rangos de litros -> máximo de días de crédito permitido (config).

    Uso EXCLUSIVO: alerta en Ventas comparando el plazo de pago real que
    Odoo otorgó contra este máximo -- NO alimenta la fórmula de recompra
    (esa usa el plazo REAL de la orden anterior, no este tabulado).
    """
    try:
        repo = get_repo()
        rows = repo.all_reglas_dias_credito_volumen()
        return [
            {
                "regla_id": r.get("regla_id", ""),
                "litros_minimo": float(r.get("litros_minimo") or 0),
                "litros_maximo": float(r["litros_maximo"]) if r.get("litros_maximo") else None,
                "dias_credito_max": int(r.get("dias_credito_max") or 0),
                "descripcion": r.get("descripcion", ""),
                "activo": str(r.get("activo", "true")).strip().lower() not in ("false", "0", "no"),
            }
            for r in rows
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/config/dias-credito-volumen")
async def post_config_dias_credito_volumen(req: ReglaDiasCreditoVolumenRequest):
    try:
        repo = get_repo()
        row = {
            "regla_id": req.regla_id,
            "litros_minimo": str(req.litros_minimo),
            "litros_maximo": str(req.litros_maximo) if req.litros_maximo is not None else "",
            "dias_credito_max": str(req.dias_credito_max),
            "descripcion": req.descripcion,
            "activo": "true" if req.activo else "false",
        }
        repo.upsert_regla_dias_credito_volumen(row)
        return {"status": "success", "message": "Regla de días de crédito registrada."}
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

        now = datetime.now()
        recibo_num = f"REC-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}"

        pagos_actualizados = repo.marcar_pagos_recibido(req.pago_ids, recibo_num, now, recibido_por)

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
    """Wrapper async -- ver ``_get_reporte_diario_sync`` (mismo hallazgo

    que ``get_ventas``, ver su docstring)."""
    return await asyncio.to_thread(
        _get_reporte_diario_sync, vendedor, fecha_desde, fecha_hasta
    )


def _get_reporte_diario_sync(
    vendedor: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
):
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        lineas = _all_lineas_rows(repo)
        pagos = _all_pagos_rows(repo)
        tasas_rows = _all_serie_tasas_rows(repo)

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
        # Fase 4/6: entregas desde el espejo -- no depende de `execute`, más
        # resiliente que antes (funciona aunque Odoo esté caído).
        so_names_all = [o.so_id for o in ordenes]
        entrega_valida_set: set[str] = (
            _entregas_desde_espejo(repo, so_names_all)[0] if so_names_all else set()
        )
        # Fase 6 (plan de consolidación de fuentes, agosto 2026): litros
        # ahora vienen del espejo Catálogo, no de una consulta en vivo a
        # product.product SIN filtro de dominio (traía TODOS los
        # productos). También corrige el mismo bug real ya encontrado en
        # el sync (product_volume, no volume genérico -- ver
        # map_producto_espejo).
        for p in repo.all_catalogo():
            if p.producto_id.isdigit():
                vol = p.volumen
                if vol == Decimal("0"):
                    vol = p.peso if p.peso != Decimal("0") else Decimal("1.0")
                prod_litros_map[int(p.producto_id)] = vol

        execute = None
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            if execute:
                journals = execute("account.journal", "search_read", [], {"fields": ["id", "name"]})
                journal_name_map = {int(j["id"]): str(j.get("name") or "") for j in journals}

                # Estado EN VIVO de cada orden -- el espejo local (estado_orden)
                # puede quedar desactualizado si una orden se cancela en Odoo
                # y el sync incremental no la vuelve a traer (ventana delta
                # vencida, downtime, etc.). Verificado en vivo: S00162
                # ($161,679.06) aparecía como "sale" en el espejo pero está
                # "cancel" en Odoo, inflando "Ventas del Año" en ese monto.
                if so_names_all:
                    so_recs = execute(
                        "sale.order",
                        "search_read",
                        [[["name", "in", so_names_all]]],
                        {"fields": ["name", "state"]},
                    )
                    for s in so_recs:
                        sname = str(s.get("name", "")).strip()
                        if sname:
                            so_states_map[sname] = str(s.get("state", "")).strip().lower()
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
                    "por_moneda": {},
                    "por_metodo": {},
                    "ves_monto": Decimal("0"),
                    "ves_eq_usd": Decimal("0"),
                }
            dia = cobranza_por_dia[fecha_key]
            dia["total_eq_bcv"] += eq_usd
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
                    (journal_name_map.get(int(metodo_raw)) if metodo_raw.isdigit() else None)
                    or metodo_raw
                    or "Efectivo"
                )
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
            ves_monto = sum((v["ves_monto"] for v in en_rango), Decimal("0"))
            ves_eq_usd = sum((v["ves_eq_usd"] for v in en_rango), Decimal("0"))
            por_metodo: dict[str, Decimal] = {}
            for v in en_rango:
                for metodo, monto_m in v["por_metodo"].items():
                    por_metodo[metodo] = por_metodo.get(metodo, Decimal("0")) + monto_m
            return {
                "total_eq_bcv": float(total_bcv),
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
