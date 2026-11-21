"""Endpoints REST y Preview HTML de Documentos Operativos (Pagos, Compras y Gastos)."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from cxc.bancos import conciliacion as bancos_conciliacion
from cxc.bancos import repositorio as bancos_repo
from cxc.capturas.modelos import CapturaInvalida
from cxc.capturas.modelos import es_efectivo as op_es_efectivo
from cxc.capturas.repositorio import RepoCapturasSQL
from cxc.config import AppConfig
from cxc.engine.equivalents import equivalente_usd_a_tasa
from cxc.engine.pagos_duplicados import detectar_pagos_duplicados
from cxc.engine.reasignaciones import pago_reasignado_mas_reciente
from cxc.odoo.directorio import ContactoOdoo, CuentaContable
from cxc.odoo.escritura import EscritorOdoo, EscrituraConfig, conectar
from cxc.operativos.contabilidad_gastos import (
    cargar_config_gastos,
    categorias_y_metodos_catalogo,
    guardar_config_gastos,
)
from cxc.operativos.lotes_efectivo import (
    RepoLotesEfectivo,
    agrupar_efectivo_pendiente_por_vendedor,
    es_pago_efectivo,
    mapa_conciliacion_pagos,
    render_recibo_lote_html,
)
from cxc.operativos.modelos import (
    BORRADOR,
    CONFIRMADO_CXC,
    ENVIADO_ODOO,
    ERROR_ODOO,
    ESTADOS_OPERATIVOS,
    ETIQUETA_POR_TIPO,
    INCOMPLETO,
    PENDIENTE_APROBACION,
    RECHAZADO,
    TIPO_FACTURA_PROVEEDOR,
    TIPO_GASTO,
    TIPO_PAGO,
    TIPO_PAGO_PROVEEDOR,
    TIPOS_OPERATIVOS,
    OperativoEstadoInvalido,
    OperativoInvalido,
    OperativoNoEncontrado,
    OperativoYaEnviado,
)
from cxc.operativos.preview_html import (
    renderizar_preview_operativo,
    renderizar_recibo_operativo,
)
from cxc.operativos.repositorio import RepoLotesCajaChicaSQL, RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import RepoCuentasTerceros, RepoCuentasTercerosSQL
from cxc.operativos.vinculaciones_docs import (
    VinculacionInvalida,
    VinculacionNoEncontrada,
    candidatos_compras_para_pago,
    candidatos_pagos_para_compra,
    candidatos_ventas_para_pago,
    desvincular_pago_compra,
    mapa_vinculaciones_compras,
    mapa_vinculaciones_ventas,
    vincular_pago_compra,
)
from cxc.permisos import tiene
from cxc.rates import Tasas
from cxc.web.capturas_api import _diarios_env, exigir_bot_key
from cxc.web.directorio_api import _obtener_contactos, _obtener_cuentas, get_lector_odoo

logger = logging.getLogger("cxc.operativos.api")

router = APIRouter()

PERMISO_APROBAR_PAGOS = "cobranza.aprobar_pagos"
PERMISO_APROBAR_COMPRAS = "compras.aprobar_documentos"
PERMISOS_VER_OPERATIVOS = (
    "pagos",
    "cobranza",
    "compras",
    PERMISO_APROBAR_PAGOS,
    PERMISO_APROBAR_COMPRAS,
    "capturas.ver",
    "capturas.gestionar",
)
PERMISOS_GESTIONAR_OPERATIVOS = (
    "pagos",
    "cobranza",
    "compras",
    PERMISO_APROBAR_PAGOS,
    PERMISO_APROBAR_COMPRAS,
    "capturas.gestionar",
)


def _usuario_op(cxc_session: str | None, *permisos: str) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if permisos and not any(tiene(user, p) for p in permisos):
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


def get_repo_terceros() -> RepoCuentasTerceros:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    return RepoCuentasTercerosSQL(repo._engine)


def get_servicio_operativos() -> ServicioOperativos:
    from cxc.web.app import app as _app
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine = repo._engine
    fabrica_terceros = _app.dependency_overrides.get(get_repo_terceros, get_repo_terceros)
    try:
        repo_terceros: RepoCuentasTerceros | None = fabrica_terceros()
    except Exception:
        repo_terceros = None
    return ServicioOperativos(
        RepoOperativosSQL(engine),
        _fabrica_escritor_op,
        repo_cxc=repo,
        obtener_contactos=_contactos_seguros,
        obtener_cuentas=_cuentas_seguras,
        diarios_extra=_diarios_env(),
        repo_capturas=RepoCapturasSQL(engine),
        repo_terceros=repo_terceros,
    )


ServicioOpDep = Annotated[ServicioOperativos, Depends(get_servicio_operativos)]
RepoTercerosDep = Annotated[RepoCuentasTerceros, Depends(get_repo_terceros)]


class OperativoValidarIn(BaseModel):
    tipo: str
    excluir_id: int | None = None
    datos: dict[str, Any] = Field(default_factory=dict)


class RechazoOpIn(BaseModel):
    motivo: str = ""


class ConfirmarCxcIn(BaseModel):
    enviar_a_odoo: bool = False


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


@router.get("/recibo/{token}", response_class=HTMLResponse)
def ver_recibo_operativo(
    token: str,
    servicio: ServicioOpDep,
    repo_terceros: RepoTercerosDep,
) -> HTMLResponse:
    try:
        doc = servicio.obtener_por_token(token)
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Recibo no encontrado") from e
    if not doc.numero_recibo:
        raise HTTPException(
            status_code=404,
            detail="Este documento aún no ha sido confirmado en CxC ni tiene recibo emitido.",
        )
    cta_dict: dict[str, Any] | None = None
    if doc.cuenta_tercero_id is not None:
        cta = repo_terceros.obtener(doc.cuenta_tercero_id)
        if cta is not None:
            cta_dict = cta.a_dict()
    return HTMLResponse(content=renderizar_recibo_operativo(doc, cta_dict))


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
    if grupo and grupo not in ("cobranza", "compras", "pagos_enviados", "compras_solo"):
        raise HTTPException(
            status_code=422,
            detail="grupo debe ser cobranza, compras, pagos_enviados o compras_solo",
        )
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


@router.post("/api/operativos/{documento_id}/confirmar-cxc")
def confirmar_operativo_en_cxc(
    documento_id: int,
    servicio: ServicioOpDep,
    body: Annotated[ConfirmarCxcIn, Body(default_factory=ConfirmarCxcIn)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    try:
        doc = servicio.obtener(documento_id)
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento operativo no encontrado") from e

    if doc.tipo == TIPO_PAGO:
        if not (tiene(user, PERMISO_APROBAR_PAGOS) or tiene(user, "cobranza.recibo")):
            raise HTTPException(
                status_code=403,
                detail="Necesitas aprobar_pagos o cobranza.recibo para confirmar en CxC.",
            )
    elif doc.tipo == TIPO_PAGO_PROVEEDOR:
        _exigir_permiso_aprobacion(user, doc.tipo)
    if body.enviar_a_odoo:
        _exigir_permiso_aprobacion(user, doc.tipo)

    try:
        confirmado = servicio.confirmar_en_cxc(
            documento_id, actor=_actor_op(user), enviar_a_odoo=body.enviar_a_odoo
        )
    except (OperativoYaEnviado, OperativoEstadoInvalido) as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except (OperativoInvalido, CapturaInvalida) as e:
        faltan = getattr(e, "faltan", [])
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": faltan})
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse(status_code=200, content=confirmado.a_dict())


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


# ── Configuración de Cuentas Zelle de Terceros y Diarios Generales ───────────


@router.get("/api/config/cuentas-terceros")
def listar_cuentas_terceros(
    repo_terceros: RepoTercerosDep,
    solo_activas: bool = False,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_op(cxc_session, "pagos", "cobranza", "configuracion", PERMISO_APROBAR_PAGOS)
    items = repo_terceros.listar(solo_activas=solo_activas)
    return {"items": [c.a_dict() for c in items], "total": len(items)}


@router.post("/api/config/cuentas-terceros")
def crear_cuenta_tercero(
    repo_terceros: RepoTercerosDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_op(cxc_session, PERMISO_APROBAR_PAGOS)
    try:
        creada = repo_terceros.crear(body, actor=_actor_op(user))
    except OperativoInvalido as e:
        return JSONResponse(status_code=422, content={"error": e.error, "faltan": e.faltan})
    return JSONResponse(status_code=201, content=creada.a_dict())


@router.put("/api/config/cuentas-terceros/{cuenta_id}")
def editar_cuenta_tercero(
    cuenta_id: int,
    repo_terceros: RepoTercerosDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    _usuario_op(cxc_session, PERMISO_APROBAR_PAGOS)
    try:
        actualizada = repo_terceros.actualizar(cuenta_id, body)
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Cuenta de tercero no encontrada") from e
    except OperativoInvalido as e:
        return JSONResponse(status_code=422, content={"error": e.error, "faltan": e.faltan})
    return JSONResponse(status_code=200, content=actualizada.a_dict())


@router.delete("/api/config/cuentas-terceros/{cuenta_id}")
def desactivar_cuenta_tercero(
    cuenta_id: int,
    repo_terceros: RepoTercerosDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    _usuario_op(cxc_session, PERMISO_APROBAR_PAGOS)
    try:
        desactivada = repo_terceros.desactivar(cuenta_id)
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Cuenta de tercero no encontrada") from e
    return JSONResponse(status_code=200, content=desactivada.a_dict())


@router.get("/api/config/diarios-generales")
def listar_diarios_generales(
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_op(cxc_session, "configuracion", "pagos", "compras", PERMISO_APROBAR_PAGOS)
    try:
        escritor = servicio._fabrica()
        if escritor is not None:
            return {"items": escritor.diarios_generales()}
    except Exception as e:
        return {"items": [], "error": str(e)}
    return {"items": []}


# ── Vista Unificada de Pagos (CxC + Espejo Odoo) ─────────────────────────────


def _odoo_base_url() -> str:
    try:
        return (AppConfig.from_env().odoo.url or "").strip().rstrip("/")
    except Exception:
        return ""


def _url_odoo_registro(model: str | None, res_id: Any) -> str:
    base = _odoo_base_url()
    sid = str(res_id or "").strip()
    if not base or not sid.isdigit():
        return ""
    modelo = (model or "account.payment").strip() or "account.payment"
    return f"{base}/web#id={int(sid)}&model={modelo}&view_type=form"


def _mapa_nombres_usuarios(repo_cxc: Any) -> dict[str, str]:
    mapa: dict[str, str] = {}
    if repo_cxc is None or not hasattr(repo_cxc, "all_usuarios_plataforma"):
        return mapa
    try:
        filas = repo_cxc.all_usuarios_plataforma()
    except Exception:
        return mapa
    for u in filas or []:
        if not isinstance(u, dict):
            continue
        nombre = str(u.get("nombre") or u.get("email") or "").strip()
        if not nombre:
            continue
        for k in (u.get("email"), u.get("user_id"), u.get("nombre")):
            if k:
                mapa[str(k).strip().lower()] = nombre
    return mapa


def _resolver_nombre_usuario(raw: Any, mapa: dict[str, str], default: str = "") -> str:
    txt = str(raw or "").strip()
    if not txt:
        return default
    return mapa.get(txt.lower(), txt)


def _tasas_para_pagos(repo_cxc: Any) -> Tasas | None:
    if repo_cxc is None:
        return None
    try:
        from cxc.web.app import tasas_vigentes

        return tasas_vigentes(repo_cxc)
    except Exception:
        try:
            hist = (
                repo_cxc.all_tasas_historicas() if hasattr(repo_cxc, "all_tasas_historicas") else []
            )
            serie = repo_cxc.all_serie_tasas() if hasattr(repo_cxc, "all_serie_tasas") else []
            return Tasas(historicas=hist, serie=serie)
        except Exception:
            return None


def _parse_fecha_pago(fecha_val: Any) -> date | None:
    if isinstance(fecha_val, datetime):
        return fecha_val.date()
    if isinstance(fecha_val, date):
        return fecha_val
    txt = str(fecha_val or "").strip()[:10]
    if not txt:
        return None
    try:
        return date.fromisoformat(txt)
    except ValueError:
        return None


def _equivalentes_pago(
    monto: Any,
    moneda: Any,
    fecha_val: Any,
    tasas: Tasas | None,
) -> dict[str, Any]:
    mon = str(moneda or "USD").upper().strip() or "USD"
    try:
        monto_dec = Decimal(str(monto)) if monto is not None else None
    except (InvalidOperation, ValueError, TypeError):
        monto_dec = None

    f_dt = _parse_fecha_pago(fecha_val)
    t_bcv_dec = tasas.bcv_usd(f_dt, arrastrar=True) if (tasas and f_dt) else None
    t_bin_dec = tasas.binance(f_dt) if (tasas and f_dt) else None
    t_eur_dec = tasas.bcv_eur(f_dt, arrastrar=True) if (tasas and f_dt) else None

    t_bcv = float(t_bcv_dec) if t_bcv_dec is not None else None
    t_bin = float(t_bin_dec) if t_bin_dec is not None else None
    t_eur = float(t_eur_dec) if t_eur_dec is not None else None

    usd_bcv = equivalente_usd_a_tasa(monto_dec, mon, t_bcv) if monto_dec is not None else None
    usd_bin = equivalente_usd_a_tasa(monto_dec, mon, t_bin) if monto_dec is not None else None
    usd_eur = equivalente_usd_a_tasa(monto_dec, mon, t_eur) if monto_dec is not None else None

    return {
        "moneda_original": mon,
        "monto_original": float(monto_dec) if monto_dec is not None else None,
        "tasa_bcv": t_bcv,
        "tasa_binance": t_bin,
        "tasa_bcv_eur": t_eur,
        "monto_bcv_usd": round(usd_bcv, 2) if usd_bcv is not None else None,
        "monto_binance_usd": round(usd_bin, 2) if usd_bin is not None else None,
        "monto_eur_usd": round(usd_eur, 2) if usd_eur is not None else None,
    }


def _estado_confirmacion_pago(
    item: dict[str, Any], *, es_efectivo: bool, conciliado_banco: bool
) -> str:
    if item.get("es_zelle_tercero"):
        if item.get("confirmado_cxc") or item.get("estado") in (CONFIRMADO_CXC, ENVIADO_ODOO):
            return "CONFIRMADO_TERCERO"
        return "PENDIENTE_TERCERO"
    if es_efectivo:
        if (
            item.get("recibido")
            or item.get("confirmado_cxc")
            or str(item.get("numero_recibo") or "").strip()
        ):
            return "RECIBIDO_EFECTIVO"
        return "PENDIENTE_EFECTIVO"
    return "CONCILIADO_BANCO" if conciliado_banco else "PENDIENTE_CONCILIACION"


def _enriquecer_pago_item(
    item: dict[str, Any],
    *,
    tasas: Tasas | None,
    usuarios_map: dict[str, str],
    odoo_num_map: dict[str, str],
    odoo_url_base: str,
    direccion: str,
    conc_map: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    eq = _equivalentes_pago(
        item.get("monto"),
        item.get("moneda"),
        item.get("fecha_documento") or item.get("creado_en"),
        tasas,
    )
    res_id = item.get("odoo_res_id") or item.get("odoo_pago_id")
    sid = str(res_id).strip() if res_id is not None else ""
    modelo = str(item.get("odoo_model") or "account.payment")
    odoo_url = (
        f"{odoo_url_base}/web#id={int(sid)}&model={modelo}&view_type=form"
        if (odoo_url_base and sid.isdigit())
        else ""
    )

    odoo_numero = str(item.get("odoo_numero") or "").strip()
    if not odoo_numero:
        raw_ref_od = str(item.get("odoo_ref") or "").strip()
        if sid and sid in odoo_num_map:
            odoo_numero = odoo_num_map[sid]
        elif raw_ref_od and ":" not in raw_ref_od:
            odoo_numero = raw_ref_od
        elif item.get("origen_sistema") == "odoo":
            odoo_numero = str(item.get("numero") or "")
        elif sid.isdigit():
            pref_od = (
                "ASIENTO"
                if (item.get("odoo_model") == "account.move" or item.get("es_zelle_tercero"))
                else "PAGO"
            )
            odoo_numero = f"{pref_od}-{sid}"

    if direccion == "enviados":
        reg_raw = item.get("creado_por") or ("Odoo" if item.get("origen_sistema") == "odoo" else "")
        apr_raw = (
            item.get("aprobado_por")
            or item.get("confirmado_cxc_por")
            or ("Confirmado en Odoo" if item.get("origen_sistema") == "odoo" else "")
        )
    else:
        reg_raw = (
            item.get("vendedor")
            or item.get("creado_por")
            or ("Odoo" if item.get("origen_sistema") == "odoo" else "")
        )
        apr_raw = item.get("confirmado_cxc_por") or item.get("aprobado_por") or ""

    es_ef = es_pago_efectivo(
        item.get("metodo_pago"), diario_nombre=item.get("diario_nombre")
    ) and not bool(item.get("es_zelle_tercero"))

    huella = str(item.get("conciliado_banco_huella") or "").strip()
    por_pago = (conc_map or {}).get("por_pago_id", {})
    por_huella = (conc_map or {}).get("por_huella", {})
    info_banco = (por_huella.get(huella) if huella else None) or (
        por_pago.get(sid) if sid else None
    )
    if not huella and info_banco:
        huella = str(info_banco.get("huella") or "")
    conciliado_banco = bool(info_banco or huella)

    if not es_ef and not item.get("es_zelle_tercero") and conciliado_banco:
        item["recibido"] = True
        item["confirmado_cxc"] = True
        if not apr_raw and info_banco and info_banco.get("decidida_por"):
            apr_raw = str(info_banco["decidida_por"])

    vend_raw = str(item.get("vendedor") or reg_raw or "").strip()
    numero_cxc = (
        str(item.get("numero") or "").strip() or None
        if item.get("origen_sistema") == "cxc"
        else None
    )

    item["equivalentes"] = eq
    item["tasa_bcv"] = eq["tasa_bcv"]
    item["tasa_binance"] = eq["tasa_binance"]
    item["tasa_bcv_eur"] = eq["tasa_bcv_eur"]
    item["monto_bcv_usd"] = eq["monto_bcv_usd"]
    item["monto_binance_usd"] = eq["monto_binance_usd"]
    item["monto_eur_usd"] = eq["monto_eur_usd"]
    item["es_efectivo"] = es_ef
    item["numero_cxc"] = numero_cxc
    item["odoo_pago_id"] = sid or None
    item["odoo_id"] = int(sid) if sid.isdigit() else None
    item["odoo_numero"] = odoo_numero or None
    item["odoo_url"] = odoo_url or None
    item["conciliado_banco"] = conciliado_banco
    item["conciliado_banco_huella"] = huella
    item["conciliado_banco_info"] = info_banco
    item["estado_confirmacion_pago"] = _estado_confirmacion_pago(
        item, es_efectivo=es_ef, conciliado_banco=conciliado_banco
    )
    item["vendedor"] = vend_raw
    item["vendedor_nombre"] = _resolver_nombre_usuario(vend_raw, usuarios_map)
    item["registrado_por"] = str(reg_raw or "")
    item["registrado_por_nombre"] = _resolver_nombre_usuario(reg_raw, usuarios_map)
    item["aprobado_o_recibido_por"] = str(apr_raw or "")
    item["aprobado_o_recibido_por_nombre"] = _resolver_nombre_usuario(apr_raw, usuarios_map)
    return item


def _odoo_recibido_a_item(
    row: dict[str, str],
    clientes_map: dict[str, str],
    metodos_map: dict[str, str],
) -> dict[str, Any]:
    pid = str(row.get("pago_id") or "").strip()
    cid = str(row.get("cliente_id") or "").strip()
    mid = str(row.get("metodo_pago") or "").strip()
    fecha_iso = str(row.get("fecha_pago") or "")[:10] or None
    try:
        monto_val: float | None = float(row.get("monto") or 0)
    except (TypeError, ValueError):
        monto_val = None
    metodo_nombre = metodos_map.get(mid, mid)
    num_odoo = str(row.get("numero_odoo") or f"ODOO-{pid}")
    vend = str(
        row.get("vendedor_email") or row.get("vendedor_nombre") or row.get("creado_por") or ""
    ).strip()
    rec_flag = str(row.get("recibido") or "").upper() == "TRUE"
    return {
        "id": None,
        "documento_id": None,
        "pago_id": pid,
        "numero": num_odoo,
        "numero_cxc": None,
        "odoo_numero": num_odoo,
        "token_preview": "",
        "preview_path": "",
        "recibo_path": "",
        "tipo": TIPO_PAGO,
        "tipo_etiqueta": "Pago recibido de cliente",
        "estado": "CONFIRMADO_ODOO",
        "origen": "odoo",
        "origen_sistema": "odoo",
        "captura_id": None,
        "partner_odoo_id": int(cid) if cid.isdigit() else None,
        "contraparte_nombre": clientes_map.get(cid, f"Cliente #{cid}" if cid else "Cliente Odoo"),
        "rif": "",
        "monto": monto_val,
        "moneda": str(row.get("moneda") or "USD"),
        "metodo_pago": metodo_nombre,
        "es_efectivo": es_pago_efectivo(metodo_nombre, diario_nombre=metodo_nombre),
        "es_zelle_tercero": False,
        "cuenta_tercero_id": None,
        "beneficiario_tercero": "",
        "banco": "",
        "diario_id": int(mid) if mid.isdigit() else None,
        "diario_nombre": metodo_nombre,
        "referencia": str(row.get("referencia") or ""),
        "pedido_ref": "",
        "factura_ref": "",
        "fecha_documento": fecha_iso,
        "notas": "",
        "alertas": [],
        "faltantes": [],
        "recibido": rec_flag,
        "confirmado_cxc": rec_flag,
        "confirmado_cxc_por": str(row.get("recibido_por") or ""),
        "vendedor": vend,
        "creado_por": vend,
        "aprobado_por": str(row.get("recibido_por") or ""),
        "numero_recibo": str(row.get("numero_recibido") or ""),
        "odoo_model": "account.payment",
        "odoo_res_id": int(pid) if pid.isdigit() else None,
        "odoo_id": int(pid) if pid.isdigit() else None,
        "odoo_pago_id": pid,
        "odoo_ref": f"account.payment:{pid}",
        "en_espejo_odoo": True,
        "creado_en": str(row.get("fecha_pago") or ""),
        "puede_editar": False,
        "puede_confirmar_cxc": False,
        "puede_aprobar": False,
        "puede_rechazar": False,
    }


def _odoo_enviado_a_item(row: dict[str, Any]) -> dict[str, Any]:
    pid = str(row.get("pago_id") or "").strip()
    pid_partner = row.get("partner_odoo_id")
    fecha_iso = str(row.get("fecha_pago") or "")[:10] or None
    num_odoo = str(row.get("numero_odoo") or f"ODOO-{pid}")
    return {
        "id": None,
        "documento_id": None,
        "pago_id": pid,
        "numero": num_odoo,
        "numero_cxc": None,
        "odoo_numero": num_odoo,
        "token_preview": "",
        "preview_path": "",
        "recibo_path": "",
        "tipo": TIPO_PAGO_PROVEEDOR,
        "tipo_etiqueta": "Pago a proveedor",
        "estado": "CONFIRMADO_ODOO",
        "origen": "odoo",
        "origen_sistema": "odoo",
        "captura_id": None,
        "partner_odoo_id": int(pid_partner) if pid_partner is not None else None,
        "contraparte_nombre": str(row.get("proveedor_nombre") or f"Proveedor #{pid_partner or ''}"),
        "rif": str(row.get("rif") or ""),
        "monto": float(row["monto"]) if row.get("monto") is not None else None,
        "moneda": str(row.get("moneda") or "USD"),
        "metodo_pago": str(row.get("diario_nombre") or ""),
        "es_efectivo": es_pago_efectivo("", diario_nombre=row.get("diario_nombre")),
        "es_zelle_tercero": False,
        "cuenta_tercero_id": None,
        "beneficiario_tercero": "",
        "banco": "",
        "diario_id": row.get("diario_id"),
        "diario_nombre": str(row.get("diario_nombre") or ""),
        "referencia": str(row.get("referencia") or ""),
        "pedido_ref": "",
        "factura_ref": "",
        "fecha_documento": fecha_iso,
        "notas": "",
        "alertas": [],
        "faltantes": [],
        "confirmado_cxc": False,
        "confirmado_cxc_por": "",
        "creado_por": str(row.get("creado_por") or ""),
        "aprobado_por": str(row.get("aprobado_por") or row.get("recibido_por") or ""),
        "numero_recibo": "",
        "odoo_model": "account.payment",
        "odoo_res_id": int(pid) if pid.isdigit() else None,
        "odoo_id": int(pid) if pid.isdigit() else None,
        "odoo_pago_id": pid,
        "odoo_ref": f"account.payment:{pid}",
        "en_espejo_odoo": True,
        "creado_en": fecha_iso or "",
        "puede_editar": False,
        "puede_confirmar_cxc": False,
        "puede_aprobar": False,
        "puede_rechazar": False,
    }


def _enriquecer_aplicaciones_recibidos(
    todos: list[dict[str, Any]],
    repo_cxc: Any,
    engine: Any,
) -> dict[str, int]:
    """Agrega vinculaciones, estado de aplicación, FIFO, duplicados y reasignaciones."""
    try:
        mapa_v = mapa_vinculaciones_ventas(engine, repo_cxc)
    except Exception:
        mapa_v = {"por_pago_id": {}, "por_pago_op_id": {}, "por_so_id": {}}

    # Duplicados en todo el universo por (cliente, monto, moneda, método/diario, fecha)
    dup_inputs: list[dict[str, Any]] = []
    claves_item: list[str] = []
    for idx, it in enumerate(todos):
        pid_k = str(
            it.get("odoo_pago_id") or it.get("pago_id") or f"op-{it.get('id') or idx}"
        ).strip()
        claves_item.append(pid_k)
        dup_inputs.append(
            {
                "pago_id": pid_k,
                "cliente_id": str(it.get("partner_odoo_id") or ""),
                "monto": it.get("monto") or 0,
                "moneda": it.get("moneda") or "",
                "metodo_pago": it.get("diario_nombre") or it.get("metodo_pago") or "",
                "fecha_pago": str(it.get("fecha_documento") or "")[:10],
            }
        )
    try:
        dups_map = detectar_pagos_duplicados(dup_inputs)
    except Exception:
        dups_map = {}

    try:
        reasig_map = pago_reasignado_mas_reciente(repo_cxc.all_auditoria())
    except Exception:
        reasig_map = {}

    cerrados_map: dict[str, dict[str, str]] = {}
    try:
        cerrados_rows = repo_cxc.all_pagos_huerfanos_cerrados()
        for r in cerrados_rows or []:
            es_dict = isinstance(r, dict)
            pid_c = str(r.get("pago_id") if es_dict else getattr(r, "pago_id", "")).strip()
            if pid_c:
                cerrados_map[pid_c] = {
                    "motivo": str(r.get("motivo") if es_dict else getattr(r, "motivo", "") or ""),
                    "cerrado_por": str(
                        r.get("cerrado_por") if es_dict else getattr(r, "cerrado_por", "") or ""
                    ),
                    "timestamp_cierre": str(
                        r.get("timestamp_cierre")
                        if es_dict
                        else getattr(r, "timestamp_cierre", "") or ""
                    ),
                }
    except Exception:
        pass

    # Órdenes abiertas por cliente para sugerencia FIFO O(1) (sin llamadas a Odoo)
    ordenes_abiertas_cli: dict[str, list[tuple[str, str, float, str | None]]] = {}
    por_so = mapa_v.get("por_so_id", {})
    try:
        for o in repo_cxc.all_ordenes():
            if str(getattr(o, "estado_orden", "") or "").lower() not in ("sale", "done"):
                continue
            vincs_so = por_so.get(o.so_id, [])
            vinc_u = sum(float(v.get("equiv_usd_bcv") or 0) for v in vincs_so)
            sal = max(0.0, round(float(o.monto_total or 0) - vinc_u, 2))
            if sal <= 0.01:
                continue
            cid = str(o.cliente_id or "").strip()
            if cid:
                ordenes_abiertas_cli.setdefault(cid, []).append(
                    (o.fecha.isoformat(), o.so_id, sal, getattr(o, "factura_id", None))
                )
        for lst in ordenes_abiertas_cli.values():
            lst.sort(key=lambda x: (x[0], x[1]))
    except Exception:
        ordenes_abiertas_cli = {}

    pend_vinc = 0
    vinc_cxc = 0
    conc_odoo_app = 0
    dups_cnt = 0
    cerrados_cnt = 0

    for idx, it in enumerate(todos):
        pid_k = claves_item[idx]
        pid_od = str(it.get("odoo_pago_id") or it.get("pago_id") or "").strip()
        doc_id = it.get("documento_id") or it.get("id")

        vincs: list[dict[str, Any]] = []
        vistos_v: set[tuple[Any, Any]] = set()
        for v in (
            *(mapa_v["por_pago_id"].get(pid_od, []) if pid_od else ()),
            *(mapa_v["por_pago_op_id"].get(int(doc_id), []) if doc_id else ()),
        ):
            vk = (v.get("origen_registro"), v.get("vinc_id"))
            if vk not in vistos_v:
                vistos_v.add(vk)
                vincs.append(v)

        monto_orig = float(it.get("monto") or 0)
        usado_orig = round(sum(float(v.get("monto_aplicado") or 0) for v in vincs), 2)
        disp_orig = max(0.0, round(monto_orig - usado_orig, 2))
        ap_usd = round(sum(float(v.get("equiv_usd_bcv") or 0) for v in vincs), 2)

        if pid_od and pid_od in cerrados_map:
            est_app = "cerrado_empresa"
            cerrados_cnt += 1
            c_info = cerrados_map[pid_od]
            it["cerrado_motivo"] = c_info.get("motivo") or ""
            it["cerrado_por"] = c_info.get("cerrado_por") or ""
            it["cerrado_timestamp"] = c_info.get("timestamp_cierre") or ""
        elif vincs and any(v.get("conciliado_odoo") for v in vincs):
            est_app = "conciliado_odoo"
            conc_odoo_app += 1
        elif vincs:
            est_app = "vinculado_cxc"
            vinc_cxc += 1
        else:
            est_app = "pendiente_vincular"
            if it.get("estado") != RECHAZADO:
                pend_vinc += 1

        sug_fifo = None
        cid_cli = str(it.get("partner_odoo_id") or "").strip()
        if (
            est_app == "pendiente_vincular"
            and it.get("estado") != RECHAZADO
            and cid_cli in ordenes_abiertas_cli
        ):
            f_ord, so_sug, sal_sug, fac_sug = ordenes_abiertas_cli[cid_cli][0]
            sug_fifo = {
                "so_id": so_sug,
                "fecha": f_ord,
                "saldo_pendiente_usd": sal_sug,
                "factura_id": fac_sug,
            }

        dups = dups_map.get(pid_k) or []
        if dups:
            dups_cnt += 1
        reasig = reasig_map.get(pid_od) if pid_od else None

        it["vinculaciones"] = vincs
        it["documentos_vinculados"] = list(
            dict.fromkeys(str(v["so_id"]) for v in vincs if v.get("so_id"))
        )
        it["monto_aplicado_usd"] = ap_usd
        it["monto_disponible_original"] = disp_orig
        it["estado_aplicacion"] = est_app
        it["sugerencia_fifo"] = sug_fifo
        it["posible_duplicado"] = bool(dups)
        it["duplicado_de"] = dups
        it["reasignado_por_odoo"] = reasig is not None
        it["reasignado_detalle"] = reasig.get("detalle_odoo") if reasig else None
        it["reasignado_fecha"] = str(reasig.get("timestamp_audit") or "")[:10] if reasig else None

    return {
        "pendientes_vincular": pend_vinc,
        "vinculados_cxc": vinc_cxc,
        "conciliados_odoo_app": conc_odoo_app,
        "duplicados": dups_cnt,
        "cerrados_empresa": cerrados_cnt,
    }


def _enriquecer_aplicaciones_enviados(todos: list[dict[str, Any]], engine: Any) -> dict[str, int]:
    """Agrega vinculaciones de compras/gastos sobre pagos enviados."""
    try:
        mapa_c = mapa_vinculaciones_compras(engine)
    except Exception:
        mapa_c = {"por_pago_op_id": {}, "por_pago_odoo_id": {}}

    vinc_cxc = 0
    conc_odoo = 0
    for it in todos:
        pid_od = str(it.get("odoo_pago_id") or it.get("pago_id") or "").strip()
        doc_id = it.get("documento_id") or it.get("id")
        vincs: list[dict[str, Any]] = []
        vistos: set[Any] = set()
        for v in (
            *(mapa_c["por_pago_odoo_id"].get(pid_od, []) if pid_od else ()),
            *(mapa_c["por_pago_op_id"].get(int(doc_id), []) if doc_id else ()),
        ):
            if v["vinc_id"] not in vistos:
                vistos.add(v["vinc_id"])
                vincs.append(v)

        monto_orig = float(it.get("monto") or 0)
        usado_orig = round(sum(float(v.get("monto_aplicado") or 0) for v in vincs), 2)
        disp_orig = max(0.0, round(monto_orig - usado_orig, 2))
        ap_usd = round(sum(float(v.get("equiv_usd_bcv") or 0) for v in vincs), 2)
        if vincs and any(v.get("conciliado_odoo") for v in vincs):
            est_app = "conciliado_odoo"
            conc_odoo += 1
        elif vincs:
            est_app = "vinculado_cxc"
            vinc_cxc += 1
        else:
            est_app = "pendiente_vincular"

        it["vinculaciones"] = vincs
        it["documentos_vinculados"] = list(
            dict.fromkeys(str(v["compra_ref"]) for v in vincs if v.get("compra_ref"))
        )
        it["monto_aplicado_usd"] = ap_usd
        it["monto_disponible_original"] = disp_orig
        it["estado_aplicacion"] = est_app
    return {
        "vinculados_cxc": vinc_cxc,
        "conciliados_odoo_app": conc_odoo,
    }


def _construir_items_pagos_unificados(
    servicio: ServicioOperativos,
    repo_terceros: RepoCuentasTerceros,
    *,
    direccion: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], Any, Any]:
    from cxc.web.app import get_repo

    tipo_cxc = TIPO_PAGO if direccion == "recibidos" else TIPO_PAGO_PROVEEDOR
    docs_cxc, _tot_cxc, _res_cxc = servicio.listar(tipo=tipo_cxc, limit=2000, offset=0)

    terceros_map = {c.cuenta_id: c.a_dict() for c in repo_terceros.listar(solo_activas=False)}
    items_cxc: list[dict[str, Any]] = []
    odoo_ids_en_cxc: set[str] = set()
    for d in docs_cxc:
        item = d.a_dict()
        if d.cuenta_tercero_id and d.cuenta_tercero_id in terceros_map:
            cta = terceros_map[d.cuenta_tercero_id]
            item["cuenta_tercero_alias"] = cta["alias"]
            item["proveedor_tercero_nombre"] = cta["proveedor_nombre"]
        item["en_espejo_odoo"] = False
        if d.odoo_res_id is not None and (not d.odoo_model or d.odoo_model == "account.payment"):
            odoo_ids_en_cxc.add(str(d.odoo_res_id))
        items_cxc.append(item)

    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    items_odoo: list[dict[str, Any]] = []
    odoo_num_map: dict[str, str] = {}
    if direccion == "recibidos":
        clientes_map = {c.cliente_id: c.nombre for c in repo_cxc.all_clientes()}
        pagos_odoo_rows = repo_cxc.all_pagos_full()
        metodos_map: dict[str, str] = {}
        for mid_u in {
            str(r.get("metodo_pago") or "").strip() for r in pagos_odoo_rows if r.get("metodo_pago")
        }:
            try:
                met = repo_cxc.get_metodo_pago(mid_u)
                if met is not None:
                    metodos_map[mid_u] = met.nombre
            except Exception:
                pass
        for r in pagos_odoo_rows:
            pid = str(r.get("pago_id") or "").strip()
            if not pid:
                continue
            num_od = str(r.get("numero_odoo") or "").strip()
            if num_od:
                odoo_num_map[pid] = num_od
            if pid in odoo_ids_en_cxc:
                for it in items_cxc:
                    if str(it.get("odoo_res_id") or "") == pid:
                        it["en_espejo_odoo"] = True
                        if num_od and not it.get("odoo_numero"):
                            it["odoo_numero"] = num_od
                        if r.get("recibido") == "TRUE":
                            it["recibido"] = True
                            it["confirmado_cxc"] = True
                        if r.get("numero_recibido") and not it.get("numero_recibo"):
                            it["numero_recibo"] = r.get("numero_recibido")
                        if r.get("vendedor_email") and not it.get("vendedor"):
                            it["vendedor"] = r.get("vendedor_email")
                continue
            items_odoo.append(_odoo_recibido_a_item(r, clientes_map, metodos_map))
    else:
        try:
            pagos_prov = repo_cxc.all_pagos_proveedores_odoo()
        except (NotImplementedError, AttributeError):
            pagos_prov = []
        for r in pagos_prov:
            pid = str(r.get("pago_id") or "").strip()
            if not pid:
                continue
            num_od = str(r.get("numero_odoo") or "").strip()
            if num_od:
                odoo_num_map[pid] = num_od
            if pid in odoo_ids_en_cxc:
                for it in items_cxc:
                    if str(it.get("odoo_res_id") or "") == pid:
                        it["en_espejo_odoo"] = True
                        if num_od and not it.get("odoo_numero"):
                            it["odoo_numero"] = num_od
                continue
            items_odoo.append(_odoo_enviado_a_item(r))

    engine = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    conc_map = mapa_conciliacion_pagos(engine)
    tasas_obj = _tasas_para_pagos(repo_cxc)
    usuarios_map = _mapa_nombres_usuarios(repo_cxc)
    odoo_base = _odoo_base_url()
    for it in [*items_cxc, *items_odoo]:
        _enriquecer_pago_item(
            it,
            tasas=tasas_obj,
            usuarios_map=usuarios_map,
            odoo_num_map=odoo_num_map,
            odoo_url_base=odoo_base,
            direccion=direccion,
            conc_map=conc_map,
        )

    todos = items_cxc + items_odoo
    if direccion == "recibidos":
        app_stats = _enriquecer_aplicaciones_recibidos(todos, repo_cxc, engine)
    else:
        app_stats = _enriquecer_aplicaciones_enviados(todos, engine)

    resumen = {
        "total": len(todos),
        "origen_cxc": len(items_cxc),
        "origen_odoo": len(items_odoo),
        "pendientes_aprobacion": sum(1 for x in items_cxc if x["estado"] == PENDIENTE_APROBACION),
        "confirmados_cxc": sum(1 for x in items_cxc if x["estado"] == CONFIRMADO_CXC),
        "incompletos": sum(1 for x in items_cxc if x["estado"] in (INCOMPLETO, BORRADOR)),
        "enviados_odoo": sum(1 for x in items_cxc if x["estado"] == ENVIADO_ODOO),
        "confirmados_odoo": len(items_odoo),
        "rechazados": sum(1 for x in items_cxc if x["estado"] == RECHAZADO),
        "errores": sum(1 for x in items_cxc if x["estado"] == ERROR_ODOO),
        "zelle_terceros": sum(1 for x in items_cxc if x.get("es_zelle_tercero")),
        "conciliados_banco": sum(1 for x in todos if x.get("conciliado_banco")),
        "pendientes_efectivo": sum(
            1 for x in todos if x.get("estado_confirmacion_pago") == "PENDIENTE_EFECTIVO"
        ),
        **app_stats,
    }
    return items_cxc, items_odoo, resumen, repo_cxc, engine


@router.get("/api/pagos/unificados")
def listar_pagos_unificados(
    servicio: ServicioOpDep,
    repo_terceros: RepoTercerosDep,
    direccion: str = "recibidos",
    origen: str = "todos",
    estado: str | None = None,
    aplicacion: str | None = None,
    vendedor: str | None = None,
    moneda: str | None = None,
    solo_duplicados: bool = False,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    if direccion not in ("recibidos", "enviados"):
        raise HTTPException(status_code=422, detail="direccion debe ser recibidos o enviados")
    if origen not in ("todos", "cxc", "odoo", "zelle_tercero"):
        raise HTTPException(
            status_code=422, detail="origen debe ser todos, cxc, odoo o zelle_tercero"
        )
    if estado and estado not in (*ESTADOS_OPERATIVOS, "CONFIRMADO_ODOO"):
        raise HTTPException(status_code=422, detail="estado inválido")

    items_cxc, items_odoo, resumen, _repo_cxc, _engine = _construir_items_pagos_unificados(
        servicio, repo_terceros, direccion=direccion
    )
    todos = items_cxc + items_odoo

    # Regla de alcance para rol vendedor comercial en recibidos
    if direccion == "recibidos" and user.get("rol") == "ventas":
        u_email = str(user.get("email") or "").strip().lower()
        u_nom = str(user.get("nombre") or "").strip().lower()
        todos = [
            x
            for x in todos
            if any(
                (u_email and u_email in str(x.get(c) or "").lower())
                or (u_nom and u_nom in str(x.get(c) or "").lower())
                for c in ("vendedor", "vendedor_nombre", "registrado_por", "registrado_por_nombre")
            )
        ]

    vendedores_disponibles = sorted(
        {
            v_nom
            for x in todos
            if (
                v_nom := str(
                    x.get("vendedor_nombre")
                    or x.get("vendedor")
                    or x.get("registrado_por_nombre")
                    or ""
                ).strip()
            )
        }
    )

    cerrados_items = (
        [x for x in todos if x.get("estado_aplicacion") == "cerrado_empresa"]
        if direccion == "recibidos"
        else []
    )
    filtrados = todos
    if direccion == "recibidos" and not aplicacion:
        filtrados = [x for x in filtrados if x.get("estado_aplicacion") != "cerrado_empresa"]

    if origen == "cxc":
        filtrados = [x for x in filtrados if x.get("origen_sistema") == "cxc"]
    elif origen == "odoo":
        filtrados = [x for x in filtrados if x.get("origen_sistema") == "odoo"]
    elif origen == "zelle_tercero":
        filtrados = [x for x in filtrados if x.get("es_zelle_tercero")]

    if estado:
        filtrados = [x for x in filtrados if x.get("estado") == estado]

    if aplicacion:
        filtrados = [x for x in filtrados if x.get("estado_aplicacion") == aplicacion]

    if vendedor and vendedor.strip() and vendedor.strip() != "*":
        vn = vendedor.strip().lower()
        filtrados = [
            x
            for x in filtrados
            if vn
            in (
                f"{x.get('vendedor_nombre', '')} {x.get('vendedor', '')} "
                f"{x.get('registrado_por_nombre', '')}"
            ).lower()
        ]

    if moneda and moneda.strip() and moneda.strip() != "*":
        mn = moneda.strip().upper()
        filtrados = [x for x in filtrados if str(x.get("moneda") or "").upper() == mn]

    if solo_duplicados:
        filtrados = [x for x in filtrados if x.get("posible_duplicado")]

    if q and q.strip():
        qn = q.strip().lower()
        filtrados = [
            x
            for x in filtrados
            if any(
                qn in str(x.get(campo) or "").lower()
                for campo in (
                    "numero",
                    "numero_recibo",
                    "contraparte_nombre",
                    "rif",
                    "referencia",
                    "beneficiario_tercero",
                    "pedido_ref",
                    "factura_ref",
                    "odoo_ref",
                    "odoo_numero",
                    "odoo_pago_id",
                    "registrado_por_nombre",
                    "aprobado_o_recibido_por_nombre",
                    "cuenta_tercero_alias",
                )
            )
            or any(qn in str(s).lower() for s in (x.get("documentos_vinculados") or []))
        ]

    filtrados.sort(
        key=lambda x: (
            str(x.get("fecha_documento") or "0000-00-00"),
            str(x.get("creado_en") or ""),
            int(x.get("id") or x.get("odoo_res_id") or 0),
        ),
        reverse=True,
    )
    lim = max(1, min(limit, 300))
    off = max(0, offset)
    pagina = filtrados[off : off + lim]
    return {
        "direccion": direccion,
        "puede_aprobar_pagos": tiene(user, PERMISO_APROBAR_PAGOS),
        "puede_aprobar_compras": tiene(user, PERMISO_APROBAR_COMPRAS),
        "puede_recibo": tiene(user, "cobranza.recibo") or tiene(user, PERMISO_APROBAR_PAGOS),
        "escritura_odoo_activa": EscrituraConfig.from_env() is not None,
        "resumen": resumen,
        "vendedores_disponibles": vendedores_disponibles,
        "cerrados_empresa": cerrados_items,
        "total": len(filtrados),
        "items": pagina,
    }


# ── Compras Unificadas (CxC + Odoo), Conciliación R4, Caja Chica y Config ─────


def get_repo_lotes_caja_chica() -> Any:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    return RepoLotesCajaChicaSQL(repo._engine)


RepoLotesDep = Annotated[Any, Depends(get_repo_lotes_caja_chica)]


def _odoo_compra_gasto_a_item(row: dict[str, Any]) -> dict[str, Any]:
    mid = str(row.get("move_id") or "").strip()
    pid_partner = row.get("partner_odoo_id")
    mtype = str(row.get("move_type") or "in_invoice")
    ref = str(row.get("referencia") or "")
    tipo_cxc = str(row.get("tipo_cxc") or "").strip() or (
        TIPO_GASTO
        if (mtype == "entry" or ref.upper().startswith("S/N-GAS-"))
        else TIPO_FACTURA_PROVEEDOR
    )
    fecha_iso = str(row.get("fecha_documento") or "")[:10] or None
    es_draft = str(row.get("estado_odoo") or "").lower() == "draft"
    num_odoo = str(row.get("numero_odoo") or f"MOVE-{mid}")
    odoo_id_int = int(mid) if mid.isdigit() else None
    return {
        "id": None,
        "documento_id": None,
        "numero": num_odoo,
        "numero_cxc": None,
        "odoo_numero": num_odoo,
        "odoo_id": odoo_id_int,
        "odoo_url": _url_odoo_registro("account.move", mid) or None,
        "token_preview": "",
        "preview_path": "",
        "recibo_path": "",
        "tipo": tipo_cxc,
        "tipo_etiqueta": ETIQUETA_POR_TIPO.get(tipo_cxc, tipo_cxc),
        "estado": BORRADOR if es_draft else "CONFIRMADO_ODOO",
        "estado_odoo": str(row.get("estado_odoo") or "posted"),
        "estado_pago": str(row.get("estado_pago") or ""),
        "origen": "odoo",
        "origen_sistema": "odoo",
        "solo_lectura": True,
        "captura_id": None,
        "partner_odoo_id": int(pid_partner) if pid_partner is not None else None,
        "contraparte_nombre": str(row.get("proveedor_nombre") or f"Proveedor #{pid_partner or ''}"),
        "rif": str(row.get("rif") or ""),
        "monto": float(row["monto_total"]) if row.get("monto_total") is not None else 0.0,
        "moneda": str(row.get("moneda") or "VES"),
        "metodo_pago": "",
        "banco": "",
        "diario_id": row.get("diario_id"),
        "diario_nombre": str(row.get("diario_nombre") or ""),
        "cuenta_contable_codigo": "",
        "cuenta_contable_nombre": "",
        "referencia": ref,
        "numero_documento": ref,
        "categoria": "",
        "concepto": "",
        "sin_factura": tipo_cxc == TIPO_GASTO,
        "asiento_contable": mtype == "entry",
        "fecha_documento": fecha_iso,
        "notas": "",
        "alertas": [],
        "faltantes": [],
        "odoo_model": "account.move",
        "odoo_res_id": odoo_id_int,
        "odoo_ref": f"account.move:{mid}",
        "en_espejo_odoo": True,
        "creado_en": fecha_iso or "",
        "puede_editar": False,
        "puede_aprobar": False,
        "puede_rechazar": False,
    }


@router.get("/api/compras/unificadas")
def listar_compras_unificadas(
    servicio: ServicioOpDep,
    origen: str = "todos",
    tipo: str = "",
    estado: str | None = None,
    q: str | None = None,
    limit: int = 150,
    offset: int = 0,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    if origen not in ("todos", "cxc", "odoo"):
        raise HTTPException(status_code=422, detail="origen debe ser todos, cxc o odoo")
    if tipo and tipo not in (TIPO_FACTURA_PROVEEDOR, TIPO_GASTO):
        raise HTTPException(status_code=422, detail="tipo debe ser factura_proveedor o gasto")
    if estado and estado not in (*ESTADOS_OPERATIVOS, "CONFIRMADO_ODOO"):
        raise HTTPException(status_code=422, detail="estado inválido")

    docs_cxc, _tot_cxc, _res_cxc = servicio.listar(grupo="compras_solo", limit=2000, offset=0)
    items_cxc: list[dict[str, Any]] = []
    odoo_moves_en_cxc: set[str] = set()
    for d in docs_cxc:
        item = d.a_dict()
        item["solo_lectura"] = False
        item["en_espejo_odoo"] = False
        item["numero_cxc"] = d.numero
        item["odoo_id"] = d.odoo_res_id
        item["odoo_numero"] = d.odoo_ref or (f"MOVE-{d.odoo_res_id}" if d.odoo_res_id else None)
        item["odoo_url"] = _url_odoo_registro(d.odoo_model or "account.move", d.odoo_res_id) or None
        if d.odoo_res_id is not None and (not d.odoo_model or d.odoo_model == "account.move"):
            odoo_moves_en_cxc.add(str(d.odoo_res_id))
        items_cxc.append(item)

    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    try:
        rows_odoo = repo_cxc.all_compras_gastos_odoo()
    except (NotImplementedError, AttributeError):
        rows_odoo = []

    items_odoo: list[dict[str, Any]] = []
    for r in rows_odoo:
        mid = str(r.get("move_id") or "").strip()
        if not mid:
            continue
        if mid in odoo_moves_en_cxc:
            num_od = str(r.get("numero_odoo") or "").strip()
            for it in items_cxc:
                if str(it.get("odoo_res_id") or "") == mid:
                    it["en_espejo_odoo"] = True
                    if num_od:
                        it["odoo_numero"] = num_od
            continue
        items_odoo.append(_odoo_compra_gasto_a_item(r))

    todos = items_cxc + items_odoo
    engine_cmp = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    try:
        mapa_c = mapa_vinculaciones_compras(engine_cmp)
    except Exception:
        mapa_c = {"por_compra_op_id": {}, "por_compra_move_id": {}}
    tasas_cmp = _tasas_para_pagos(repo_cxc)
    for it in todos:
        cid_op = it.get("documento_id") or it.get("id")
        mid_od = str(it.get("odoo_res_id") or it.get("odoo_id") or "").strip()
        vincs: list[dict[str, Any]] = []
        vistos_v: set[Any] = set()
        for v in (
            *(mapa_c["por_compra_op_id"].get(int(cid_op), []) if cid_op else ()),
            *(mapa_c["por_compra_move_id"].get(mid_od, []) if mid_od else ()),
        ):
            if v["vinc_id"] not in vistos_v:
                vistos_v.add(v["vinc_id"])
                vincs.append(v)
        eq_cmp = _equivalentes_pago(
            it.get("monto"),
            it.get("moneda"),
            it.get("fecha_documento") or it.get("creado_en"),
            tasas_cmp,
        )
        tot_usd = float(eq_cmp.get("monto_bcv_usd") or it.get("monto") or 0)
        vinc_usd = round(sum(float(v.get("equiv_usd_bcv") or 0) for v in vincs), 2)
        it["vinculaciones_count"] = len(vincs)
        it["monto_vinculado_usd"] = vinc_usd
        it["saldo_pendiente_vinc_usd"] = max(0.0, round(tot_usd - vinc_usd, 2))
        it["pagos_vinculados"] = [
            {
                "vinc_id": v.get("vinc_id"),
                "pago_ref": v.get("pago_ref"),
                "monto_aplicado": v.get("monto_aplicado"),
                "moneda": v.get("moneda"),
                "equiv_usd_bcv": v.get("equiv_usd_bcv"),
                "conciliado_odoo": bool(v.get("conciliado_odoo")),
            }
            for v in vincs[:6]
        ]

    resumen = {
        "total": len(todos),
        "origen_cxc": len(items_cxc),
        "origen_odoo": len(items_odoo),
        "pendientes_aprobacion": sum(1 for x in items_cxc if x["estado"] == PENDIENTE_APROBACION),
        "incompletos": sum(1 for x in items_cxc if x["estado"] in (INCOMPLETO, BORRADOR)),
        "enviados_odoo": sum(1 for x in items_cxc if x["estado"] == ENVIADO_ODOO),
        "confirmados_odoo": len(items_odoo),
        "rechazados": sum(1 for x in items_cxc if x["estado"] == RECHAZADO),
        "errores": sum(1 for x in items_cxc if x["estado"] == ERROR_ODOO),
        "asientos_sin_factura": sum(1 for x in todos if x.get("asiento_contable")),
    }

    filtrados = todos
    if origen == "cxc":
        filtrados = [x for x in filtrados if x.get("origen_sistema") == "cxc"]
    elif origen == "odoo":
        filtrados = [x for x in filtrados if x.get("origen_sistema") == "odoo"]
    if tipo:
        filtrados = [x for x in filtrados if x.get("tipo") == tipo]
    if estado:
        filtrados = [x for x in filtrados if x.get("estado") == estado]
    if q and q.strip():
        qn = q.strip().lower()
        filtrados = [
            x
            for x in filtrados
            if any(
                qn in str(x.get(c) or "").lower()
                for c in (
                    "numero",
                    "contraparte_nombre",
                    "rif",
                    "referencia",
                    "numero_documento",
                    "concepto",
                    "odoo_ref",
                )
            )
        ]

    filtrados.sort(
        key=lambda x: (
            str(x.get("fecha_documento") or "0000-00-00"),
            str(x.get("creado_en") or ""),
            int(x.get("id") or x.get("odoo_res_id") or 0),
        ),
        reverse=True,
    )
    lim = max(1, min(limit, 300))
    off = max(0, offset)
    return {
        "puede_aprobar_compras": tiene(user, PERMISO_APROBAR_COMPRAS),
        "escritura_odoo_activa": EscrituraConfig.from_env() is not None,
        "resumen": resumen,
        "total": len(filtrados),
        "items": filtrados[off : off + lim],
    }


def _construir_conciliacion_sistema(
    servicio: ServicioOperativos,
    user: dict[str, Any],
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    estados_excluidos = (RECHAZADO, INCOMPLETO, BORRADOR)
    docs_g, _, _ = servicio.listar(tipo=TIPO_GASTO, limit=500, offset=0)
    docs_p, _, _ = servicio.listar(tipo=TIPO_PAGO_PROVEEDOR, limit=500, offset=0)
    docs_ing, _, _ = servicio.listar(tipo=TIPO_PAGO, limit=500, offset=0)

    egresos_docs = [d for d in [*docs_g, *docs_p] if d.estado not in estados_excluidos]
    ingresos_docs = [
        d
        for d in docs_ing
        if d.estado not in estados_excluidos
        and not op_es_efectivo(d.metodo_pago)
        and not d.es_zelle_tercero
    ]

    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    engine = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    movs_banco: list[Any] = []
    if engine is not None:
        try:
            movs_banco = list(bancos_repo.movimientos(engine))
        except SQLAlchemyError:
            movs_banco = []

    sin_huella_egr = [d for d in egresos_docs if not d.conciliado_banco_huella]
    sin_huella_ing = [d for d in ingresos_docs if not d.conciliado_banco_huella]
    cands_r4 = bancos_conciliacion.egresos_desde_operativos(sin_huella_egr)
    cands_r5 = bancos_conciliacion.ingresos_desde_operativos(sin_huella_ing)
    props = (
        bancos_conciliacion.proponer(
            movs_banco,
            [],
            egresos_cxc=cands_r4,
            ingresos_cxc=cands_r5,
        )
        if movs_banco
        else []
    )

    mov_por_huella = {m.mov.huella: m for m in movs_banco}
    doc_por_id = {d.documento_id: d for d in [*egresos_docs, *ingresos_docs]}
    sugerencias_r4: list[dict[str, Any]] = []
    sugerencias_r5: list[dict[str, Any]] = []
    for p in props:
        if p.regla not in ("R4", "R5") or not p.candidatos:
            continue
        m_cls = mov_por_huella.get(p.huella)
        d_match = doc_por_id.get(int(p.candidatos[0]))
        if m_cls is None or d_match is None:
            continue
        item = {
            "regla": p.regla,
            "direccion": "egreso" if p.regla == "R4" else "ingreso",
            "huella": p.huella,
            "documento_id": d_match.documento_id,
            "numero": d_match.numero,
            "numero_cxc": d_match.numero,
            "origen": d_match.origen,
            "origen_sistema": "cxc",
            "odoo_numero": d_match.odoo_ref or None,
            "odoo_id": d_match.odoo_res_id,
            "odoo_url": _url_odoo_registro(
                d_match.odoo_model
                or ("account.payment" if d_match.tipo == TIPO_PAGO else "account.move"),
                d_match.odoo_res_id,
            )
            or None,
            "tipo": d_match.tipo,
            "contraparte": d_match.contraparte_nombre,
            "monto_doc": float(d_match.monto or 0),
            "moneda_doc": d_match.moneda,
            "fecha_doc": (d_match.fecha_documento.isoformat() if d_match.fecha_documento else None),
            "referencia_doc": d_match.referencia,
            "banco": m_cls.mov.banco,
            "fecha_banco": m_cls.mov.fecha.isoformat(),
            "monto_banco": float(abs(m_cls.mov.neto)),
            "moneda_banco": m_cls.mov.moneda,
            "referencia_banco": m_cls.mov.referencia,
            "descripcion_banco": m_cls.mov.descripcion,
            "puntaje": p.puntaje,
            "motivo": p.motivo,
        }
        if p.regla == "R4":
            sugerencias_r4.append(item)
        else:
            sugerencias_r5.append(item)

    def _enriquecer_op_conc(d_op: Any) -> dict[str, Any]:
        out: dict[str, Any] = dict(d_op.a_dict())
        out["numero_cxc"] = d_op.numero
        out["odoo_numero"] = d_op.odoo_ref or None
        out["odoo_id"] = d_op.odoo_res_id
        out["odoo_url"] = (
            _url_odoo_registro(
                d_op.odoo_model
                or ("account.payment" if d_op.tipo == TIPO_PAGO else "account.move"),
                d_op.odoo_res_id,
            )
            or None
        )
        return out

    conciliados_cnt = sum(1 for d in egresos_docs if d.conciliado_banco_huella)
    ing_conc_cnt = sum(1 for d in ingresos_docs if d.conciliado_banco_huella)
    sugerencias_sistema = sorted(
        [*sugerencias_r4, *sugerencias_r5],
        key=lambda x: (-int(x.get("puntaje") or 0), str(x.get("fecha_banco") or "")),
    )
    return {
        "puede_conciliar": tiene(user, PERMISO_APROBAR_COMPRAS) or tiene(user, "bancos.conciliar"),
        "total_egresos": len(egresos_docs),
        "conciliados_count": conciliados_cnt,
        "pendientes_count": len(egresos_docs) - conciliados_cnt,
        "total_ingresos": len(ingresos_docs),
        "ingresos_conciliados_count": ing_conc_cnt,
        "ingresos_pendientes_count": len(ingresos_docs) - ing_conc_cnt,
        "sugerencias_r4": sugerencias_r4,
        "sugerencias_r5": sugerencias_r5,
        "sugerencias_sistema": sugerencias_sistema,
        "egresos": [_enriquecer_op_conc(d) for d in egresos_docs[:200]],
        "ingresos": [_enriquecer_op_conc(d) for d in ingresos_docs[:200]],
    }


@router.get("/api/compras/conciliacion-gastos")
def listar_conciliacion_gastos(
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    return _construir_conciliacion_sistema(servicio, user)


@router.get("/api/bancos/conciliacion/operativos-sistema")
def listar_conciliacion_operativos_sistema(
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS, "bancos.ver", "bancos.conciliar")
    return _construir_conciliacion_sistema(servicio, user)


@router.post("/api/compras/conciliacion-gastos/vincular")
def vincular_gasto_banco(
    servicio: ServicioOpDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user_act = _usuario_op(cxc_session, PERMISO_APROBAR_COMPRAS, "bancos.conciliar")
    doc_id = body.get("documento_id")
    if not doc_id or not str(doc_id).isdigit():
        raise HTTPException(status_code=422, detail="documento_id inválido")
    try:
        doc = servicio.obtener(int(str(doc_id)))
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento no encontrado") from e
    huella = str(body.get("huella") or "").strip()[:64]
    campos_act: dict[str, Any] = {"conciliado_banco_huella": huella}
    from cxc.operativos.lotes_efectivo import es_pago_efectivo

    if (
        doc.tipo == TIPO_PAGO
        and not doc.es_zelle_tercero
        and not es_pago_efectivo(doc.metodo_pago, diario_nombre=doc.diario_nombre)
    ):
        if huella:
            if not doc.confirmado_cxc:
                campos_act["confirmado_cxc"] = True
                campos_act["confirmado_cxc_por"] = _actor_op(user_act)
                campos_act["confirmado_cxc_en"] = datetime.now()
            if doc.estado == PENDIENTE_APROBACION:
                campos_act["estado"] = CONFIRMADO_CXC
        elif not doc.numero_recibo:
            campos_act["confirmado_cxc"] = False
            campos_act["confirmado_cxc_por"] = ""
            campos_act["confirmado_cxc_en"] = None
            if doc.estado == CONFIRMADO_CXC:
                campos_act["estado"] = PENDIENTE_APROBACION
    actualizado = servicio._repo.actualizar(doc.documento_id, campos_act)
    return JSONResponse(status_code=200, content={"ok": True, "documento": actualizado.a_dict()})


@router.get("/api/compras/lotes-caja-chica")
def listar_lotes_caja_chica(
    repo_lotes: RepoLotesDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    return {
        "puede_gestionar": tiene(user, PERMISO_APROBAR_COMPRAS),
        "items": repo_lotes.listar(limit=50),
    }


@router.post("/api/compras/lotes-caja-chica")
def crear_lote_caja_chica(
    repo_lotes: RepoLotesDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_op(cxc_session, PERMISO_APROBAR_COMPRAS)
    raw_ids = body.get("documento_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return JSONResponse(
            status_code=422, content={"error": "Selecciona al menos un gasto menor para el lote."}
        )
    doc_ids = [int(x) for x in raw_ids if str(x).isdigit()]
    if not doc_ids:
        return JSONResponse(status_code=422, content={"error": "documento_ids inválidos."})
    lote = repo_lotes.crear(
        responsable=str(body.get("responsable") or _actor_op(user)),
        moneda=str(body.get("moneda") or "USD"),
        documento_ids=doc_ids,
        notas=str(body.get("notas") or ""),
        actor=_actor_op(user),
    )
    return JSONResponse(status_code=201, content=lote)


@router.post("/api/compras/lotes-caja-chica/{lote_id}/cerrar")
def cerrar_lote_caja_chica(
    lote_id: int,
    repo_lotes: RepoLotesDep,
    servicio: ServicioOpDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_op(cxc_session, PERMISO_APROBAR_COMPRAS)
    lote_actual = repo_lotes.obtener(lote_id)
    if lote_actual is None:
        raise HTTPException(status_code=404, detail="Lote de caja chica no encontrado")
    contabilizar = bool(body.get("contabilizar", False))
    enviados: list[str] = []
    errores: list[str] = []
    if contabilizar:
        for g in lote_actual.get("gastos") or []:
            gid = g.get("documento_id")
            if gid and g.get("estado") in (PENDIENTE_APROBACION, ERROR_ODOO):
                try:
                    d_env = servicio.aprobar_y_enviar_a_odoo(int(gid), actor=_actor_op(user))
                    enviados.append(d_env.numero)
                except Exception as exc:
                    errores.append(f"{g.get('numero', gid)}: {exc}")
    nuevo_estado = "CONTABILIZADO" if (contabilizar and not errores and enviados) else "CERRADO"
    cerrado = repo_lotes.cerrar(lote_id, estado=nuevo_estado)
    return JSONResponse(
        status_code=200,
        content={"ok": True, "lote": cerrado, "enviados": enviados, "errores": errores},
    )


@router.get("/api/config/gastos-contabilidad")
def obtener_config_gastos_contabilidad(
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    user = _usuario_op(cxc_session)
    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    return {
        "config": cargar_config_gastos(repo_cxc),
        **categorias_y_metodos_catalogo(),
        "puede_editar": tiene(user, PERMISO_APROBAR_COMPRAS) or user.get("rol") == "admin",
    }


@router.post("/api/config/gastos-contabilidad")
def guardar_config_gastos_contabilidad(
    servicio: ServicioOpDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    from cxc.web.app import get_repo

    _usuario_op(cxc_session, PERMISO_APROBAR_COMPRAS)
    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    try:
        cfg = guardar_config_gastos(repo_cxc, body)
    except ValueError as e:
        return JSONResponse(status_code=422, content={"error": str(e)})
    return JSONResponse(status_code=200, content={"ok": True, "config": cfg})


class LoteEfectivoIn(BaseModel):
    vendedor_email: str
    vendedor_nombre: str = ""
    pago_ids: list[str] = Field(default_factory=list)
    documento_ids: list[int] = Field(default_factory=list)
    observaciones: str = ""
    fecha_lote: str | None = None


@router.get("/api/pagos/efectivo/lotes-pendientes")
def listar_lotes_efectivo_pendientes(
    servicio: ServicioOpDep,
    repo_terceros: RepoTercerosDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    items_cxc, items_odoo, _res, _repo_cxc, _engine = _construir_items_pagos_unificados(
        servicio, repo_terceros, direccion="recibidos"
    )
    grupos = agrupar_efectivo_pendiente_por_vendedor([*items_cxc, *items_odoo])
    return {
        "total_vendedores": len(grupos),
        "total_pagos": sum(g["cantidad_pagos"] for g in grupos),
        "grupos": grupos,
    }


@router.get("/api/pagos/efectivo/lotes")
def listar_lotes_efectivo(
    servicio: ServicioOpDep,
    vendedor_email: str | None = None,
    limit: int = 100,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    engine = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    if engine is None:
        return {"items": []}
    return {"items": RepoLotesEfectivo(engine).listar(vendedor_email=vendedor_email, limit=limit)}


@router.post("/api/pagos/efectivo/lotes")
def crear_lote_efectivo(
    body: LoteEfectivoIn,
    servicio: ServicioOpDep,
    repo_terceros: RepoTercerosDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario_op(cxc_session, PERMISO_APROBAR_PAGOS, "pagos", "cobranza")
    items_cxc, items_odoo, _res, repo_cxc, engine = _construir_items_pagos_unificados(
        servicio, repo_terceros, direccion="recibidos"
    )
    if engine is None:
        raise HTTPException(status_code=503, detail="Base de datos SQL no disponible.")

    todos = [*items_cxc, *items_odoo]
    por_pago_id: dict[str, dict[str, Any]] = {}
    por_doc_id: dict[int, dict[str, Any]] = {}
    for it in todos:
        pid = str(it.get("pago_id") or it.get("odoo_pago_id") or "").strip()
        if pid:
            por_pago_id[pid] = it
        did = it.get("documento_id")
        if did is not None and str(did).isdigit():
            por_doc_id[int(str(did))] = it

    seleccionados: list[dict[str, Any]] = []
    vistos: set[tuple[Any, Any]] = set()
    for raw_pid in body.pago_ids:
        pid_s = str(raw_pid or "").strip()
        if not pid_s:
            continue
        it_m = por_pago_id.get(pid_s)
        if it_m is None:
            raise HTTPException(status_code=422, detail=f"Pago {pid_s} no encontrado.")
        clave = (it_m.get("documento_id"), it_m.get("pago_id"))
        if clave not in vistos:
            vistos.add(clave)
            seleccionados.append(it_m)
    for raw_did in body.documento_ids:
        did_i = int(raw_did)
        it_m = por_doc_id.get(did_i)
        if it_m is None:
            raise HTTPException(
                status_code=422, detail=f"Documento operativo #{did_i} no encontrado."
            )
        clave = (it_m.get("documento_id"), it_m.get("pago_id"))
        if clave not in vistos:
            vistos.add(clave)
            seleccionados.append(it_m)

    if not seleccionados:
        raise HTTPException(
            status_code=422,
            detail="Selecciona al menos un pago en efectivo para generar el recibo por lote.",
        )

    fecha_lote_dt: date | None = None
    if body.fecha_lote and body.fecha_lote.strip():
        try:
            fecha_lote_dt = date.fromisoformat(body.fecha_lote.strip()[:10])
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="fecha_lote inválida (usa AAAA-MM-DD)."
            ) from exc

    usuarios_map = _mapa_nombres_usuarios(repo_cxc)
    vend_email = body.vendedor_email.strip()
    vend_nom = body.vendedor_nombre.strip() or _resolver_nombre_usuario(vend_email, usuarios_map)
    actor = _resolver_nombre_usuario(_actor_op(user), usuarios_map, default=_actor_op(user))
    try:
        lote = RepoLotesEfectivo(engine).crear_lote(
            vendedor_email=vend_email,
            vendedor_nombre=vend_nom,
            items=seleccionados,
            recibido_por=actor,
            observaciones=body.observaciones,
            fecha_lote=fecha_lote_dt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(status_code=201, content={"ok": True, "lote": lote})


@router.get("/recibo-lote/{lote_id}", response_class=HTMLResponse)
def ver_recibo_lote_efectivo(
    lote_id: int,
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> HTMLResponse:
    from cxc.web.app import get_repo

    _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    engine = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Base de datos SQL no disponible.")
    lote = RepoLotesEfectivo(engine).obtener(lote_id)
    if lote is None:
        raise HTTPException(status_code=404, detail="Recibo por lote no encontrado.")
    return HTMLResponse(content=render_recibo_lote_html(lote))


# ── Vinculación bidireccional Pagos ↔ Ventas y Compras/Gastos ─────────────────


class VincularCompraIn(BaseModel):
    compra_operativo_id: int | None = None
    compra_odoo_move_id: str | None = None
    pago_operativo_id: int | None = None
    pago_odoo_id: str | None = None
    monto_aplicado: float | str | None = None
    origen_vinculacion: str = "manual"


def _escritor_vinc_op() -> EscritorOdoo | None:
    if "PYTEST_CURRENT_TEST" in os.environ:
        return None
    try:
        return _fabrica_escritor_op()
    except Exception:
        return None


@router.get("/api/pagos/vinculaciones/ordenes-candidatas")
def listar_ordenes_candidatas_para_pago(
    servicio: ServicioOpDep,
    pago_id: str | None = None,
    pago_operativo_id: int | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    engine = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Base de datos SQL no disponible.")
    try:
        res = candidatos_ventas_para_pago(
            engine, repo_cxc, pago_id=pago_id, pago_operativo_id=pago_operativo_id
        )
    except VinculacionNoEncontrada as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "escritura_odoo_activa": EscrituraConfig.from_env() is not None,
        **res,
    }


@router.get("/api/compras/vinculaciones/candidatos")
def listar_candidatos_vinculacion_compra(
    servicio: ServicioOpDep,
    compra_operativo_id: int | None = None,
    compra_odoo_move_id: str | None = None,
    pago_operativo_id: int | None = None,
    pago_odoo_id: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    engine = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Base de datos SQL no disponible.")
    try:
        if compra_operativo_id is not None or compra_odoo_move_id:
            res = candidatos_pagos_para_compra(
                engine,
                repo_cxc,
                compra_operativo_id=compra_operativo_id,
                compra_odoo_move_id=compra_odoo_move_id,
            )
        elif pago_operativo_id is not None or pago_odoo_id:
            res = candidatos_compras_para_pago(
                engine,
                repo_cxc,
                pago_operativo_id=pago_operativo_id,
                pago_odoo_id=pago_odoo_id,
            )
        else:
            raise HTTPException(
                status_code=422, detail="Indica compra o pago para consultar candidatos."
            )
    except VinculacionNoEncontrada as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "escritura_odoo_activa": EscrituraConfig.from_env() is not None,
        **res,
    }


@router.post("/api/compras/vinculaciones")
def crear_vinculacion_compra_api(
    body: VincularCompraIn,
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    from cxc.web.app import get_repo

    user = _usuario_op(cxc_session, *PERMISOS_GESTIONAR_OPERATIVOS)
    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    engine = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    if engine is None:
        return JSONResponse(status_code=503, content={"error": "Base de datos SQL no disponible."})
    try:
        res = vincular_pago_compra(
            engine,
            repo_cxc,
            compra_operativo_id=body.compra_operativo_id,
            compra_odoo_move_id=body.compra_odoo_move_id,
            pago_operativo_id=body.pago_operativo_id,
            pago_odoo_id=body.pago_odoo_id,
            monto_aplicado=body.monto_aplicado,
            origen_vinculacion=body.origen_vinculacion or "manual",
            actor=_actor_op(user),
            escritor=_escritor_vinc_op(),
        )
    except VinculacionNoEncontrada as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VinculacionInvalida as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=res)


@router.delete("/api/compras/vinculaciones/{vinc_id}")
def eliminar_vinculacion_compra_api(
    vinc_id: int,
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    from cxc.web.app import get_repo

    _usuario_op(cxc_session, *PERMISOS_GESTIONAR_OPERATIVOS)
    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    engine = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    if engine is None:
        return JSONResponse(status_code=503, content={"error": "Base de datos SQL no disponible."})
    try:
        res = desvincular_pago_compra(engine, vinc_id)
    except VinculacionNoEncontrada as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VinculacionInvalida as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=res)
