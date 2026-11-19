"""Endpoints REST de Reportes sobre BD Propia y Siembra Histórica Odoo."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, Cookie, Depends, HTTPException, Response

from cxc.config import AppConfig
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.engine.saldos_propios import (
    calcular_saldos_propios,
    comparativo_bd_vs_odoo,
    estado_cuenta_cliente,
    exportar_saldos_csv,
)
from cxc.odoo.client import OdooXmlRpcReader
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.permisos import tiene
from cxc.sync.siembra_odoo import SiembraOdooService

router = APIRouter()

PERMISOS_VER_REPORTES = (
    "reportes",
    "reporte",
    "auditoria",
    "ventas",
    "pagos",
    "cobranza",
)
PERMISOS_VER_AUDITORIA = ("auditoria", "reportes", "reporte")


def _usuario_rep(cxc_session: str | None, *permisos: str) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if permisos and user.get("rol") != "admin" and not any(tiene(user, p) for p in permisos):
        raise HTTPException(status_code=403, detail="Sin permisos para consultar reportes.")
    return user


def _es_admin_siembra(user: dict[str, Any]) -> bool:
    return user.get("rol") == "admin" or tiene(user, "sistema.recalcular")


def _cargar_extras_bd() -> tuple[Sequence[Any], Sequence[Any]]:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine = getattr(repo, "_engine", None)
    if engine is None:
        return (), ()
    try:
        docs_com, _, _ = RepoDocumentosSQL(engine).listar(limit=5000, offset=0)
    except Exception:
        docs_com = []
    try:
        docs_op, _, _ = RepoOperativosSQL(engine).listar(limit=5000, offset=0)
    except Exception:
        docs_op = []
    return docs_com, docs_op


def get_extras_reportes_bd() -> tuple[Sequence[Any], Sequence[Any]]:
    return _cargar_extras_bd()


ExtrasDep = Annotated[tuple[Sequence[Any], Sequence[Any]], Depends(get_extras_reportes_bd)]


def get_siembra_service() -> SiembraOdooService:
    from cxc.web.app import get_repo

    repo = get_repo()
    cfg = AppConfig.from_env()
    reader = OdooXmlRpcReader(cfg.odoo)
    return SiembraOdooService(repo, reader, engine=getattr(repo, "_engine", None))


SiembraDep = Annotated[SiembraOdooService, Depends(get_siembra_service)]


def _parse_hoy(hoy: str | None) -> date | None:
    if not hoy or not hoy.strip():
        return None
    try:
        return date.fromisoformat(hoy.strip()[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Parámetro hoy inválido (AAAA-MM-DD).") from exc


@router.get("/api/reportes/saldos-propios")
def obtener_saldos_propios(
    extras: ExtrasDep,
    vendedor: str | None = None,
    antiguedad: str | None = None,
    q: str | None = None,
    solo_con_saldo: bool = False,
    hoy: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    _usuario_rep(cxc_session, *PERMISOS_VER_REPORTES)
    docs_com, docs_op = extras
    return calcular_saldos_propios(
        get_repo(),
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
        hoy=_parse_hoy(hoy),
        vendedor=vendedor,
        antiguedad=antiguedad,
        q=q,
        solo_con_saldo=solo_con_saldo,
    )


@router.get("/api/reportes/estado-cuenta/{cliente_id}")
def obtener_estado_cuenta_propio(
    cliente_id: str,
    extras: ExtrasDep,
    hoy: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    _usuario_rep(cxc_session, *PERMISOS_VER_REPORTES)
    docs_com, docs_op = extras
    res = estado_cuenta_cliente(
        get_repo(),
        cliente_id,
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
        hoy=_parse_hoy(hoy),
    )
    if res is None:
        raise HTTPException(status_code=404, detail="Cliente sin movimientos en BD propia.")
    return res


@router.get("/api/reportes/saldos-propios/csv")
def descargar_saldos_propios_csv(
    extras: ExtrasDep,
    vendedor: str | None = None,
    antiguedad: str | None = None,
    q: str | None = None,
    solo_con_saldo: bool = False,
    hoy: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> Response:
    from cxc.web.app import get_repo

    _usuario_rep(cxc_session, *PERMISOS_VER_REPORTES)
    docs_com, docs_op = extras
    rep = calcular_saldos_propios(
        get_repo(),
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
        hoy=_parse_hoy(hoy),
        vendedor=vendedor,
        antiguedad=antiguedad,
        q=q,
        solo_con_saldo=solo_con_saldo,
    )
    contenido = exportar_saldos_csv(rep)
    return Response(
        content=contenido,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="saldos_bd_propia_cxc.csv"'},
    )


@router.get("/api/auditoria/comparativo-bd-odoo")
def obtener_comparativo_bd_odoo(
    extras: ExtrasDep,
    hoy: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    _usuario_rep(cxc_session, *PERMISOS_VER_AUDITORIA)
    docs_com, docs_op = extras
    return comparativo_bd_vs_odoo(
        get_repo(),
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
        hoy=_parse_hoy(hoy),
    )


@router.get("/api/admin/siembra-odoo/estado")
def estado_siembra_odoo(
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    user = _usuario_rep(cxc_session)
    repo = get_repo()
    est = SiembraOdooService.estado_actual(repo, engine=getattr(repo, "_engine", None))
    est["puede_sembrar"] = _es_admin_siembra(user)
    return est


@router.post("/api/admin/siembra-odoo")
def ejecutar_siembra_odoo(
    background_tasks: BackgroundTasks,
    srv: SiembraDep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
    cxc_session: str | None = Cookie(default=None),
) -> Response:
    import json

    user = _usuario_rep(cxc_session)
    if not _es_admin_siembra(user):
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: necesitas permiso «sistema.recalcular» o rol Admin.",
        )
    solo_correlativos = bool(body.get("solo_correlativos", False))
    sincrono = bool(body.get("sincrono", False))
    actor = str(user.get("email") or user.get("nombre") or "admin")

    if sincrono:
        res = srv.ejecutar(actor=actor, solo_correlativos=solo_correlativos)
        return Response(
            status_code=200,
            content=json.dumps({"ok": True, "resultado": res}, ensure_ascii=False),
            media_type="application/json",
        )

    background_tasks.add_task(srv.ejecutar, actor=actor, solo_correlativos=solo_correlativos)
    return Response(
        status_code=202,
        content=json.dumps(
            {
                "ok": True,
                "status": "started",
                "solo_correlativos": solo_correlativos,
                "message": "Siembra histórica de Odoo iniciada en segundo plano.",
            },
            ensure_ascii=False,
        ),
        media_type="application/json",
    )
