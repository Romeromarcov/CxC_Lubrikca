"""Catálogo de descuentos del motor CxC y validación de parámetros en tiempo de cotización.

Evalúa cada descuento elegido en el bot o en el panel de CxC contra:
- Reglas activas y vigentes en CxC (Pronto Pago, Volumen/Fidelidad, Primera Compra/Obsequio,
  Recompra, Diferencial Cambiario, Producto).
- Aplicabilidad y exclusión por lista comercial (ej. «Pago VES Nov 2026»).
- Marca, presentación/envase, categoría y umbrales mínimos de unidades/cajas/litros/USD.
- Exclusiones mutuas entre componentes del motor (``exclusiones``).
- Historial real sincronizado en CxC (``clientes``, ``ordenes_venta``, ``lineas_orden``,
  ``pagos``, ``vinculaciones``, ``ventas_teoricos``, ``reglas_dias_credito_volumen``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from cxc.capturas.modelos import normalizar_nombre
from cxc.decimal_utils import q2
from cxc.engine.business_days import fin_ventana_contado
from cxc.engine.discounts import normalizar_componente_descuento
from cxc.engine.effective_dating import (
    _excluida_por_lista,
    _excluida_por_moneda,
    _match_categoria,
    _match_lista,
    _match_marca,
    _match_producto_codigo,
    _match_producto_especial,
    _vigente,
)
from cxc.engine.equivalents import valor_pagado_usd
from cxc.engine.rutas_pago import (
    RUTA_BCV,
    RUTA_BINANCE,
    etiqueta_ruta,
    normalizar_ruta,
    ruta_de_lista,
)
from cxc.models import (
    Cliente,
    EstadoVinculacion,
    ExclusionRegla,
    LineaOrden,
    OrdenVenta,
    get_marca_fallback,
)
from cxc.repositories import Repository

from .catalogo import extraer_presentacion, tipo_envase_de_presentacion

logger = logging.getLogger("cxc.documentos.evaluador")

IVA_VENEZUELA = Decimal("0.16")

_sync_guard_lock = threading.Lock()
_ultimo_sync_forzado_ts: float = 0.0
_COOLDOWN_SYNC_SEGUNDOS = 120.0
_MAX_EDAD_SYNC_MINUTOS = 4


def asegurar_datos_frescos_cxc(repo: Repository | None) -> dict[str, Any]:
    """Verifica frescura de pagos, clientes y ventas en CxC; corre delta rápido si está vencido."""
    if repo is None:
        return {"sincronizado": False, "ultimo_sync": None, "refrescado_ahora": False}

    ultimo = repo.get_last_sync()
    ahora = datetime.now()
    edad_min: float | None = None
    if ultimo is not None:
        ultimo_naive = ultimo.replace(tzinfo=None) if ultimo.tzinfo else ultimo
        edad_min = max(0.0, (ahora - ultimo_naive).total_seconds() / 60.0)

    # Solo intenta sync incremental en vivo cuando hay credenciales reales de Odoo y el espejo
    # lleva más de _MAX_EDAD_SYNC_MINUTOS sin actualizarse (con cooldown para no saturar).
    refrescado = False
    if (
        (ultimo is None or (edad_min is not None and edad_min > _MAX_EDAD_SYNC_MINUTOS))
        and ultimo is not None  # nunca dispara full bootstrap síncrono desde una cotización
        and os.environ.get("ODOO_URL")
        and os.environ.get("ODOO_API_KEY")
        and "PYTEST_CURRENT_TEST" not in os.environ
    ):
        global _ultimo_sync_forzado_ts
        mono = time.monotonic()
        if mono - _ultimo_sync_forzado_ts >= _COOLDOWN_SYNC_SEGUNDOS and _sync_guard_lock.acquire(
            blocking=False
        ):
            try:
                from cxc.config import AppConfig
                from cxc.odoo.client import OdooXmlRpcReader
                from cxc.sync.incremental import IncrementalSync

                _ultimo_sync_forzado_ts = mono
                cfg = AppConfig.from_env()
                reader = OdooXmlRpcReader(cfg.odoo)
                IncrementalSync(repo, reader).run(ahora, sync_catalogo=False)
                ultimo = repo.get_last_sync()
                edad_min = 0.0
                refrescado = True
            except Exception as exc:
                logger.warning("No se pudo refrescar delta CxC antes de cotizar: %s", exc)
            finally:
                _sync_guard_lock.release()

    return {
        "sincronizado": ultimo is not None,
        "ultimo_sync": ultimo.isoformat(timespec="seconds") if ultimo else None,
        "edad_minutos": round(edad_min, 1) if edad_min is not None else None,
        "refrescado_ahora": refrescado,
    }


_FAMILIA_LABELS: dict[str, str] = {
    "pronto_pago": "Pronto Pago / Contado",
    "volumen": "Volumen por Orden",
    "fidelidad": "Fidelidad Acumulada",
    "primera_compra": "Primera Compra / Obsequio",
    "recompra": "Recompra / Ventana de Pago",
    "diferencial": "Diferencial Cambiario",
    "producto": "Promoción por Producto",
    "especial": "Descuento Especial Comercial",
}


def _cargar_mapeo_listas_eval(repo: Repository | None) -> dict[str, Any]:
    if repo is None:
        return {}
    try:
        raw = repo.get_config("pricelist_mapeo_unificado")
        if raw and isinstance(raw, str):
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _listas_validas_repo(repo: Repository) -> tuple[list[str], list[str]]:
    mapeo = _cargar_mapeo_listas_eval(repo)
    valid_ves = [
        pid
        for pid, m in mapeo.items()
        if isinstance(m, Mapping)
        and (
            normalizar_ruta(m.get("ruta")) == RUTA_BCV
            or (not normalizar_ruta(m.get("ruta")) and m.get("moneda") == "ves")
        )
    ]
    valid_usd = [
        pid
        for pid, m in mapeo.items()
        if isinstance(m, Mapping)
        and (
            normalizar_ruta(m.get("ruta")) == RUTA_BINANCE
            or (not normalizar_ruta(m.get("ruta")) and m.get("moneda") == "usd")
        )
    ]
    if valid_ves or valid_usd:
        return valid_ves, valid_usd
    ves_str = repo.get_config("valid_pricelists_ves") or ""
    usd_str = repo.get_config("valid_pricelists_usd") or ""
    valid_ves = [x.strip() for x in ves_str.split(",") if x.strip()]
    valid_usd = [x.strip() for x in usd_str.split(",") if x.strip()]
    return valid_ves, valid_usd


def _normalizar_descuentos_especiales(
    descuentos_especiales: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    salida: list[dict[str, Any]] = []
    for idx, raw in enumerate(descuentos_especiales or [], start=1):
        if not isinstance(raw, Mapping):
            continue
        motivo = str(
            raw.get("motivo") or raw.get("descripcion") or raw.get("titulo") or ""
        ).strip()
        try:
            pct = max(Decimal("0"), min(Decimal("100"), Decimal(str(raw.get("porcentaje") or "0"))))
        except Exception:
            pct = Decimal("0")
        try:
            monto_fijo = max(Decimal("0"), Decimal(str(raw.get("monto_usd") or "0")))
        except Exception:
            monto_fijo = Decimal("0")
        if pct <= Decimal("0") and monto_fijo <= Decimal("0") and not motivo:
            continue
        salida.append(
            {
                "id": str(raw.get("id") or f"ESP-{idx}"),
                "motivo": motivo or f"Descuento comercial especial #{idx}",
                "porcentaje": float(q2(pct)),
                "monto_usd": float(q2(monto_fijo)),
            }
        )
    return salida


def _pct_humano(fraccion_o_pct: Decimal) -> Decimal:
    """Normaliza porcentaje de regla (0.08 -> 8.00; si ya viene >=1 como 8 -> 8.00)."""
    d = Decimal(str(fraccion_o_pct or "0"))
    if d <= Decimal("1") and d > Decimal("0"):
        return q2(d * Decimal("100"))
    return q2(d)


def _categoria_linea_catalogo(marca: str, envase: str, litros_u: Decimal) -> str:
    if envase in ("TAMBOR", "PAILA") or litros_u >= Decimal("18"):
        return "Industrial"
    return "Comercial"


def obtener_catalogo_descuentos(
    repo: Repository | None,
    *,
    fecha: date | None = None,
    lista_precios_id: str | None = None,
) -> list[dict[str, Any]]:
    """Devuelve todas las reglas de descuento activas y vigentes del motor CxC."""
    if repo is None:
        return []
    hoy = fecha or date.today()
    valid_ves, valid_usd = _listas_validas_repo(repo)
    lista_ref = str(lista_precios_id or "").strip()
    if lista_ref and lista_ref not in valid_ves and lista_ref not in valid_usd:
        # Si la lista activa por defecto es VES (ej. Pago VES Nov 2026), inclúyela en valid_ves
        valid_ves = [*valid_ves, lista_ref]

    catalogo_out: list[dict[str, Any]] = []

    # 1. Pronto Pago (DescuentosMarcaCategoria)
    for r_pp in repo.descuentos_marca_categoria():
        if not _vigente(r_pp.vigencia_desde, r_pp.vigencia_hasta, r_pp.activo, hoy):
            continue
        pct = _pct_humano(r_pp.porcentaje)
        es_ves = (r_pp.monedas_aplicables or "").strip().upper() == "VES"
        componente = "contado_ves" if es_ves else "contado"
        moneda_lbl = (
            "cualquier moneda" if r_pp.monedas_aplicables in ("*", "") else r_pp.monedas_aplicables
        )
        titulo = (
            r_pp.descripcion.strip()
            if r_pp.descripcion and r_pp.descripcion.strip()
            else f"Pronto Pago {pct}% ({r_pp.marca} · {r_pp.categoria})"
        )
        cond = (
            f"Pago en {moneda_lbl} dentro de {r_pp.ventana_pago_dias} día(s) hábil(es) "
            f"desde {r_pp.ventana_pago_tipo}"
        )
        aplica_lista = not lista_ref or (
            _match_lista(r_pp.listas_aplicables, lista_ref, valid_ves, valid_usd)
            and not _excluida_por_lista(r_pp.listas_excluidas, lista_ref, valid_ves, valid_usd)
        )
        catalogo_out.append(
            {
                "codigo": r_pp.regla_id,
                "familia": "pronto_pago",
                "componente": componente,
                "titulo": titulo,
                "porcentaje": float(pct),
                "es_obsequio": False,
                "marca": r_pp.marca,
                "categoria": r_pp.categoria,
                "monedas_aplicables": r_pp.monedas_aplicables,
                "condicion": cond,
                "aplica_lista_actual": aplica_lista,
            }
        )

    # 2. Volumen y Fidelidad acumulada (DescuentosVolumen)
    for r_vol in repo.descuentos_volumen():
        if not _vigente(r_vol.vigencia_desde, r_vol.vigencia_hasta, r_vol.activo, hoy):
            continue
        pct = _pct_humano(r_vol.porcentaje)
        es_acum = (r_vol.tipo_evaluacion or "orden").lower() == "acumulado"
        max_txt = f"–{r_vol.max_unidades:g}" if r_vol.max_unidades < Decimal("99999") else "+"
        ventana_txt = f" acumulados ({r_vol.dias_evaluacion}d)" if es_acum else " en la orden"
        titulo = (
            r_vol.descripcion.strip()
            if r_vol.descripcion and r_vol.descripcion.strip()
            else (
                f"{'Fidelidad' if es_acum else 'Volumen'} {pct}% "
                f"({r_vol.min_unidades:g}{max_txt} {r_vol.unidad_medida})"
            )
        )
        aplica_lista = not lista_ref or (
            _match_lista(r_vol.listas_aplicables, lista_ref, valid_ves, valid_usd)
            and not _excluida_por_lista(r_vol.listas_excluidas, lista_ref, valid_ves, valid_usd)
        )
        catalogo_out.append(
            {
                "codigo": r_vol.regla_id,
                "familia": "fidelidad" if es_acum else "volumen",
                "componente": "volumen",
                "titulo": titulo,
                "porcentaje": float(pct),
                "es_obsequio": False,
                "marca": r_vol.marca,
                "categoria": r_vol.categoria,
                "condicion": (
                    f"Mínimo {r_vol.min_unidades:g}{max_txt} {r_vol.unidad_medida}"
                    f"{ventana_txt} ({r_vol.marca} · {r_vol.categoria})"
                ),
                "aplica_lista_actual": aplica_lista,
            }
        )

    # 3. Primera Compra y Promociones de Obsequio (PromocionPrimeraCompra)
    for r_pr in repo.promociones_primera_compra():
        if not _vigente(r_pr.vigencia_desde, r_pr.vigencia_hasta, r_pr.activo, hoy):
            continue
        es_obsequio = (r_pr.tipo_beneficio or "").lower() == "producto"
        pct = Decimal("0") if es_obsequio else _pct_humano(r_pr.valor)
        titulo = (
            r_pr.descripcion.strip()
            if r_pr.descripcion and r_pr.descripcion.strip()
            else (
                f"Promo Obsequio ({r_pr.compra_minima:g}+ {r_pr.unidad_medida} → {r_pr.valor:g} u.)"
                if es_obsequio
                else f"Primera Compra {pct}% ({r_pr.marca})"
            )
        )
        partes_cond: list[str] = []
        if r_pr.solo_primera_compra:
            partes_cond.append("Exclusivo 1era compra (sin órdenes previas)")
        if r_pr.compra_minima > Decimal("0"):
            partes_cond.append(f"Mínimo {r_pr.compra_minima:g} {r_pr.unidad_medida}")
        if es_obsequio and r_pr.productos:
            partes_cond.append(f"Obsequio SKU/ID: {r_pr.productos}")
        aplica_lista = not lista_ref or (
            _match_lista(r_pr.listas_aplicables, lista_ref, valid_ves, valid_usd)
            and not _excluida_por_lista(r_pr.listas_excluidas, lista_ref, valid_ves, valid_usd)
        )
        catalogo_out.append(
            {
                "codigo": r_pr.regla_id,
                "familia": "primera_compra",
                "componente": "primera_compra",
                "titulo": titulo,
                "porcentaje": float(pct),
                "es_obsequio": es_obsequio,
                "obsequio_detalle": (
                    f"{r_pr.valor:g} unidad(es) de obsequio ({r_pr.productos})"
                    if es_obsequio
                    else ""
                ),
                "marca": r_pr.marca,
                "categoria": r_pr.categoria,
                "condicion": " · ".join(partes_cond) or "Promoción comercial vigente",
                "aplica_lista_actual": aplica_lista,
            }
        )

    # 4. Recompra / Recurrencia (DescuentosRecompra)
    for r_rec in repo.descuentos_recompra():
        if not _vigente(r_rec.vigencia_desde, r_rec.vigencia_hasta, r_rec.activo, hoy):
            continue
        pct = _pct_humano(r_rec.porcentaje)
        max_txt = f"–{r_rec.max_unidades:g}" if r_rec.max_unidades < Decimal("99999") else "+"
        titulo = (
            r_rec.descripcion.strip()
            if r_rec.descripcion and r_rec.descripcion.strip()
            else f"Recompra {pct}% ({r_rec.min_unidades:g}{max_txt} {r_rec.unidad_medida})"
        )
        aplica_lista = not lista_ref or (
            _match_lista(r_rec.listas_aplicables, lista_ref, valid_ves, valid_usd)
            and not _excluida_por_lista(r_rec.listas_excluidas, lista_ref, valid_ves, valid_usd)
        )
        catalogo_out.append(
            {
                "codigo": r_rec.regla_id,
                "familia": "recompra",
                "componente": "recurrencia",
                "titulo": titulo,
                "porcentaje": float(pct),
                "es_obsequio": False,
                "marca": r_rec.marca,
                "categoria": r_rec.categoria,
                "condicion": (
                    f"{r_rec.min_unidades:g}{max_txt} {r_rec.unidad_medida} · "
                    f"Orden anterior pagada dentro de {r_rec.ventana_pago_tipo} "
                    f"+ {r_rec.ventana_pago_dias}d"
                ),
                "aplica_lista_actual": aplica_lista,
            }
        )

    # 5. Diferencial Cambiario (DescuentosDiferencialCambiario)
    ultima_tasa = repo.last_serie_tasa()
    pct_binance = Decimal("0")
    if (
        ultima_tasa is not None
        and ultima_tasa.tasa_bcv > Decimal("0")
        and ultima_tasa.tasa_binance > ultima_tasa.tasa_bcv
    ):
        pct_binance = q2(
            (Decimal("1") - (ultima_tasa.tasa_bcv / ultima_tasa.tasa_binance)) * Decimal("100")
        )

    for r_dif in repo.descuentos_diferencial_cambiario():
        if r_dif.tipo_diferencial == "candidato_cierre_factura":
            continue
        if not _vigente(r_dif.vigencia_desde, r_dif.vigencia_hasta, r_dif.activo, hoy):
            continue
        es_fijo = r_dif.tipo_diferencial == "fijo_35_ves_usd"
        pct = _pct_humano(r_dif.porcentaje_fijo) if es_fijo else pct_binance
        titulo = (
            r_dif.descripcion.strip()
            if r_dif.descripcion and r_dif.descripcion.strip()
            else (
                f"Diferencial Fijo VES→USD ({pct}%)"
                if es_fijo
                else f"Equiparar Tasa Binance (~{pct}%)"
            )
        )
        cond = (
            "Pago en divisas USD sobre lista comercial VES"
            if es_fijo
            else "Pago en VES equiparado a tasa Binance sin pagos huérfanos"
        )
        aplica_lista = not lista_ref or (
            _match_lista(r_dif.listas_aplicables, lista_ref, valid_ves, valid_usd)
            and not _excluida_por_lista(r_dif.listas_excluidas, lista_ref, valid_ves, valid_usd)
        )
        catalogo_out.append(
            {
                "codigo": r_dif.regla_id,
                "familia": "diferencial",
                "componente": "bcv_completo",
                "titulo": titulo,
                "porcentaje": float(pct),
                "es_obsequio": False,
                "marca": r_dif.marca,
                "categoria": r_dif.categoria,
                "condicion": cond,
                "aplica_lista_actual": aplica_lista,
            }
        )

    # 6. Promociones específicas por Producto/SKU (DescuentosProducto)
    for r_prod in repo.descuentos_producto():
        if not _vigente(r_prod.vigencia_desde, r_prod.vigencia_hasta, r_prod.activo, hoy):
            continue
        pct = _pct_humano(r_prod.porcentaje)
        titulo = (
            r_prod.descripcion.strip()
            if r_prod.descripcion and r_prod.descripcion.strip()
            else f"Promo Producto {pct}% ({r_prod.productos})"
        )
        aplica_lista = not lista_ref or (
            _match_lista(r_prod.listas_aplicables, lista_ref, valid_ves, valid_usd)
            and not _excluida_por_lista(r_prod.listas_excluidas, lista_ref, valid_ves, valid_usd)
        )
        catalogo_out.append(
            {
                "codigo": r_prod.regla_id,
                "familia": "producto",
                "componente": "producto",
                "titulo": titulo,
                "porcentaje": float(pct),
                "es_obsequio": False,
                "marca": r_prod.marca,
                "categoria": r_prod.categoria,
                "condicion": (
                    f"Productos {r_prod.productos} "
                    f"(mín. {r_prod.min_unidades:g} {r_prod.unidad_medida})"
                ),
                "aplica_lista_actual": aplica_lista,
            }
        )

    for it in catalogo_out:
        it["familia_label"] = _FAMILIA_LABELS.get(str(it.get("familia") or ""), "Descuento")

    return catalogo_out


def _resolver_cliente_en_espejo(
    repo: Repository,
    *,
    partner_odoo_id: int | None,
    rif: str,
    cliente_nombre: str,
) -> Cliente | None:
    todos = repo.all_clientes()
    if partner_odoo_id is not None:
        por_id = repo.get_cliente(str(partner_odoo_id))
        if por_id is not None:
            return por_id
    if rif:
        rif_limpio = rif.replace("-", "").upper()
        for c in todos:
            vat_c = str(getattr(c, "rif", "") or "").replace("-", "").upper()
            if vat_c and vat_c == rif_limpio:
                return c
    if cliente_nombre:
        nom_norm = normalizar_nombre(cliente_nombre)
        coincidencias = [c for c in todos if normalizar_nombre(c.nombre) == nom_norm]
        if len(coincidencias) == 1:
            return coincidencias[0]
    return None


def _historial_cliente(
    repo: Repository,
    cliente_id: str | None,
    *,
    hoy: date,
    valid_usd: list[str],
) -> dict[str, Any]:
    """Resume órdenes previas, pago de la última orden, vencidas y pagos sin aplicar en CxC."""
    if not cliente_id:
        return {
            "cliente_encontrado": False,
            "cliente_id": None,
            "ordenes_previas": 0,
            "ultima_orden_so": None,
            "ultima_orden_fecha": None,
            "ultima_orden_pagada": False,
            "ultima_orden_abonado_usd": 0.0,
            "ultima_orden_objetivo_usd": 0.0,
            "ordenes_vencidas": [],
            "tiene_pagos_sin_aplicar": False,
            "ordenes": [],
            "lineas_por_orden": {},
            "feriados": frozenset(),
        }

    ordenes_cli = [
        o
        for o in repo.all_ordenes()
        if str(o.cliente_id) == str(cliente_id)
        and str(getattr(o, "estado_orden", "sale")).lower() != "cancel"
    ]
    ordenes_cli.sort(key=lambda o: (o.fecha, o.so_id))

    todas_vincs = repo.all_vinculaciones()
    vincs_por_so: dict[str, list[Any]] = {}
    aplicado_por_pago: dict[str, Decimal] = {}
    for v in todas_vincs:
        if v.estado in (EstadoVinculacion.CONCILIADO, EstadoVinculacion.PENDIENTE):
            vincs_por_so.setdefault(v.so_id, []).append(v)
            aplicado_por_pago[v.pago_id] = aplicado_por_pago.get(v.pago_id, Decimal("0")) + Decimal(
                str(v.monto_aplicado or "0")
            )

    teoricos_map = {t.so_id: t for t in repo.all_ventas_teoricos()}
    feriados_set = frozenset(f.fecha for f in repo.feriados())

    def _objetivo_y_pagado(o: OrdenVenta) -> tuple[Decimal, Decimal, bool]:
        t = teoricos_map.get(o.so_id)
        objetivo = Decimal(str(o.monto_total or "0"))
        if t is not None:
            es_usd = str(o.lista_precios or "").strip() in set(valid_usd)
            bruto = t.teorico_usd if es_usd else t.teorico_ves
            desc = t.descuentos_teorico_usd if es_usd else t.descuentos_teorico_ves
            neto = Decimal(str(bruto)) - Decimal(str(desc))
            if neto > Decimal("0"):
                objetivo = q2(neto * (Decimal("1") + IVA_VENEZUELA))
        vincs_o = vincs_por_so.get(o.so_id, [])
        abonado = q2(valor_pagado_usd(vincs_o)) if vincs_o else Decimal("0")
        estado_f = str(getattr(o, "estado_factura", "") or "").lower()
        pagada_odoo = estado_f in ("paid", "in_payment", "pagado")
        pagada = pagada_odoo or (objetivo > Decimal("0") and abonado + Decimal("0.50") >= objetivo)
        return objetivo, abonado, pagada

    ultima: OrdenVenta | None = ordenes_cli[-1] if ordenes_cli else None
    ult_obj, ult_abo, ult_pagada = (
        _objetivo_y_pagado(ultima) if ultima is not None else (Decimal("0"), Decimal("0"), False)
    )

    vencidas: list[str] = []
    for o in ordenes_cli:
        venc = getattr(o, "fecha_vencimiento", None) or (
            o.fecha + timedelta(days=int(o.dias_credito or 0))
        )
        if venc < hoy:
            _, _, ok_p = _objetivo_y_pagado(o)
            if not ok_p:
                vencidas.append(o.so_id)

    cerrados_ids = {str(r.get("pago_id") or "") for r in repo.all_pagos_huerfanos_cerrados()}
    pagos_cli = [p for p in repo.all_pagos() if str(p.cliente_id) == str(cliente_id)]
    tiene_huerfanos = False
    for p in pagos_cli:
        if p.pago_id in cerrados_ids:
            continue
        if aplicado_por_pago.get(p.pago_id, Decimal("0")) <= Decimal("0"):
            tiene_huerfanos = True
            break

    so_set = {o.so_id for o in ordenes_cli}
    lineas_por_so: dict[str, list[LineaOrden]] = {s: [] for s in so_set}
    if so_set:
        for ln in repo.all_lineas():
            if ln.so_id in lineas_por_so:
                lineas_por_so[ln.so_id].append(ln)

    return {
        "cliente_encontrado": True,
        "cliente_id": str(cliente_id),
        "ordenes_previas": len(ordenes_cli),
        "ultima_orden_so": ultima.so_id if ultima else None,
        "ultima_orden_fecha": ultima.fecha.isoformat() if ultima else None,
        "ultima_orden_objeto": ultima,
        "ultima_orden_pagada": ult_pagada,
        "ultima_orden_abonado_usd": float(ult_abo),
        "ultima_orden_objetivo_usd": float(ult_obj),
        "ordenes_vencidas": vencidas,
        "tiene_pagos_sin_aplicar": tiene_huerfanos,
        "ordenes": ordenes_cli,
        "lineas_por_orden": lineas_por_so,
        "feriados": feriados_set,
    }


def _medir_lineas(
    lineas_cot: Sequence[dict[str, Any]],
    *,
    marca_regla: str,
    cat_regla: str,
    unidad: str,
    productos_regla: str = "*",
) -> tuple[Decimal, Decimal]:
    """Devuelve ``(cantidad_en_unidad, subtotal_usd_calificable)`` de las líneas."""
    u = (unidad or "UNIDADES").strip().upper()
    cant_total = Decimal("0")
    sub_match = Decimal("0")
    for ln in lineas_cot:
        marca_ln = str(ln.get("marca") or "").strip() or get_marca_fallback()
        pres_ln = str(
            ln.get("presentacion") or extraer_presentacion(str(ln.get("descripcion") or ""))
        )
        envase = tipo_envase_de_presentacion(pres_ln, str(ln.get("descripcion") or ""))
        litros_u = Decimal(str(ln.get("litros_unidad") or "0"))
        cat_ln = _categoria_linea_catalogo(marca_ln, envase, litros_u)
        prod_ref = (
            f"{ln.get('codigo') or ''} {ln.get('producto_id') or ''} "
            f"{ln.get('descripcion') or ''}"
        ).strip()

        if not _match_marca(marca_regla, marca_ln):
            continue
        if not _match_categoria(cat_regla, cat_ln, envase):
            continue
        if productos_regla != "*" and not _match_producto_codigo(productos_regla, prod_ref):
            continue

        cant = Decimal(str(ln.get("cantidad") or "0"))
        precio = Decimal(str(ln.get("precio_unitario") or "0"))
        sub_bruto = q2(cant * precio)
        sub_match += sub_bruto

        if u in ("LITROS", "L"):
            cant_total += q2(cant * litros_u)
        elif u in ("USD", "DOLARES", "MONTO"):
            cant_total += sub_bruto
        else:
            cant_total += cant
    return cant_total, sub_match


def evaluar_descuentos_cotizacion(
    repo: Repository | None,
    *,
    lineas: Sequence[dict[str, Any]],
    descuentos_codigos: Sequence[str],
    descuentos_especiales: Sequence[dict[str, Any]] | None = None,
    lista_precios_id: str = "21",
    lista_precios_nombre: str = "Pago VES Nov 2026",
    moneda_lista: str = "VES",
    partner_odoo_id: int | None = None,
    rif: str = "",
    cliente_nombre: str = "",
    es_primera_compra: bool | None = None,
    moneda_pago_esperada: str | None = None,
    dias_credito_solicitados: int | None = None,
    fecha: date | None = None,
    verificar_frescura: bool = True,
) -> dict[str, Any]:
    """Evalúa los descuentos elegidos contra el motor de CxC y calcula totales + alertas."""
    hoy = fecha or date.today()
    frescura = (
        asegurar_datos_frescos_cxc(repo)
        if verificar_frescura
        else {"sincronizado": False, "ultimo_sync": None, "refrescado_ahora": False}
    )

    mapeo_listas_eval = _cargar_mapeo_listas_eval(repo)
    ruta_pago = ruta_de_lista(
        lista_precios_id,
        mapeo_listas_eval,
        lista_nombre=lista_precios_nombre,
        moneda_lista=moneda_lista,
    )
    ruta_pago_label = etiqueta_ruta(ruta_pago)
    espec_limpios = _normalizar_descuentos_especiales(descuentos_especiales)

    subtotal_lista = Decimal("0")
    total_litros = Decimal("0")
    for ln in lineas:
        cant = Decimal(str(ln.get("cantidad") or "0"))
        precio = Decimal(str(ln.get("precio_unitario") or "0"))
        litros_u = Decimal(str(ln.get("litros_unidad") or "0"))
        subtotal_lista += q2(cant * precio)
        total_litros += q2(cant * litros_u)
    subtotal_lista = q2(subtotal_lista)
    total_litros = q2(total_litros)

    tasa_bcv: Decimal | None = None
    if repo is not None:
        ultima_s = repo.last_serie_tasa()
        if ultima_s is not None and ultima_s.tasa_bcv > Decimal("0"):
            tasa_bcv = q2(ultima_s.tasa_bcv)

    codigos_limpios = list(
        dict.fromkeys(str(c).strip() for c in (descuentos_codigos or []) if str(c).strip())
    )

    espec_sin_repo: list[dict[str, Any]] = []
    tot_esp_sin_repo = Decimal("0")
    alertas_esp_sin_repo: list[str] = []
    for esp in espec_limpios:
        pct_e = Decimal(str(esp["porcentaje"]))
        m_e = (
            q2(subtotal_lista * pct_e / Decimal("100"))
            if pct_e > Decimal("0")
            else q2(Decimal(str(esp["monto_usd"])))
        )
        tot_esp_sin_repo += m_e
        item_e = {
            **esp,
            "familia": "especial",
            "familia_label": _FAMILIA_LABELS["especial"],
            "monto_estimado_usd": float(m_e),
            "requiere_aprobacion": True,
        }
        espec_sin_repo.append(item_e)
        alertas_esp_sin_repo.append(
            f"[ESPECIAL] {esp['motivo']} ({pct_e:g}% / -${m_e:.2f}): "
            "requiere aprobación administrativa en CxC."
        )

    if repo is None:
        tot_d_sr = min(subtotal_lista, q2(tot_esp_sin_repo))
        sub_neto_sr = q2(subtotal_lista - tot_d_sr)
        iva_sin_repo = q2(sub_neto_sr * IVA_VENEZUELA)
        tot_sin_repo = q2(sub_neto_sr + iva_sin_repo)
        alertas_sr = (
            ["No hay repositorio CxC disponible para validar parámetros de descuentos."]
            if codigos_limpios
            else []
        ) + alertas_esp_sin_repo
        return {
            "lista_precios_id": lista_precios_id,
            "lista_precios_nombre": lista_precios_nombre,
            "moneda_lista": moneda_lista,
            "ruta_pago": ruta_pago,
            "ruta_pago_label": ruta_pago_label,
            "tasa_bcv": None,
            "subtotal_lista": float(subtotal_lista),
            "total_descuentos": float(tot_d_sr),
            "subtotal_neto": float(sub_neto_sr),
            "monto_iva": float(iva_sin_repo),
            "total_con_iva": float(tot_sin_repo),
            "total_con_iva_ves": None,
            "total_litros": float(total_litros),
            "max_dias_credito_sugerido": None,
            "dias_credito_solicitados": dias_credito_solicitados,
            "dias_credito_cumple": True,
            "cumple_todo": len(codigos_limpios) == 0 and len(espec_sin_repo) == 0,
            "requiere_aprobacion_manual": bool(espec_sin_repo or codigos_limpios),
            "descuentos": [],
            "descuentos_especiales": espec_sin_repo,
            "alertas": alertas_sr,
            "cliente_cxc": {"cliente_encontrado": False, "ordenes_previas": 0},
            "frescura_cxc": frescura,
        }

    valid_ves, valid_usd = _listas_validas_repo(repo)
    lista_ref = str(lista_precios_id or "").strip()
    if moneda_lista.upper() == "VES" and lista_ref and lista_ref not in valid_ves:
        valid_ves = [*valid_ves, lista_ref]
    elif moneda_lista.upper() == "USD" and lista_ref and lista_ref not in valid_usd:
        valid_usd = [*valid_usd, lista_ref]

    cliente_obj = _resolver_cliente_en_espejo(
        repo,
        partner_odoo_id=partner_odoo_id,
        rif=rif,
        cliente_nombre=cliente_nombre,
    )
    hist = _historial_cliente(
        repo,
        cliente_obj.cliente_id
        if cliente_obj
        else (str(partner_odoo_id) if partner_odoo_id else None),
        hoy=hoy,
        valid_usd=valid_usd,
    )

    alertas_globales: list[str] = []
    if hist["ordenes_vencidas"]:
        muestra = ", ".join(hist["ordenes_vencidas"][:3])
        alertas_globales.append(
            f"Cartera CxC: el cliente presenta {len(hist['ordenes_vencidas'])} orden(es) "
            f"vencida(s) con saldo pendiente ({muestra})."
        )

    # Plazo máximo de crédito permitido según litros cotizados
    candidatos_cred: list[int] = []
    for reg_cred in repo.all_reglas_dias_credito_volumen():
        if str(reg_cred.get("activo", "TRUE")).strip().upper() in ("FALSE", "0", "NO"):
            continue
        l_min = Decimal(
            str(reg_cred.get("litros_minimo") or reg_cred.get("litros_min") or "0")
        )
        raw_max = (
            reg_cred.get("litros_maximo")
            if reg_cred.get("litros_maximo") not in (None, "")
            else reg_cred.get("litros_max")
        )
        l_max = Decimal(str(raw_max)) if raw_max not in (None, "") else None
        if l_min <= total_litros and (l_max is None or total_litros <= l_max):
            dias_val = int(
                Decimal(
                    str(reg_cred.get("dias_credito_max") or reg_cred.get("dias_max") or "0")
                )
            )
            candidatos_cred.append(dias_val)
    max_dias_credito: int | None = max(candidatos_cred) if candidatos_cred else None
    dias_credito_cumple = True
    if (
        dias_credito_solicitados is not None
        and max_dias_credito is not None
        and dias_credito_solicitados > max_dias_credito
    ):
        dias_credito_cumple = False
        alertas_globales.append(
            f"Plazo de crédito solicitado ({dias_credito_solicitados} días) excede el máximo "
            f"permitido por motor ({max_dias_credito} días para {total_litros:g} L)."
        )
    if (
        dias_credito_solicitados is not None
        and dias_credito_solicitados > 0
        and partner_odoo_id is None
        and (es_primera_compra is True or hist["ordenes_previas"] == 0)
    ):
        dias_credito_cumple = False
        alertas_globales.append(
            f"Crédito solicitado ({dias_credito_solicitados} días) para cliente nuevo / "
            "sin ficha verificada en Odoo: requiere aprobación en CxC."
        )

    # Mapas rápidos de reglas por código
    pp_map = {r.regla_id: r for r in repo.descuentos_marca_categoria()}
    vol_map = {r.regla_id: r for r in repo.descuentos_volumen()}
    pr_map = {r.regla_id: r for r in repo.promociones_primera_compra()}
    rec_map = {r.regla_id: r for r in repo.descuentos_recompra()}
    dif_map = {r.regla_id: r for r in repo.descuentos_diferencial_cambiario()}
    prod_map = {r.regla_id: r for r in repo.descuentos_producto()}

    evaluados: list[dict[str, Any]] = []

    for cod in codigos_limpios:
        motivos_fallo: list[str] = []
        condicion_txt = ""
        pct = Decimal("0")
        monto_desc = Decimal("0")
        es_obsequio = False
        obsequio_detalle = ""
        componente = ""
        titulo = cod
        familia = "desconocida"

        if cod in pp_map:
            r_pp = pp_map[cod]
            familia = "pronto_pago"
            es_ves = (r_pp.monedas_aplicables or "").strip().upper() == "VES"
            componente = "contado_ves" if es_ves else "contado"
            pct = _pct_humano(r_pp.porcentaje)
            titulo = r_pp.descripcion.strip() or f"Pronto Pago {pct}%"
            condicion_txt = (
                f"Sujeto a pago en {r_pp.monedas_aplicables or '*'} dentro de "
                f"{r_pp.ventana_pago_dias} día(s) hábil(es) desde {r_pp.ventana_pago_tipo}"
            )
            if not _vigente(r_pp.vigencia_desde, r_pp.vigencia_hasta, r_pp.activo, hoy):
                motivos_fallo.append("La regla de pronto pago no está vigente o está inactiva.")
            if lista_ref and (
                not _match_lista(r_pp.listas_aplicables, lista_ref, valid_ves, valid_usd)
                or _excluida_por_lista(r_pp.listas_excluidas, lista_ref, valid_ves, valid_usd)
            ):
                motivos_fallo.append(
                    f"La regla {cod} no aplica a la lista comercial «{lista_precios_nombre}»."
                )
            if moneda_pago_esperada:
                mp = moneda_pago_esperada.strip().upper()
                if _excluida_por_moneda(r_pp.monedas_excluidas, mp):
                    motivos_fallo.append(f"La regla {cod} excluye pagos en {mp}.")
                if r_pp.monedas_aplicables not in ("*", ""):
                    valid_m = [x.strip().upper() for x in r_pp.monedas_aplicables.split(",")]
                    if mp not in valid_m:
                        motivos_fallo.append(
                            f"La regla {cod} exige pago en {r_pp.monedas_aplicables} "
                            f"(indicado: {mp})."
                        )
            # Verifica productos calificables + palabras clave especiales (ELITE/SS)
            _, sub_match = _medir_lineas(
                lineas, marca_regla=r_pp.marca, cat_regla=r_pp.categoria, unidad="USD"
            )
            if any(k in cod.upper() for k in ("ELITE", "SS")):
                sub_esp = Decimal("0")
                for ln in lineas:
                    desc_ln = str(ln.get("descripcion") or "")
                    if _match_producto_especial(r_pp, desc_ln, r_pp.categoria):
                        sub_esp += q2(
                            Decimal(str(ln.get("cantidad") or "0"))
                            * Decimal(str(ln.get("precio_unitario") or "0"))
                        )
                sub_match = sub_esp
            if sub_match <= Decimal("0"):
                motivos_fallo.append(
                    f"Ningún producto cotizado califica para marca «{r_pp.marca}» / "
                    f"envase «{r_pp.categoria}»."
                )
            base_calc = subtotal_lista if r_pp.aplica_a == "subtotal" else sub_match
            monto_desc = q2(base_calc * (pct / Decimal("100")))

        elif cod in vol_map:
            r_vol = vol_map[cod]
            es_acum = (r_vol.tipo_evaluacion or "orden").lower() == "acumulado"
            familia = "fidelidad" if es_acum else "volumen"
            componente = "volumen"
            pct = _pct_humano(r_vol.porcentaje)
            titulo = r_vol.descripcion.strip() or f"{'Fidelidad' if es_acum else 'Volumen'} {pct}%"
            condicion_txt = (
                f"Rango {r_vol.min_unidades:g}–{r_vol.max_unidades:g} {r_vol.unidad_medida} "
                f"({'acumulado ' + str(r_vol.dias_evaluacion) + 'd' if es_acum else 'en la orden'})"
            )
            if not _vigente(r_vol.vigencia_desde, r_vol.vigencia_hasta, r_vol.activo, hoy):
                motivos_fallo.append("La regla de volumen no está vigente o está inactiva.")
            if lista_ref and (
                not _match_lista(r_vol.listas_aplicables, lista_ref, valid_ves, valid_usd)
                or _excluida_por_lista(r_vol.listas_excluidas, lista_ref, valid_ves, valid_usd)
            ):
                motivos_fallo.append(
                    f"La regla {cod} no aplica a la lista comercial «{lista_precios_nombre}»."
                )
            cant_actual, sub_match = _medir_lineas(
                lineas,
                marca_regla=r_vol.marca,
                cat_regla=r_vol.categoria,
                unidad=r_vol.unidad_medida,
            )
            cant_eval = cant_actual
            if es_acum:
                desde_acum = (
                    hoy - timedelta(days=int(r_vol.dias_evaluacion))
                    if int(r_vol.dias_evaluacion or 0) > 0
                    else date(2000, 1, 1)
                )
                previo_acum = Decimal("0")
                for o_prev in hist["ordenes"]:
                    if o_prev.fecha < desde_acum:
                        continue
                    for l_prev in hist["lineas_por_orden"].get(o_prev.so_id, []):
                        if not _match_marca(r_vol.marca, l_prev.resolved_marca):
                            continue
                        env_p = tipo_envase_de_presentacion(l_prev.presentacion, l_prev.producto)
                        if not _match_categoria(r_vol.categoria, l_prev.categoria_madre, env_p):
                            continue
                        u_med = (r_vol.unidad_medida or "UNIDADES").upper()
                        if u_med in ("LITROS", "L"):
                            # Estima litros de línea histórica si están en catálogo
                            prod_cat = next(
                                (
                                    p
                                    for p in repo.all_catalogo()
                                    if p.codigo == l_prev.producto
                                    or p.producto_id == l_prev.producto
                                ),
                                None,
                            )
                            vol_p = prod_cat.volumen if prod_cat else Decimal("0")
                            previo_acum += q2(l_prev.cantidad * vol_p)
                        elif u_med in ("USD", "MONTO"):
                            previo_acum += q2(l_prev.cantidad * l_prev.precio_unitario)
                        else:
                            previo_acum += l_prev.cantidad
                cant_eval = q2(previo_acum + cant_actual)
            if sub_match <= Decimal("0"):
                motivos_fallo.append(
                    f"Ningún producto cotizado coincide con marca «{r_vol.marca}» / "
                    f"categoría «{r_vol.categoria}»."
                )
            elif cant_eval < r_vol.min_unidades:
                motivos_fallo.append(
                    f"No alcanza el umbral mínimo de {r_vol.min_unidades:g} {r_vol.unidad_medida} "
                    f"(evaluado: {cant_eval:g} {r_vol.unidad_medida})."
                )
            elif cant_eval > r_vol.max_unidades:
                motivos_fallo.append(
                    f"Supera el máximo del tramo ({r_vol.max_unidades:g} {r_vol.unidad_medida}); "
                    "selecciona el tramo de volumen correspondiente."
                )
            base_calc = subtotal_lista if r_vol.aplica_a == "subtotal" else sub_match
            monto_desc = q2(base_calc * (pct / Decimal("100")))

        elif cod in pr_map:
            r_pr = pr_map[cod]
            familia = "primera_compra"
            componente = "primera_compra"
            es_obsequio = (r_pr.tipo_beneficio or "").lower() == "producto"
            pct = Decimal("0") if es_obsequio else _pct_humano(r_pr.valor)
            titulo = r_pr.descripcion.strip() or (
                f"Promo Obsequio ({r_pr.productos})" if es_obsequio else f"Primera Compra {pct}%"
            )
            obsequio_detalle = (
                f"{r_pr.valor:g} unidad(es) de obsequio ({r_pr.productos})" if es_obsequio else ""
            )
            condicion_txt = (
                "Exclusivo primera compra"
                if r_pr.solo_primera_compra
                else "Promoción por compra mínima"
            )
            if not _vigente(r_pr.vigencia_desde, r_pr.vigencia_hasta, r_pr.activo, hoy):
                motivos_fallo.append("La promoción no está vigente o está inactiva.")
            if lista_ref and (
                not _match_lista(r_pr.listas_aplicables, lista_ref, valid_ves, valid_usd)
                or _excluida_por_lista(r_pr.listas_excluidas, lista_ref, valid_ves, valid_usd)
            ):
                motivos_fallo.append(
                    f"La promoción {cod} no aplica a la lista comercial «{lista_precios_nombre}»."
                )
            if r_pr.solo_primera_compra:
                if hist["ordenes_previas"] > 0:
                    motivos_fallo.append(
                        f"El cliente ya registra {hist['ordenes_previas']} orden(es) previa(s) "
                        f"en CxC (última: {hist['ultima_orden_so']}); "
                        "no califica como primera compra."
                    )
                elif es_primera_compra is False:
                    motivos_fallo.append(
                        "El cliente está marcado como cliente recurrente; esta regla es "
                        "exclusiva de primera compra."
                    )
            cant_calif, sub_match = _medir_lineas(
                lineas,
                marca_regla=r_pr.marca,
                cat_regla=r_pr.categoria or r_pr.categorias_aplica,
                unidad=r_pr.unidad_medida,
            )
            if r_pr.compra_minima > Decimal("0") and cant_calif < r_pr.compra_minima:
                motivos_fallo.append(
                    f"Compra mínima requerida: {r_pr.compra_minima:g} {r_pr.unidad_medida} "
                    f"(cotizado calificable: {cant_calif:g})."
                )
            if not es_obsequio:
                base_calc = subtotal_lista if not r_pr.categorias_descuento else sub_match
                if sub_match <= Decimal("0") and r_pr.marca != "*":
                    motivos_fallo.append(
                        f"Ningún producto cotizado califica para marca «{r_pr.marca}»."
                    )
                monto_desc = q2(base_calc * (pct / Decimal("100")))

        elif cod in rec_map:
            r_rec = rec_map[cod]
            familia = "recompra"
            componente = "recurrencia"
            pct = _pct_humano(r_rec.porcentaje)
            titulo = r_rec.descripcion.strip() or f"Recompra {pct}%"
            condicion_txt = (
                f"Tramo {r_rec.min_unidades:g}–{r_rec.max_unidades:g} {r_rec.unidad_medida} · "
                f"Orden anterior pagada ({r_rec.ventana_pago_tipo} + {r_rec.ventana_pago_dias}d)"
            )
            if not _vigente(r_rec.vigencia_desde, r_rec.vigencia_hasta, r_rec.activo, hoy):
                motivos_fallo.append("La regla de recompra no está vigente o está inactiva.")
            if lista_ref and (
                not _match_lista(r_rec.listas_aplicables, lista_ref, valid_ves, valid_usd)
                or _excluida_por_lista(r_rec.listas_excluidas, lista_ref, valid_ves, valid_usd)
            ):
                motivos_fallo.append(
                    f"La regla {cod} no aplica a la lista comercial «{lista_precios_nombre}»."
                )
            if hist["ordenes_previas"] == 0:
                motivos_fallo.append(
                    "El cliente no registra órdenes previas sincronizadas en CxC; "
                    "Recompra exige una compra anterior pagada."
                )
            else:
                ult_ord: OrdenVenta = hist["ultima_orden_objeto"]
                if not hist["ultima_orden_pagada"]:
                    motivos_fallo.append(
                        f"La orden anterior del cliente ({ult_ord.so_id}) no figura totalmente "
                        f"pagada en CxC (abonado ${hist['ultima_orden_abonado_usd']:.2f} de "
                        f"${hist['ultima_orden_objetivo_usd']:.2f})."
                    )
                else:
                    base_fecha = (
                        (
                            getattr(ult_ord, "fecha_vencimiento", None)
                            or (ult_ord.fecha + timedelta(days=int(ult_ord.dias_credito or 0)))
                        )
                        if r_rec.ventana_pago_tipo == "vencimiento"
                        else (ult_ord.fecha_entrega or ult_ord.fecha)
                    )
                    fecha_tope = fin_ventana_contado(
                        base_fecha, int(r_rec.ventana_pago_dias or 0), hist["feriados"]
                    )
                    if hoy > fecha_tope:
                        motivos_fallo.append(
                            f"La ventana de recompra de la orden anterior {ult_ord.so_id} "
                            f"venció el {fecha_tope.isoformat()}."
                        )

            cant_rec, sub_match = _medir_lineas(
                lineas,
                marca_regla=r_rec.marca,
                cat_regla=r_rec.categoria,
                unidad=r_rec.unidad_medida,
            )
            if sub_match <= Decimal("0"):
                motivos_fallo.append(
                    f"Ningún producto cotizado califica para Recompra "
                    f"({r_rec.marca} · {r_rec.categoria})."
                )
            elif cant_rec < r_rec.min_unidades:
                motivos_fallo.append(
                    f"Cantidad insuficiente para este tramo de Recompra: requiere mínimo "
                    f"{r_rec.min_unidades:g} {r_rec.unidad_medida} (actual: {cant_rec:g})."
                )
            elif cant_rec > r_rec.max_unidades:
                motivos_fallo.append(
                    f"Supera el máximo del tramo ({r_rec.max_unidades:g} {r_rec.unidad_medida}); "
                    "elige el siguiente tramo de Recompra."
                )
            base_calc = subtotal_lista if r_rec.aplica_a == "subtotal" else sub_match
            monto_desc = q2(base_calc * (pct / Decimal("100")))

        elif cod in dif_map:
            r_dif = dif_map[cod]
            familia = "diferencial"
            componente = "bcv_completo"
            es_fijo = r_dif.tipo_diferencial == "fijo_35_ves_usd"
            ultima_t = repo.last_serie_tasa()
            pct_bin = (
                q2((Decimal("1") - (ultima_t.tasa_bcv / ultima_t.tasa_binance)) * Decimal("100"))
                if (
                    ultima_t is not None
                    and ultima_t.tasa_bcv > Decimal("0")
                    and ultima_t.tasa_binance > ultima_t.tasa_bcv
                )
                else Decimal("0")
            )
            pct = _pct_humano(r_dif.porcentaje_fijo) if es_fijo else pct_bin
            titulo = r_dif.descripcion.strip() or f"Diferencial Cambiario {pct}%"
            condicion_txt = (
                "Sujeto a pago efectivo/divisas USD sobre lista comercial VES"
                if es_fijo
                else "Sujeto a pago en VES equiparado a tasa Binance"
            )
            if not _vigente(r_dif.vigencia_desde, r_dif.vigencia_hasta, r_dif.activo, hoy):
                motivos_fallo.append("La regla de diferencial cambiario no está vigente.")
            if lista_ref and (
                not _match_lista(r_dif.listas_aplicables, lista_ref, valid_ves, valid_usd)
                or _excluida_por_lista(r_dif.listas_excluidas, lista_ref, valid_ves, valid_usd)
            ):
                motivos_fallo.append(
                    f"La regla {cod} está excluida para la lista «{lista_precios_nombre}»."
                )
            if moneda_lista.upper() == "USD":
                motivos_fallo.append(
                    "El diferencial cambiario nunca aplica sobre listas en USD "
                    "(evita doble descuento)."
                )
            if es_fijo and moneda_pago_esperada and moneda_pago_esperada.strip().upper() == "VES":
                motivos_fallo.append(
                    "El diferencial fijo VES→USD (35%) requiere pago en divisas USD, no en VES."
                )
            if not es_fijo and hist["tiene_pagos_sin_aplicar"]:
                motivos_fallo.append(
                    "El cliente registra pagos sin aplicar/conciliar (pagos huérfanos) en CxC, "
                    "lo cual bloquea la regla Equiparar Binance."
                )
            _, sub_match = _medir_lineas(
                lineas, marca_regla=r_dif.marca, cat_regla=r_dif.categoria, unidad="USD"
            )
            base_calc = subtotal_lista if r_dif.aplica_a == "subtotal" else sub_match
            monto_desc = q2(base_calc * (pct / Decimal("100")))

        elif cod in prod_map:
            r_pr_sku = prod_map[cod]
            familia = "producto"
            componente = "producto"
            pct = _pct_humano(r_pr_sku.porcentaje)
            titulo = r_pr_sku.descripcion.strip() or f"Promo Producto {pct}%"
            condicion_txt = (
                f"Productos {r_pr_sku.productos} "
                f"(mín. {r_pr_sku.min_unidades:g} {r_pr_sku.unidad_medida})"
            )
            if not _vigente(r_pr_sku.vigencia_desde, r_pr_sku.vigencia_hasta, r_pr_sku.activo, hoy):
                motivos_fallo.append("La promoción de producto no está vigente.")
            if lista_ref and (
                not _match_lista(r_pr_sku.listas_aplicables, lista_ref, valid_ves, valid_usd)
                or _excluida_por_lista(r_pr_sku.listas_excluidas, lista_ref, valid_ves, valid_usd)
            ):
                motivos_fallo.append(
                    f"La promoción {cod} no aplica a la lista «{lista_precios_nombre}»."
                )
            cant_p, sub_match = _medir_lineas(
                lineas,
                marca_regla=r_pr_sku.marca,
                cat_regla=r_pr_sku.categoria,
                unidad=r_pr_sku.unidad_medida,
                productos_regla=r_pr_sku.productos,
            )
            if sub_match <= Decimal("0"):
                motivos_fallo.append(
                    f"Ningún producto cotizado coincide con los SKUs de la regla "
                    f"({r_pr_sku.productos})."
                )
            elif cant_p < r_pr_sku.min_unidades or cant_p > r_pr_sku.max_unidades:
                motivos_fallo.append(
                    f"Cantidad fuera del rango de la promoción ({r_pr_sku.min_unidades:g}–"
                    f"{r_pr_sku.max_unidades:g} {r_pr_sku.unidad_medida}; actual: {cant_p:g})."
                )
            base_calc = subtotal_lista if r_pr_sku.aplica_a == "subtotal" else sub_match
            monto_desc = q2(base_calc * (pct / Decimal("100")))

        else:
            motivos_fallo.append(f"El código de descuento «{cod}» no existe en el catálogo de CxC.")

        evaluados.append(
            {
                "codigo": cod,
                "familia": familia,
                "componente": componente,
                "titulo": titulo,
                "porcentaje": float(pct),
                "monto_estimado_usd": float(monto_desc),
                "es_obsequio": es_obsequio,
                "obsequio_detalle": obsequio_detalle,
                "condicion": condicion_txt,
                "cumple": len(motivos_fallo) == 0,
                "alerta": " ".join(motivos_fallo),
            }
        )

    # Resolver exclusiones mutuas configuradas en el motor (ej. Volumen <-> Recompra,
    # Primera Compra <-> Recompra, Pronto Pago VES <-> Diferencial Cambiario)
    exclusiones_activas: list[ExclusionRegla] = [
        ex for ex in repo.exclusiones() if getattr(ex, "activo", True)
    ]
    for ex in exclusiones_activas:
        comp_a = normalizar_componente_descuento(ex.regla_tipo_a)
        comp_b = normalizar_componente_descuento(ex.regla_tipo_b)
        items_a = [it for it in evaluados if it["componente"] == comp_a]
        items_b = [it for it in evaluados if it["componente"] == comp_b]
        if items_a and items_b:
            monto_a = sum(Decimal(str(x["monto_estimado_usd"])) for x in items_a)
            monto_b = sum(Decimal(str(x["monto_estimado_usd"])) for x in items_b)
            perdedores = items_b if monto_a >= monto_b else items_a
            ganadores = items_a if monto_a >= monto_b else items_b
            nombres_a = ", ".join(x["codigo"] for x in items_a)
            nombres_b = ", ".join(x["codigo"] for x in items_b)
            msg_ex = (
                f"Exclusión mutua del motor ({comp_a} ↔ {comp_b}): «{nombres_a}» y «{nombres_b}» "
                f"no pueden acumularse; prevalece «{ganadores[0]['codigo']}» por mayor beneficio."
            )
            for p_it in perdedores:
                p_it["cumple"] = False
                p_it["excluido_por"] = ganadores[0]["codigo"]
                p_it["alerta"] = f"{p_it['alerta']} {msg_ex}".strip()

    # Conflicto entre días de crédito solicitados y descuentos de Pronto Pago / Contado
    if dias_credito_solicitados is not None and dias_credito_solicitados > 0:
        for it in evaluados:
            if it["familia"] == "pronto_pago" or it["componente"] in ("contado", "contado_ves"):
                it["cumple"] = False
                msg_cred_pp = (
                    f"Conflicto de plazo: crédito de {dias_credito_solicitados} día(s) "
                    f"no es compatible con Pronto Pago / Contado."
                )
                it["alerta"] = f"{it['alerta']} {msg_cred_pp}".strip()

    # Evitar duplicar dos tramos de la misma familia (ej. 2 tramos de Recompra o Volumen)
    for fam_unica in ("recompra", "volumen", "diferencial", "pronto_pago"):
        misma_fam = [it for it in evaluados if it["familia"] == fam_unica]
        if len(misma_fam) > 1:
            misma_fam.sort(key=lambda x: float(x["monto_estimado_usd"]), reverse=True)
            for extra in misma_fam[1:]:
                msg_dup = (
                    f"Seleccionaste múltiples reglas de «{fam_unica}»; solo aplica un tramo "
                    f"(prevalece «{misma_fam[0]['codigo']}»)."
                )
                extra["cumple"] = False
                extra["excluido_por"] = misma_fam[0]["codigo"]
                extra["alerta"] = f"{extra['alerta']} {msg_dup}".strip()

    # Recolectar alertas y sumar descuentos efectivos en la cotización
    todas_alertas = list(alertas_globales)
    total_descuentos = Decimal("0")
    for it in evaluados:
        it["familia_label"] = _FAMILIA_LABELS.get(str(it.get("familia") or ""), "Descuento")
        if it["alerta"]:
            todas_alertas.append(f"[{it['codigo']}] {it['alerta']}")
        if not it.get("excluido_por") and not it["es_obsequio"]:
            # Proyecta el descuento elegido salvo exclusión, registrando alertas del motor.
            total_descuentos += Decimal(str(it["monto_estimado_usd"]))

    espec_evaluados: list[dict[str, Any]] = []
    for esp in espec_limpios:
        pct_e = Decimal(str(esp["porcentaje"]))
        m_e = (
            q2(subtotal_lista * pct_e / Decimal("100"))
            if pct_e > Decimal("0")
            else q2(Decimal(str(esp["monto_usd"])))
        )
        item_e = {
            **esp,
            "familia": "especial",
            "familia_label": _FAMILIA_LABELS["especial"],
            "monto_estimado_usd": float(m_e),
            "requiere_aprobacion": True,
        }
        espec_evaluados.append(item_e)
        total_descuentos += m_e
        todas_alertas.append(
            f"[ESPECIAL] {esp['motivo']} ({pct_e:g}% / -${m_e:.2f}): "
            "requiere aprobación administrativa en CxC."
        )

    if total_descuentos > subtotal_lista:
        total_descuentos = subtotal_lista
    total_descuentos = q2(total_descuentos)
    subtotal_neto = q2(subtotal_lista - total_descuentos)
    monto_iva = q2(subtotal_neto * IVA_VENEZUELA)
    total_con_iva = q2(subtotal_neto + monto_iva)
    total_con_iva_ves = q2(total_con_iva * tasa_bcv) if tasa_bcv is not None else None

    cumple_todo = (
        all(bool(it["cumple"]) for it in evaluados)
        and dias_credito_cumple
        and len(espec_evaluados) == 0
    )

    return {
        "lista_precios_id": lista_precios_id,
        "lista_precios_nombre": lista_precios_nombre,
        "moneda_lista": moneda_lista,
        "ruta_pago": ruta_pago,
        "ruta_pago_label": ruta_pago_label,
        "tasa_bcv": float(tasa_bcv) if tasa_bcv is not None else None,
        "subtotal_lista": float(subtotal_lista),
        "total_descuentos": float(total_descuentos),
        "subtotal_neto": float(subtotal_neto),
        "monto_iva": float(monto_iva),
        "total_con_iva": float(total_con_iva),
        "total_con_iva_ves": float(total_con_iva_ves) if total_con_iva_ves is not None else None,
        "total_litros": float(total_litros),
        "max_dias_credito_sugerido": max_dias_credito,
        "dias_credito_solicitados": dias_credito_solicitados,
        "dias_credito_cumple": dias_credito_cumple,
        "cumple_todo": cumple_todo,
        "requiere_aprobacion_manual": not cumple_todo,
        "descuentos": evaluados,
        "descuentos_especiales": espec_evaluados,
        "alertas": todas_alertas,
        "cliente_cxc": {
            "cliente_encontrado": hist["cliente_encontrado"],
            "cliente_id": hist["cliente_id"],
            "ordenes_previas": hist["ordenes_previas"],
            "ultima_orden_so": hist["ultima_orden_so"],
            "ultima_orden_pagada": hist["ultima_orden_pagada"],
            "ordenes_vencidas": hist["ordenes_vencidas"],
            "tiene_pagos_sin_aplicar": hist["tiene_pagos_sin_aplicar"],
        },
        "frescura_cxc": frescura,
    }
