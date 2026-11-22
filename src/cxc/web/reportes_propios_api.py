"""Endpoints REST de Reportes sobre BD Propia y Siembra Histórica Odoo."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, Cookie, Depends, HTTPException, Response

from cxc.config import AppConfig
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.engine.saldos_cxp import (
    calcular_saldos_cxp,
    estado_cuenta_proveedor,
    exportar_cxp_csv,
)
from cxc.engine.saldos_propios import (
    calcular_saldos_propios,
    comparativo_bd_vs_odoo,
    estado_cuenta_cliente,
    exportar_saldos_csv,
)
from cxc.odoo.client import OdooXmlRpcReader
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.terceros import RepoCuentasTercerosSQL
from cxc.operativos.vinculaciones_docs import mapa_vinculaciones_compras
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
    "compras",
)
PERMISOS_VER_CXP = ("compras", "compras.aprobar_documentos")
PERMISOS_VER_AUDITORIA = ("auditoria", "reportes", "reporte")
_PERMISOS_CARTERA_GLOBAL_CXC = (
    "compras",
    "auditoria.decidir",
    "cobranza.aprobar_pagos",
    "ventas.aprobar_pedidos",
    "bancos.ver",
)


def _usuario_rep(cxc_session: str | None, *permisos: str) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if permisos and user.get("rol") != "admin" and not any(tiene(user, p) for p in permisos):
        raise HTTPException(status_code=403, detail="Sin permisos para consultar reportes.")
    return user


def _es_alcance_global_cxc(user: dict[str, Any]) -> bool:
    return user.get("rol") == "admin" or any(tiene(user, p) for p in _PERMISOS_CARTERA_GLOBAL_CXC)


def _filtro_vendedor_para_usuario(
    user: dict[str, Any], vendedor_solicitado: str | None
) -> str | None:
    """Acota reportes CxC al propio vendedor cuando su perfil no tiene alcance global."""
    if _es_alcance_global_cxc(user):
        return vendedor_solicitado
    return str(user.get("email") or user.get("nombre") or "").strip() or "__sin_vendedor__"


def _acotar_respuesta_cxc_vendedor(
    rep: dict[str, Any], user: dict[str, Any]
) -> dict[str, Any]:
    if _es_alcance_global_cxc(user):
        return rep
    vend_email = str(user.get("email") or "").strip().lower()
    salida = dict(rep)
    salida["kpis_globales"] = salida.get("kpis") or {}
    salida["vendedores"] = [
        v
        for v in (salida.get("vendedores") or [])
        if str(v.get("vendedor_email") or "").strip().lower() == vend_email
    ]
    return salida


def _es_admin_siembra(user: dict[str, Any]) -> bool:
    return user.get("rol") == "admin" or tiene(user, "sistema.recalcular")


def _listar_todos_paginado(listar_fn: Any, *, batch: int = 2000) -> list[Any]:
    todos: list[Any] = []
    offset = 0
    while True:
        chunk, total, _res = listar_fn(limit=batch, offset=offset)
        todos.extend(chunk)
        offset += len(chunk)
        if not chunk or offset >= total:
            break
    return todos


def _cargar_extras_bd() -> tuple[Sequence[Any], Sequence[Any]]:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine = getattr(repo, "_engine", None)
    if engine is None:
        return (), ()
    try:
        docs_com = _listar_todos_paginado(RepoDocumentosSQL(engine).listar)
    except Exception:
        docs_com = []
    try:
        docs_op = _listar_todos_paginado(RepoOperativosSQL(engine).listar)
    except Exception:
        docs_op = []
    return docs_com, docs_op


def get_extras_reportes_bd() -> tuple[Sequence[Any], Sequence[Any]]:
    return _cargar_extras_bd()


def get_cuentas_terceros_reportes_bd() -> Sequence[Any]:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine = getattr(repo, "_engine", None)
    if engine is None:
        return ()
    try:
        return RepoCuentasTercerosSQL(engine).listar(solo_activas=False)
    except Exception:
        return ()


ExtrasDep = Annotated[tuple[Sequence[Any], Sequence[Any]], Depends(get_extras_reportes_bd)]
TercerosRepDep = Annotated[Sequence[Any], Depends(get_cuentas_terceros_reportes_bd)]


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

    user = _usuario_rep(cxc_session, *PERMISOS_VER_REPORTES)
    docs_com, docs_op = extras
    rep = calcular_saldos_propios(
        get_repo(),
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
        hoy=_parse_hoy(hoy),
        vendedor=_filtro_vendedor_para_usuario(user, vendedor),
        antiguedad=antiguedad,
        q=q,
        solo_con_saldo=solo_con_saldo,
    )
    return _acotar_respuesta_cxc_vendedor(rep, user)


@router.get("/api/reportes/estado-cuenta/{cliente_id}")
def obtener_estado_cuenta_propio(
    cliente_id: str,
    extras: ExtrasDep,
    hoy: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    user = _usuario_rep(cxc_session, *PERMISOS_VER_REPORTES)
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
    if not _es_alcance_global_cxc(user):
        vend_cliente = str((res.get("resumen") or {}).get("vendedor_email") or "").strip().lower()
        vend_usr = str(user.get("email") or "").strip().lower()
        if not vend_usr or vend_cliente != vend_usr:
            raise HTTPException(
                status_code=403,
                detail="Solo puedes consultar el estado de cuenta de tus clientes asignados.",
            )
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

    user = _usuario_rep(cxc_session, *PERMISOS_VER_REPORTES)
    docs_com, docs_op = extras
    rep = calcular_saldos_propios(
        get_repo(),
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
        hoy=_parse_hoy(hoy),
        vendedor=_filtro_vendedor_para_usuario(user, vendedor),
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


@router.get("/api/reportes/cxp-propios")
def obtener_saldos_cxp_propios(
    extras: ExtrasDep,
    terceros: TercerosRepDep,
    antiguedad: str | None = None,
    q: str | None = None,
    solo_con_saldo: bool = False,
    hoy: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    _usuario_rep(cxc_session, *PERMISOS_VER_CXP)
    _docs_com, docs_op = extras
    return calcular_saldos_cxp(
        get_repo(),
        docs_operativos=docs_op,
        cuentas_terceros=terceros,
        hoy=_parse_hoy(hoy),
        antiguedad=antiguedad,
        q=q,
        solo_con_saldo=solo_con_saldo,
    )


@router.get("/api/reportes/cxp/estado-cuenta/{proveedor_id}")
def obtener_estado_cuenta_cxp(
    proveedor_id: str,
    extras: ExtrasDep,
    terceros: TercerosRepDep,
    hoy: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    from cxc.web.app import get_repo

    _usuario_rep(cxc_session, *PERMISOS_VER_CXP)
    _docs_com, docs_op = extras
    repo = get_repo()
    res = estado_cuenta_proveedor(
        repo,
        proveedor_id,
        docs_operativos=docs_op,
        cuentas_terceros=terceros,
        hoy=_parse_hoy(hoy),
    )
    if res is None:
        raise HTTPException(status_code=404, detail="Proveedor sin movimientos en BD propia.")

    vincs_prov: list[dict[str, Any]] = []
    engine = getattr(repo, "_engine", None)
    if engine is not None:
        try:
            mapa_c = mapa_vinculaciones_compras(engine)
            prov_nom = str((res.get("resumen") or {}).get("proveedor_nombre") or "").strip().lower()
            vistos_vc: set[int] = set()
            for bucket in (
                *(mapa_c.get("por_compra_op_id") or {}).values(),
                *(mapa_c.get("por_compra_move_id") or {}).values(),
            ):
                for v in bucket:
                    vid = int(v.get("vinc_id") or 0)
                    if vid in vistos_vc:
                        continue
                    v_pid = str(v.get("proveedor_odoo_id") or "").strip()
                    v_pnom = str(v.get("proveedor_nombre") or "").strip().lower()
                    if (v_pid and v_pid == str(proveedor_id).strip()) or (
                        prov_nom and v_pnom == prov_nom
                    ):
                        vistos_vc.add(vid)
                        vincs_prov.append(v)
            vincs_prov.sort(
                key=lambda x: (str(x.get("creado_en") or ""), int(x.get("vinc_id") or 0))
            )
        except Exception:
            vincs_prov = []
    res["vinculaciones_compras"] = vincs_prov
    return res


@router.get("/api/reportes/cxp-propios/csv")
def descargar_saldos_cxp_csv(
    extras: ExtrasDep,
    terceros: TercerosRepDep,
    antiguedad: str | None = None,
    q: str | None = None,
    solo_con_saldo: bool = False,
    hoy: str | None = None,
    cxc_session: str | None = Cookie(default=None),
) -> Response:
    from cxc.web.app import get_repo

    _usuario_rep(cxc_session, *PERMISOS_VER_CXP)
    _docs_com, docs_op = extras
    rep = calcular_saldos_cxp(
        get_repo(),
        docs_operativos=docs_op,
        cuentas_terceros=terceros,
        hoy=_parse_hoy(hoy),
        antiguedad=antiguedad,
        q=q,
        solo_con_saldo=solo_con_saldo,
    )
    contenido = exportar_cxp_csv(rep)
    return Response(
        content=contenido,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="saldos_cxp_bd_propia.csv"'},
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
