"""Endpoints REST y Preview HTML de Documentos Operativos (Pagos, Compras y Gastos)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from cxc.bancos import conciliacion as bancos_conciliacion
from cxc.bancos import repositorio as bancos_repo
from cxc.capturas.modelos import CapturaInvalida
from cxc.capturas.repositorio import RepoCapturasSQL
from cxc.odoo.directorio import ContactoOdoo, CuentaContable
from cxc.odoo.escritura import EscritorOdoo, EscrituraConfig, conectar
from cxc.operativos.contabilidad_gastos import (
    cargar_config_gastos,
    categorias_y_metodos_catalogo,
    guardar_config_gastos,
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
from cxc.permisos import tiene
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
    return {
        "id": None,
        "documento_id": None,
        "numero": str(row.get("numero_odoo") or f"ODOO-{pid}"),
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
        "es_efectivo": "efectivo" in metodo_nombre.lower() or "cash" in metodo_nombre.lower(),
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
        "confirmado_cxc": str(row.get("recibido") or "").upper() == "TRUE",
        "confirmado_cxc_por": str(row.get("recibido_por") or ""),
        "numero_recibo": str(row.get("numero_recibido") or ""),
        "odoo_model": "account.payment",
        "odoo_res_id": int(pid) if pid.isdigit() else None,
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
    return {
        "id": None,
        "documento_id": None,
        "numero": str(row.get("numero_odoo") or f"ODOO-{pid}"),
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
        "es_efectivo": "efectivo" in str(row.get("diario_nombre") or "").lower(),
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
        "numero_recibo": "",
        "odoo_model": "account.payment",
        "odoo_res_id": int(pid) if pid.isdigit() else None,
        "odoo_pago_id": pid,
        "odoo_ref": f"account.payment:{pid}",
        "en_espejo_odoo": True,
        "creado_en": fecha_iso or "",
        "puede_editar": False,
        "puede_confirmar_cxc": False,
        "puede_aprobar": False,
        "puede_rechazar": False,
    }


@router.get("/api/pagos/unificados")
def listar_pagos_unificados(
    servicio: ServicioOpDep,
    repo_terceros: RepoTercerosDep,
    direccion: str = "recibidos",
    origen: str = "todos",
    estado: str | None = None,
    q: str | None = None,
    limit: int = 150,
    offset: int = 0,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    if direccion not in ("recibidos", "enviados"):
        raise HTTPException(status_code=422, detail="direccion debe ser recibidos o enviados")
    if origen not in ("todos", "cxc", "odoo", "zelle_tercero"):
        raise HTTPException(
            status_code=422, detail="origen debe ser todos, cxc, odoo o zelle_tercero"
        )
    if estado and estado not in (*ESTADOS_OPERATIVOS, "CONFIRMADO_ODOO"):
        raise HTTPException(status_code=422, detail="estado inválido")

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
            if pid in odoo_ids_en_cxc:
                for it in items_cxc:
                    if str(it.get("odoo_res_id") or "") == pid:
                        it["en_espejo_odoo"] = True
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
            if pid in odoo_ids_en_cxc:
                for it in items_cxc:
                    if str(it.get("odoo_res_id") or "") == pid:
                        it["en_espejo_odoo"] = True
                continue
            items_odoo.append(_odoo_enviado_a_item(r))

    todos = items_cxc + items_odoo
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
    }

    filtrados = todos
    if origen == "cxc":
        filtrados = [x for x in filtrados if x.get("origen_sistema") == "cxc"]
    elif origen == "odoo":
        filtrados = [x for x in filtrados if x.get("origen_sistema") == "odoo"]
    elif origen == "zelle_tercero":
        filtrados = [x for x in filtrados if x.get("es_zelle_tercero")]

    if estado:
        filtrados = [x for x in filtrados if x.get("estado") == estado]

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
                    "cuenta_tercero_alias",
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
    pagina = filtrados[off : off + lim]
    return {
        "direccion": direccion,
        "puede_aprobar_pagos": tiene(user, PERMISO_APROBAR_PAGOS),
        "puede_aprobar_compras": tiene(user, PERMISO_APROBAR_COMPRAS),
        "puede_recibo": tiene(user, "cobranza.recibo") or tiene(user, PERMISO_APROBAR_PAGOS),
        "escritura_odoo_activa": EscrituraConfig.from_env() is not None,
        "resumen": resumen,
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
    return {
        "id": None,
        "documento_id": None,
        "numero": str(row.get("numero_odoo") or f"MOVE-{mid}"),
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
        "odoo_res_id": int(mid) if mid.isdigit() else None,
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
            for it in items_cxc:
                if str(it.get("odoo_res_id") or "") == mid:
                    it["en_espejo_odoo"] = True
            continue
        items_odoo.append(_odoo_compra_gasto_a_item(r))

    todos = items_cxc + items_odoo
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


@router.get("/api/compras/conciliacion-gastos")
def listar_conciliacion_gastos(
    servicio: ServicioOpDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    user = _usuario_op(cxc_session, *PERMISOS_VER_OPERATIVOS)
    docs_g, _, _ = servicio.listar(tipo=TIPO_GASTO, limit=500, offset=0)
    docs_p, _, _ = servicio.listar(tipo=TIPO_PAGO_PROVEEDOR, limit=500, offset=0)
    egresos_docs = [
        d for d in [*docs_g, *docs_p] if d.estado not in (RECHAZADO, INCOMPLETO, BORRADOR)
    ]

    repo_cxc = servicio._repo_cxc if servicio._repo_cxc is not None else get_repo()
    engine = getattr(servicio._repo, "_engine", None) or getattr(repo_cxc, "_engine", None)
    movs_banco: list[Any] = []
    if engine is not None:
        try:
            movs_banco = [m for m in bancos_repo.movimientos(engine) if m.mov.neto < 0]
        except SQLAlchemyError:
            movs_banco = []

    sin_huella = [d for d in egresos_docs if not d.conciliado_banco_huella]
    cands_r4 = bancos_conciliacion.egresos_desde_operativos(sin_huella)
    props = bancos_conciliacion.proponer(movs_banco, [], egresos_cxc=cands_r4) if movs_banco else []

    mov_por_huella = {m.mov.huella: m for m in movs_banco}
    doc_por_id = {d.documento_id: d for d in egresos_docs}
    sugerencias_r4: list[dict[str, Any]] = []
    for p in props:
        if p.regla != "R4" or not p.candidatos:
            continue
        m_cls = mov_por_huella.get(p.huella)
        d_match = doc_por_id.get(int(p.candidatos[0]))
        if m_cls is None or d_match is None:
            continue
        sugerencias_r4.append(
            {
                "huella": p.huella,
                "documento_id": d_match.documento_id,
                "numero": d_match.numero,
                "tipo": d_match.tipo,
                "contraparte": d_match.contraparte_nombre,
                "monto_doc": float(d_match.monto or 0),
                "moneda_doc": d_match.moneda,
                "fecha_doc": (
                    d_match.fecha_documento.isoformat() if d_match.fecha_documento else None
                ),
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
        )

    conciliados_cnt = sum(1 for d in egresos_docs if d.conciliado_banco_huella)
    return {
        "puede_conciliar": tiene(user, PERMISO_APROBAR_COMPRAS) or tiene(user, "bancos.conciliar"),
        "total_egresos": len(egresos_docs),
        "conciliados_count": conciliados_cnt,
        "pendientes_count": len(egresos_docs) - conciliados_cnt,
        "sugerencias_r4": sugerencias_r4,
        "egresos": [d.a_dict() for d in egresos_docs[:200]],
    }


@router.post("/api/compras/conciliacion-gastos/vincular")
def vincular_gasto_banco(
    servicio: ServicioOpDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    _usuario_op(cxc_session, PERMISO_APROBAR_COMPRAS, "bancos.conciliar")
    doc_id = body.get("documento_id")
    if not doc_id or not str(doc_id).isdigit():
        raise HTTPException(status_code=422, detail="documento_id inválido")
    try:
        doc = servicio.obtener(int(str(doc_id)))
    except OperativoNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Documento no encontrado") from e
    huella = str(body.get("huella") or "").strip()[:64]
    actualizado = servicio._repo.actualizar(doc.documento_id, {"conciliado_banco_huella": huella})
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
