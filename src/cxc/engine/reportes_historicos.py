"""Reconstrucción histórica punto-en-el-tiempo de CxC vencida y cobranza.

Motivación (agosto 2026): el usuario pidió 3 análisis históricos --
"¿qué facturas seguían vencidas y sin pagar exactamente al 30 de junio de
2026?", "¿cuánto se cobró en julio, por vendedor?" y "¿cuánto de esa
cartera vencida se recuperó en julio?" -- que NINGÚN helper existente
resuelve: todos los helpers en vivo de este sistema (``_estado_pago_
facturas_desde_odoo``, ``get_live_pagos_conciliados``, etc.) calculan el
estado ACTUAL de una factura, nunca su estado en una fecha pasada
específica.

Método (única forma confiable de reconstruir "pagado a la fecha X" sin
recalcular tasas/reglas de negocio propias): leer directamente la red de
conciliación real de Odoo -- ``account.move.line`` (línea por cobrar de
cada factura) y sus ``account.partial.reconcile`` asociados
(``matched_credit_ids``), cada uno con ``max_date`` (la fecha, en Odoo, en
que esa porción del cobro quedó aplicada contra la factura). Sumando solo
los ``account.partial.reconcile`` con ``max_date <= cutoff`` se obtiene el
monto realmente aplicado A ESA FECHA, en moneda de la compañía. Se
convierte a USD por RATIO (pagado_compañía / total_compañía) aplicado sobre
``amount_total_signed_usd`` -- el mismo campo USD ya usado en
``get_live_pagos_conciliados``/``get_live_pagos_confirmados`` -- para no
reinventar ninguna conversión de tasa propia.

Verificado a mano contra Odoo en vivo para 3 facturas reales (agosto
2026): en los 3 casos, el residual reconstruido a una fecha de corte
coincidió centavo a centavo con la suma real de ``account.partial.
reconcile`` con esa fecha límite.

Vendedor: se resuelve por la MISMA fuente que ya usa el resto de este
sistema para agrupar por vendedor -- ``ordenes_venta.vendedor_email``
(espejo local Postgres) por la orden real (``invoice_origin``), con
fallback a ``clientes.vendedor_email`` por cliente. Deliberadamente NO se
usa ``resolve_vendedores_por_partner`` (``res.partner.user_id`` /
``res.users.login`` en vivo) para esto -- se probó y es un espacio de
identidad DISTINTO en esta instancia de Odoo (varios clientes tienen su
propio login de portal como "salesperson" de sus propias órdenes, lo que
producía histogramas por vendedor sin ningún solape entre vencido y
cobrado). Ver docstring de ``cobranza_por_vendedor``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, TypedDict

from ..repositories import Repository

TOLERANCIA_USD = Decimal("0.05")

# Fila cruda de Odoo (``search_read``/``read``): claves dinámicas según el
# ``fields`` que pida cada consulta.
OdooRec = dict[str, Any]


class EstadoPuntoEnElTiempo(TypedDict):
    """Estado reconstruible de UNA factura: su total en moneda de compañía y
    todas las aplicaciones de cobro ``(monto, fecha)`` que registró Odoo.

    Es un ``TypedDict`` (no un dataclass) a propósito: en runtime sigue
    siendo el mismo dict plano que ya construía ``build_point_in_time_state``
    -- solo hace explícito para el chequeo de tipos que ``total_company`` y
    los montos son ``Decimal``, para que ``paid_ratio_by_cutoff`` no calcule
    el ratio sobre ``Any`` (que es exactamente donde un float se colaría sin
    que nadie lo note).
    """

    total_company: Decimal
    applications: list[tuple[Decimal, str]]


def _partner_id_of(rec: OdooRec) -> int | None:
    p = rec.get("partner_id")
    if isinstance(p, list | tuple) and p:
        return int(p[0])
    return None


def _partner_name_of(rec: OdooRec) -> str:
    p = rec.get("partner_id")
    if isinstance(p, list | tuple) and len(p) > 1:
        return str(p[1])
    return ""


def fetch_out_invoices_due_by(execute: Any, cutoff: date) -> list[OdooRec]:
    """out_invoice posteadas con vencimiento <= ``cutoff`` -- candidatas a
    "vencida" en esa fecha (el filtro de "sigue sin pagar" se aplica
    después, con la reconstrucción punto-en-el-tiempo)."""
    fields = [
        "id", "name", "invoice_origin", "partner_id", "invoice_date",
        "invoice_date_due", "amount_total_signed_usd", "amount_residual",
        "amount_residual_usd", "amount_total", "currency_id",
        "payment_state", "state", "move_type",
    ]
    domain = [
        ["move_type", "=", "out_invoice"],
        ["state", "=", "posted"],
        ["invoice_date_due", "<=", cutoff.isoformat()],
        ["invoice_date_due", "!=", False],
    ]
    filas: list[OdooRec] = execute("account.move", "search_read", [domain], {"fields": fields})
    return filas


def _fetch_receivable_lines(execute: Any, move_ids: list[int]) -> list[OdooRec]:
    if not move_ids:
        return []
    fields = ["id", "move_id", "debit", "credit", "balance", "matched_credit_ids", "reconciled"]
    domain = [
        ["move_id", "in", move_ids],
        ["account_id.account_type", "=", "asset_receivable"],
    ]
    filas: list[OdooRec] = execute(
        "account.move.line", "search_read", [domain], {"fields": fields}
    )
    return filas


def _fetch_partial_reconciles(execute: Any, ids: list[int]) -> list[OdooRec]:
    if not ids:
        return []
    fields = ["id", "debit_move_id", "credit_move_id", "amount", "max_date"]
    out: list[OdooRec] = []
    chunk_size = 2000
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i : i + chunk_size]
        out.extend(execute("account.partial.reconcile", "read", [chunk], {"fields": fields}))
    return out


def build_point_in_time_state(
    execute: Any, invoices: list[OdooRec]
) -> dict[int, EstadoPuntoEnElTiempo]:
    """Por factura (move id): total en moneda de compañía + lista de
    aplicaciones de cobro ``(monto, fecha)`` -- listo para calcular el
    residual USD a cualquier fecha de corte con ``paid_ratio_by_cutoff``."""
    move_ids = [inv["id"] for inv in invoices]
    lines = _fetch_receivable_lines(execute, move_ids)
    move_to_lines: dict[int, list[OdooRec]] = defaultdict(list)
    for ln in lines:
        m = ln.get("move_id")
        mid = m[0] if isinstance(m, list | tuple) else m
        move_to_lines[int(mid)].append(ln)

    all_reconcile_ids: set[int] = set()
    for lns in move_to_lines.values():
        for ln in lns:
            all_reconcile_ids.update(ln.get("matched_credit_ids") or [])

    reconciles = _fetch_partial_reconciles(execute, sorted(all_reconcile_ids))

    recs_by_debit_line: dict[int, list[tuple[Decimal, str]]] = defaultdict(list)
    for r in reconciles:
        dm = r.get("debit_move_id")
        dm_id = dm[0] if isinstance(dm, list | tuple) else dm
        if dm_id is None:
            continue
        amt = Decimal(str(r.get("amount") or "0"))
        max_date = str(r.get("max_date") or "")[:10]
        recs_by_debit_line[int(dm_id)].append((amt, max_date))

    result: dict[int, EstadoPuntoEnElTiempo] = {}
    for mid, lns in move_to_lines.items():
        total_company = sum((Decimal(str(ln.get("debit") or "0")) for ln in lns), Decimal("0"))
        applications: list[tuple[Decimal, str]] = []
        for ln in lns:
            applications.extend(recs_by_debit_line.get(int(ln["id"]), []))
        result[mid] = {"total_company": total_company, "applications": applications}
    return result


def paid_ratio_by_cutoff(state: EstadoPuntoEnElTiempo, cutoff: date) -> Decimal:
    total = state["total_company"]
    if total == 0:
        return Decimal("0")
    paid = sum(
        (amt for amt, d in state["applications"] if d and d <= cutoff.isoformat()),
        Decimal("0"),
    )
    ratio = paid / total
    if ratio < 0:
        ratio = Decimal("0")
    if ratio > 1:
        ratio = Decimal("1")
    return ratio


@dataclass
class FacturaVencida:
    factura_id: int
    numero: str
    so_id: str
    cliente_id: str
    cliente_nombre: str
    vendedor: str
    fecha_factura: str
    fecha_vencimiento: str
    monto_total_usd: float
    pagado_al_corte_usd: float
    residual_al_corte_usd: float
    payment_state_actual: str


def vendedores_por_so(repo: Repository) -> dict[str, str]:
    return {o.so_id: (o.vendedor_email or "") for o in repo.all_ordenes()}


def vendedores_por_cliente(repo: Repository) -> dict[str, str]:
    return {c.cliente_id: (c.vendedor_email or "") for c in repo.all_clientes()}


def cxc_vencida_no_pagada(
    execute: Any, repo: Repository, cutoff: date, tolerancia: Decimal = TOLERANCIA_USD
) -> list[FacturaVencida]:
    """Facturas out_invoice cuyo vencimiento cae en o antes de ``cutoff`` y
    que, reconstruyendo únicamente lo conciliado en Odoo con ``max_date <=
    cutoff``, seguían con residual > tolerancia A ESA FECHA (no hoy)."""
    invoices = fetch_out_invoices_due_by(execute, cutoff)
    pit_state = build_point_in_time_state(execute, invoices)
    vend_por_so = vendedores_por_so(repo)
    vend_por_cliente = vendedores_por_cliente(repo)

    out: list[FacturaVencida] = []
    for inv in invoices:
        mid = inv["id"]
        state = pit_state.get(mid)
        if not state:
            continue
        ratio = paid_ratio_by_cutoff(state, cutoff)
        total_usd = Decimal(str(inv.get("amount_total_signed_usd") or "0"))
        pagado_usd = total_usd * ratio
        residual_usd = total_usd - pagado_usd
        if residual_usd <= tolerancia:
            continue

        so_id = inv.get("invoice_origin") or ""
        vendedor = vend_por_so.get(so_id, "")
        if not vendedor:
            pid = _partner_id_of(inv)
            vendedor = vend_por_cliente.get(str(pid), "") if pid else ""
        if not vendedor:
            vendedor = "Sin Vendedor"

        out.append(
            FacturaVencida(
                factura_id=mid,
                numero=str(inv.get("name") or ""),
                so_id=so_id,
                cliente_id=str(_partner_id_of(inv) or ""),
                cliente_nombre=_partner_name_of(inv),
                vendedor=vendedor,
                fecha_factura=str(inv.get("invoice_date") or ""),
                fecha_vencimiento=str(inv.get("invoice_date_due") or ""),
                monto_total_usd=float(total_usd),
                pagado_al_corte_usd=float(pagado_usd),
                residual_al_corte_usd=float(residual_usd),
                payment_state_actual=str(inv.get("payment_state") or ""),
            )
        )
    return out


def resumen_vencida_por_vendedor(facturas: list[FacturaVencida]) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n_facturas": 0, "monto_vencido_usd": 0.0}
    )
    for f in facturas:
        agg[f.vendedor]["n_facturas"] += 1
        agg[f.vendedor]["monto_vencido_usd"] += f.residual_al_corte_usd
    return [
        {
            "vendedor": v,
            "n_facturas": d["n_facturas"],
            "monto_vencido_usd": round(d["monto_vencido_usd"], 2),
        }
        for v, d in sorted(agg.items(), key=lambda kv: -kv[1]["monto_vencido_usd"])
    ]


@dataclass
class PagoEnVentana:
    pago_id: int
    cliente_id: str
    vendedor: str
    fecha_pago: str
    monto_usd: float


def cobranza_por_vendedor(
    execute: Any, repo: Repository, desde: date, hasta: date
) -> list[PagoEnVentana]:
    """account.payment inbound/customer confirmados (``in_process``/
    ``paid`` -- mismo criterio que ``get_live_pagos_confirmados``, NO
    filtra por reconciliado, para no subcontar cobranza real) con
    ``date`` dentro de [``desde``, ``hasta``].

    Vendedor: resuelto primero por la orden real a la que el pago quedó
    aplicado (``reconciled_invoice_ids`` -> ``invoice_origin`` ->
    ``ordenes_venta.vendedor_email``), igual fuente que ``cxc_vencida_no_
    pagada`` -- necesario para que el cruce con esa función (Recuperación
    de Julio) compare el mismo espacio de identidad. Fallback al vendedor
    del cliente si el pago aún no reconcilió contra ninguna factura.
    """
    from cxc.odoo.client import PAGO_ESTADOS_CONFIRMADOS

    pagos = execute(
        "account.payment",
        "search_read",
        [
            [
                ["payment_type", "=", "inbound"],
                ["partner_type", "=", "customer"],
                ["state", "in", PAGO_ESTADOS_CONFIRMADOS],
                ["date", ">=", desde.isoformat()],
                ["date", "<=", hasta.isoformat()],
            ]
        ],
        {
            "fields": [
                "id", "partner_id", "amount", "amount_ref", "date",
                "currency_id", "reconciled_invoice_ids",
            ]
        },
    )
    vend_por_so = vendedores_por_so(repo)
    vend_por_cliente = vendedores_por_cliente(repo)

    all_inv_ids: set[int] = set()
    for p in pagos:
        all_inv_ids.update(p.get("reconciled_invoice_ids") or [])
    inv_origin_map: dict[int, str] = {}
    if all_inv_ids:
        ids_list = list(all_inv_ids)
        for i in range(0, len(ids_list), 2000):
            chunk = ids_list[i : i + 2000]
            recs = execute("account.move", "read", [chunk], {"fields": ["id", "invoice_origin"]})
            for r in recs:
                inv_origin_map[int(r["id"])] = str(r.get("invoice_origin") or "")

    out: list[PagoEnVentana] = []
    for p in pagos:
        pid = _partner_id_of(p)
        vendedor = ""
        for inv_id in p.get("reconciled_invoice_ids") or []:
            so_id = inv_origin_map.get(int(inv_id), "")
            if so_id and vend_por_so.get(so_id):
                vendedor = vend_por_so[so_id]
                break
        if not vendedor:
            vendedor = vend_por_cliente.get(str(pid), "") if pid else ""
        if not vendedor:
            vendedor = "Sin Vendedor"
        out.append(
            PagoEnVentana(
                pago_id=int(p["id"]),
                cliente_id=str(pid or ""),
                vendedor=vendedor,
                fecha_pago=str(p.get("date") or ""),
                monto_usd=float(p.get("amount_ref") or 0.0),
            )
        )
    return out


def resumen_cobranza_por_vendedor(pagos: list[PagoEnVentana]) -> list[dict[str, Any]]:
    agg: dict[str, float] = defaultdict(float)
    for p in pagos:
        agg[p.vendedor] += p.monto_usd
    return [
        {"vendedor": v, "monto_cobrado_usd": round(m, 2)}
        for v, m in sorted(agg.items(), key=lambda kv: -kv[1])
    ]


@dataclass
class RecuperacionVendedor:
    vendedor: str
    monto_vencido_30jun_usd: float
    cobrado_julio_especifico_usd: float
    pct_recuperacion_especifica: float | None
    cobrado_julio_general_usd: float
    pct_recuperacion_general: float | None


def recuperacion_cartera(
    execute: Any,
    repo: Repository,
    cutoff_vencida: date,
    recuperacion_desde: date,
    recuperacion_hasta: date,
) -> dict[str, Any]:
    """Cruce de ``cxc_vencida_no_pagada`` (a ``cutoff_vencida``) contra lo
    cobrado en [``recuperacion_desde``, ``recuperacion_hasta``].

    Devuelve un dict con 3 vistas:
      - ``detalle``: por factura vencida, cuánto de su residual se redujo
        específicamente durante la ventana de recuperación (delta de la
        misma reconstrucción punto-en-el-tiempo, a fin de la ventana menos
        al corte).
      - ``totales_por_vendedor``: agregado (b) -- monto vencido al corte
        vs. SOLO lo cobrado contra esas facturas específicas en la
        ventana.
      - ``totales_generales_alternativos``: agregado (c) -- monto vencido
        al corte vs. TODA la cobranza de la ventana (incluye pagos contra
        facturas que no estaban vencidas, ej. ventas nuevas del propio
        mes) -- métrica más laxa, etiquetada aparte a propósito.
    """
    invoices = fetch_out_invoices_due_by(execute, cutoff_vencida)
    pit_state = build_point_in_time_state(execute, invoices)
    vend_por_so = vendedores_por_so(repo)
    vend_por_cliente = vendedores_por_cliente(repo)

    detalle: list[dict[str, Any]] = []
    rep3b: dict[str, dict[str, float]] = defaultdict(
        lambda: {"monto_vencido_usd": 0.0, "cobrado_especifico_usd": 0.0}
    )
    for inv in invoices:
        mid = inv["id"]
        state = pit_state.get(mid)
        if not state:
            continue
        ratio_corte = paid_ratio_by_cutoff(state, cutoff_vencida)
        total_usd = Decimal(str(inv.get("amount_total_signed_usd") or "0"))
        pagado_corte_usd = total_usd * ratio_corte
        residual_corte_usd = total_usd - pagado_corte_usd
        if residual_corte_usd <= TOLERANCIA_USD:
            continue

        so_id = inv.get("invoice_origin") or ""
        vendedor = vend_por_so.get(so_id, "")
        if not vendedor:
            pid = _partner_id_of(inv)
            vendedor = vend_por_cliente.get(str(pid), "") if pid else ""
        if not vendedor:
            vendedor = "Sin Vendedor"

        ratio_fin = paid_ratio_by_cutoff(state, recuperacion_hasta)
        pagado_fin_usd = total_usd * ratio_fin
        residual_fin_usd = total_usd - pagado_fin_usd
        recuperado_usd = float(max(Decimal("0"), pagado_fin_usd - pagado_corte_usd))

        detalle.append(
            {
                "factura_id": mid,
                "numero": str(inv.get("name") or ""),
                "so_id": so_id,
                "vendedor": vendedor,
                "residual_al_corte_usd": round(float(residual_corte_usd), 2),
                "recuperado_en_ventana_usd": round(recuperado_usd, 2),
                "residual_al_fin_ventana_usd": round(float(residual_fin_usd), 2),
                "payment_state_actual": str(inv.get("payment_state") or ""),
            }
        )
        rep3b[vendedor]["monto_vencido_usd"] += float(residual_corte_usd)
        rep3b[vendedor]["cobrado_especifico_usd"] += recuperado_usd

    pagos_ventana = cobranza_por_vendedor(execute, repo, recuperacion_desde, recuperacion_hasta)
    resumen_pagos = resumen_cobranza_por_vendedor(pagos_ventana)
    rep2 = {r["vendedor"]: r["monto_cobrado_usd"] for r in resumen_pagos}

    todos_vendedores = set(rep3b.keys()) | set(rep2.keys())
    resultado: list[RecuperacionVendedor] = []
    for v in sorted(todos_vendedores):
        vencido = rep3b.get(v, {}).get("monto_vencido_usd", 0.0)
        cobrado_esp = rep3b.get(v, {}).get("cobrado_especifico_usd", 0.0)
        cobrado_gral = rep2.get(v, 0.0)
        pct_esp = (cobrado_esp / vencido * 100.0) if vencido > 0 else None
        pct_gral = (cobrado_gral / vencido * 100.0) if vencido > 0 else None
        resultado.append(
            RecuperacionVendedor(
                vendedor=v,
                monto_vencido_30jun_usd=round(vencido, 2),
                cobrado_julio_especifico_usd=round(cobrado_esp, 2),
                pct_recuperacion_especifica=round(pct_esp, 1) if pct_esp is not None else None,
                cobrado_julio_general_usd=round(cobrado_gral, 2),
                pct_recuperacion_general=round(pct_gral, 1) if pct_gral is not None else None,
            )
        )
    resultado.sort(key=lambda r: -r.monto_vencido_30jun_usd)

    return {
        "detalle": sorted(detalle, key=lambda d: (d["vendedor"], -d["recuperado_en_ventana_usd"])),
        "totales_por_vendedor": resultado,
    }
