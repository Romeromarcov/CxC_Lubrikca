"""Sincronización Odoo → Documentos Comerciales CxC y bloqueo tras confirmación.

Cuando un pedido enviado desde CxC a Odoo es editado en Odoo (cantidades o
lista de precios), ajusta las líneas y totales del documento en CxC para
reflejar exactamente lo que está en Odoo. Una vez que el pedido queda en estado
confirmado en Odoo (``sale`` o ``done``), marca el metadata ``sync_odoo`` y
bloquea su edición en CxC.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from cxc.decimal_utils import q2
from cxc.engine.rutas_pago import ruta_de_lista
from cxc.models import LineaOrden, OrdenVenta, Producto
from cxc.repositories import Repository

from .evaluador_descuentos import IVA_VENEZUELA
from .modelos import ENVIADO_ODOO, PENDIENTE_APROBACION, RECHAZADO, DocumentoComercial
from .repositorio import RepoDocumentos

ESTADOS_ODOO_CONFIRMADOS = frozenset({"sale", "done"})


def _catalogo_por_id(
    repo_cxc: Repository, catalogo: Sequence[Producto] | None = None
) -> dict[str, Producto]:
    if catalogo is not None:
        items = list(catalogo)
    else:
        try:
            items = list(repo_cxc.all_catalogo())
        except NotImplementedError:
            items = []
    return {str(p.producto_id): p for p in items if p.producto_id}


def _huella_lineas_doc(doc: DocumentoComercial) -> list[tuple[str, Decimal, Decimal, Decimal]]:
    return sorted(
        (
            str(ln.producto_id or "").strip(),
            q2(ln.cantidad),
            q2(ln.precio_unitario),
            q2(ln.descuento_pct),
        )
        for ln in doc.lineas
    )


def _huella_lineas_odoo(
    lineas_odoo: Sequence[LineaOrden],
) -> list[tuple[str, Decimal, Decimal, Decimal]]:
    return sorted(
        (
            str(ln.producto or "").strip(),
            q2(ln.cantidad),
            q2(ln.precio_unitario),
            q2(ln.descuento),
        )
        for ln in lineas_odoo
    )


def _construir_lineas_desde_odoo(
    lineas_odoo: Sequence[LineaOrden], prod_map: Mapping[str, Producto]
) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    salida: list[dict[str, Any]] = []
    subtotal_sum = Decimal("0.00")
    litros_sum = Decimal("0.00")

    for idx, ln in enumerate(lineas_odoo, start=1):
        pid = str(ln.producto or "").strip()
        prod = prod_map.get(pid)
        litros_u = Decimal(str(prod.volumen)) if prod else Decimal("0")
        cant = q2(ln.cantidad)
        precio_u = q2(ln.precio_unitario)
        desc_pct = q2(ln.descuento)
        sub_ln = (
            q2(ln.subtotal)
            if ln.subtotal and ln.subtotal > Decimal("0")
            else q2(cant * precio_u * (Decimal("1") - desc_pct / Decimal("100")))
        )
        subtotal_sum += sub_ln
        litros_sum += (litros_u * cant).quantize(Decimal("0.01"))
        salida.append(
            {
                "orden": idx,
                "producto_id": pid,
                "codigo": prod.codigo if prod else "",
                "descripcion": ((prod.nombre if prod else "") or ln.nombre or f"Producto #{pid}"),
                "presentacion": ln.presentacion_odoo or "",
                "litros_unidad": litros_u,
                "cantidad": cant,
                "precio_unitario": precio_u,
                "descuento_pct": desc_pct,
                "subtotal": sub_ln,
            }
        )
    return salida, q2(subtotal_sum), litros_sum.quantize(Decimal("0.01"))


def orden_confirmada_en_odoo(
    orden: OrdenVenta | None, doc: DocumentoComercial | None = None
) -> bool:
    """True si la orden espejo está confirmada (``sale``/``done``) o marcada en sync_odoo."""
    if orden is not None:
        return (orden.estado_orden or "").strip().lower() in ESTADOS_ODOO_CONFIRMADOS
    if doc is not None and isinstance(doc.validacion_motor, dict):
        sync_meta = doc.validacion_motor.get("sync_odoo")
        if isinstance(sync_meta, dict):
            return bool(sync_meta.get("confirmado_odoo"))
    return False


def sincronizar_documentos_desde_odoo(
    repo_cxc: Repository,
    repo_docs: RepoDocumentos,
    *,
    catalogo: Sequence[Producto] | None = None,
    pricelist_mapeo: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Refleja cambios de cantidades, lista de precios y confirmación de Odoo sobre docs CxC."""
    docs: list[DocumentoComercial] = []
    offset = 0
    while True:
        chunk, tot, _res = repo_docs.listar(limit=500, offset=offset)
        docs.extend(chunk)
        offset += len(chunk)
        if not chunk or offset >= tot:
            break
    prod_map: dict[str, Producto] | None = None
    conteos = {"revisados": 0, "ajustados": 0, "confirmados": 0, "cancelados": 0}

    for doc in docs:
        so_name = (doc.odoo_order_name or "").strip()
        if not so_name:
            continue
        conteos["revisados"] += 1
        orden = repo_cxc.get_orden(so_name)
        if orden is None:
            continue

        est_odoo = (orden.estado_orden or "sale").strip().lower()
        confirmado = est_odoo in ESTADOS_ODOO_CONFIRMADOS

        if est_odoo == "cancel":
            if doc.estado != RECHAZADO:
                vm = dict(doc.validacion_motor or {})
                vm["sync_odoo"] = {
                    "estado_odoo": "cancel",
                    "confirmado_odoo": False,
                    "ajustado_desde_odoo": True,
                    "sincronizado_en": datetime.now().isoformat(timespec="seconds"),
                }
                repo_docs.actualizar(
                    doc.documento_id,
                    {
                        "estado": RECHAZADO,
                        "error_odoo": "Cancelado en Odoo.",
                        "validacion_motor": vm,
                    },
                )
                conteos["cancelados"] += 1
            continue

        lineas_odoo = repo_cxc.lineas_de_orden(so_name)
        lista_odoo_id = str(orden.lista_precios or "").strip()
        lista_cambio = bool(
            lista_odoo_id and lista_odoo_id != str(doc.lista_precios_id or "").strip()
        )
        lineas_cambio = bool(lineas_odoo) and (
            _huella_lineas_odoo(lineas_odoo) != _huella_lineas_doc(doc)
        )
        sub_doc = q2(doc.subtotal)
        desc_cxc_doc = min(sub_doc, q2(doc.total_descuentos))
        tot_bruto_sin_desc = q2(sub_doc * (Decimal("1") + IVA_VENEZUELA))
        solo_diferencia_desc_cxc = (
            not lineas_cambio
            and not lista_cambio
            and desc_cxc_doc > Decimal("0")
            and orden.monto_total > Decimal("0")
            and abs(q2(orden.monto_total) - tot_bruto_sin_desc) <= Decimal("0.05")
        )
        total_cambio = (
            bool(orden.monto_total > Decimal("0"))
            and q2(orden.monto_total) != q2(doc.total_con_iva)
            and not solo_diferencia_desc_cxc
        )
        dias_odoo = int(getattr(orden, "dias_credito", 0) or 0)
        dias_cambio = dias_odoo > 0 and dias_odoo != int(getattr(doc, "dias_credito", 0) or 0)

        vm_prev = dict(doc.validacion_motor or {})
        raw_sync = vm_prev.get("sync_odoo")
        sync_prev: dict[str, Any] = raw_sync if isinstance(raw_sync, dict) else {}
        estado_prev_confirmado = bool(sync_prev.get("confirmado_odoo"))
        est_prev_odoo = str(sync_prev.get("estado_odoo") or "")

        necesita_update = (
            lista_cambio
            or lineas_cambio
            or total_cambio
            or dias_cambio
            or (confirmado and not estado_prev_confirmado)
            or (est_odoo != est_prev_odoo)
            or (confirmado and doc.estado in (PENDIENTE_APROBACION,))
        )
        if not necesita_update:
            continue

        cambios_cab: dict[str, Any] = {}
        nuevas_lineas: list[dict[str, Any]] | None = None

        if dias_cambio:
            cambios_cab["dias_credito"] = dias_odoo

        if lista_cambio:
            cambios_cab["lista_precios_id"] = lista_odoo_id
            info_lista = (pricelist_mapeo or {}).get(lista_odoo_id)
            if isinstance(info_lista, Mapping):
                nombre_lista = str(info_lista.get("name") or info_lista.get("nombre") or "").strip()
                mon_lista = str(info_lista.get("moneda") or "").strip().upper()
            else:
                nombre_lista = str(info_lista or "").strip()
                mon_lista = ""
            if nombre_lista:
                cambios_cab["lista_precios_nombre"] = nombre_lista
            if mon_lista in ("USD", "VES"):
                cambios_cab["moneda_lista"] = mon_lista
            cambios_cab["ruta_pago"] = ruta_de_lista(
                lista_odoo_id,
                pricelist_mapeo if isinstance(pricelist_mapeo, Mapping) else None,
                lista_nombre=nombre_lista,
                moneda_lista=mon_lista,
            )

        if lineas_cambio:
            if prod_map is None:
                prod_map = _catalogo_por_id(repo_cxc, catalogo)
            nuevas_lineas, sub_odoo, lit_odoo = _construir_lineas_desde_odoo(lineas_odoo, prod_map)
            desc_cxc = min(sub_odoo, q2(doc.total_descuentos))
            sub_neto = q2(sub_odoo - desc_cxc)
            if desc_cxc > Decimal("0"):
                iva_odoo = q2(sub_neto * IVA_VENEZUELA)
                tot_odoo = q2(sub_neto + iva_odoo)
            else:
                tot_odoo = (
                    q2(orden.monto_total)
                    if orden.monto_total > Decimal("0")
                    else q2(sub_odoo * (Decimal("1") + IVA_VENEZUELA))
                )
                iva_odoo = max(Decimal("0.00"), q2(tot_odoo - sub_odoo))
            cambios_cab.update(
                {
                    "subtotal": sub_odoo,
                    "subtotal_neto": sub_neto,
                    "monto_iva": iva_odoo,
                    "total_con_iva": tot_odoo,
                    "total_litros": lit_odoo,
                }
            )
        elif total_cambio:
            tot_odoo = q2(orden.monto_total)
            sub_base = (
                q2(doc.subtotal)
                if doc.subtotal > Decimal("0")
                else q2(tot_odoo / (Decimal("1") + IVA_VENEZUELA))
            )
            if sub_base > tot_odoo:
                sub_base = q2(tot_odoo / (Decimal("1") + IVA_VENEZUELA))
            desc_cxc = min(sub_base, q2(doc.total_descuentos))
            sub_neto = q2(sub_base - desc_cxc)
            iva_odoo = max(Decimal("0.00"), q2(tot_odoo - sub_neto))
            cambios_cab.update(
                {
                    "subtotal": sub_base,
                    "subtotal_neto": sub_neto,
                    "monto_iva": iva_odoo,
                    "total_con_iva": tot_odoo,
                }
            )

        hubo_ajuste = bool(lista_cambio or lineas_cambio or total_cambio or dias_cambio)
        if confirmado and doc.estado != ENVIADO_ODOO:
            cambios_cab["estado"] = ENVIADO_ODOO
            cambios_cab["tipo"] = "pedido"

        vm_prev["sync_odoo"] = {
            "estado_odoo": est_odoo,
            "confirmado_odoo": confirmado,
            "ajustado_desde_odoo": bool(hubo_ajuste or sync_prev.get("ajustado_desde_odoo")),
            "sincronizado_en": datetime.now().isoformat(timespec="seconds"),
        }
        cambios_cab["validacion_motor"] = vm_prev

        repo_docs.actualizar(doc.documento_id, cambios_cab, nuevas_lineas)
        if hubo_ajuste:
            conteos["ajustados"] += 1
        if confirmado and not estado_prev_confirmado:
            conteos["confirmados"] += 1

    return conteos
