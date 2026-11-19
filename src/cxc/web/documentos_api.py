"""Endpoints REST y Preview HTML de Documentos Comerciales (Cotizaciones y Pedidos)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from cxc.capturas.modelos import CapturaInvalida
from cxc.documentos.catalogo import (
    CatalogoSQLOdoo,
    DatosPreciosOdoo,
    buscar_productos,
    cargar_datos_precios_odoo,
    resolver_producto_unico,
)
from cxc.documentos.modelos import (
    BORRADOR,
    COTIZACION,
    ENVIADO_ODOO,
    ERROR_ODOO,
    ESTADOS_DOCUMENTO,
    PEDIDO,
    PENDIENTE_APROBACION,
    RECHAZADO,
    TIPOS_DOCUMENTO,
    DocumentoInvalido,
)
from cxc.documentos.politica_ventas import (
    cargar_politica_global,
    eliminar_override_cliente,
    guardar_override_cliente,
    guardar_politica_global,
    listar_overrides_clientes,
)
from cxc.documentos.preview_html import renderizar_preview_cotizacion
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.documentos.servicio import (
    DocumentoNoEncontrado,
    DocumentoYaEnviado,
    ServicioDocumentos,
)
from cxc.documentos.sync_odoo import (
    ESTADOS_ODOO_CONFIRMADOS,
    orden_confirmada_en_odoo,
    sincronizar_documentos_desde_odoo,
)
from cxc.odoo.directorio import filtrar_contactos
from cxc.odoo.escritura import EscritorOdoo, EscrituraConfig, conectar
from cxc.permisos import tiene
from cxc.web.capturas_api import exigir_bot_key
from cxc.web.directorio_api import LectorDep, _obtener_contactos

logger = logging.getLogger("cxc.documentos.api")

router = APIRouter()

PERMISOS_VER_DOCS = ("ventas", "ventas.aprobar_pedidos", "capturas.ver", "capturas.gestionar")
PERMISOS_GESTIONAR_DOCS = ("ventas", "ventas.aprobar_pedidos", "capturas.gestionar")
PERMISO_APROBAR_PEDIDOS = "ventas.aprobar_pedidos"


def _usuario_doc(cxc_session: str | None, *permisos: str) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if permisos and not any(tiene(user, p) for p in permisos):
        raise HTTPException(status_code=403, detail="Sin permisos para esta operación comercial")
    return user


def _actor_doc(user: dict[str, Any]) -> str:
    return str(user.get("email") or user.get("nombre") or "usuario")


def _fabrica_escritor_doc() -> EscritorOdoo | None:
    cfg = EscrituraConfig.from_env()
    if cfg is None:
        return None
    return EscritorOdoo(conectar(cfg))


def _lector_precios_odoo() -> DatosPreciosOdoo:
    from cxc.config import AppConfig
    from cxc.odoo.directorio import conectar_lectura
    from cxc.web.app import get_valid_pricelists_usd_and_ves

    cfg = AppConfig.from_env()
    execute = conectar_lectura(cfg.odoo)
    usd_lists, ves_lists = get_valid_pricelists_usd_and_ves()
    usd_ids = [int(x) for x in usd_lists if str(x).isdigit()]
    ves_ids = [int(x) for x in ves_lists if str(x).isdigit()]
    return cargar_datos_precios_odoo(execute, usd_ids, ves_ids)


def get_servicio_documentos() -> ServicioDocumentos:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine = repo._engine
    return ServicioDocumentos(
        RepoDocumentosSQL(engine),
        CatalogoSQLOdoo(engine, _lector_precios_odoo),
        _fabrica_escritor_doc,
        repo_cxc=repo,
    )


ServicioDocsDep = Annotated[ServicioDocumentos, Depends(get_servicio_documentos)]


class DocumentoBodyIn(BaseModel):
    tipo: str = "cotizacion"
    partner_odoo_id: int | None = None
    tipo_contacto: str | None = None
    cliente_nombre: str
    rif: str | None = ""
    email: str | None = ""
    telefono: str | None = ""
    direccion: str | None = ""
    plus_code: str | None = ""
    latitud: float | None = None
    longitud: float | None = None
    ubicacion: dict[str, Any] | None = None
    es_primera_compra: bool = True
    moneda_lista: str = "VES"
    lista_precios_id: str | None = None
    lista_precios_nombre: str | None = None
    moneda_pago_esperada: str | None = None
    vendedor_email: str | None = ""
    fecha_entrega: str | None = None
    condiciones_negociacion: str | None = ""
    observaciones: str | None = ""
    descuentos_codigos: list[str] = Field(default_factory=list)
    lineas: list[dict[str, Any]] = Field(default_factory=list)


class RechazoIn(BaseModel):
    motivo: str = ""


# ── Endpoints para Bots de Telegram (X-Bot-Key) ──────────────────────────────


@router.get("/api/bots/catalogo", dependencies=[Depends(exigir_bot_key)])
def bot_buscar_catalogo(
    servicio: ServicioDocsDep,
    q: str | None = None,
    producto_id: str | None = None,
    limit: int = 12,
    refresh: bool = False,
) -> dict[str, Any]:
    items = servicio.catalogo_items(refresh=refresh)
    lista_def = servicio.lista_comercial_defecto(refresh=refresh)
    unico, candidatos = resolver_producto_unico(items, producto_id=producto_id, texto=q)
    lista = candidatos if (q or producto_id) else buscar_productos(items, None, limite=limit)
    lim = max(1, min(limit, 50))
    return {
        "total_catalogo": len(items),
        "lista_defecto": lista_def.a_dict(),
        "unico": unico.a_dict() if unico else None,
        "items": [it.a_dict() for it in lista[:lim]],
    }


@router.get("/api/bots/contactos", dependencies=[Depends(exigir_bot_key)])
def bot_buscar_contactos(
    lector: LectorDep,
    q: str | None = None,
    clasificacion: str | None = None,
    limit: int = 10,
    refresh: bool = False,
) -> dict[str, Any]:
    todos = _obtener_contactos(lector, refresh=refresh)
    comerciales = (
        todos
        if clasificacion == "proveedor"
        else [c for c in todos if c.clasificacion in ("prospecto", "cliente", "ambos")]
    )
    filtrados = filtrar_contactos(comerciales, clasificacion=clasificacion, q=q)
    items_out: list[dict[str, Any]] = [
        {**c.a_dict(), "origen": "bot" if c.es_borrador_telegram else "odoo"} for c in filtrados
    ]
    vistos_odoo: set[int] = {c.id for c in filtrados}

    # Agrega contactos/prospectos nacidos en Bots/CxC aún sin partner en Odoo (Fase 6)
    try:
        from cxc.web.app import app as _app
        from cxc.web.contactos_api import get_servicio_contactos

        fabrica_c = _app.dependency_overrides.get(get_servicio_contactos, get_servicio_contactos)
        svc_c = fabrica_c()
        rol_filtro = "proveedor" if clasificacion == "proveedor" else None
        locales, _, _ = svc_c.listar(rol=rol_filtro, q=q, limit=50, offset=0)
        for lc in locales:
            if lc.odoo_partner_id is not None and lc.odoo_partner_id in vistos_odoo:
                continue
            if (
                clasificacion
                and clasificacion != lc.rol
                and not (clasificacion in ("cliente", "proveedor") and lc.rol == "ambos")
            ):
                continue
            items_out.append(lc.a_dict())
    except Exception:
        pass

    lim = max(1, min(limit, 30))
    return {
        "total_filtrado": len(items_out),
        "items": items_out[:lim],
    }


@router.get("/api/bots/descuentos/catalogo", dependencies=[Depends(exigir_bot_key)])
def bot_catalogo_descuentos(
    servicio: ServicioDocsDep,
    lista_precios_id: str | None = None,
) -> dict[str, Any]:
    lista_def = servicio.lista_comercial_defecto()
    items = servicio.catalogo_descuentos(lista_precios_id=lista_precios_id or lista_def.lista_id)
    return {
        "lista_defecto": lista_def.a_dict(),
        "total": len(items),
        "items": items,
    }


@router.post("/api/bots/descuentos/evaluar", dependencies=[Depends(exigir_bot_key)])
def bot_evaluar_descuentos(
    servicio: ServicioDocsDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> JSONResponse:
    try:
        res = servicio.evaluar_descuentos(body)
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=res)


@router.post(
    "/api/bots/documentos/{documento_id}/convertir-pedido",
    dependencies=[Depends(exigir_bot_key)],
)
def bot_convertir_cotizacion_a_pedido(
    documento_id: int,
    servicio: ServicioDocsDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> JSONResponse:
    actor = str(body.get("vendedor_email") or "bot_telegram")
    try:
        ped = servicio.convertir_en_pedido(documento_id, body, actor=actor)
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento comercial no encontrado") from e
    except DocumentoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=ped.a_dict(incluir_lineas=True))


# ── Preview compartible de Cotización y conversión directa a Pedido ──────────


@router.get("/cotizacion/{token}", response_class=HTMLResponse)
def ver_preview_cotizacion(token: str, servicio: ServicioDocsDep) -> HTMLResponse:
    try:
        doc = servicio.obtener_por_token(token)
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Cotización no encontrada") from e
    return HTMLResponse(content=renderizar_preview_cotizacion(doc))


@router.post("/cotizacion/{token}/convertir-pedido")
def convertir_preview_a_pedido(
    token: str,
    servicio: ServicioDocsDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> JSONResponse:
    try:
        ped = servicio.convertir_por_token(token, body, actor="preview_cotizacion")
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Cotización no encontrada") from e
    except DocumentoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=ped.a_dict(incluir_lineas=True))


# ── Endpoints Web de CxC (Sección Ventas → Cotizaciones y Pedidos) ───────────


@router.get("/api/documentos/catalogo")
def listar_catalogo_documentos(
    servicio: ServicioDocsDep,
    q: str | None = None,
    limit: int = 100,
    refresh: bool = False,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_doc(cxc_session, *PERMISOS_VER_DOCS)
    todos = servicio.catalogo_items(refresh=refresh)
    lista_def = servicio.lista_comercial_defecto(refresh=refresh)
    filtrados = buscar_productos(todos, q, limite=max(1, min(limit, 500)))
    return {
        "total_catalogo": len(todos),
        "lista_defecto": lista_def.a_dict(),
        "items": [it.a_dict() for it in filtrados],
    }


@router.get("/api/documentos/descuentos/catalogo")
def listar_catalogo_descuentos_web(
    servicio: ServicioDocsDep,
    lista_precios_id: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_doc(cxc_session, *PERMISOS_VER_DOCS)
    lista_def = servicio.lista_comercial_defecto()
    items = servicio.catalogo_descuentos(lista_precios_id=lista_precios_id or lista_def.lista_id)
    return {
        "lista_defecto": lista_def.a_dict(),
        "total": len(items),
        "items": items,
    }


@router.post("/api/documentos/descuentos/evaluar")
def evaluar_descuentos_web(
    servicio: ServicioDocsDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    _usuario_doc(cxc_session, *PERMISOS_VER_DOCS)
    try:
        res = servicio.evaluar_descuentos(body)
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=res)


@router.get("/api/documentos")
def listar_documentos(
    servicio: ServicioDocsDep,
    tipo: str | None = None,
    estado: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_doc(cxc_session, *PERMISOS_VER_DOCS)
    if tipo and tipo not in TIPOS_DOCUMENTO:
        raise HTTPException(
            status_code=422, detail=f"tipo debe ser uno de {', '.join(TIPOS_DOCUMENTO)}"
        )
    if estado and estado not in ESTADOS_DOCUMENTO:
        raise HTTPException(
            status_code=422, detail=f"estado debe ser uno de {', '.join(ESTADOS_DOCUMENTO)}"
        )
    items, total, resumen = servicio.listar(
        tipo=tipo,
        estado=estado,
        q=q,
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
    )
    return {
        "puede_aprobar": tiene(user, PERMISO_APROBAR_PEDIDOS),
        "lista_defecto": servicio.lista_comercial_defecto().a_dict(),
        "resumen": resumen,
        "total": total,
        "items": [d.a_dict(incluir_lineas=True) for d in items],
    }


@router.get("/api/documentos/{documento_id}")
def obtener_documento(
    documento_id: int,
    servicio: ServicioDocsDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_doc(cxc_session, *PERMISOS_VER_DOCS)
    try:
        out = servicio.obtener(documento_id).a_dict(incluir_lineas=True)
        out["puede_aprobar"] = tiene(user, PERMISO_APROBAR_PEDIDOS)
        return out
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento comercial no encontrado") from e


@router.post("/api/documentos")
def crear_documento(
    body: DocumentoBodyIn,
    servicio: ServicioDocsDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_doc(cxc_session, *PERMISOS_GESTIONAR_DOCS)
    actor = _actor_doc(user)
    datos = body.model_dump()
    if not datos.get("vendedor_email"):
        datos["vendedor_email"] = str(user.get("email") or "")
    try:
        creado = servicio.crear(datos, origen="web", actor=actor)
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=201, content=creado.a_dict(incluir_lineas=True))


@router.put("/api/documentos/{documento_id}")
def editar_documento(
    documento_id: int,
    body: dict[str, Any],
    servicio: ServicioDocsDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_doc(cxc_session, *PERMISOS_GESTIONAR_DOCS)
    try:
        actualizado = servicio.editar(documento_id, body, actor=_actor_doc(user))
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento comercial no encontrado") from e
    except DocumentoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=actualizado.a_dict(incluir_lineas=True))


@router.post("/api/documentos/{documento_id}/convertir-pedido")
def convertir_documento_a_pedido_web(
    documento_id: int,
    servicio: ServicioDocsDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_doc(cxc_session, *PERMISOS_GESTIONAR_DOCS)
    try:
        ped = servicio.convertir_en_pedido(documento_id, body, actor=_actor_doc(user))
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento comercial no encontrado") from e
    except DocumentoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=ped.a_dict(incluir_lineas=True))


@router.post("/api/documentos/{documento_id}/aprobar")
def aprobar_y_enviar_pedido_a_odoo(
    documento_id: int,
    servicio: ServicioDocsDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    """Autoriza un Pedido pendiente en CxC y lo envía a Odoo (precios normales + chatter)."""
    user = _usuario_doc(cxc_session, PERMISO_APROBAR_PEDIDOS)
    try:
        aprobado = servicio.aprobar_pedido(documento_id, actor=_actor_doc(user))
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento comercial no encontrado") from e
    except DocumentoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(status_code=200, content=aprobado.a_dict(incluir_lineas=True))


@router.post("/api/documentos/{documento_id}/rechazar")
def rechazar_pedido_pendiente(
    documento_id: int,
    servicio: ServicioDocsDep,
    body: Annotated[RechazoIn, Body(default_factory=RechazoIn)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_doc(cxc_session, PERMISO_APROBAR_PEDIDOS)
    try:
        rechazado = servicio.rechazar_pedido(
            documento_id, motivo=body.motivo, actor=_actor_doc(user)
        )
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento comercial no encontrado") from e
    except DocumentoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    return JSONResponse(status_code=200, content=rechazado.a_dict(incluir_lineas=True))


@router.post("/api/documentos/{documento_id}/enviar-odoo")
def enviar_documento_a_odoo(
    documento_id: int,
    servicio: ServicioDocsDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_doc(cxc_session, *PERMISOS_GESTIONAR_DOCS)
    try:
        doc_prev = servicio.obtener(documento_id)
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento comercial no encontrado") from e

    # Un Pedido pendiente de aprobación exige permiso explícito de autorizador
    if (doc_prev.tipo == PEDIDO or doc_prev.estado == PENDIENTE_APROBACION) and not tiene(
        user, PERMISO_APROBAR_PEDIDOS
    ):
        raise HTTPException(
            status_code=403,
            detail="Solo un usuario autorizado puede aprobar y enviar pedidos a Odoo.",
        )

    try:
        enviado = servicio.enviar_a_odoo(
            documento_id,
            actor=_actor_doc(user),
            registrar_aprobacion=(doc_prev.tipo == PEDIDO),
        )
    except DocumentoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(status_code=200, content=enviado.a_dict(incluir_lineas=True))


# ── Ventas Unificadas (CxC + Odoo) y Política de Crédito / Auto-envío ────────


def _repo_y_engine(servicio: ServicioDocumentos) -> tuple[Any, Any]:
    from cxc.web.app import get_repo

    repo_cxc = getattr(servicio, "_repo_cxc", None) or get_repo()
    engine = getattr(getattr(servicio, "_repo", None), "_engine", None) or getattr(
        repo_cxc, "_engine", None
    )
    return repo_cxc, engine


def _estado_unificado_odoo(estado_orden: str) -> str:
    est = (estado_orden or "sale").strip().lower()
    if est == "cancel":
        return RECHAZADO
    if est in ("draft", "sent"):
        return BORRADOR
    return ENVIADO_ODOO


def _sincronizar_desde_espejo_seguro(servicio: ServicioDocumentos, repo_cxc: Any) -> dict[str, int]:
    from cxc.web.app import get_pricelist_mapeo

    try:
        mapeo = get_pricelist_mapeo(repo_cxc)
    except Exception:
        mapeo = {}
    try:
        return sincronizar_documentos_desde_odoo(
            repo_cxc,
            servicio._repo,
            pricelist_mapeo=mapeo,
        )
    except Exception as exc:
        logger.debug("Sync espejo -> documentos omitido: %s", exc)
        return {"revisados": 0, "ajustados": 0, "confirmados": 0, "cancelados": 0}


@router.post("/api/ventas/sincronizar-odoo")
def sincronizar_ventas_desde_odoo_api(
    servicio: ServicioDocsDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_doc(cxc_session, *PERMISOS_VER_DOCS)
    repo_cxc, _ = _repo_y_engine(servicio)
    res = _sincronizar_desde_espejo_seguro(servicio, repo_cxc)
    return {"ok": True, **res}


@router.get("/api/ventas/unificadas")
def listar_ventas_unificadas(
    servicio: ServicioDocsDep,
    origen: str = "todos",
    tipo: str = "",
    estado: str = "",
    q: str = "",
    limit: int = 150,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_pricelist_mapeo

    user = _usuario_doc(cxc_session, *PERMISOS_VER_DOCS)
    orig_filtro = (origen or "todos").strip().lower()
    if orig_filtro not in ("todos", "cxc", "odoo"):
        raise HTTPException(status_code=422, detail="origen debe ser 'todos', 'cxc' o 'odoo'")
    tipo_filtro = (tipo or "").strip().lower()
    estado_filtro = (estado or "").strip().upper()
    q_filtro = (q or "").strip().lower()

    repo_cxc, _ = _repo_y_engine(servicio)
    _sincronizar_desde_espejo_seguro(servicio, repo_cxc)

    docs_cxc, _, _ = servicio.listar(limit=500, offset=0)
    ordenes_all = list(repo_cxc.all_ordenes())
    ordenes_por_so = {str(o.so_id or "").strip().upper(): o for o in ordenes_all if o.so_id}

    try:
        pricelists_map = get_pricelist_mapeo(repo_cxc)
    except Exception:
        pricelists_map = {}

    clientes_map = {str(c.cliente_id): c for c in repo_cxc.all_clientes()}
    vinculados_so: set[str] = {
        d.odoo_order_name.strip().upper()
        for d in docs_cxc
        if d.odoo_order_name and d.odoo_order_name.strip()
    }

    filas: list[dict[str, Any]] = []
    for d in docs_cxc:
        item = d.a_dict(incluir_lineas=True)
        so_key = (d.odoo_order_name or "").strip().upper()
        ord_esp = ordenes_por_so.get(so_key)
        confirmado = orden_confirmada_en_odoo(ord_esp, d) or d.estado == ENVIADO_ODOO
        vm = d.validacion_motor if isinstance(d.validacion_motor, dict) else {}
        raw_sync = vm.get("sync_odoo")
        sync_meta: dict[str, Any] = raw_sync if isinstance(raw_sync, dict) else {}
        item["origen_sistema"] = "cxc"
        item["estado_odoo"] = (
            ord_esp.estado_orden
            if ord_esp is not None
            else str(sync_meta.get("estado_odoo") or ("sale" if d.estado == ENVIADO_ODOO else ""))
        )
        item["confirmado_odoo"] = bool(confirmado)
        item["ajustado_desde_odoo"] = bool(sync_meta.get("ajustado_desde_odoo"))
        item["solo_lectura"] = bool(confirmado)
        filas.append(item)

    for o in ordenes_all:
        so_key = str(o.so_id or "").strip().upper()
        if not so_key or so_key in vinculados_so:
            continue
        cli = clientes_map.get(str(o.cliente_id))
        est_unif = _estado_unificado_odoo(o.estado_orden)
        est_raw = (o.estado_orden or "sale").strip().lower()
        fecha_iso = o.fecha.isoformat()
        try:
            lineas_o = repo_cxc.lineas_de_orden(o.so_id)
        except Exception:
            lineas_o = []
        tot_con_imp = round(float(o.monto_total or 0), 2)
        if lineas_o:
            sub_o = round(
                sum(
                    float(
                        ln.subtotal
                        if ln.subtotal and ln.subtotal > 0
                        else ln.cantidad * ln.precio_unitario
                    )
                    for ln in lineas_o
                ),
                2,
            )
        else:
            sub_o = round(tot_con_imp / 1.16, 2) if tot_con_imp > 0 else 0.0
        iva_o = max(0.0, round(tot_con_imp - sub_o, 2))
        pl_info = pricelists_map.get(str(o.lista_precios or ""), {})
        pl_nombre = str(pl_info.get("name") or pl_info.get("nombre") or o.lista_precios or "—")
        pl_mon = str(pl_info.get("moneda") or "USD").upper()
        if pl_mon not in ("USD", "VES"):
            pl_mon = "USD"
        filas.append(
            {
                "id": None,
                "numero": o.so_id,
                "token_preview": "",
                "preview_path": "",
                "tipo": PEDIDO,
                "estado": est_unif,
                "estado_odoo": o.estado_orden,
                "confirmado_odoo": est_raw in ESTADOS_ODOO_CONFIRMADOS,
                "ajustado_desde_odoo": False,
                "origen": "odoo",
                "origen_sistema": "odoo",
                "solo_lectura": True,
                "captura_id": None,
                "partner_odoo_id": int(o.cliente_id) if str(o.cliente_id).isdigit() else None,
                "tipo_contacto": "cliente",
                "cliente_nombre": cli.nombre if cli else f"Cliente #{o.cliente_id}",
                "rif": str(getattr(cli, "rif", "") or ""),
                "email": "",
                "telefono": "",
                "direccion": "",
                "moneda_lista": pl_mon,
                "lista_precios_id": str(o.lista_precios or ""),
                "lista_precios_nombre": pl_nombre,
                "vendedor_email": o.vendedor_email,
                "subtotal": sub_o,
                "total_descuentos": 0.0,
                "subtotal_neto": sub_o,
                "monto_iva": iva_o,
                "total_con_iva": tot_con_imp,
                "total_litros": 0.0,
                "n_lineas": len(lineas_o),
                "lineas_sin_catalogo": 0,
                "descuentos": [],
                "alertas_motor": [],
                "auto_enviado": False,
                "motivos_retencion": [],
                "odoo_order_id": None,
                "odoo_order_name": o.so_id,
                "facturada": bool(o.facturada),
                "entregada_completa": bool(o.entregada_completa),
                "creado_por": "odoo",
                "creado_en": f"{fecha_iso}T00:00:00",
                "actualizado_en": f"{fecha_iso}T00:00:00",
            }
        )

    confirmados = [
        x for x in filas if x["estado"] == ENVIADO_ODOO and x.get("estado_odoo", "sale") != "cancel"
    ]
    resumen = {
        "total": len(filas),
        "origen_cxc": sum(1 for x in filas if x["origen_sistema"] == "cxc"),
        "origen_odoo": sum(1 for x in filas if x["origen_sistema"] == "odoo"),
        "cotizaciones_borrador": sum(
            1 for x in filas if x["tipo"] == COTIZACION and x["estado"] == BORRADOR
        ),
        "total_cotizaciones": sum(
            1 for x in filas if x["tipo"] == COTIZACION and x["estado"] == BORRADOR
        ),
        "pedidos_pendientes_aprobacion": sum(
            1 for x in filas if x["estado"] == PENDIENTE_APROBACION
        ),
        "pendientes_aprobacion": sum(1 for x in filas if x["estado"] == PENDIENTE_APROBACION),
        "pedidos_auto_enviados": sum(
            1 for x in filas if x["estado"] == ENVIADO_ODOO and x.get("auto_enviado")
        ),
        "auto_enviados": sum(
            1 for x in filas if x["estado"] == ENVIADO_ODOO and x.get("auto_enviado")
        ),
        "enviados_odoo": len(confirmados),
        "total_confirmados": len(confirmados),
        "subtotal_confirmado_usd": round(
            sum(float(x.get("subtotal") or 0) for x in confirmados), 2
        ),
        "total_con_iva_confirmado_usd": round(
            sum(float(x.get("total_con_iva") or x.get("subtotal") or 0) for x in confirmados), 2
        ),
        "rechazados": sum(1 for x in filas if x["estado"] == RECHAZADO),
        "con_error": sum(1 for x in filas if x["estado"] == ERROR_ODOO),
    }

    def _coincide(it: dict[str, Any]) -> bool:
        if orig_filtro != "todos" and it["origen_sistema"] != orig_filtro:
            return False
        if tipo_filtro and it["tipo"] != tipo_filtro:
            return False
        if estado_filtro and it["estado"] != estado_filtro:
            return False
        if q_filtro:
            bolsa = (
                f"{it.get('numero', '')} {it.get('odoo_order_name', '')} "
                f"{it.get('cliente_nombre', '')} {it.get('rif', '')} "
                f"{it.get('vendedor_email', '')}"
            ).lower()
            if q_filtro not in bolsa:
                return False
        return True

    filtradas = [it for it in filas if _coincide(it)]
    filtradas.sort(key=lambda x: str(x.get("actualizado_en") or "")[:19], reverse=True)
    lim = max(1, min(limit, 500))

    return {
        "puede_aprobar": tiene(user, PERMISO_APROBAR_PEDIDOS),
        "lista_defecto": servicio.lista_comercial_defecto().a_dict(),
        "politica_ventas": cargar_politica_global(repo_cxc),
        "resumen": resumen,
        "total": len(filtradas),
        "items": filtradas[:lim],
    }


@router.get("/api/config/politica-ventas")
def obtener_politica_ventas(
    servicio: ServicioDocsDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_doc(cxc_session)
    repo_cxc, engine = _repo_y_engine(servicio)
    return {
        "politica": cargar_politica_global(repo_cxc),
        "overrides": listar_overrides_clientes(engine),
        "puede_editar": tiene(user, PERMISO_APROBAR_PEDIDOS) or user.get("rol") == "admin",
    }


@router.post("/api/config/politica-ventas")
def guardar_politica_ventas_api(
    servicio: ServicioDocsDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    _usuario_doc(cxc_session, PERMISO_APROBAR_PEDIDOS)
    repo_cxc, _ = _repo_y_engine(servicio)
    try:
        pol = guardar_politica_global(repo_cxc, body)
    except ValueError as e:
        return JSONResponse(status_code=422, content={"error": str(e)})
    return JSONResponse(status_code=200, content={"ok": True, "politica": pol})


@router.put("/api/config/politica-ventas/clientes/{partner_odoo_id}")
def guardar_override_cliente_api(
    partner_odoo_id: int,
    servicio: ServicioDocsDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_doc(cxc_session, PERMISO_APROBAR_PEDIDOS)
    _, engine = _repo_y_engine(servicio)
    if engine is None:
        return JSONResponse(
            status_code=503, content={"error": "Base de datos SQL no disponible para overrides."}
        )
    try:
        ov = guardar_override_cliente(engine, partner_odoo_id, body, actor=_actor_doc(user))
    except ValueError as e:
        return JSONResponse(status_code=422, content={"error": str(e)})
    return JSONResponse(status_code=200, content={"ok": True, "override": ov})


@router.delete("/api/config/politica-ventas/clientes/{partner_odoo_id}")
def eliminar_override_cliente_api(
    partner_odoo_id: int,
    servicio: ServicioDocsDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    _usuario_doc(cxc_session, PERMISO_APROBAR_PEDIDOS)
    _, engine = _repo_y_engine(servicio)
    if engine is None:
        return JSONResponse(
            status_code=503, content={"error": "Base de datos SQL no disponible para overrides."}
        )
    eliminado = eliminar_override_cliente(engine, partner_odoo_id)
    if not eliminado:
        raise HTTPException(status_code=404, detail="Override de cliente no encontrado")
    return JSONResponse(status_code=200, content={"ok": True, "partner_odoo_id": partner_odoo_id})
