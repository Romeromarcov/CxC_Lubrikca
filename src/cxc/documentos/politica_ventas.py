"""Motor de Política Comercial, Límites de Crédito y Auto-Envío Inteligente a Odoo.

Evalúa si un Pedido generado en CxC cumple el 100% de los parámetros comerciales y
de cartera para pasar automáticamente a Odoo (``ENVIADO_ODOO``), o si debe quedar
retenido en ``PENDIENTE_APROBACION`` con motivos explícitos de revisión.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Engine, delete, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from cxc.db.schema import documentos_operativos, politica_credito_cliente
from cxc.decimal_utils import q2
from cxc.engine.equivalents import valor_pagado_bcv_usd, valor_pagado_binance_usd
from cxc.engine.rutas_pago import RUTA_BINANCE, normalizar_ruta, ruta_de_lista
from cxc.models import EstadoVinculacion, OrdenVenta
from cxc.repositories import Repository

from .evaluador_descuentos import _resolver_cliente_en_espejo
from .modelos import PEDIDO, DocumentoComercial

logger = logging.getLogger("cxc.documentos.politica")

KEY_AUTOSEND_ACTIVO = "ventas_autosend_activo"
KEY_LIMITE_CREDITO_USD = "ventas_limite_credito_usd"
KEY_LIMITE_FACTURAS = "ventas_limite_facturas_pendientes"
KEY_RETENER_VENCIDAS = "ventas_retener_si_vencidas"
KEY_DIAS_MORA_MAX = "ventas_dias_mora_max"

DEFAULT_POLITICA_GLOBAL: dict[str, Any] = {
    "autosend_activo": True,
    "limite_credito_usd": 0.0,
    "limite_facturas_pendientes": 0,
    "retener_si_vencidas": True,
    "dias_mora_max": 0,
}


def _parse_bool(raw: Any, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    txt = str(raw).strip().lower()
    if txt in ("1", "true", "si", "sí", "yes", "on"):
        return True
    if txt in ("0", "false", "no", "off"):
        return False
    return default


def _parse_decimal_no_neg(raw: Any, campo: str, default: Decimal = Decimal("0")) -> Decimal:
    if raw in (None, ""):
        return default
    try:
        val = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Valor numérico inválido en {campo}: {raw!r}.") from e
    if not val.is_finite() or val < Decimal("0"):
        raise ValueError(f"{campo} debe ser un número mayor o igual a cero.")
    return q2(val)


def _parse_int_no_neg(raw: Any, campo: str, default: int = 0) -> int:
    if raw in (None, ""):
        return default
    try:
        val = int(Decimal(str(raw).strip().replace(",", ".")))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"Valor entero inválido en {campo}: {raw!r}.") from e
    if val < 0:
        raise ValueError(f"{campo} debe ser mayor o igual a cero.")
    return val


def cargar_politica_global(repo_cxc: Repository | None) -> dict[str, Any]:
    """Lee la política global de ventas y límites desde ``app_settings``."""
    if repo_cxc is None:
        return dict(DEFAULT_POLITICA_GLOBAL)
    return {
        "autosend_activo": _parse_bool(repo_cxc.get_config(KEY_AUTOSEND_ACTIVO), True),
        "limite_credito_usd": float(
            _parse_decimal_no_neg(
                repo_cxc.get_config(KEY_LIMITE_CREDITO_USD),
                "limite_credito_usd",
                Decimal("0"),
            )
        ),
        "limite_facturas_pendientes": _parse_int_no_neg(
            repo_cxc.get_config(KEY_LIMITE_FACTURAS),
            "limite_facturas_pendientes",
            0,
        ),
        "retener_si_vencidas": _parse_bool(repo_cxc.get_config(KEY_RETENER_VENCIDAS), True),
        "dias_mora_max": _parse_int_no_neg(
            repo_cxc.get_config(KEY_DIAS_MORA_MAX),
            "dias_mora_max",
            0,
        ),
    }


def guardar_politica_global(repo_cxc: Repository, datos: dict[str, Any]) -> dict[str, Any]:
    """Valida y persiste la política global de auto-envío y límites de crédito."""
    actual = cargar_politica_global(repo_cxc)
    autosend = _parse_bool(
        datos.get("autosend_activo"),
        bool(actual["autosend_activo"]),
    )
    lim_cred = _parse_decimal_no_neg(
        datos.get("limite_credito_usd", actual["limite_credito_usd"]),
        "limite_credito_usd",
        Decimal("0"),
    )
    lim_fact = _parse_int_no_neg(
        datos.get("limite_facturas_pendientes", actual["limite_facturas_pendientes"]),
        "limite_facturas_pendientes",
        0,
    )
    retener_venc = _parse_bool(
        datos.get("retener_si_vencidas"),
        bool(actual["retener_si_vencidas"]),
    )
    dias_mora = _parse_int_no_neg(
        datos.get("dias_mora_max", actual["dias_mora_max"]),
        "dias_mora_max",
        0,
    )

    repo_cxc.set_config(KEY_AUTOSEND_ACTIVO, "1" if autosend else "0")
    repo_cxc.set_config(KEY_LIMITE_CREDITO_USD, f"{lim_cred:.2f}")
    repo_cxc.set_config(KEY_LIMITE_FACTURAS, str(lim_fact))
    repo_cxc.set_config(KEY_RETENER_VENCIDAS, "1" if retener_venc else "0")
    repo_cxc.set_config(KEY_DIAS_MORA_MAX, str(dias_mora))
    return cargar_politica_global(repo_cxc)


def _fila_override_a_dict(r: Any) -> dict[str, Any]:
    return {
        "partner_odoo_id": int(r.partner_odoo_id),
        "cliente_nombre": str(r.cliente_nombre or ""),
        "limite_credito_usd": (
            float(r.limite_credito_usd) if r.limite_credito_usd is not None else None
        ),
        "limite_facturas_pendientes": (
            int(r.limite_facturas_pendientes) if r.limite_facturas_pendientes is not None else None
        ),
        "dias_mora_max": int(r.dias_mora_max) if r.dias_mora_max is not None else None,
        "bloquear_auto_envio": bool(r.bloquear_auto_envio),
        "notas": str(r.notas or ""),
        "actualizado_por": str(r.actualizado_por or "sistema"),
        "actualizado_en": r.actualizado_en.isoformat(timespec="seconds")
        if r.actualizado_en
        else None,
    }


def obtener_override_cliente(
    engine: Engine | None, partner_odoo_id: int | None
) -> dict[str, Any] | None:
    if engine is None or not partner_odoo_id:
        return None
    try:
        with engine.connect() as conn:
            fila = conn.execute(
                select(politica_credito_cliente).where(
                    politica_credito_cliente.c.partner_odoo_id == int(partner_odoo_id)
                )
            ).first()
        return _fila_override_a_dict(fila) if fila is not None else None
    except SQLAlchemyError:
        return None


def listar_overrides_clientes(engine: Engine | None) -> list[dict[str, Any]]:
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            filas = conn.execute(
                select(politica_credito_cliente).order_by(
                    politica_credito_cliente.c.cliente_nombre.asc(),
                    politica_credito_cliente.c.partner_odoo_id.asc(),
                )
            ).all()
        return [_fila_override_a_dict(r) for r in filas]
    except SQLAlchemyError:
        return []


def guardar_override_cliente(
    engine: Engine,
    partner_odoo_id: int,
    datos: dict[str, Any],
    *,
    actor: str = "sistema",
) -> dict[str, Any]:
    pid = int(partner_odoo_id)
    if pid <= 0:
        raise ValueError("partner_odoo_id inválido.")
    lim_cred = (
        _parse_decimal_no_neg(datos.get("limite_credito_usd"), "limite_credito_usd")
        if datos.get("limite_credito_usd") not in (None, "")
        else None
    )
    lim_fact = (
        _parse_int_no_neg(datos.get("limite_facturas_pendientes"), "limite_facturas_pendientes")
        if datos.get("limite_facturas_pendientes") not in (None, "")
        else None
    )
    dias_mora = (
        _parse_int_no_neg(datos.get("dias_mora_max"), "dias_mora_max")
        if datos.get("dias_mora_max") not in (None, "")
        else None
    )
    valores = {
        "cliente_nombre": str(datos.get("cliente_nombre") or "").strip(),
        "limite_credito_usd": lim_cred,
        "limite_facturas_pendientes": lim_fact,
        "dias_mora_max": dias_mora,
        "bloquear_auto_envio": _parse_bool(datos.get("bloquear_auto_envio"), False),
        "notas": str(datos.get("notas") or "").strip(),
        "actualizado_por": actor,
        "actualizado_en": datetime.now(),
    }
    with engine.begin() as conn:
        existe = conn.execute(
            select(politica_credito_cliente.c.partner_odoo_id).where(
                politica_credito_cliente.c.partner_odoo_id == pid
            )
        ).first()
        if existe is None:
            fila = conn.execute(
                insert(politica_credito_cliente)
                .values(partner_odoo_id=pid, **valores)
                .returning(*politica_credito_cliente.c)
            ).one()
        else:
            fila = conn.execute(
                update(politica_credito_cliente)
                .where(politica_credito_cliente.c.partner_odoo_id == pid)
                .values(**valores)
                .returning(*politica_credito_cliente.c)
            ).one()
    return _fila_override_a_dict(fila)


def eliminar_override_cliente(engine: Engine, partner_odoo_id: int) -> bool:
    try:
        with engine.begin() as conn:
            res = conn.execute(
                delete(politica_credito_cliente).where(
                    politica_credito_cliente.c.partner_odoo_id == int(partner_odoo_id)
                )
            )
        return int(res.rowcount or 0) > 0
    except SQLAlchemyError:
        return False


def _pagos_confirmados_pendientes_odoo_usd(
    engine: Engine | None,
    partner_odoo_id: int | None,
    repo_cxc: Repository | None,
) -> Decimal:
    """Pagos ya confirmados en CxC (recibo emitido) pero aún no reflejados en Odoo."""
    if engine is None or not partner_odoo_id:
        return Decimal("0")
    tasa_bcv = Decimal("0")
    if repo_cxc is not None:
        ultima = repo_cxc.last_serie_tasa()
        if ultima is not None and ultima.tasa_bcv > Decimal("0"):
            tasa_bcv = Decimal(str(ultima.tasa_bcv))
    try:
        with engine.connect() as conn:
            filas = conn.execute(
                select(
                    documentos_operativos.c.monto,
                    documentos_operativos.c.moneda,
                ).where(
                    documentos_operativos.c.tipo == "pago",
                    documentos_operativos.c.estado == "CONFIRMADO_CXC",
                    documentos_operativos.c.partner_odoo_id == int(partner_odoo_id),
                )
            ).all()
    except SQLAlchemyError:
        return Decimal("0")

    total = Decimal("0")
    for monto_raw, mon_raw in filas:
        m = Decimal(str(monto_raw or "0"))
        if m <= Decimal("0"):
            continue
        mon = str(mon_raw or "USD").strip().upper()
        if mon == "VES":
            if tasa_bcv > Decimal("0"):
                total += q2(m / tasa_bcv)
        else:
            total += q2(m)
    return q2(total)


def calcular_metricas_cartera_cliente(
    repo_cxc: Repository | None,
    engine: Engine | None = None,
    *,
    partner_odoo_id: int | None = None,
    rif: str = "",
    cliente_nombre: str = "",
    hoy: date | None = None,
) -> dict[str, Any]:
    """Calcula deuda actual, facturas/órdenes abiertas y mora del cliente en CxC."""
    ref_hoy = hoy or date.today()
    if repo_cxc is None:
        return {
            "cliente_id": str(partner_odoo_id) if partner_odoo_id else None,
            "saldo_pendiente_usd": 0.0,
            "pagos_cxc_confirmados_usd": 0.0,
            "facturas_sin_pagar": 0,
            "ordenes_vencidas": [],
            "max_dias_mora": 0,
        }

    cliente_obj = _resolver_cliente_en_espejo(
        repo_cxc,
        partner_odoo_id=partner_odoo_id,
        rif=rif,
        cliente_nombre=cliente_nombre,
    )
    cid = (
        cliente_obj.cliente_id
        if cliente_obj is not None
        else (str(partner_odoo_id) if partner_odoo_id else None)
    )
    if not cid:
        return {
            "cliente_id": None,
            "saldo_pendiente_usd": 0.0,
            "pagos_cxc_confirmados_usd": 0.0,
            "facturas_sin_pagar": 0,
            "ordenes_vencidas": [],
            "max_dias_mora": 0,
        }

    mapeo_pl: dict[str, Any] = {}
    try:
        raw_pl = repo_cxc.get_config("pricelist_mapeo_unificado")
        if raw_pl and isinstance(raw_pl, str):
            parsed = json.loads(raw_pl)
            if isinstance(parsed, dict):
                mapeo_pl = parsed
    except Exception:
        mapeo_pl = {}
    valid_usd = {
        pid
        for pid, m in mapeo_pl.items()
        if isinstance(m, dict)
        and (
            normalizar_ruta(m.get("ruta")) == RUTA_BINANCE
            or (not normalizar_ruta(m.get("ruta")) and m.get("moneda") == "usd")
        )
    } or {
        x.strip()
        for x in (repo_cxc.get_config("valid_pricelists_usd") or "").split(",")
        if x.strip()
    }
    teoricos_map = {t.so_id: t for t in repo_cxc.all_ventas_teoricos()}
    vincs_por_so: dict[str, list[Any]] = {}
    for v in repo_cxc.all_vinculaciones():
        if v.estado in (EstadoVinculacion.CONCILIADO, EstadoVinculacion.PENDIENTE):
            vincs_por_so.setdefault(v.so_id, []).append(v)

    saldo_total = Decimal("0")
    facturas_abiertas = 0
    vencidas_det: list[dict[str, Any]] = []
    max_mora = 0

    for o in repo_cxc.all_ordenes():
        if str(o.cliente_id) != str(cid):
            continue
        if str(getattr(o, "estado_orden", "sale") or "").lower() == "cancel":
            continue
        if isinstance(o, OrdenVenta):
            t = teoricos_map.get(o.so_id)
            ruta_o = ruta_de_lista(o.lista_precios, mapeo_pl)
            es_usd = (
                ruta_o == RUTA_BINANCE
                or str(o.lista_precios or "").strip() in valid_usd
            )
            objetivo = Decimal(str(o.monto_total or "0"))
            if t is not None:
                bruto = t.teorico_usd if es_usd else t.teorico_ves
                desc = t.descuentos_teorico_usd if es_usd else t.descuentos_teorico_ves
                neto = Decimal(str(bruto)) - Decimal(str(desc))
                if neto > Decimal("0"):
                    objetivo = q2(neto * Decimal("1.16"))
            vincs_o = vincs_por_so.get(o.so_id, [])
            if vincs_o:
                abonado = q2(
                    valor_pagado_binance_usd(vincs_o)
                    if es_usd
                    else valor_pagado_bcv_usd(vincs_o)
                )
            else:
                abonado = Decimal("0")
            estado_f = str(getattr(o, "estado_factura", "") or "").lower()
            pagada_odoo = estado_f in ("paid", "in_payment", "pagado")
            if pagada_odoo:
                continue
            pend = q2(objetivo - abonado)
            if pend <= Decimal("0.50"):
                continue
            saldo_total += pend
            facturas_abiertas += 1
            base_fecha = o.fecha_entrega or o.fecha
            venc = getattr(o, "fecha_vencimiento", None) or (
                base_fecha + timedelta(days=int(o.dias_credito or 0))
            )
            if venc < ref_hoy:
                dias_mora = (ref_hoy - venc).days
                max_mora = max(max_mora, dias_mora)
                vencidas_det.append(
                    {"so_id": o.so_id, "dias_mora": dias_mora, "saldo_usd": float(pend)}
                )

    pid_efectivo = (
        int(cid) if cid.isdigit() else (int(partner_odoo_id) if partner_odoo_id else None)
    )
    pagos_cxc = _pagos_confirmados_pendientes_odoo_usd(engine, pid_efectivo, repo_cxc)
    saldo_neto = max(Decimal("0"), q2(saldo_total - pagos_cxc))

    return {
        "cliente_id": cid,
        "saldo_pendiente_usd": float(saldo_neto),
        "pagos_cxc_confirmados_usd": float(pagos_cxc),
        "facturas_sin_pagar": facturas_abiertas,
        "ordenes_vencidas": vencidas_det,
        "max_dias_mora": max_mora,
    }


def evaluar_politica_auto_envio(
    doc: DocumentoComercial,
    repo_cxc: Repository | None,
    *,
    engine: Engine | None = None,
    hoy: date | None = None,
) -> dict[str, Any]:
    """Evalúa si ``doc`` (Pedido) califica para auto-envío inmediato a Odoo."""
    pol_global = cargar_politica_global(repo_cxc)
    override = obtener_override_cliente(engine, doc.partner_odoo_id)

    lim_credito = Decimal(
        str(
            override["limite_credito_usd"]
            if override and override.get("limite_credito_usd") is not None
            else pol_global["limite_credito_usd"]
        )
    )
    lim_facturas = int(
        override["limite_facturas_pendientes"]
        if override and override.get("limite_facturas_pendientes") is not None
        else pol_global["limite_facturas_pendientes"]
    )
    dias_mora_max = int(
        override["dias_mora_max"]
        if override and override.get("dias_mora_max") is not None
        else pol_global["dias_mora_max"]
    )
    politica_efectiva = {
        "autosend_activo": bool(pol_global["autosend_activo"]),
        "limite_credito_usd": float(lim_credito),
        "limite_facturas_pendientes": lim_facturas,
        "retener_si_vencidas": bool(pol_global["retener_si_vencidas"]),
        "dias_mora_max": dias_mora_max,
        "bloquear_auto_envio": bool(override and override.get("bloquear_auto_envio")),
        "tiene_override_cliente": override is not None,
    }

    metricas = calcular_metricas_cartera_cliente(
        repo_cxc,
        engine,
        partner_odoo_id=doc.partner_odoo_id,
        rif=doc.rif,
        cliente_nombre=doc.cliente_nombre,
        hoy=hoy,
    )
    monto_pedido = q2(
        doc.total_con_iva
        if doc.total_con_iva > Decimal("0")
        else (
            doc.subtotal_neto + doc.monto_iva if doc.subtotal_neto > Decimal("0") else doc.subtotal
        )
    )
    deuda_actual = Decimal(str(metricas["saldo_pendiente_usd"]))
    proyeccion_deuda = q2(deuda_actual + monto_pedido)

    motivos: list[str] = []
    if doc.tipo != PEDIDO:
        motivos.append("Solo los pedidos formales se envían automáticamente a Odoo.")
    if not politica_efectiva["autosend_activo"]:
        motivos.append("Auto-envío a Odoo desactivado en Configuración → Política de Ventas.")
    if politica_efectiva["bloquear_auto_envio"]:
        motivos.append("Cliente marcado con revisión manual obligatoria en su política de crédito.")

    # 1. Catálogo real y precios válidos
    sin_cat = [ln for ln in doc.lineas if not (ln.producto_id and ln.producto_id.isdigit())]
    sin_precio = [ln for ln in doc.lineas if ln.precio_unitario <= Decimal("0")]
    if sin_cat or sin_precio:
        motivos.append(
            f"Hay {max(len(sin_cat), len(sin_precio))} línea(s) sin producto válido "
            "del catálogo de Odoo o sin precio de lista."
        )

    # 2. Descuentos manuales en línea y descuentos especiales fuera del motor
    if any(ln.descuento_pct > Decimal("0") for ln in doc.lineas):
        motivos.append("Contiene descuento(s) manual(es) en línea fuera del catálogo del motor.")
    especs = getattr(doc, "descuentos_especiales", ()) or ()
    tiene_alerta_espec = isinstance(doc.validacion_motor, dict) and any(
        str(a or "").startswith("[ESPECIAL]") for a in (doc.validacion_motor.get("alertas") or [])
    )
    if especs and not tiene_alerta_espec:
        motivos.append(
            f"Incluye {len(especs)} descuento(s) especial(es) comercial(es): "
            "requiere aprobación en CxC."
        )
    dias_cred_doc = int(getattr(doc, "dias_credito", 0) or 0)
    if dias_cred_doc > 0 and doc.partner_odoo_id is None:
        tiene_alerta_cred_nuevo = isinstance(doc.validacion_motor, dict) and any(
            "cliente nuevo" in str(a or "").lower()
            for a in (doc.validacion_motor.get("alertas") or [])
        )
        if not tiene_alerta_cred_nuevo:
            motivos.append(
                f"Plazo de crédito ({dias_cred_doc} días) solicitado para cliente "
                "sin ficha verificada en Odoo."
            )

    # 3. Alertas del motor de descuentos
    codigos_con_alerta: set[str] = set()
    for d in doc.descuentos_elegidos:
        if d.get("cumple") is False:
            cod = str(d.get("codigo") or "")
            if cod:
                codigos_con_alerta.add(cod)
            al = str(d.get("alerta") or "no cumple parámetros del motor").strip()
            motivos.append(f"Descuento [{cod}] no cumple parámetros: {al}")
    if isinstance(doc.validacion_motor, dict):
        for alerta in doc.validacion_motor.get("alertas") or []:
            txt_a = str(alerta or "").strip()
            if not txt_a or txt_a.startswith("Cartera CxC:"):
                continue
            if txt_a.startswith("[") and "]" in txt_a:
                cod_a = txt_a[1 : txt_a.index("]")]
                if cod_a in codigos_con_alerta:
                    continue
            motivos.append(txt_a)

    # 4. Ficha fiscal/geográfica completa del cliente
    faltan_ficha: list[str] = []
    if not doc.cliente_nombre.strip():
        faltan_ficha.append("nombre")
    if not doc.rif.strip():
        faltan_ficha.append("rif")
    if doc.partner_odoo_id is None:
        if not doc.email.strip():
            faltan_ficha.append("email")
        if not doc.direccion.strip():
            faltan_ficha.append("direccion")
        if doc.latitud is None or doc.longitud is None:
            faltan_ficha.append("ubicacion")
    if faltan_ficha:
        motivos.append(
            f"Ficha del cliente incompleta para auto-envío (falta: {', '.join(faltan_ficha)})."
        )

    # 5. Mora / facturas vencidas según días de gracia configurables
    venc_que_exceden = [
        v for v in metricas["ordenes_vencidas"] if int(v["dias_mora"]) > dias_mora_max
    ]
    if politica_efectiva["retener_si_vencidas"] and venc_que_exceden:
        sos = ", ".join(v["so_id"] for v in venc_que_exceden[:3])
        max_m = max(int(v["dias_mora"]) for v in venc_que_exceden)
        motivos.append(
            f"Cartera vencida: {len(venc_que_exceden)} orden(es) con más de "
            f"{dias_mora_max} día(s) de mora (máx. {max_m}d: {sos})."
        )

    # 6. Límite de facturas/órdenes sin pagar
    n_abiertas = int(metricas["facturas_sin_pagar"])
    if lim_facturas > 0 and n_abiertas >= lim_facturas:
        motivos.append(
            f"El cliente tiene {n_abiertas} factura(s)/orden(es) sin pagar "
            f"(límite permitido: {lim_facturas})."
        )

    # 7. Límite de crédito USD
    if lim_credito > Decimal("0") and proyeccion_deuda > lim_credito:
        motivos.append(
            f"Excede límite de crédito (${lim_credito:,.2f}): "
            f"deuda actual ${deuda_actual:,.2f} + pedido ${monto_pedido:,.2f} "
            f"= ${proyeccion_deuda:,.2f}."
        )

    return {
        "puede_auto_enviar": len(motivos) == 0,
        "motivos_retencion": motivos,
        "metricas_cliente": {
            **metricas,
            "monto_pedido_usd": float(monto_pedido),
            "deuda_proyectada_usd": float(proyeccion_deuda),
        },
        "politica_aplicada": politica_efectiva,
    }
