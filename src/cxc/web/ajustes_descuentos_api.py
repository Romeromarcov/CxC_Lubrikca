"""Endpoints REST para ajustes por regla y descuentos especiales en órdenes Odoo existentes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cxc.ajustes_descuentos import (
    SETTING_LIMPIEZA_ACTIVA,
    AjusteDescuentoDeshabilitado,
    AjusteDescuentoError,
    ServicioAjustesDescuentos,
    aplicar_ajustes_a_descuentos_orden,
)
from cxc.permisos import tiene

router = APIRouter()

PERMISOS_VER_AJUSTES = ("ventas", "facturacion", "configuracion")
PERMISOS_EDITAR_AJUSTES = ("ventas", "facturacion")


def _usuario_ajustes(cxc_session: str | None, *permisos: str) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if permisos and user.get("rol") != "admin" and not any(tiene(user, p) for p in permisos):
        raise HTTPException(
            status_code=403, detail="Sin permisos para gestionar ajustes de descuentos."
        )
    return user


def _actor_ajustes(user: dict[str, Any]) -> str:
    return str(user.get("email") or user.get("nombre") or "usuario")


def _invalidar_caches_ventas() -> None:
    try:
        from cxc.web import app as _app

        _app._VENTAS_CACHE["data"] = None
        _app._VENTAS_CACHE["timestamp"] = 0.0
        _app._SALDOS_REALES_CACHE["data"] = None
        _app._SALDOS_REALES_CACHE["timestamp"] = 0.0
    except Exception:
        pass


def get_servicio_ajustes() -> ServicioAjustesDescuentos:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine = getattr(repo, "_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Base de datos SQL no disponible.")
    return ServicioAjustesDescuentos(engine)


AjustesDep = Annotated[ServicioAjustesDescuentos, Depends(get_servicio_ajustes)]


class LimpiezaToggleIn(BaseModel):
    activa: bool


class ExcluirMotorIn(BaseModel):
    descuento_origen: str = ""
    regla_id: str = ""
    motivo: str = ""


class EspecialDescuentoIn(BaseModel):
    tipo_valor: str = "porcentaje"
    porcentaje: float | None = None
    monto_usd: float | None = None
    descripcion: str = ""
    motivo: str = ""


def _payload_orden(so_id: str, repo: Any, srv: ServicioAjustesDescuentos) -> dict[str, Any]:
    orden = repo.get_orden(so_id)
    if orden is None:
        raise HTTPException(status_code=404, detail=f"Orden {so_id} no encontrada.")

    limpieza = srv.limpieza_activa()
    try:
        es_odoo = srv.es_orden_odoo_existente(so_id)
        motivo_bloqueo = ""
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AjusteDescuentoError as exc:
        es_odoo = False
        motivo_bloqueo = str(exc)

    if not limpieza and not motivo_bloqueo:
        motivo_bloqueo = "La limpieza temporal de descuentos está cerrada en Configuración."

    bandeja = next((b for b in repo.all_bandeja() if b.so_id == so_id), None)
    subtotal_base = (
        float(bandeja.precio_base_calculado)
        if bandeja and float(bandeja.precio_base_calculado or 0) > 0
        else float(orden.monto_total or 0)
    )
    descuentos_motor = list(bandeja.descuentos_detalle) if bandeja else []
    historial = srv.listar_por_so(so_id, solo_activos=False)
    activos = [a for a in historial if a.get("activo")]
    calc = aplicar_ajustes_a_descuentos_orden(descuentos_motor, subtotal_base, activos)

    return {
        "so_id": orden.so_id,
        "odoo_order_id": getattr(orden, "odoo_order_id", None),
        "cliente_id": orden.cliente_id,
        "fecha": orden.fecha.isoformat() if orden.fecha else "",
        "subtotal_base_usd": round(subtotal_base, 2),
        "limpieza_activa": limpieza,
        "es_orden_odoo_existente": es_odoo,
        "puede_editar": bool(limpieza and es_odoo),
        "motivo_bloqueo": motivo_bloqueo,
        "resumen": {
            "total_motor_original": calc["total_motor_original"],
            "total_motor_vigente": calc["total_motor_vigente"],
            "total_excluido": calc["total_excluido"],
            "total_especial": calc["total_especial"],
            "total_descuento_ajustado": calc["total_descuento_ajustado"],
            "tiene_ajustes": calc["tiene_ajustes"],
            "count_excluidos": calc["count_excluidos"],
            "count_especiales": calc["count_especiales"],
        },
        "motor_items": calc["motor_items"],
        "especiales": calc["especiales"],
        "historial": historial,
    }


@router.get("/api/config/limpieza-descuentos-odoo")
def obtener_estado_limpieza_descuentos(
    srv: AjustesDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_ajustes(cxc_session, *PERMISOS_VER_AJUSTES)
    return {
        "activa": srv.limpieza_activa(),
        "setting_key": SETTING_LIMPIEZA_ACTIVA,
    }


@router.post("/api/config/limpieza-descuentos-odoo")
def guardar_estado_limpieza_descuentos(
    body: LimpiezaToggleIn,
    srv: AjustesDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = _usuario_ajustes(cxc_session, "configuracion")
    activa = srv.set_limpieza_activa(bool(body.activa))
    _invalidar_caches_ventas()
    return {
        "ok": True,
        "activa": activa,
        "setting_key": SETTING_LIMPIEZA_ACTIVA,
        "actualizado_por": _actor_ajustes(user),
    }


@router.get("/api/ordenes/{so_id}/ajustes-descuentos")
def obtener_ajustes_descuento_orden(
    so_id: str,
    srv: AjustesDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    _usuario_ajustes(cxc_session, *PERMISOS_VER_AJUSTES)
    return _payload_orden(so_id, get_repo(), srv)


@router.post("/api/ordenes/{so_id}/ajustes-descuentos/excluir-motor")
def excluir_regla_motor_orden(
    so_id: str,
    body: ExcluirMotorIn,
    srv: AjustesDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    from cxc.web.app import get_repo

    user = _usuario_ajustes(cxc_session, *PERMISOS_EDITAR_AJUSTES)
    try:
        ajuste = srv.excluir_descuento_motor(
            so_id=so_id,
            descuento_origen=body.descuento_origen,
            regla_id=body.regla_id,
            motivo=body.motivo,
            actor=_actor_ajustes(user),
        )
    except AjusteDescuentoDeshabilitado as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AjusteDescuentoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _invalidar_caches_ventas()
    payload = _payload_orden(so_id, get_repo(), srv)
    return JSONResponse(status_code=201, content={"ok": True, "ajuste": ajuste, **payload})


@router.post("/api/ordenes/{so_id}/ajustes-descuentos/especial")
def agregar_descuento_especial_orden(
    so_id: str,
    body: EspecialDescuentoIn,
    srv: AjustesDep,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    from cxc.web.app import get_repo

    user = _usuario_ajustes(cxc_session, *PERMISOS_EDITAR_AJUSTES)
    try:
        ajuste = srv.agregar_descuento_especial(
            so_id=so_id,
            tipo_valor=body.tipo_valor,
            porcentaje=body.porcentaje,
            monto_usd=body.monto_usd,
            descripcion=body.descripcion,
            motivo=body.motivo,
            actor=_actor_ajustes(user),
        )
    except AjusteDescuentoDeshabilitado as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AjusteDescuentoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _invalidar_caches_ventas()
    payload = _payload_orden(so_id, get_repo(), srv)
    return JSONResponse(status_code=201, content={"ok": True, "ajuste": ajuste, **payload})


@router.delete("/api/ordenes/{so_id}/ajustes-descuentos/{ajuste_id}")
def anular_ajuste_descuento_orden(
    so_id: str,
    ajuste_id: int,
    srv: AjustesDep,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    user = _usuario_ajustes(cxc_session, *PERMISOS_EDITAR_AJUSTES)
    try:
        ajuste = srv.anular_ajuste(so_id=so_id, ajuste_id=ajuste_id, actor=_actor_ajustes(user))
    except AjusteDescuentoDeshabilitado as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AjusteDescuentoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _invalidar_caches_ventas()
    payload = _payload_orden(so_id, get_repo(), srv)
    return {"ok": True, "anulado": ajuste, **payload}
