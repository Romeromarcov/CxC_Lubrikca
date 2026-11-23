"""Servicios de sincronización delta y push idempotente para App Móvil Offline (/api/v1/sync/*)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine

from cxc.capturas.modelos import CapturaInvalida
from cxc.contactos.modelos import ContactoDuplicadoError, ContactoInvalido
from cxc.contactos.repositorio import RepoContactosSQL
from cxc.contactos.servicio import ServicioContactos
from cxc.decimal_utils import q2
from cxc.documentos.modelos import BORRADOR, COTIZACION, ENVIADO_ODOO, PEDIDO, DocumentoInvalido
from cxc.documentos.politica_ventas import cargar_politica_global, listar_overrides_clientes
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.documentos.servicio import ServicioDocumentos
from cxc.engine.rutas_pago import RUTA_BCV, RUTA_BINANCE, normalizar_ruta, ruta_de_lista
from cxc.engine.saldos_propios import ESTADOS_ORDEN_CONFIRMADA, calcular_saldos_propios
from cxc.operativos.cuentas_receptoras import RepoCuentasReceptorasSQL
from cxc.operativos.lotes_efectivo import RepoLotesEfectivo
from cxc.operativos.modelos import OperativoInvalido
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import RepoCuentasTercerosSQL
from cxc.permisos import tiene
from cxc.repositories import Repository

from .repositorio import ApiV1Conflicto, RepoApiV1SQL

_PERMISOS_CARTERA_GLOBAL = (
    "compras",
    "auditoria.decidir",
    "cobranza.aprobar_pagos",
    "ventas.aprobar_pedidos",
    "bancos.ver",
)


def _es_alcance_global(user: dict[str, Any]) -> bool:
    return user.get("rol") == "admin" or any(tiene(user, p) for p in _PERMISOS_CARTERA_GLOBAL)


def _tasas_snapshot(repo: Repository) -> dict[str, Any]:
    ultima = repo.last_serie_tasa()
    if ultima is None:
        return {
            "tasa_bcv": 0.0,
            "tasa_binance": 0.0,
            "tasa_bcv_euro": None,
            "timestamp": None,
        }
    return {
        "tasa_bcv": float(ultima.tasa_bcv),
        "tasa_binance": float(ultima.tasa_binance),
        "tasa_bcv_euro": (
            float(ultima.tasa_bcv_euro) if ultima.tasa_bcv_euro is not None else None
        ),
        "tasa_binance_manana": (
            float(ultima.tasa_binance_manana) if ultima.tasa_binance_manana is not None else None
        ),
        "tasa_binance_tarde": (
            float(ultima.tasa_binance_tarde) if ultima.tasa_binance_tarde is not None else None
        ),
        "tasa_binance_diario": (
            float(ultima.tasa_binance_diario) if ultima.tasa_binance_diario is not None else None
        ),
        "timestamp": ultima.timestamp.isoformat(timespec="seconds"),
    }


def _naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _contactos_maestro_para_sync(
    engine: Engine | None,
    *,
    cursor_dt: datetime | None = None,
) -> list[dict[str, Any]]:
    if engine is None:
        return []
    cursor_norm = _naive_utc(cursor_dt)
    salida: list[dict[str, Any]] = []
    for c in RepoContactosSQL(engine).listar_activos():
        act_norm = _naive_utc(c.actualizado_en)
        if cursor_norm is not None and act_norm is not None and act_norm < cursor_norm:
            continue
        salida.append(
            {
                "contacto_id": c.contacto_id,
                "odoo_partner_id": c.odoo_partner_id,
                "rol": c.rol,
                "nombre": c.nombre,
                "nombre_normalizado": c.nombre_normalizado,
                "rif": c.rif,
                "rif_normalizado": c.rif_normalizado,
                "telefono": c.telefono,
                "vendedor_email": c.vendedor_email,
                "actualizado_en": c.actualizado_en.isoformat() if c.actualizado_en else None,
            }
        )
    return salida


def _cargar_mapeo_listas(repo: Repository) -> dict[str, Any]:
    try:
        raw_m = repo.get_config("pricelist_mapeo_unificado")
        if raw_m and isinstance(raw_m, str):
            parsed = json.loads(raw_m)
            if isinstance(parsed, dict):
                return parsed
    except Exception:
        pass
    return {}


def _ordenes_pendientes_fifo_por_cliente(
    repo: Repository,
    *,
    clientes_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Calcula obligaciones pendientes en USD por cliente y ruta ordenadas FIFO."""
    mapeo = _cargar_mapeo_listas(repo)
    credito_por_ruta: dict[str, dict[str, Decimal]] = {}
    for c in clientes_rows:
        cid = str(c.get("cliente_id") or "")
        if not cid:
            continue
        credito_por_ruta[cid] = {
            RUTA_BCV: Decimal(str(c.get("pagos_bcv_usd") or 0))
            + Decimal(str(c.get("descuentos_confirmados_usd") or 0)),
            RUTA_BINANCE: Decimal(str(c.get("pagos_binance_usd") or 0)),
        }

    ordenes_por_cli: dict[str, list[Any]] = {}
    for o in repo.all_ordenes():
        if o.estado_orden not in ESTADOS_ORDEN_CONFIRMADA:
            continue
        cid = str(o.cliente_id or "")
        if cid in credito_por_ruta:
            ordenes_por_cli.setdefault(cid, []).append(o)

    pendientes: dict[str, list[dict[str, Any]]] = {}
    for cid, ords in ordenes_por_cli.items():
        rem_bcv = q2(credito_por_ruta[cid][RUTA_BCV])
        rem_bin = q2(credito_por_ruta[cid][RUTA_BINANCE])
        lista_cli: list[dict[str, Any]] = []
        for o in sorted(ords, key=lambda x: (x.fecha, str(x.so_id))):
            ruta_o = normalizar_ruta(ruta_de_lista(o.lista_precios, mapeo)) or RUTA_BCV
            monto_o = q2(Decimal(str(o.monto_total or 0)))
            if monto_o <= Decimal("0"):
                continue
            if ruta_o == RUTA_BINANCE:
                cub = min(monto_o, rem_bin)
                rem_bin = q2(rem_bin - cub)
            else:
                cub = min(monto_o, rem_bcv)
                rem_bcv = q2(rem_bcv - cub)
            pend = q2(monto_o - cub)
            if pend <= Decimal("0.01"):
                continue
            f_base = o.fecha_entrega or o.fecha
            f_venc = (
                f_base + timedelta(days=int(o.dias_credito or 0))
                if isinstance(f_base, date)
                else None
            )
            lista_cli.append(
                {
                    "so_id": str(o.so_id),
                    "fecha": o.fecha.isoformat(),
                    "fecha_vencimiento": f_venc.isoformat() if f_venc else None,
                    "dias_credito": int(o.dias_credito or 0),
                    "ruta_pago": ruta_o,
                    "monto_total_usd": float(monto_o),
                    "saldo_pendiente_usd": float(pend),
                }
            )
        if lista_cli:
            pendientes[cid] = lista_cli
    return pendientes


def _clientes_para_vendedor(
    *,
    repo: Repository,
    engine: Engine | None,
    user: dict[str, Any],
    docs_comerciales: list[Any],
    docs_operativos: list[Any],
) -> list[dict[str, Any]]:
    vend_filtro = None if _es_alcance_global(user) else str(user.get("email") or "").strip().lower()
    saldos = calcular_saldos_propios(
        repo,
        docs_comerciales=docs_comerciales,
        docs_operativos=docs_operativos,
        vendedor=vend_filtro,
    )
    pol_global = cargar_politica_global(repo)
    overrides_raw = listar_overrides_clientes(engine) if engine is not None else []
    overrides = {
        int(ov["partner_odoo_id"]): ov
        for ov in overrides_raw
        if ov.get("partner_odoo_id") is not None
    }
    clientes_raw = list(saldos.get("clientes") or [])
    ord_pend_por_cli = _ordenes_pendientes_fifo_por_cliente(repo, clientes_rows=clientes_raw)

    salida: list[dict[str, Any]] = []
    for c in clientes_raw:
        cid = str(c.get("cliente_id") or "")
        pid = int(cid) if cid.isdigit() else None
        ov = overrides.get(pid) if pid is not None else None
        v_bcv = Decimal(str(c.get("ventas_bcv_usd") or 0))
        p_bcv = Decimal(str(c.get("pagos_bcv_usd") or 0))
        v_bin = Decimal(str(c.get("ventas_binance_usd") or 0))
        p_bin = Decimal(str(c.get("pagos_binance_usd") or 0))
        salida.append(
            {
                "cliente_id": cid,
                "partner_odoo_id": pid,
                "cliente_nombre": str(c.get("cliente_nombre") or ""),
                "vendedor_email": str(c.get("vendedor_email") or ""),
                "saldo_deudor_usd": float(c.get("saldo_deudor_usd") or 0.0),
                "saldo_favor_usd": float(c.get("saldo_favor_usd") or 0.0),
                "saldo_bcv_usd": float(max(Decimal("0.00"), q2(v_bcv - p_bcv))),
                "saldo_binance_usd": float(max(Decimal("0.00"), q2(v_bin - p_bin))),
                "ventas_bcv_usd": float(q2(v_bcv)),
                "pagos_bcv_usd": float(q2(p_bcv)),
                "ventas_binance_usd": float(q2(v_bin)),
                "pagos_binance_usd": float(q2(p_bin)),
                "vencido_usd": float(c.get("vencido_usd") or 0.0),
                "vigente_usd": float(c.get("vigente_usd") or 0.0),
                "max_dias_mora": int(c.get("dias_mora_max") or c.get("max_dias_mora") or 0),
                "tramos": dict(c.get("tramos") or {}),
                "politica_credito": ov or pol_global,
                "ordenes_pendientes": ord_pend_por_cli.get(cid, []),
            }
        )
    return salida


def construir_cartera_vendedor(
    *,
    repo: Repository,
    engine: Engine | None,
    user: dict[str, Any],
) -> dict[str, Any]:
    """Construye la vista de cartera del vendedor separada estrictamente por Ruta BCV vs Binance."""
    docs_com = RepoDocumentosSQL(engine).listar(limit=2000)[0] if engine is not None else []
    docs_op = RepoOperativosSQL(engine).listar(limit=2000)[0] if engine is not None else []
    clientes = _clientes_para_vendedor(
        repo=repo,
        engine=engine,
        user=user,
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
    )
    items: list[dict[str, Any]] = []
    tot_bcv = Decimal("0.00")
    tot_bin = Decimal("0.00")
    tot_vencido = Decimal("0.00")
    for cli in clientes:
        s_bcv = Decimal(str(cli["saldo_bcv_usd"]))
        s_bin = Decimal(str(cli["saldo_binance_usd"]))
        tot_bcv += s_bcv
        tot_bin += s_bin
        tot_vencido += Decimal(str(cli["vencido_usd"]))
        ords = list(cli.get("ordenes_pendientes") or [])
        items.append(
            {
                "cliente_id": cli["cliente_id"],
                "partner_odoo_id": cli["partner_odoo_id"],
                "cliente_nombre": cli["cliente_nombre"],
                "vendedor_email": cli["vendedor_email"],
                "saldo_deudor_usd": cli["saldo_deudor_usd"],
                "saldo_favor_usd": cli["saldo_favor_usd"],
                "vencido_usd": cli["vencido_usd"],
                "max_dias_mora": cli["max_dias_mora"],
                "tramos": cli["tramos"],
                "ruta_bcv": {
                    "ventas_usd": cli["ventas_bcv_usd"],
                    "pagos_usd": cli["pagos_bcv_usd"],
                    "saldo_usd": cli["saldo_bcv_usd"],
                    "ordenes_pendientes": [o for o in ords if o["ruta_pago"] == RUTA_BCV],
                },
                "ruta_binance": {
                    "ventas_usd": cli["ventas_binance_usd"],
                    "pagos_usd": cli["pagos_binance_usd"],
                    "saldo_usd": cli["saldo_binance_usd"],
                    "ordenes_pendientes": [o for o in ords if o["ruta_pago"] == RUTA_BINANCE],
                },
            }
        )
    return {
        "vendedor_email": str(user.get("email") or ""),
        "alcance_global": _es_alcance_global(user),
        "tasas_vigentes": _tasas_snapshot(repo),
        "resumen": {
            "total_clientes": len(items),
            "saldo_bcv_usd": float(q2(tot_bcv)),
            "saldo_binance_usd": float(q2(tot_bin)),
            "vencido_usd": float(q2(tot_vencido)),
        },
        "clientes": items,
    }


def construir_bootstrap_vendedor(
    *,
    repo: Repository,
    engine: Engine | None,
    user: dict[str, Any],
    servicio_docs: ServicioDocumentos,
) -> dict[str, Any]:
    cursor_ahora = datetime.now().isoformat(timespec="seconds")
    docs_com = RepoDocumentosSQL(engine).listar(limit=2000)[0] if engine is not None else []
    docs_op = RepoOperativosSQL(engine).listar(limit=2000)[0] if engine is not None else []
    cuentas_rec = (
        [
            c.a_dict()
            for c in RepoCuentasReceptorasSQL(engine).listar(solo_activas=True, solo_propias=True)
        ]
        if engine is not None
        else []
    )
    cuentas_ter = (
        [c.a_dict() for c in RepoCuentasTercerosSQL(engine).listar(solo_activas=True)]
        if engine is not None
        else []
    )
    lista_def = servicio_docs.lista_comercial_defecto()
    return {
        "modo": "bootstrap",
        "cursor": cursor_ahora,
        "tasas_vigentes": _tasas_snapshot(repo),
        "politica_ventas": cargar_politica_global(repo),
        "lista_defecto": lista_def.a_dict(),
        "listas_precio": servicio_docs.listas_precio_activas(),
        "descuentos_catalogo": servicio_docs.catalogo_descuentos(
            lista_precios_id=lista_def.lista_id
        ),
        "cuentas_receptoras": cuentas_rec,
        "cuentas_terceros": cuentas_ter,
        "contactos_maestro": _contactos_maestro_para_sync(engine),
        "catalogo": [it.a_dict() for it in servicio_docs.catalogo_items()],
        "clientes": _clientes_para_vendedor(
            repo=repo,
            engine=engine,
            user=user,
            docs_comerciales=docs_com,
            docs_operativos=docs_op,
        ),
        "tumbstones": {"cuentas_receptoras_inactivas": []},
    }


def construir_delta_vendedor(
    *,
    repo: Repository,
    engine: Engine | None,
    user: dict[str, Any],
    servicio_docs: ServicioDocumentos,
    cursor_str: str = "",
) -> dict[str, Any]:
    cursor_dt: datetime | None = None
    if cursor_str.strip():
        try:
            cursor_dt = datetime.fromisoformat(cursor_str.strip())
        except ValueError:
            cursor_dt = None

    snap = construir_bootstrap_vendedor(
        repo=repo,
        engine=engine,
        user=user,
        servicio_docs=servicio_docs,
    )
    inactivas: list[int] = []
    if engine is not None:
        todas_cr = RepoCuentasReceptorasSQL(engine).listar(solo_activas=False, solo_propias=True)
        for c in todas_cr:
            if not c.activa and (cursor_dt is None or c.actualizado_en >= cursor_dt):
                inactivas.append(c.cuenta_id)
        if cursor_dt is not None:
            snap["cuentas_receptoras"] = [
                c.a_dict() for c in todas_cr if c.activa and c.actualizado_en >= cursor_dt
            ]
    if cursor_dt is not None:
        snap["contactos_maestro"] = _contactos_maestro_para_sync(engine, cursor_dt=cursor_dt)
    snap["modo"] = "delta"
    snap["desde_cursor"] = cursor_str or None
    snap["tumbstones"] = {"cuentas_receptoras_inactivas": inactivas}
    return snap


def _procesar_item_push(
    *,
    tipo: str,
    payload: dict[str, Any],
    vendedor_email: str,
    engine: Engine | None,
    servicio_docs: ServicioDocumentos,
    servicio_ops: ServicioOperativos,
    servicio_contactos: ServicioContactos,
) -> tuple[int, dict[str, Any]]:
    actor = vendedor_email or "app_movil"
    if tipo in (COTIZACION, PEDIDO):
        try:
            datos_doc = dict(payload)
            datos_doc["tipo"] = tipo
            if not datos_doc.get("vendedor_email"):
                datos_doc["vendedor_email"] = vendedor_email
            doc = servicio_docs.crear(
                datos_doc,
                origen="app_movil",
                actor=actor,
                auto_enviar=(tipo == PEDIDO),
            )
            return 200, {
                "estado": "OK",
                "reintentable": False,
                "documento": doc.a_dict(incluir_lineas=True),
                "retenido_aprobacion": doc.estado != ENVIADO_ODOO and doc.estado != BORRADOR,
            }
        except (DocumentoInvalido, CapturaInvalida, ValueError) as exc:
            return 422, {
                "estado": "ERROR_VALIDACION",
                "reintentable": False,
                "error": str(exc),
                "faltan": list(getattr(exc, "faltan", [])),
            }

    if tipo == "pago":
        try:
            datos_pago = dict(payload)
            if not datos_pago.get("vendedor_email"):
                datos_pago["vendedor_email"] = vendedor_email
            val = servicio_ops.validar_en_vivo("pago", datos_pago)
            dest = val.get("destino_validacion") or {}
            if dest.get("bloqueante"):
                return 422, {
                    "estado": "CONFLICTO_NEGOCIO",
                    "codigo_negocio": str(dest.get("estado") or "DESTINO_INVALIDO"),
                    "reintentable": False,
                    "error": str(
                        dest.get("mensaje") or "La cuenta destino no pertenece a Lubrikca."
                    ),
                    "destino_validacion": dest,
                }
            dups = list(val.get("posibles_duplicados") or [])
            if dups:
                return 409, {
                    "estado": "CONFLICTO_NEGOCIO",
                    "codigo_negocio": "POSIBLE_DUPLICADO",
                    "reintentable": False,
                    "error": str(dups[0].get("detalle") or "Posible pago duplicado detectado."),
                    "posibles_duplicados": dups,
                }
            op = servicio_ops.crear(datos_pago, origen="app_movil", actor=actor)
            return 200, {
                "estado": "OK",
                "reintentable": False,
                "operativo": op.a_dict(),
            }
        except (OperativoInvalido, CapturaInvalida, ValueError) as exc:
            return 422, {
                "estado": "ERROR_VALIDACION",
                "reintentable": False,
                "error": str(exc),
                "faltan": list(getattr(exc, "faltan", [])),
            }

    if tipo == "contacto":
        try:
            datos_c = dict(payload)
            if not datos_c.get("vendedor_email"):
                datos_c["vendedor_email"] = vendedor_email
            creado = servicio_contactos.crear(
                datos_c,
                origen="cxc",
                actor=actor,
                forzar_duplicado=bool(datos_c.get("forzar_duplicado")),
            )
            return 200, {
                "estado": "OK",
                "reintentable": False,
                "contacto": creado.a_dict(),
            }
        except ContactoDuplicadoError as exc:
            return 409, {
                "estado": "CONFLICTO_NEGOCIO",
                "codigo_negocio": "CONTACTO_DUPLICADO",
                "reintentable": False,
                "error": str(exc),
                "candidatos": list(exc.duplicados),
            }
        except (ContactoInvalido, ValueError) as exc:
            return 422, {
                "estado": "ERROR_VALIDACION",
                "reintentable": False,
                "error": str(exc),
            }

    if tipo == "lote_efectivo":
        if engine is None:
            return 503, {
                "estado": "ERROR_TRANSITORIO",
                "reintentable": True,
                "error": "Base de datos sin soporte de lotes de efectivo.",
            }
        try:
            items = list(payload.get("items") or [])
            lote = RepoLotesEfectivo(engine).crear_lote(
                vendedor_email=vendedor_email,
                vendedor_nombre=str(payload.get("vendedor_nombre") or vendedor_email),
                items=items,
                recibido_por=str(payload.get("recibido_por") or actor),
                observaciones=str(payload.get("observaciones") or ""),
            )
            return 200, {
                "estado": "OK",
                "reintentable": False,
                "lote": lote,
            }
        except ValueError as exc:
            return 422, {
                "estado": "ERROR_VALIDACION",
                "reintentable": False,
                "error": str(exc),
            }

    return 422, {
        "estado": "ERROR_VALIDACION",
        "reintentable": False,
        "error": f"Tipo de operación push no soportado: {tipo!r}.",
    }


def procesar_push_batch(
    *,
    items: list[dict[str, Any]],
    vendedor_email: str,
    device_id: str,
    repo_v1: RepoApiV1SQL,
    engine: Engine | None,
    servicio_docs: ServicioDocumentos,
    servicio_ops: ServicioOperativos,
    servicio_contactos: ServicioContactos,
) -> dict[str, Any]:
    resultados: list[dict[str, Any]] = []
    for raw in items:
        idem = str(raw.get("idempotency_key") or "").strip()
        tipo = str(raw.get("tipo") or "").strip().lower()
        payload = dict(raw.get("payload") or {})
        if not idem:
            resultados.append(
                {
                    "idempotency_key": "",
                    "tipo": tipo,
                    "estado": "ERROR_VALIDACION",
                    "http_status": 422,
                    "reintentable": False,
                    "error": "idempotency_key es obligatorio.",
                }
            )
            continue

        try:
            ya_existia, st_prev, resp_prev = repo_v1.obtener_o_reservar_idempotencia(
                idempotency_key=idem,
                actor_tipo="vendedor",
                actor_id=vendedor_email,
                endpoint="/api/v1/sync/push",
                payload={"tipo": tipo, "payload": payload},
            )
        except ApiV1Conflicto as exc:
            resultados.append(
                {
                    "idempotency_key": idem,
                    "tipo": tipo,
                    "estado": "CONFLICTO_NEGOCIO",
                    "http_status": 409,
                    "reintentable": False,
                    "error": str(exc),
                }
            )
            continue

        if ya_existia and resp_prev is not None:
            resultados.append(
                {
                    **resp_prev,
                    "idempotency_key": idem,
                    "tipo": tipo,
                    "estado": "DUPLICADO_IDEMPOTENTE",
                    "estado_original": resp_prev.get("estado"),
                    "http_status": st_prev,
                    "reintentable": False,
                }
            )
            continue

        http_st, res_item = _procesar_item_push(
            tipo=tipo,
            payload=payload,
            vendedor_email=vendedor_email,
            engine=engine,
            servicio_docs=servicio_docs,
            servicio_ops=servicio_ops,
            servicio_contactos=servicio_contactos,
        )
        salida_item = {
            "idempotency_key": idem,
            "tipo": tipo,
            "http_status": http_st,
            **res_item,
        }
        if http_st < 500:
            repo_v1.guardar_idempotencia(
                idempotency_key=idem,
                actor_tipo="vendedor",
                actor_id=vendedor_email,
                endpoint="/api/v1/sync/push",
                payload={"tipo": tipo, "payload": payload},
                status_code=http_st,
                response_data=salida_item,
            )
        resultados.append(salida_item)

    if device_id.strip():
        repo_v1.actualizar_sync_dispositivo(
            device_id=device_id.strip(),
            cursor=datetime.now().isoformat(timespec="seconds"),
        )
    return {
        "procesados": len(resultados),
        "resultados": resultados,
        "cursor": datetime.now().isoformat(timespec="seconds"),
    }
