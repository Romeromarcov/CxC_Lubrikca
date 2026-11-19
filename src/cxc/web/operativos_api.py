"""Endpoints REST y Preview HTML de Documentos Operativos (Pagos, Compras y Gastos)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from cxc.capturas.modelos import CapturaInvalida
from cxc.capturas.repositorio import RepoCapturasSQL
from cxc.odoo.directorio import ContactoOdoo, CuentaContable
from cxc.odoo.escritura import EscritorOdoo, EscrituraConfig, conectar
from cxc.operativos.modelos import (
    ESTADOS_OPERATIVOS,
    TIPO_PAGO,
    TIPOS_OPERATIVOS,
    OperativoEstadoInvalido,
    OperativoInvalido,
    OperativoNoEncontrado,
    OperativoYaEnviado,
)
from cxc.operativos.preview_html import renderizar_preview_operativo
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.permisos import tiene
from cxc.web.capturas_api import _diarios_env, exigir_bot_key
from cxc.web.directorio_api import _obtener_contactos, _obtener_cuentas, get_lector_odoo

logger = logging.getLogger("cxc.operativos.api")

router = APIRouter()

PERMISO_APROBAR_PAGOS = "cobranza.aprobar_pagos"
PERMISO_APROBAR_COMPRAS = "compras.aprobar_documentos"
PERMISOS_VER_OPERATIVOS = (
    "cobranza",
    PERMISO_APROBAR_PAGOS,
    PERMISO_APROBAR_COMPRAS,
    "capturas.ver",
    "capturas.gestionar",
)
PERMISOS_GESTIONAR_OPERATIVOS = (
    "cobranza",
    PERMISO_APROBAR_PAGOS,
    PERMISO_APROBAR_COMPRAS,
    "capturas.gestionar",
)


def _usuario_op(cxc_session: str | None, *permisos: str) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if not any(tiene(user, p) for p in permisos):
        raise HTTPException(status_code=403, detail="Sin permisos para documentos operativos")
    return user


def _actor_op(user: dict[str, Any]) -> str:
    return str(user.get("email") or user.get("nombre") or "usuario")


def _permiso_aprobacion_para(tipo: str) -> str:
    return PERMISO_APROBAR_PAGOS if tipo == TIPO_PAGO else PERMISO_APROBAR_COMPRAS


def _exigir_permiso_aprobacion(user: dict[str, Any], tipo: str) -> None:
    req = _permiso_aprobacion_para(tipo)
    if not tiene(user, req):
        lbl = "pagos recibidos" if tipo == TIPO_PAGO else "facturas, pagos a proveedores y gastos"
        raise HTTPException(
            status_code=403,
            detail=f"Necesitas el permiso «{req}» para autorizar {lbl}.",
        )


def _fabrica_escritor_op() -> EscritorOdoo | None:
    cfg = EscrituraConfig.from_env()
    if cfg is None:
        return None
    return EscritorOdoo(conectar(cfg))


def _contactos_seguros() -> Sequence[ContactoOdoo]:
    from cxc.web.app import app as _app

    try:
        fabrica_lector = _app.dependency_overrides.get(get_lector_odoo, get_lector_odoo)
        lector = fabrica_lector()
        return _obtener_contactos(lector, refresh=False)
    except Exception:
        return ()


def _cuentas_seguras() -> Sequence[CuentaContable]:
    from cxc.web.app import app as _app

    try:
        fabrica_lector = _app.dependency_overrides.get(get_lector_odoo, get_lector_odoo)
        lector = fabrica_lector()
        return _obtener_cuentas(lector, refresh=False)
    except Exception:
        return ()


def get_servicio_operativos() -> ServicioOperativos:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine = repo._engine
    return ServicioOperativos(
        RepoOperativosSQL(engine),
        _fabrica_escritor_op,
        repo_cxc=repo,
        obtener_contactos=_contactos_seguros,
        obtener_cuentas=_cuentas_seguras,
        diarios_extra=_diarios_env(),
        repo_capturas=RepoCapturasSQL(engine),
    )


ServicioOpDep = Annotated[ServicioOperativos, Depends(get_servicio_operativos)]


class OperativoValidarIn(BaseModel):
    tipo: str
    excluir_id: int | None = None
    datos: dict[str, Any] = Field(default_factory=dict)


class RechazoOpIn(BaseModel):
    motivo: str = ""


# ── Endpoints para Bots de Telegram (X-Bot-Key) ──────────────────────────────


@router.get("/api/bots/operativos/catalogos", dependencies=[Depends(exigir_bot_key)])
def bot_catalogos_operativos(servicio: ServicioOpDep) -> dict[str, Any]:
    return servicio.catalogos()


@router.post("/api/bots/operativos/validar", dependencies=[Depends(exigir_bot_key)])
def bot_validar_operativo(
    body: OperativoValidarIn,
    servicio: ServicioOpDep,
) -> JSONResponse:
    try:
        res = servicio.validar_en_vivo(body.tipo, body.datos, excluir_id=body.excluir_id)
    except (OperativoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=res)


# ── Preview Público y Confirmación de Incompletos (fuera de /api/) ───────────


@router.get("/comprobante/{token}", response_class=HTMLResponse)
def ver_preview_operativo(token: str, servicio: ServicioOpDep) -> HTMLResponse:
    try:
        doc = servicio.obtener_por_token(token)
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado") from e
    return HTMLResponse(content=renderizar_preview_operativo(doc))


@router.post("/comprobante/{token}/confirmar")
def confirmar_preview_operativo(
    token: str,
    servicio: ServicioOpDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> JSONResponse:
    try:
        doc = servicio.confirmar_por_token(token, body, actor="preview_comprobante")
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado") from e
    except (OperativoYaEnviado, OperativoEstadoInvalido) as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (OperativoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=doc.a_dict())


# ── Endpoints Web de CxC (Cobranza y Compras/Gastos) ─────────────────────────


@router.get("/api/operativos/catalogos")
def listar_catalogos_operativos(
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    return servicio.catalogos()


@router.post("/api/operativos/validar")
def validar_operativo_web(
    body: OperativoValidarIn,
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    try:
        res = servicio.validar_en_vivo(body.tipo, body.datos, excluir_id=body.excluir_id)
    except (OperativoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=res)


@router.get("/api/operativos")
def listar_operativos(
    servicio: ServicioOpDep,
    tipo: str | None = None,
    grupo: str | None = None,
    estado: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    if tipo and tipo not in TIPOS_OPERATIVOS:
        raise HTTPException(
            status_code=422, detail=f"tipo debe ser uno de {', '.join(TIPOS_OPERATIVOS)}"
        )
    if estado and estado not in ESTADOS_OPERATIVOS:
        raise HTTPException(
            status_code=422, detail=f"estado debe ser uno de {', '.join(ESTADOS_OPERATIVOS)}"
        )
    if grupo and grupo not in ("cobranza", "compras"):
        raise HTTPException(status_code=422, detail="grupo debe ser cobranza o compras")
    items, total, resumen = servicio.listar(
        tipo=tipo,
        grupo=grupo,
        estado=estado,
        q=q,
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
    )
    return {
        "puede_aprobar_pagos": tiene(user, PERMISO_APROBAR_PAGOS),
        "puede_aprobar_compras": tiene(user, PERMISO_APROBAR_COMPRAS),
        "escritura_odoo_activa": EscrituraConfig.from_env() is not None,
        "resumen": resumen,
        "total": total,
        "items": [d.a_dict() for d in items],
    }


@router.get("/api/operativos/{documento_id}")
def obtener_operativo(
    documento_id: int,
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    try:
        doc = servicio.obtener(documento_id)
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento operativo no encontrado") from e
    out = doc.a_dict()
    out["puede_aprobar_este_tipo"] = tiene(user, _permiso_aprobacion_para(doc.tipo))
    return out


@router.post("/api/operativos")
def crear_operativo(
    servicio: ServicioOpDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_op(cxc_session, *PERMISOS_GESTIONAR_OPERATIVOS)
    try:
        creado = servicio.crear(body, origen="web", actor=_actor_op(user))
    except (OperativoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=201, content=creado.a_dict())


@router.put("/api/operativos/{documento_id}")
def editar_operativo(
    documento_id: int,
    servicio: ServicioOpDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_op(cxc_session, *PERMISOS_GESTIONAR_OPERATIVOS)
    try:
        actualizado = servicio.editar(documento_id, body, actor=_actor_op(user))
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento operativo no encontrado") from e
    except OperativoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (OperativoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    return JSONResponse(status_code=200, content=actualizado.a_dict())


@router.post("/api/operativos/{documento_id}/aprobar")
def aprobar_y_enviar_operativo_a_odoo(
    documento_id: int,
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    try:
        doc = servicio.obtener(documento_id)
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento operativo no encontrado") from e

    _exigir_permiso_aprobacion(user, doc.tipo)
    try:
        aprobado = servicio.aprobar_y_enviar_a_odoo(documento_id, actor=_actor_op(user))
    except (OperativoYaEnviado, OperativoEstadoInvalido) as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (OperativoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(status_code=200, content=aprobado.a_dict())


@router.post("/api/operativos/{documento_id}/rechazar")
def rechazar_operativo(
    documento_id: int,
    servicio: ServicioOpDep,
    body: Annotated[RechazoOpIn, Body(default_factory=RechazoOpIn)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    try:
        doc = servicio.obtener(documento_id)
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento operativo no encontrado") from e

    _exigir_permiso_aprobacion(user, doc.tipo)
    try:
        rechazado = servicio.rechazar(documento_id, motivo=body.motivo, actor=_actor_op(user))
    except OperativoYaEnviado as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    return JSONResponse(status_code=200, content=rechazado.a_dict())
