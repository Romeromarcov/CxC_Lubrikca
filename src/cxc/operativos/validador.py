"""Validación en vivo de Pagos, Facturas de Proveedor, Pagos a Proveedor y Gastos.

Consulta el repositorio CxC (órdenes abiertas, pagos registrados, tasas BCV/Binance),
los documentos operativos previos (detección temprana de duplicados), el directorio de
contactos y el plan de cuentas de Odoo para devolver alertas accionables y sugerencias
tanto al bot de Telegram como al panel de CxC antes de enviar a Odoo.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from cxc.bancos.diarios import DIARIOS
from cxc.capturas.modelos import es_efectivo, normalizar_nombre, normalizar_rif
from cxc.capturas.servicio import _Incompleto, resolver_diario
from cxc.odoo.directorio import (
    ContactoOdoo,
    CuentaContable,
    sugerir_para_concepto,
)
from cxc.repositories import Repository

from .modelos import (
    CATEGORIAS_GASTO,
    TIPO_FACTURA_PROVEEDOR,
    TIPO_GASTO,
    TIPO_PAGO,
    TIPO_PAGO_PROVEEDOR,
    extraer_campos_canonicos,
    validar_campos_operativo,
)


def listar_diarios_bancarios(diarios_extra: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Lista los diarios bancarios/caja configurados en CxC + env ``CAPTURAS_DIARIOS``."""
    out: list[dict[str, Any]] = []
    vistos_ids: set[int] = set()
    for clave, d in DIARIOS.items():
        jid = d.get("journal_id")
        if jid is None:
            continue
        jid_int = int(jid)
        vistos_ids.add(jid_int)
        out.append(
            {
                "clave": clave,
                "codigo": d["codigo"],
                "id": jid_int,
                "nombre": d["nombre"],
                "moneda": d["moneda"],
                "tipo": "banco",
            }
        )
    for k, jid in sorted((diarios_extra or {}).items()):
        jid_int = int(jid)
        if jid_int in vistos_ids:
            continue
        vistos_ids.add(jid_int)
        kl = k.lower()
        mon = "USD" if "usd" in kl else ("VES" if "ves" in kl or "bs" in kl else "")
        out.append(
            {
                "clave": k,
                "codigo": k.upper()[:10],
                "id": jid_int,
                "nombre": k.replace("_", " ").title(),
                "moneda": mon,
                "tipo": "efectivo" if "efectivo" in kl or "caja" in kl else "extra",
            }
        )
    return out


def resolver_diario_opcional(
    datos: dict[str, Any], diarios_extra: dict[str, int] | None = None
) -> dict[str, Any] | None:
    """Resuelve ``{id, nombre, moneda, tipo}`` para un pago sin lanzar excepciones."""
    catalogo = listar_diarios_bancarios(diarios_extra)
    por_id = {int(d["id"]): d for d in catalogo}
    diario_id = datos.get("diario_id")
    if diario_id is not None and str(diario_id).isdigit():
        found = por_id.get(int(str(diario_id)))
        if found:
            return found
        return {
            "id": int(str(diario_id)),
            "nombre": str(datos.get("diario_nombre") or f"Diario #{diario_id}"),
            "moneda": str(datos.get("moneda") or ""),
            "tipo": "manual",
        }
    try:
        resolved_id = resolver_diario(
            {
                "moneda": datos.get("moneda") or "VES",
                "banco": datos.get("banco") or "",
                "metodo_pago": datos.get("metodo_pago") or "",
            },
            diarios_extra or {},
        )
        found = por_id.get(int(resolved_id))
        if found:
            return found
        return {
            "id": int(resolved_id),
            "nombre": str(datos.get("banco") or f"Diario #{resolved_id}"),
            "moneda": str(datos.get("moneda") or ""),
            "tipo": "env",
        }
    except _Incompleto:
        return None


def _buscar_contraparte(
    tipo: str,
    datos: dict[str, Any],
    contactos: Sequence[ContactoOdoo],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Busca cliente/proveedor en ``contactos`` por id, RIF o nombre."""
    if not contactos:
        return None, []
    pid = datos.get("partner_odoo_id")
    if pid is not None:
        for c in contactos:
            if c.id == int(pid):
                return c.a_dict(), []

    rif = str(datos.get("rif") or "").strip()
    if rif:
        try:
            rif_norm = normalizar_rif(rif).replace("-", "")
        except ValueError:
            rif_norm = rif.upper().replace("-", "").replace(" ", "")
        matches_rif = [
            c
            for c in contactos
            if c.rif and c.rif.upper().replace("-", "").replace(" ", "") == rif_norm
        ]
        if len(matches_rif) == 1:
            return matches_rif[0].a_dict(), []
        if len(matches_rif) > 1:
            return None, [c.a_dict() for c in matches_rif[:5]]

    nombre = str(datos.get("contraparte_nombre") or "").strip()
    if not nombre:
        return None, []

    nombre_norm = normalizar_nombre(nombre)
    es_cliente = tipo == TIPO_PAGO
    relevantes = [
        c
        for c in contactos
        if (
            c.clasificacion in ("cliente", "ambos", "prospecto")
            if es_cliente
            else c.clasificacion in ("proveedor", "ambos")
        )
    ]
    # Intentar primero sobre el rol esperado y luego sobre todo el directorio
    for pool in (relevantes, contactos):
        exactos = [c for c in pool if normalizar_nombre(c.nombre) == nombre_norm]
        if len(exactos) == 1:
            return exactos[0].a_dict(), []
        if len(exactos) > 1:
            return None, [c.a_dict() for c in exactos[:5]]

        parciales = [c for c in pool if nombre_norm and nombre_norm in normalizar_nombre(c.nombre)]
        if len(parciales) == 1:
            return parciales[0].a_dict(), []
        if len(parciales) > 1:
            return None, [c.a_dict() for c in parciales[:5]]

    return None, []


def _equivalente_usd(
    moneda: str, monto: Decimal | None, repo_cxc: Repository | None
) -> dict[str, Any] | None:
    if repo_cxc is None or monto is None or monto <= 0 or moneda != "VES":
        return None
    try:
        ultima = repo_cxc.last_serie_tasa()
    except Exception:
        return None
    if ultima is None:
        return None
    res: dict[str, Any] = {}
    if getattr(ultima, "tasa_bcv", None) and ultima.tasa_bcv > 0:
        res["tasa_bcv"] = float(ultima.tasa_bcv)
        res["usd_bcv"] = round(float(monto / ultima.tasa_bcv), 2)
    if getattr(ultima, "tasa_binance", None) and ultima.tasa_binance > 0:
        res["tasa_binance"] = float(ultima.tasa_binance)
        res["usd_binance"] = round(float(monto / ultima.tasa_binance), 2)
    return res or None


def _ordenes_abiertas_cliente(
    partner_id: int | None,
    nombre_cliente: str,
    pedido_ref: str,
    repo_cxc: Repository | None,
) -> list[dict[str, Any]]:
    if repo_cxc is None:
        return []
    try:
        clientes = repo_cxc.all_clientes()
        ordenes = repo_cxc.all_ordenes()
    except Exception:
        return []

    cid_match: str | None = str(partner_id) if partner_id is not None else None
    if cid_match is None and pedido_ref:
        so = next(
            (o for o in ordenes if o.so_id.strip().upper() == pedido_ref.strip().upper()),
            None,
        )
        if so:
            cid_match = so.cliente_id
    if cid_match is None and nombre_cliente:
        nn = normalizar_nombre(nombre_cliente)
        cli = next((c for c in clientes if normalizar_nombre(c.nombre) == nn), None)
        if cli:
            cid_match = cli.cliente_id
    if cid_match is None:
        return []

    abiertas = [
        o
        for o in ordenes
        if str(o.cliente_id) == str(cid_match)
        and str(o.estado_orden or "").lower() not in ("cancel", "draft", "sent")
        and not o.facturada
    ]
    abiertas.sort(key=lambda o: o.fecha, reverse=True)
    return [
        {
            "so_id": o.so_id,
            "fecha": o.fecha.isoformat(),
            "monto_total": float(o.monto_total),
            "lista_precios": o.lista_precios,
            "facturada": bool(o.facturada),
        }
        for o in abiertas[:5]
    ]


def _buscar_duplicados(
    tipo: str,
    datos: dict[str, Any],
    *,
    excluir_id: int | None,
    repo_operativos: Any,
    repo_cxc: Repository | None,
) -> list[dict[str, Any]]:
    dups: list[dict[str, Any]] = []
    ref = str(datos.get("referencia") or "").strip()
    num_doc = str(datos.get("numero_documento") or "").strip()

    if repo_operativos is not None:
        if (
            tipo in (TIPO_PAGO, TIPO_PAGO_PROVEEDOR)
            and ref
            and not es_efectivo(datos.get("metodo_pago"))
        ):
            prevs = repo_operativos.buscar_duplicados_referencia(tipo, ref, excluir_id=excluir_id)
            for p in prevs:
                dups.append(
                    {
                        "fuente": "cxc_operativos",
                        "numero": p.numero,
                        "estado": p.estado,
                        "detalle": (
                            f"Posible referencia duplicada con {p.numero} "
                            f"({p.contraparte_nombre or 'sin contraparte'}, "
                            f"{p.monto} {p.moneda}, {p.estado})"
                        ),
                    }
                )
        if tipo in (TIPO_FACTURA_PROVEEDOR, TIPO_GASTO) and num_doc:
            prevs_f = repo_operativos.buscar_duplicados_factura(
                datos.get("contraparte_nombre") or "",
                datos.get("rif") or "",
                num_doc,
                excluir_id=excluir_id,
            )
            for pf in prevs_f:
                dups.append(
                    {
                        "fuente": "cxc_operativos",
                        "numero": pf.numero,
                        "estado": pf.estado,
                        "detalle": (
                            f"La factura/documento {num_doc} ya existe en CxC "
                            f"como {pf.numero} ({pf.estado})."
                        ),
                    }
                )

    if tipo == TIPO_PAGO and repo_cxc is not None and ref and datos.get("monto"):
        try:
            pagos_cxc = repo_cxc.all_pagos_full()
            ref_low = ref.lower()
            for row in pagos_cxc:
                ref_row = str(row.get("referencia") or row.get("memo") or "").strip().lower()
                if ref_row and ref_low in ref_row:
                    dups.append(
                        {
                            "fuente": "pagos_odoo",
                            "numero": str(row.get("pago_id") or ""),
                            "estado": "EN_ODOO",
                            "detalle": (
                                f"Referencia «{ref}» coincide con pago ya sincronizado en Odoo "
                                f"({row.get('pago_id') or row.get('cliente') or 'pago existente'})."
                            ),
                        }
                    )
                    break
        except Exception:
            pass

    return dups


def validar_documento_operativo(
    tipo: str,
    raw_datos: dict[str, Any],
    *,
    excluir_id: int | None = None,
    repo_operativos: Any = None,
    repo_cxc: Repository | None = None,
    contactos_odoo: Sequence[ContactoOdoo] = (),
    cuentas_odoo: Sequence[CuentaContable] = (),
    diarios_extra: dict[str, int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normaliza ``raw_datos`` y ejecuta validación completa para Bot y Web CxC.

    Devuelve ``(datos_normalizados, validacion)``.
    """
    datos = extraer_campos_canonicos(tipo, raw_datos)
    faltantes = validar_campos_operativo(tipo, datos)
    alertas: list[str] = []

    contraparte, candidatos = _buscar_contraparte(tipo, datos, contactos_odoo)
    creara_proveedor = False

    if tipo == TIPO_PAGO:
        if not contraparte and candidatos:
            alertas.append(
                f"Hay {len(candidatos)} clientes coincidentes en Odoo; selecciona el correcto."
            )
        elif not contraparte and not datos.get("pedido_ref") and contactos_odoo:
            alertas.append(
                "Cliente aún no identificado exactamente en el directorio de Odoo "
                "(indica RIF, pedido o selecciona al aprobar)."
            )
    elif tipo == TIPO_PAGO_PROVEEDOR:
        if not contraparte and candidatos:
            alertas.append(
                f"Hay {len(candidatos)} proveedores coincidentes en Odoo; selecciona el correcto."
            )
        elif not contraparte and contactos_odoo:
            alertas.append(
                "Proveedor aún no identificado exactamente en Odoo "
                "(se requiere proveedor existente para registrar el pago)."
            )
    elif tipo in (TIPO_FACTURA_PROVEEDOR, TIPO_GASTO):
        if (
            not contraparte
            and not candidatos
            and (datos.get("contraparte_nombre") or datos.get("rif"))
        ):
            creara_proveedor = True
            alertas.append(
                "Proveedor nuevo en Odoo: se creará automáticamente al autorizar el documento."
            )
        elif candidatos:
            alertas.append(
                f"Hay {len(candidatos)} proveedores similares en Odoo; verifica antes de aprobar."
            )

    diario_sug: dict[str, Any] | None = None
    if tipo in (TIPO_PAGO, TIPO_PAGO_PROVEEDOR):
        diario_sug = resolver_diario_opcional(datos, diarios_extra)
        if diario_sug is None:
            alertas.append(
                "Banco / diario no resuelto automáticamente: selecciona el diario bancario "
                "o caja antes de aprobar."
            )
        if es_efectivo(datos.get("metodo_pago")):
            regla_odoo = (
                "Efectivo: en Odoo se enviará con campo «memo» vacío "
                "y detalle completo en el chatter."
            )
        else:
            regla_odoo = (
                "Transferencia / digital: en Odoo el campo «memo» llevará SOLO la referencia "
                "y el detalle completo irá al chatter."
            )
    else:
        if tipo == TIPO_GASTO and datos.get("sin_factura"):
            regla_odoo = (
                "Gasto sin factura: irá al diario de compras VES por defecto "
                "(o el diario elegido), sin impuestos, ref S/N-GAS-XXXXX y nota completa "
                "en narration + chatter."
            )
        else:
            regla_odoo = (
                "Compras CxP: irá al diario de compras VES por defecto (o el diario elegido), "
                "sin impuestos y detalle en narration + chatter."
            )

    # Validación matemática base + IVA vs monto total
    if (
        tipo in (TIPO_FACTURA_PROVEEDOR, TIPO_GASTO)
        and datos.get("base_imponible") is not None
        and datos.get("iva") is not None
        and datos.get("monto") is not None
    ):
        suma = Decimal(datos["base_imponible"]) + Decimal(datos["iva"])
        total = Decimal(datos["monto"])
        if abs(suma - total) > Decimal("0.10"):
            alertas.append(
                f"Base ({datos['base_imponible']}) + IVA ({datos['iva']}) = {suma} "
                f"difiere del total ({total})."
            )

    # Sugerencia de cuenta contable desde Odoo (account.account) para factura_proveedor / gasto
    cuenta_sugerida: dict[str, Any] | None = None
    categoria_sugerida = datos.get("categoria") or ""
    alternativas: list[dict[str, Any]] = []
    if tipo in (TIPO_FACTURA_PROVEEDOR, TIPO_GASTO) and cuentas_odoo:
        concepto_eval = " ".join(
            x
            for x in (
                datos.get("concepto"),
                datos.get("categoria"),
                datos.get("contraparte_nombre"),
            )
            if x
        )
        sug = sugerir_para_concepto(concepto_eval or tipo, cuentas_odoo)
        cuenta_sugerida = sug.get("cuenta_sugerida")
        alternativas = list(sug.get("alternativas") or [])
        if not categoria_sugerida and sug.get("categoria_sugerida") in CATEGORIAS_GASTO:
            categoria_sugerida = str(sug["categoria_sugerida"])

    # Equivalente USD para VES
    equiv_usd = _equivalente_usd(str(datos.get("moneda") or ""), datos.get("monto"), repo_cxc)

    # Órdenes abiertas si es pago recibido de cliente
    ordenes_abiertas: list[dict[str, Any]] = []
    if tipo == TIPO_PAGO:
        pid_res = contraparte["id"] if contraparte else datos.get("partner_odoo_id")
        ordenes_abiertas = _ordenes_abiertas_cliente(
            pid_res,
            datos.get("contraparte_nombre") or "",
            datos.get("pedido_ref") or "",
            repo_cxc,
        )

    duplicados = _buscar_duplicados(
        tipo,
        datos,
        excluir_id=excluir_id,
        repo_operativos=repo_operativos,
        repo_cxc=repo_cxc,
    )
    for dup in duplicados:
        alertas.append(dup["detalle"])

    validacion = {
        "ok": not faltantes and not duplicados,
        "faltantes": faltantes,
        "alertas": alertas,
        "contraparte_resuelta": contraparte,
        "contraparte_candidatos": candidatos,
        "creara_proveedor_nuevo": creara_proveedor,
        "diario_sugerido": diario_sug,
        "cuenta_sugerida": cuenta_sugerida,
        "categoria_sugerida": categoria_sugerida,
        "cuentas_alternativas": alternativas,
        "equivalente_usd": equiv_usd,
        "ordenes_abiertas": ordenes_abiertas,
        "posibles_duplicados": duplicados,
        "regla_odoo": regla_odoo,
    }
    return datos, validacion
