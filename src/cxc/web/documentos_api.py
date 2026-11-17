"""Endpoints REST de Documentos Comerciales (Cotizaciones y Pedidos) y consultas para Bots."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cxc.capturas.modelos import CapturaInvalida
from cxc.documentos.catalogo import (
    CatalogoSQLOdoo,
    buscar_productos,
    cargar_precios_odoo_por_producto,
    resolver_producto_unico,
)
from cxc.documentos.modelos import (
    ESTADOS_DOCUMENTO,
    TIPOS_DOCUMENTO,
    DocumentoInvalido,
)
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

PERMISOS_VER_DOCS = ("ventas", "capturas.ver", "capturas.gestionar")
PERMISOS_GESTIONAR_DOCS = ("ventas", "capturas.gestionar")


def _usuario_doc(cxc_session: str | None, *permisos: str) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if not any(tiene(user, p) for p in permisos):
        raise HTTPException(status_code=403, detail="Sin permisos para documentos comerciales")
    return user


def _actor_doc(user: dict[str, Any]) -> str:
    return str(user.get("email") or user.get("nombre") or "usuario")


def _fabrica_escritor_doc() -> EscritorOdoo | None:
    cfg = EscrituraConfig.from_env()
    if cfg is None:
        return None
    return EscritorOdoo(conectar(cfg))


def _lector_precios_odoo() -> dict[str, Any]:
    from cxc.config import AppConfig
    from cxc.odoo.directorio import conectar_lectura
    from cxc.web.app import get_valid_pricelists_usd_and_ves

    cfg = AppConfig.from_env()
    execute = conectar_lectura(cfg.odoo)
    usd_lists, ves_lists = get_valid_pricelists_usd_and_ves()
    usd_ids = [int(x) for x in usd_lists if str(x).isdigit()]
    ves_ids = [int(x) for x in ves_lists if str(x).isdigit()]
    return cargar_precios_odoo_por_producto(execute, usd_ids, ves_ids)


def get_servicio_documentos() -> ServicioDocumentos:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine = repo._engine
    return ServicioDocumentos(
        RepoDocumentosSQL(engine),
        CatalogoSQLOdoo(engine, _lector_precios_odoo),
        _fabrica_escritor_doc,
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
    moneda_lista: str = "USD"
    vendedor_email: str | None = ""
    fecha_entrega: str | None = None
    condiciones_negociacion: str | None = ""
    observaciones: str | None = ""
    lineas: list[dict[str, Any]] = Field(default_factory=list)


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
    unico, candidatos = resolver_producto_unico(items, producto_id=producto_id, texto=q)
    lista = candidatos if (q or producto_id) else buscar_productos(items, None, limite=limit)
    lim = max(1, min(limit, 50))
    return {
        "total_catalogo": len(items),
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
    # Para ventas mostramos prospectos, clientes y ambos (salvo que pidan proveedores)
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
    filtrados = buscar_productos(todos, q, limite=max(1, min(limit, 500)))
    return {
        "total_catalogo": len(todos),
        "items": [it.a_dict() for it in filtrados],
    }


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
    _usuario_doc(cxc_session, *PERMISOS_VER_DOCS)
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
    _usuario_doc(cxc_session, *PERMISOS_VER_DOCS)
    try:
        return servicio.obtener(documento_id).a_dict(incluir_lineas=True)
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


@router.post("/api/documentos/{documento_id}/enviar-odoo")
def enviar_documento_a_odoo(
    documento_id: int,
    servicio: ServicioDocsDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_doc(cxc_session, *PERMISOS_GESTIONAR_DOCS)
    try:
        enviado = servicio.enviar_a_odoo(documento_id, actor=_actor_doc(user))
    except DocumentoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento comercial no encontrado") from e
    except DocumentoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (DocumentoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(status_code=200, content=enviado.a_dict(incluir_lineas=True))
