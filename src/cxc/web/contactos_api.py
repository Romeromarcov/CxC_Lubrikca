"""API REST del Maestro Único de Contactos (Odoo + Bots + CxC) y Anti-Duplicados (Fase 6)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from cxc.contactos.modelos import (
    ESTADOS_VALIDACION,
    ORIGENES_CONTACTO,
    ROLES_CONTACTO,
    ContactoDuplicadoError,
    ContactoInvalido,
    ContactoNoEncontrado,
)
from cxc.contactos.repositorio import RepoContactosSQL
from cxc.contactos.servicio import ServicioContactos
from cxc.odoo.directorio import OdooDirectorioError
from cxc.odoo.escritura import EscritorOdoo, EscrituraConfig, conectar
from cxc.permisos import tiene
from cxc.web.capturas_api import exigir_bot_key
from cxc.web.directorio_api import (
    _obtener_contactos,
    get_lector_odoo,
)

logger = logging.getLogger("cxc.web.contactos_api")

router = APIRouter()

PERMISOS_CONTACTOS_VER = (
    "contactos",
    "ventas",
    "compras",
    "reportes",
    "auditoria",
    "capturas.ver",
    "capturas.gestionar",
    "bots.ver",
    "bots.gestionar",
)
PERMISOS_CONTACTOS_GESTION = (
    "contactos.gestionar",
    "ventas.aprobar_pedidos",
    "capturas.gestionar",
    "compras.aprobar_documentos",
)


def _engine() -> Engine:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine: Engine = repo._engine
    return engine


def _fabrica_escritor() -> EscritorOdoo | None:
    cfg = EscrituraConfig.from_env()
    if cfg is None:
        return None
    return EscritorOdoo(conectar(cfg))


def get_servicio_contactos() -> ServicioContactos:
    eng = _engine()
    return ServicioContactos(
        RepoContactosSQL(eng),
        escritor_factory=_fabrica_escritor,
        engine=eng,
    )


ServicioContactosDep = Annotated[ServicioContactos, Depends(get_servicio_contactos)]


def _exigir_permiso(cxc_session: str | None, permisos: tuple[str, ...]) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if not any(tiene(user, p) for p in permisos):
        raise HTTPException(status_code=403, detail="Sin permisos para el módulo de contactos")
    return user


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("email") or user.get("nombre") or "usuario")


def _auto_hidratar(
    svc: ServicioContactos, *, refresh: bool = False
) -> tuple[dict[str, int] | None, str | None]:
    """Sincroniza desde Odoo (solo lectura) y locales si está vacío o ``refresh=True``."""
    activos = svc.repo.listar_activos()
    if activos and not refresh:
        return None, None
    stats: dict[str, int] | None = None
    warning: str | None = None
    try:
        from cxc.web.app import app as _app

        fabrica_lector = _app.dependency_overrides.get(get_lector_odoo, get_lector_odoo)
        lector = fabrica_lector()
        contactos_odoo = _obtener_contactos(lector, refresh=refresh)
        stats = svc.sincronizar_desde_odoo(contactos_odoo)
    except HTTPException as exc:
        warning = str(exc.detail)
    except OdooDirectorioError as exc:
        warning = str(exc)
    except Exception as exc:  # pragma: no cover - contingencia de red/entorno
        warning = f"Odoo no disponible para sincronización: {exc}"
    try:
        importados = svc.absorber_locales()
        if stats is not None:
            stats["locales_importados"] = importados
        elif importados:
            stats = {"locales_importados": importados}
    except Exception as exc:
        logger.debug("absorber_locales en _auto_hidratar: %s", exc)
    return stats, warning


class ContactoCrearIn(BaseModel):
    nombre: str
    rol: str = "prospecto"
    rif: str = ""
    telefono: str = ""
    email: str = ""
    direccion: str = ""
    ciudad: str = ""
    latitud: float | None = None
    longitud: float | None = None
    plus_code: str = ""
    ubicacion: dict[str, Any] | None = None
    vendedor_email: str = ""
    notas: str = ""
    forzar_duplicado: bool = False
    enviar_a_odoo: bool = False


class ContactoEditarIn(BaseModel):
    nombre: str | None = None
    rol: str | None = None
    rif: str | None = None
    telefono: str | None = None
    email: str | None = None
    direccion: str | None = None
    ciudad: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    plus_code: str | None = None
    ubicacion: dict[str, Any] | None = None
    vendedor_email: str | None = None
    notas: str | None = None
    sincronizar_odoo: bool = False


class VerificarDuplicadoIn(BaseModel):
    nombre: str = ""
    rif: str = ""
    telefono: str = ""
    email: str = ""
    rol: str = "prospecto"
    excluir_id: int | None = None
    excluir_odoo_id: int | None = None


class FusionarContactoIn(BaseModel):
    maestro_id: int = Field(..., ge=1)


# ── Endpoints Web (Maestro Único) ────────────────────────────────────────────


@router.get("/api/contactos/maestro")
def listar_contactos_maestro(
    svc: ServicioContactosDep,
    origen: str | None = None,
    rol: str | None = None,
    estado_validacion: str | None = None,
    solo_incompletos: bool = False,
    solo_duplicados: bool = False,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    refresh: bool = False,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _exigir_permiso(cxc_session, PERMISOS_CONTACTOS_VER)
    if origen and origen not in ORIGENES_CONTACTO:
        raise HTTPException(
            status_code=422, detail=f"origen debe ser uno de: {', '.join(ORIGENES_CONTACTO)}"
        )
    if rol and rol not in ROLES_CONTACTO:
        raise HTTPException(
            status_code=422, detail=f"rol debe ser uno de: {', '.join(ROLES_CONTACTO)}"
        )
    if estado_validacion and estado_validacion not in ESTADOS_VALIDACION:
        raise HTTPException(
            status_code=422,
            detail=f"estado_validacion debe ser uno de: {', '.join(ESTADOS_VALIDACION)}",
        )

    sync_stats, sync_warning = _auto_hidratar(svc, refresh=refresh)
    items, total_filtrado, resumen = svc.listar(
        origen=origen or None,
        rol=rol or None,
        estado_validacion=estado_validacion or None,
        solo_incompletos=solo_incompletos,
        solo_duplicados=solo_duplicados,
        q=q or None,
        limit=limit,
        offset=offset,
    )
    return {
        "ok": True,
        "resumen": resumen,
        "total_filtrado": total_filtrado,
        "limit": max(1, min(limit, 500)),
        "offset": max(0, offset),
        "items": [c.a_dict() for c in items],
        "puede_gestionar": any(tiene(user, p) for p in PERMISOS_CONTACTOS_GESTION),
        "sync_stats": sync_stats,
        "sync_warning": sync_warning,
    }


@router.post("/api/contactos/maestro/sincronizar")
def sincronizar_contactos_maestro(
    svc: ServicioContactosDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _exigir_permiso(cxc_session, PERMISOS_CONTACTOS_GESTION)
    stats, warning = _auto_hidratar(svc, refresh=True)
    return {
        "ok": True,
        "stats": stats or {"creados": 0, "actualizados": 0, "pares_duplicados": 0},
        "warning": warning,
        "resumen": svc.repo.resumen(),
    }


@router.post("/api/contactos/maestro/verificar-duplicados")
def verificar_duplicados_maestro(
    body: VerificarDuplicadoIn,
    svc: ServicioContactosDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _exigir_permiso(cxc_session, PERMISOS_CONTACTOS_VER)
    _auto_hidratar(svc, refresh=False)
    res = svc.verificar_duplicados(
        body.model_dump(),
        excluir_id=body.excluir_id,
        excluir_odoo_id=body.excluir_odoo_id,
    )
    return {"ok": True, **res}


@router.get("/api/contactos/maestro/duplicados")
def listar_duplicados_maestro(
    svc: ServicioContactosDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _exigir_permiso(cxc_session, PERMISOS_CONTACTOS_VER)
    _auto_hidratar(svc, refresh=False)
    pares = svc.cola_duplicados()
    return {
        "ok": True,
        "total": len(pares),
        "pares": pares,
        "puede_gestionar": any(tiene(user, p) for p in PERMISOS_CONTACTOS_GESTION),
    }


@router.post("/api/contactos/maestro")
def crear_contacto_maestro(
    body: ContactoCrearIn,
    svc: ServicioContactosDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _exigir_permiso(cxc_session, PERMISOS_CONTACTOS_GESTION)
    actor = _actor(user)
    try:
        creado = svc.crear(
            body.model_dump(exclude={"forzar_duplicado", "enviar_a_odoo"}),
            origen="cxc",
            actor=actor,
            forzar_duplicado=body.forzar_duplicado,
        )
        if body.enviar_a_odoo and creado.contacto_id is not None:
            creado = svc.validar_y_enviar_a_odoo(creado.contacto_id, actor=actor)
    except ContactoDuplicadoError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "error": str(exc), "duplicados": exc.duplicados},
        )
    except ContactoInvalido as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(status_code=201, content={"ok": True, "contacto": creado.a_dict()})


@router.put("/api/contactos/maestro/{contacto_id}")
def editar_contacto_maestro(
    contacto_id: int,
    body: ContactoEditarIn,
    svc: ServicioContactosDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _exigir_permiso(cxc_session, PERMISOS_CONTACTOS_GESTION)
    actor = _actor(user)
    cambios = body.model_dump(exclude={"sincronizar_odoo"}, exclude_none=True)
    try:
        act = svc.editar(
            contacto_id,
            cambios,
            actor=actor,
            sincronizar_odoo=body.sincronizar_odoo,
        )
    except ContactoNoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContactoDuplicadoError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "error": str(exc), "duplicados": exc.duplicados},
        )
    except ContactoInvalido as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(status_code=200, content={"ok": True, "contacto": act.a_dict()})


@router.post("/api/contactos/maestro/{contacto_id}/validar-odoo")
def validar_y_enviar_contacto_odoo(
    contacto_id: int,
    svc: ServicioContactosDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _exigir_permiso(cxc_session, PERMISOS_CONTACTOS_GESTION)
    try:
        act = svc.validar_y_enviar_a_odoo(contacto_id, actor=_actor(user))
    except ContactoNoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContactoDuplicadoError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "error": str(exc), "duplicados": exc.duplicados},
        )
    except ContactoInvalido as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(status_code=200, content={"ok": True, "contacto": act.a_dict()})


@router.post("/api/contactos/maestro/{contacto_id}/fusionar")
def fusionar_contacto_maestro(
    contacto_id: int,
    body: FusionarContactoIn,
    svc: ServicioContactosDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _exigir_permiso(cxc_session, PERMISOS_CONTACTOS_GESTION)
    try:
        res = svc.fusionar(contacto_id, body.maestro_id, actor=_actor(user))
    except ContactoNoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContactoInvalido as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **res}


@router.post("/api/contactos/maestro/{contacto_id}/descartar")
def descartar_contacto_maestro(
    contacto_id: int,
    svc: ServicioContactosDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _exigir_permiso(cxc_session, PERMISOS_CONTACTOS_GESTION)
    try:
        c = svc.descartar(contacto_id, actor=_actor(user))
    except ContactoNoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "contacto": c.a_dict()}


# ── Endpoints para Bots de Telegram (X-Bot-Key) ──────────────────────────────


@router.post("/api/bots/contactos/verificar", dependencies=[Depends(exigir_bot_key)])
def bot_verificar_contacto(
    body: VerificarDuplicadoIn,
    svc: ServicioContactosDep,
) -> dict[str, Any]:
    _auto_hidratar(svc, refresh=False)
    return {"ok": True, **svc.verificar_duplicados(body.model_dump())}


@router.post("/api/bots/contactos", dependencies=[Depends(exigir_bot_key)])
def bot_crear_contacto(
    body: ContactoCrearIn,
    svc: ServicioContactosDep,
) -> JSONResponse:
    _auto_hidratar(svc, refresh=False)
    try:
        creado = svc.crear(
            body.model_dump(exclude={"forzar_duplicado", "enviar_a_odoo"}),
            origen="bot",
            actor=body.vendedor_email or "bot",
            forzar_duplicado=body.forzar_duplicado,
        )
    except ContactoDuplicadoError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "error": str(exc), "duplicados": exc.duplicados},
        )
    except ContactoInvalido as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(status_code=201, content={"ok": True, "contacto": creado.a_dict()})
