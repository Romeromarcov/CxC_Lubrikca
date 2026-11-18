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
    ESTADOS_DOCUMENTO,
    PEDIDO,
    PENDIENTE_APROBACION,
    TIPOS_DOCUMENTO,
    DocumentoInvalido,
)
from cxc.documentos.preview_html import renderizar_preview_cotizacion
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.documentos.servicio import (
    DocumentoNoEncontrado,
    DocumentoYaEnviado,
    ServicioDocumentos,
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
    if not any(tiene(user, p) for p in permisos):
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
    lim = max(1, min(limit, 30))
    return {
        "total_filtrado": len(filtrados),
        "items": [c.a_dict() for c in filtrados[:lim]],
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
