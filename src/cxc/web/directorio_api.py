"""Endpoints de consulta en Odoo: directorio de contactos y plan de cuentas.

Rutas:
- ``GET /api/odoo/contactos``: lista los contactos de Odoo clasificados en ``prospecto``,
  ``cliente``, ``proveedor`` y ``ambos`` (más el marcador ``es_borrador_telegram``), con totales
  por categoría, búsqueda y paginación.
- ``GET /api/odoo/plan-cuentas``: devuelve el plan de cuentas activo de Odoo
  (``account.account``), la propuesta de clasificación para las categorías de compras y gastos
  de Lubrikca y el clasificador interactivo por concepto libre.

Ambas rutas son estrictamente de **solo lectura** (``OdooConfig.from_env()``) y mantienen un
caché breve en memoria (120 s, recargable con ``refresh=true``) para que filtrar o buscar desde
la pantalla sea instantáneo sin saturar el XML-RPC de Odoo.
"""

from __future__ import annotations

import threading
import time
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException

from cxc.config import OdooConfig
from cxc.odoo.directorio import (
    CLASIFICACIONES_CONTACTO,
    ContactoOdoo,
    CuentaContable,
    LectorDirectorioOdoo,
    OdooDirectorioError,
    conectar_lectura,
    filtrar_contactos,
    filtrar_plan_cuentas,
    proponer_clasificacion_gastos,
    resumir_contactos,
    resumir_plan_cuentas,
    sugerir_para_concepto,
)
from cxc.permisos import tiene

router = APIRouter()

PERMISOS_DIRECTORIO = (
    "contactos",
    "compras",
    "pagos",
    "cobranza",
    "configuracion",
    "reportes",
    "capturas.ver",
    "capturas.gestionar",
    "bots.ver",
    "bots.gestionar",
    "auditoria",
    "reporte",
)

CACHE_TTL_SEGUNDOS = 120.0
_lock = threading.Lock()
_cache_contactos: tuple[float, list[ContactoOdoo]] | None = None
_cache_cuentas: tuple[float, list[CuentaContable]] | None = None


def limpiar_cache_directorio() -> None:
    """Limpia el caché en memoria (útil en tests o al forzar recarga)."""
    global _cache_contactos, _cache_cuentas
    with _lock:
        _cache_contactos = None
        _cache_cuentas = None


def _usuario_autorizado(cxc_session: str | None) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if not any(tiene(user, p) for p in PERMISOS_DIRECTORIO):
        raise HTTPException(
            status_code=403, detail="Sin permisos para consultar el directorio de Odoo"
        )
    return user


def get_lector_odoo() -> LectorDirectorioOdoo:
    try:
        cfg = OdooConfig.from_env()
    except KeyError as e:
        raise HTTPException(
            status_code=503,
            detail="Falta configurar la conexión de lectura a Odoo (ODOO_URL/DB/USER/PASSWORD).",
        ) from e
    try:
        return LectorDirectorioOdoo(conectar_lectura(cfg))
    except OdooDirectorioError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


LectorDep = Annotated[LectorDirectorioOdoo, Depends(get_lector_odoo)]


def _obtener_contactos(lector: LectorDirectorioOdoo, refresh: bool = False) -> list[ContactoOdoo]:
    global _cache_contactos
    ahora = time.monotonic()
    with _lock:
        if not refresh and _cache_contactos is not None:
            ts, datos = _cache_contactos
            if ahora - ts < CACHE_TTL_SEGUNDOS:
                return datos
    try:
        nuevos = lector.leer_contactos()
    except OdooDirectorioError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    with _lock:
        _cache_contactos = (ahora, nuevos)
    return nuevos


def _obtener_cuentas(lector: LectorDirectorioOdoo, refresh: bool = False) -> list[CuentaContable]:
    global _cache_cuentas
    ahora = time.monotonic()
    with _lock:
        if not refresh and _cache_cuentas is not None:
            ts, datos = _cache_cuentas
            if ahora - ts < CACHE_TTL_SEGUNDOS:
                return datos
    try:
        nuevas = lector.leer_plan_cuentas()
    except OdooDirectorioError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    with _lock:
        _cache_cuentas = (ahora, nuevas)
    return nuevas


@router.get("/api/odoo/contactos")
def listar_contactos_odoo(
    lector: LectorDep,
    clasificacion: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    refresh: bool = False,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_autorizado(cxc_session)
    if clasificacion and clasificacion not in CLASIFICACIONES_CONTACTO:
        opciones = ", ".join(CLASIFICACIONES_CONTACTO)
        raise HTTPException(
            status_code=422,
            detail=f"clasificacion debe ser uno de: {opciones}",
        )

    todos = _obtener_contactos(lector, refresh=refresh)
    resumen = resumir_contactos(todos)
    filtrados = filtrar_contactos(todos, clasificacion=clasificacion, q=q)

    lim = max(1, min(limit, 500))
    off = max(0, offset)
    pagina = filtrados[off : off + lim]

    return {
        "resumen": resumen,
        "total_filtrado": len(filtrados),
        "limit": lim,
        "offset": off,
        "items": [c.a_dict() for c in pagina],
    }


@router.get("/api/odoo/plan-cuentas")
def consultar_plan_cuentas_odoo(
    lector: LectorDep,
    grupo: str | None = None,
    account_type: str | None = None,
    q: str | None = None,
    concepto: str | None = None,
    refresh: bool = False,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario_autorizado(cxc_session)
    cuentas = _obtener_cuentas(lector, refresh=refresh)
    resumen = resumir_plan_cuentas(cuentas)
    propuestas = proponer_clasificacion_gastos(cuentas)
    filtradas = filtrar_plan_cuentas(cuentas, grupo=grupo, account_type=account_type, q=q)
    sugerencia = sugerir_para_concepto(concepto, cuentas) if concepto and concepto.strip() else None

    return {
        "resumen": resumen,
        "propuestas": propuestas,
        "sugerencia_concepto": sugerencia,
        "total_filtrado": len(filtradas),
        "cuentas": [c.a_dict() for c in filtradas],
    }
