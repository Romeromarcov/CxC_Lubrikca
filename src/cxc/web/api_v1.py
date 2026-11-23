"""Router FastAPI v1 (/api/v1/*) para App Móvil Offline de Vendedores y Portal de Clientes."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from cxc import permisos as accesos
from cxc.api_v1.jwt_tokens import (
    AUD_PORTAL,
    AUD_VENDEDOR,
    DEFAULT_ACCESS_TTL_SECONDS,
    RateLimiterV1,
    crear_access_token,
    secret_v1_desde_entorno,
    verificar_access_token,
)
from cxc.api_v1.repositorio import (
    ApiV1Conflicto,
    ApiV1Error,
    ApiV1NoAutorizado,
    ApiV1NoEncontrado,
    RepoApiV1SQL,
)
from cxc.api_v1.storage import AdjuntoError, AdjuntosStorage
from cxc.api_v1.sync_service import (
    _es_alcance_global,
    construir_bootstrap_vendedor,
    construir_cartera_vendedor,
    construir_delta_vendedor,
    procesar_push_batch,
)
from cxc.auth import (
    autenticar_usuario,
    buscar_usuario_plataforma,
    password_token_version,
)
from cxc.capturas.modelos import CapturaInvalida, normalizar_nombre
from cxc.config import AppConfig
from cxc.documentos.modelos import COTIZACION, DocumentoInvalido
from cxc.documentos.preview_html import renderizar_preview_cotizacion
from cxc.documentos.repositorio import RepoDocumentosSQL
from cxc.documentos.servicio import DocumentoNoEncontrado, ServicioDocumentos
from cxc.engine.rutas_pago import RUTA_BCV, RUTA_BINANCE, normalizar_ruta, ruta_de_lista
from cxc.engine.saldos_propios import estado_cuenta_cliente
from cxc.odoo.client import _connect
from cxc.operativos.cuentas_receptoras import RepoCuentasReceptorasSQL
from cxc.operativos.lotes_efectivo import RepoLotesEfectivo
from cxc.operativos.modelos import (
    ENVIADO_ODOO,
    TIPO_PAGO,
    OperativoInvalido,
    OperativoNoEncontrado,
)
from cxc.operativos.preview_html import (
    renderizar_preview_operativo,
    renderizar_recibo_operativo,
)
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import RepoCuentasTercerosSQL
from cxc.repositories import Repository
from cxc.web.contactos_api import get_servicio_contactos
from cxc.web.documentos_api import get_servicio_documentos
from cxc.web.operativos_api import get_servicio_operativos

logger = logging.getLogger("cxc.web.api_v1")

router = APIRouter(prefix="/api/v1", tags=["api_v1"])

_RATE_LIMITER_V1 = RateLimiterV1(window_seconds=300, max_attempts=10)

_RE_UPLOAD_LOCAL = re.compile(r"^/api/v1/adjuntos/upload/[^/]+$")
_RUTAS_PUBLICAS_V1_POST = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/portal/auth/login",
        "/api/v1/portal/auth/activar-invitacion",
        "/api/v1/portal/auth/refresh",
        "/api/v1/portal/auth/logout",
    }
)


def es_ruta_v1_publica(metodo: str, ruta: str) -> bool:
    """Rutas bajo ``/api/v1/`` que el middleware deja pasar sin cookie ni Bearer JWT previo."""
    if metodo == "POST" and ruta in _RUTAS_PUBLICAS_V1_POST:
        return True
    return metodo == "PUT" and _RE_UPLOAD_LOCAL.match(ruta) is not None


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "unknown")


def _repo_y_engine() -> tuple[Repository, Engine]:
    from cxc.web.app import get_repo

    repo: Any = get_repo()
    engine = getattr(repo, "_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=503, detail="API v1 requiere backend PostgreSQL configurado."
        )
    return repo, engine


def get_repo_api_v1() -> RepoApiV1SQL:
    _repo, engine = _repo_y_engine()
    return RepoApiV1SQL(engine)


def _jwt_secret() -> str:
    from cxc.web import app as web_app

    return secret_v1_desde_entorno(str(web_app.SECRET_KEY))


def get_adjuntos_storage() -> AdjuntosStorage:
    return AdjuntosStorage(get_repo_api_v1(), _jwt_secret())


def _resolver_dep(fabrica: Any) -> Any:
    from cxc.web.app import app as _app

    return _app.dependency_overrides.get(fabrica, fabrica)()


RepoV1Dep = Annotated[RepoApiV1SQL, Depends(get_repo_api_v1)]
StorageV1Dep = Annotated[AdjuntosStorage, Depends(get_adjuntos_storage)]


def _extraer_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Token Bearer requerido.")
    tok = authorization[7:].strip()
    if not tok:
        raise HTTPException(status_code=401, detail="Token Bearer vacío.")
    return tok


def _pwd_version_usuario(u_row: dict[str, Any]) -> str:
    return password_token_version(u_row.get("password_hash"))


def _usuario_vendedor_desde_repo(repo: Repository, email: str) -> dict[str, Any]:
    from cxc.web import app as web_app

    u_row = buscar_usuario_plataforma(repo, email)
    if not u_row or u_row.get("activo") == "FALSE":
        raise HTTPException(status_code=401, detail="Usuario interno inactivo o inexistente.")
    rol = str(u_row.get("rol") or "ventas")
    return {
        "email": str(u_row.get("email") or email).strip().lower(),
        "nombre": str(u_row.get("nombre_odoo") or email),
        "rol": rol,
        "permisos": accesos.efectivos(rol, web_app._ajustes_de_acceso(repo, email)),
        "_pwd_ver": _pwd_version_usuario(u_row),
    }


def exigir_vendedor_v1(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tok = _extraer_bearer(authorization)
    sec = _jwt_secret()
    payload = verificar_access_token(tok, sec)
    if payload is None:
        raise HTTPException(status_code=401, detail="Token inválido o expirado.")
    if payload.get("aud") != AUD_VENDEDOR:
        raise HTTPException(
            status_code=403,
            detail="El token de portal de cliente no tiene acceso a rutas de vendedor.",
        )
    repo, _eng = _repo_y_engine()
    user = _usuario_vendedor_desde_repo(repo, str(payload["sub"]))
    pwd_tok = str(payload.get("pwd_v") or "")
    if pwd_tok and user["_pwd_ver"] and pwd_tok != user["_pwd_ver"]:
        raise HTTPException(status_code=401, detail="Sesión revocada por cambio de clave.")
    return user


def exigir_portal_cliente_v1(
    repo_v1: RepoV1Dep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tok = _extraer_bearer(authorization)
    sec = _jwt_secret()
    payload = verificar_access_token(tok, sec)
    if payload is None:
        raise HTTPException(status_code=401, detail="Token inválido o expirado.")
    if payload.get("aud") != AUD_PORTAL:
        raise HTTPException(
            status_code=403,
            detail="Audiencia JWT no autorizada para el portal de clientes.",
        )
    try:
        cuenta_id = int(str(payload["sub"]))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Sujeto de token inválido.") from exc
    cuenta = repo_v1.obtener_cuenta_portal(cuenta_id)
    if cuenta is None or not bool(cuenta.get("activo")):
        raise HTTPException(status_code=401, detail="Cuenta de portal inactiva o inexistente.")
    pwd_tok = str(payload.get("pwd_v") or "")
    if pwd_tok and cuenta.get("_pwd_ver") and pwd_tok != cuenta["_pwd_ver"]:
        raise HTTPException(status_code=401, detail="Sesión revocada por cambio de clave.")
    return cuenta


def exigir_cualquier_v1(
    repo_v1: RepoV1Dep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tok = _extraer_bearer(authorization)
    payload = verificar_access_token(tok, _jwt_secret())
    if payload is None:
        raise HTTPException(status_code=401, detail="Token inválido o expirado.")
    aud = str(payload.get("aud") or "")
    if aud == AUD_VENDEDOR:
        user = exigir_vendedor_v1(authorization=authorization)
        return {"actor_tipo": "vendedor", "actor_id": user["email"], "user": user}
    if aud == AUD_PORTAL:
        cuenta = exigir_portal_cliente_v1(repo_v1=repo_v1, authorization=authorization)
        return {
            "actor_tipo": "cliente_portal",
            "actor_id": str(cuenta["cuenta_id"]),
            "cuenta": cuenta,
        }
    raise HTTPException(status_code=403, detail="Audiencia JWT no permitida.")


def _admin_portal(cxc_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    es_admin = user.get("rol") == "admin"
    if not es_admin and not (
        accesos.tiene(user, "contactos")
        or accesos.tiene(user, "contactos.gestionar")
        or accesos.tiene(user, "ventas.aprobar_pedidos")
    ):
        raise HTTPException(
            status_code=403, detail="Sin permisos para administrar cuentas de portal."
        )
    return user


VendedorV1Dep = Annotated[dict[str, Any], Depends(exigir_vendedor_v1)]
ClientePortalV1Dep = Annotated[dict[str, Any], Depends(exigir_portal_cliente_v1)]
CualquierActorV1Dep = Annotated[dict[str, Any], Depends(exigir_cualquier_v1)]


# ── Modelos Pydantic v1 ───────────────────────────────────────────────────────


class LoginV1In(BaseModel):
    email: str
    password: str
    device_id: str = ""
    plataforma: str = "android"
    push_token: str = ""
    app_version: str = ""


class RefreshV1In(BaseModel):
    refresh_token: str


class LogoutV1In(BaseModel):
    refresh_token: str = ""
    device_id: str = ""


class SyncPushItemIn(BaseModel):
    idempotency_key: str = ""
    tipo: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SyncPushBatchIn(BaseModel):
    device_id: str = ""
    items: list[SyncPushItemIn] = Field(default_factory=list)


class PresignAdjuntoIn(BaseModel):
    mime: str
    bytes: int
    sha256: str
    nombre_archivo: str = ""


class InvitacionPortalIn(BaseModel):
    cliente_id: str
    partner_odoo_id: int | None = None
    cliente_nombre: str = ""
    email: str
    rol_portal: str = "titular"
    ttl_horas: int = 48


class ActivarInvitacionIn(BaseModel):
    token: str
    password: str
    telefono: str = ""


class ActualizarCuentaPortalIn(BaseModel):
    activo: bool | None = None
    rol_portal: str | None = None


# ── Auth Vendedores (App Móvil) ───────────────────────────────────────────────


@router.post("/auth/login")
def v1_auth_login(body: LoginV1In, request: Request, repo_v1: RepoV1Dep) -> dict[str, Any]:
    ip = _client_ip(request)
    email_clean = body.email.strip().lower()
    if not _RATE_LIMITER_V1.registrar_o_bloquear(f"v1_vend:{ip}", email_clean):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos fallidos. Espera 5 minutos antes de reintentar.",
        )

    repo, _eng = _repo_y_engine()
    execute = None
    try:
        cfg = AppConfig.from_env()
        execute = _connect(cfg.odoo)
    except Exception:
        execute = None

    user_info, err_msg = autenticar_usuario(execute, repo, email_clean, body.password)
    if not user_info:
        raise HTTPException(status_code=401, detail=err_msg or "Credenciales inválidas.")

    _RATE_LIMITER_V1.limpiar(f"v1_vend:{ip}", email_clean)
    pwd_ver = str(user_info.pop("_pwd_ver", "") or "")
    user_completo = _usuario_vendedor_desde_repo(repo, email_clean)

    disp = repo_v1.registrar_dispositivo(
        device_id=body.device_id,
        sujeto_tipo="vendedor",
        sujeto_id=email_clean,
        plataforma=body.plataforma,
        push_token=body.push_token,
        app_version=body.app_version,
    )
    refresh_tok, _ref_row = repo_v1.emitir_refresh_token(
        sujeto_tipo="vendedor",
        sujeto_id=email_clean,
        device_id=str(disp["device_id"]),
        password_version=pwd_ver,
        ip_origen=ip,
    )
    access_tok = crear_access_token(
        subject=email_clean,
        audience=AUD_VENDEDOR,
        secret_key=_jwt_secret(),
        claims={
            "rol": user_completo["rol"],
            "nombre": user_completo["nombre"],
            "device_id": disp["device_id"],
            "pwd_v": pwd_ver,
        },
        ttl_seconds=DEFAULT_ACCESS_TTL_SECONDS,
    )
    return {
        "token_type": "Bearer",
        "access_token": access_tok,
        "refresh_token": refresh_tok,
        "expires_in": DEFAULT_ACCESS_TTL_SECONDS,
        "device_id": disp["device_id"],
        "user": {k: v for k, v in user_completo.items() if k != "_pwd_ver"},
    }


@router.post("/auth/refresh")
def v1_auth_refresh(body: RefreshV1In, request: Request, repo_v1: RepoV1Dep) -> dict[str, Any]:
    repo, _eng = _repo_y_engine()
    try:
        # Verificar usuario y pwd_ver actuales antes de confirmar la rotación si ya existía
        nuevo_rt, fila = repo_v1.rotar_refresh_token(
            raw_refresh_token=body.refresh_token,
            expected_sujeto_tipo="vendedor",
            ip_origen=_client_ip(request),
        )
        user = _usuario_vendedor_desde_repo(repo, str(fila["sujeto_id"]))
        if (
            fila.get("password_version")
            and user["_pwd_ver"]
            and str(fila["password_version"]) != user["_pwd_ver"]
        ):
            repo_v1.revocar_refresh_tokens_sujeto(sujeto_tipo="vendedor", sujeto_id=user["email"])
            raise ApiV1NoAutorizado("Sesión invalidada tras cambio de contraseña.")
    except ApiV1NoAutorizado as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    access_tok = crear_access_token(
        subject=user["email"],
        audience=AUD_VENDEDOR,
        secret_key=_jwt_secret(),
        claims={
            "rol": user["rol"],
            "nombre": user["nombre"],
            "device_id": fila.get("device_id", ""),
            "pwd_v": user["_pwd_ver"],
        },
        ttl_seconds=DEFAULT_ACCESS_TTL_SECONDS,
    )
    return {
        "token_type": "Bearer",
        "access_token": access_tok,
        "refresh_token": nuevo_rt,
        "expires_in": DEFAULT_ACCESS_TTL_SECONDS,
        "user": {k: v for k, v in user.items() if k != "_pwd_ver"},
    }


@router.post("/auth/logout")
def v1_auth_logout(
    body: LogoutV1In,
    repo_v1: RepoV1Dep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sujeto_id = ""
    if authorization and authorization.lower().startswith("bearer "):
        payload = verificar_access_token(
            authorization[7:].strip(), _jwt_secret(), expected_audience=AUD_VENDEDOR
        )
        if payload:
            sujeto_id = str(payload["sub"])
    revocados = repo_v1.revocar_refresh_tokens_sujeto(
        sujeto_tipo="vendedor",
        sujeto_id=sujeto_id,
        device_id=body.device_id or None,
        raw_refresh_token=body.refresh_token or None,
    )
    return {"status": "ok", "revocados": revocados}


@router.get("/auth/me")
def v1_auth_me(user: VendedorV1Dep) -> dict[str, Any]:
    return {"user": {k: v for k, v in user.items() if k != "_pwd_ver"}}


# ── Sincronización Offline Vendedores (/api/v1/sync/*) ────────────────────────


@router.get("/sync/bootstrap")
def v1_sync_bootstrap(user: VendedorV1Dep) -> dict[str, Any]:
    repo, engine = _repo_y_engine()
    servicio_docs = _resolver_dep(get_servicio_documentos)
    return construir_bootstrap_vendedor(
        repo=repo,
        engine=engine,
        user=user,
        servicio_docs=servicio_docs,
    )


@router.get("/sync/delta")
def v1_sync_delta(user: VendedorV1Dep, cursor: str = "") -> dict[str, Any]:
    repo, engine = _repo_y_engine()
    servicio_docs = _resolver_dep(get_servicio_documentos)
    return construir_delta_vendedor(
        repo=repo,
        engine=engine,
        user=user,
        servicio_docs=servicio_docs,
        cursor_str=cursor,
    )


@router.post("/sync/push")
def v1_sync_push(
    body: SyncPushBatchIn,
    user: VendedorV1Dep,
    repo_v1: RepoV1Dep,
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _repo, engine = _repo_y_engine()
    servicio_docs = _resolver_dep(get_servicio_documentos)
    servicio_ops = _resolver_dep(get_servicio_operativos)
    servicio_contactos = _resolver_dep(get_servicio_contactos)
    hdr_key = (x_idempotency_key or "").strip()
    items_norm: list[dict[str, Any]] = []
    for idx, it in enumerate(body.items):
        d = it.model_dump()
        if not str(d.get("idempotency_key") or "").strip() and hdr_key:
            d["idempotency_key"] = hdr_key if len(body.items) == 1 else f"{hdr_key}:{idx}"
        items_norm.append(d)
    return procesar_push_batch(
        items=items_norm,
        vendedor_email=str(user["email"]),
        device_id=body.device_id,
        repo_v1=repo_v1,
        engine=engine,
        servicio_docs=servicio_docs,
        servicio_ops=servicio_ops,
        servicio_contactos=servicio_contactos,
    )


@router.get("/stock/verificar")
def v1_stock_verificar(
    _user: VendedorV1Dep,
    q: str = "",
    producto_id: str = "",
    refresh: bool = False,
) -> dict[str, Any]:
    svc_docs: ServicioDocumentos = _resolver_dep(get_servicio_documentos)
    pid_f = producto_id.strip()
    q_low = q.strip().lower()
    ahora = datetime.now().isoformat(timespec="seconds")
    salida: list[dict[str, Any]] = []
    for it in svc_docs.catalogo_items(refresh=refresh):
        d = it.a_dict()
        if pid_f and str(d.get("producto_id") or "") != pid_f:
            continue
        if q_low:
            claves = ("codigo", "nombre", "marca", "presentacion", "etiqueta")
            hay = " ".join(str(d.get(k) or "") for k in claves).lower()
            if q_low not in hay:
                continue
        salida.append(
            {
                "producto_id": d["producto_id"],
                "codigo": d["codigo"],
                "nombre": d["nombre"],
                "marca": d["marca"],
                "presentacion": d["presentacion"],
                "precio_usd": d["precio_usd"],
                "precio_ves": d["precio_ves"],
                "precios_por_lista": d["precios_por_lista"],
                "stock_disponible": d["stock_disponible"],
                "actualizado_en": ahora,
            }
        )
    return {
        "total": len(salida),
        "actualizado_en": ahora,
        "items": salida,
    }


@router.get("/vendedor/lotes-efectivo")
def v1_vendedor_lotes_efectivo(
    user: VendedorV1Dep,
    limit: int = 100,
) -> dict[str, Any]:
    _repo, engine = _repo_y_engine()
    global_scope = _es_alcance_global(user)
    vend = None if global_scope else str(user["email"])
    lotes = RepoLotesEfectivo(engine).listar(vendedor_email=vend, limit=limit)
    return {
        "vendedor_email": str(user["email"]),
        "alcance_global": global_scope,
        "total": len(lotes),
        "items": lotes,
    }


@router.get("/vendedor/cartera")
def v1_vendedor_cartera(user: VendedorV1Dep) -> dict[str, Any]:
    repo, engine = _repo_y_engine()
    return construir_cartera_vendedor(repo=repo, engine=engine, user=user)


# ── Adjuntos (Comprobantes R2/S3 o Local HMAC) ────────────────────────────────


@router.post("/adjuntos/presign")
def v1_adjuntos_presign(
    body: PresignAdjuntoIn,
    actor: CualquierActorV1Dep,
    storage: StorageV1Dep,
) -> JSONResponse:
    try:
        res = storage.preparar_presign(
            mime=body.mime,
            bytes_len=body.bytes,
            sha256_hex=body.sha256,
            nombre_archivo=body.nombre_archivo,
            actor_tipo=str(actor["actor_tipo"]),
            actor_id=str(actor["actor_id"]),
        )
    except AdjuntoError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=res)


@router.put("/adjuntos/upload/{upload_token}")
async def v1_adjuntos_upload_local(
    upload_token: str,
    request: Request,
    storage: StorageV1Dep,
) -> JSONResponse:
    contenido = await request.body()
    ct = request.headers.get("content-type", "")
    try:
        reg = storage.recibir_upload_local(
            upload_token=upload_token,
            contenido=contenido,
            content_type=ct,
        )
    except AdjuntoError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    return JSONResponse(
        status_code=200,
        content={
            "adjunto_id": int(reg["adjunto_id"]),
            "storage_key": reg["storage_key"],
            "storage_backend": reg["storage_backend"],
            "sha256": reg["sha256"],
            "bytes": int(reg["bytes"]),
        },
    )


# ── Administración de Invitaciones y Cuentas del Portal (Interno · Cookie) ────


@router.post("/portal/invitaciones")
def v1_admin_crear_invitacion_portal(
    body: InvitacionPortalIn,
    repo_v1: RepoV1Dep,
    user: Annotated[dict[str, Any], Depends(_admin_portal)],
) -> JSONResponse:
    try:
        token_raw, inv = repo_v1.crear_invitacion_portal(
            cliente_id=body.cliente_id,
            partner_odoo_id=body.partner_odoo_id,
            cliente_nombre=body.cliente_nombre,
            email=body.email,
            rol_portal=body.rol_portal,
            creado_por=str(user.get("email") or user.get("nombre") or "admin"),
            ttl_horas=body.ttl_horas,
        )
    except ApiV1Error as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    return JSONResponse(
        status_code=201,
        content={
            **inv,
            "token": token_raw,
            "enlace_activacion": f"/portal/activar?token={token_raw}",
        },
    )


@router.get("/portal/invitaciones/cuentas")
def v1_admin_listar_cuentas_portal(
    repo_v1: RepoV1Dep,
    _user: Annotated[dict[str, Any], Depends(_admin_portal)],
    cliente_id: str | None = None,
) -> dict[str, Any]:
    items = repo_v1.listar_cuentas_portal(cliente_id=cliente_id)
    return {"total": len(items), "items": items}


@router.put("/portal/invitaciones/cuentas/{cuenta_id}")
def v1_admin_actualizar_cuenta_portal(
    cuenta_id: int,
    body: ActualizarCuentaPortalIn,
    repo_v1: RepoV1Dep,
    _user: Annotated[dict[str, Any], Depends(_admin_portal)],
) -> JSONResponse:
    try:
        actualizado = repo_v1.actualizar_cuenta_portal(
            cuenta_id,
            activo=body.activo,
            rol_portal=body.rol_portal,
        )
    except ApiV1NoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApiV1Error as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=actualizado)


# ── Auth Portal de Clientes ───────────────────────────────────────────────────


@router.post("/portal/auth/activar-invitacion")
def v1_portal_activar_invitacion(
    body: ActivarInvitacionIn,
    repo_v1: RepoV1Dep,
) -> JSONResponse:
    try:
        cuenta = repo_v1.activar_invitacion_portal(
            token=body.token,
            password=body.password,
            telefono=body.telefono,
        )
    except ApiV1NoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApiV1Conflicto as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    except ApiV1Error as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    return JSONResponse(status_code=200, content={"status": "activada", "cuenta": cuenta})


@router.post("/portal/auth/login")
def v1_portal_auth_login(
    body: LoginV1In,
    request: Request,
    repo_v1: RepoV1Dep,
) -> dict[str, Any]:
    ip = _client_ip(request)
    email_clean = body.email.strip().lower()
    if not _RATE_LIMITER_V1.registrar_o_bloquear(f"v1_portal:{ip}", email_clean):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos fallidos. Espera 5 minutos antes de reintentar.",
        )
    try:
        cuenta = repo_v1.autenticar_cuenta_portal(email=email_clean, password=body.password)
    except ApiV1NoAutorizado as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _RATE_LIMITER_V1.limpiar(f"v1_portal:{ip}", email_clean)
    sujeto_id = str(cuenta["cuenta_id"])
    disp = repo_v1.registrar_dispositivo(
        device_id=body.device_id,
        sujeto_tipo="cliente_portal",
        sujeto_id=sujeto_id,
        plataforma=body.plataforma or "web_pwa",
        push_token=body.push_token,
        app_version=body.app_version,
    )
    pwd_ver = str(cuenta.get("_pwd_ver") or "")
    refresh_tok, _ = repo_v1.emitir_refresh_token(
        sujeto_tipo="cliente_portal",
        sujeto_id=sujeto_id,
        device_id=str(disp["device_id"]),
        password_version=pwd_ver,
        ip_origen=ip,
    )
    access_tok = crear_access_token(
        subject=sujeto_id,
        audience=AUD_PORTAL,
        secret_key=_jwt_secret(),
        claims={
            "email": cuenta["email"],
            "cliente_id": cuenta["cliente_id"],
            "partner_odoo_id": cuenta.get("partner_odoo_id"),
            "rol_portal": cuenta["rol_portal"],
            "pwd_v": pwd_ver,
        },
        ttl_seconds=DEFAULT_ACCESS_TTL_SECONDS,
    )
    return {
        "token_type": "Bearer",
        "access_token": access_tok,
        "refresh_token": refresh_tok,
        "expires_in": DEFAULT_ACCESS_TTL_SECONDS,
        "cuenta": {
            "cuenta_id": int(cuenta["cuenta_id"]),
            "cliente_id": cuenta["cliente_id"],
            "partner_odoo_id": cuenta.get("partner_odoo_id"),
            "cliente_nombre": cuenta.get("cliente_nombre") or "",
            "email": cuenta["email"],
            "rol_portal": cuenta["rol_portal"],
        },
    }


@router.post("/portal/auth/refresh")
def v1_portal_auth_refresh(
    body: RefreshV1In,
    request: Request,
    repo_v1: RepoV1Dep,
) -> dict[str, Any]:
    try:
        nuevo_rt, fila = repo_v1.rotar_refresh_token(
            raw_refresh_token=body.refresh_token,
            expected_sujeto_tipo="cliente_portal",
            ip_origen=_client_ip(request),
        )
        cuenta = repo_v1.obtener_cuenta_portal(int(str(fila["sujeto_id"])))
        if cuenta is None or not bool(cuenta.get("activo")):
            repo_v1.revocar_refresh_tokens_sujeto(
                sujeto_tipo="cliente_portal", sujeto_id=str(fila["sujeto_id"])
            )
            raise ApiV1NoAutorizado("Cuenta de portal desactivada.")
    except ApiV1NoAutorizado as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    access_tok = crear_access_token(
        subject=str(cuenta["cuenta_id"]),
        audience=AUD_PORTAL,
        secret_key=_jwt_secret(),
        claims={
            "email": cuenta["email"],
            "cliente_id": cuenta["cliente_id"],
            "partner_odoo_id": cuenta.get("partner_odoo_id"),
            "rol_portal": cuenta["rol_portal"],
            "pwd_v": str(cuenta.get("_pwd_ver") or ""),
        },
        ttl_seconds=DEFAULT_ACCESS_TTL_SECONDS,
    )
    return {
        "token_type": "Bearer",
        "access_token": access_tok,
        "refresh_token": nuevo_rt,
        "expires_in": DEFAULT_ACCESS_TTL_SECONDS,
    }


@router.post("/portal/auth/logout")
def v1_portal_auth_logout(
    body: LogoutV1In,
    repo_v1: RepoV1Dep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    sujeto_id = ""
    if authorization and authorization.lower().startswith("bearer "):
        payload = verificar_access_token(
            authorization[7:].strip(), _jwt_secret(), expected_audience=AUD_PORTAL
        )
        if payload:
            sujeto_id = str(payload["sub"])
    revocados = repo_v1.revocar_refresh_tokens_sujeto(
        sujeto_tipo="cliente_portal",
        sujeto_id=sujeto_id,
        device_id=body.device_id or None,
        raw_refresh_token=body.refresh_token or None,
    )
    return {"status": "ok", "revocados": revocados}


# ── Operación Portal de Clientes (/api/v1/portal/*) ───────────────────────────


def _es_dueno_registro(cuenta: dict[str, Any], *, partner_id: int | None, nombre: str) -> bool:
    pid_cta = cuenta.get("partner_odoo_id")
    if pid_cta is not None and partner_id is not None:
        return int(pid_cta) == int(partner_id)
    if str(cuenta["cliente_id"]).isdigit() and partner_id is not None:
        return int(str(cuenta["cliente_id"])) == int(partner_id)
    nom_cta = normalizar_nombre(str(cuenta.get("cliente_nombre") or ""))
    nom_doc = normalizar_nombre(nombre)
    return bool(nom_cta and nom_doc and nom_cta == nom_doc)


@router.get("/portal/me")
def v1_portal_me(cuenta: ClientePortalV1Dep) -> dict[str, Any]:
    return {
        "cuenta_id": int(cuenta["cuenta_id"]),
        "cliente_id": cuenta["cliente_id"],
        "partner_odoo_id": cuenta.get("partner_odoo_id"),
        "cliente_nombre": cuenta.get("cliente_nombre") or "",
        "email": cuenta["email"],
        "rol_portal": cuenta["rol_portal"],
    }


@router.get("/portal/estado-cuenta")
def v1_portal_estado_cuenta(cuenta: ClientePortalV1Dep) -> dict[str, Any]:
    repo, engine = _repo_y_engine()
    docs_com = RepoDocumentosSQL(engine).listar(limit=2000)[0]
    docs_op = RepoOperativosSQL(engine).listar(limit=2000)[0]
    ec = estado_cuenta_cliente(
        repo,
        str(cuenta["cliente_id"]),
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
    )
    resumen: dict[str, Any]
    if ec is None:
        resumen = {
            "cliente_id": str(cuenta["cliente_id"]),
            "cliente_nombre": str(cuenta.get("cliente_nombre") or ""),
            "ventas_bcv_usd": 0.0,
            "pagos_bcv_usd": 0.0,
            "ventas_binance_usd": 0.0,
            "pagos_binance_usd": 0.0,
            "saldo_deudor_usd": 0.0,
            "saldo_favor_usd": 0.0,
            "tramos": {},
        }
        movs: list[dict[str, Any]] = []
    else:
        resumen = dict(ec.get("resumen") or {})
        movs = list(ec.get("movimientos") or [])

    movs_bcv = [m for m in movs if normalizar_ruta(m.get("ruta_pago")) != RUTA_BINANCE]
    movs_bin = [m for m in movs if normalizar_ruta(m.get("ruta_pago")) == RUTA_BINANCE]

    v_bcv = float(resumen.get("ventas_bcv_usd") or 0.0)
    p_bcv = float(resumen.get("pagos_bcv_usd") or 0.0)
    v_bin = float(resumen.get("ventas_binance_usd") or 0.0)
    p_bin = float(resumen.get("pagos_binance_usd") or 0.0)

    pagos_rev = [
        {
            "documento_id": op.documento_id,
            "numero": op.numero,
            "numero_recibo": op.numero_recibo,
            "estado": op.estado,
            "fecha_documento": op.fecha_documento.isoformat() if op.fecha_documento else None,
            "monto": float(op.monto) if op.monto is not None else None,
            "moneda": op.moneda,
            "referencia": op.referencia,
            "banco_destino": str(
                ((op.validacion or {}).get("datos_destino") or {}).get("banco_destino")
                or op.banco
                or ""
            ),
        }
        for op in docs_op
        if op.tipo == TIPO_PAGO
        and op.estado != ENVIADO_ODOO
        and _es_dueno_registro(
            cuenta,
            partner_id=op.partner_odoo_id,
            nombre=op.contraparte_nombre,
        )
    ]

    return {
        "cliente_id": str(cuenta["cliente_id"]),
        "cliente_nombre": str(resumen.get("cliente_nombre") or cuenta.get("cliente_nombre") or ""),
        "resumen": resumen,
        "ruta_bcv": {
            "ventas_usd": v_bcv,
            "pagos_usd": p_bcv,
            "saldo_usd": round(max(0.0, v_bcv - p_bcv), 2),
            "movimientos": movs_bcv,
        },
        "ruta_binance": {
            "ventas_usd": v_bin,
            "pagos_usd": p_bin,
            "saldo_usd": round(max(0.0, v_bin - p_bin), 2),
            "movimientos": movs_bin,
        },
        "pagos_en_revision": pagos_rev,
    }


@router.get("/portal/documentos/{documento_id}/recibo", response_class=HTMLResponse)
def v1_portal_recibo_documento(
    documento_id: int,
    cuenta: ClientePortalV1Dep,
    tipo: str = "operativo",
) -> HTMLResponse:
    _repo, engine = _repo_y_engine()
    if tipo == "comercial":
        svc_docs: ServicioDocumentos = _resolver_dep(get_servicio_documentos)
        try:
            doc = svc_docs.obtener(documento_id)
        except DocumentoNoEncontrado as exc:
            raise HTTPException(status_code=404, detail="Documento no encontrado.") from exc
        if not _es_dueno_registro(
            cuenta, partner_id=doc.partner_odoo_id, nombre=doc.cliente_nombre
        ):
            raise HTTPException(status_code=403, detail="No autorizado para este documento.")
        return HTMLResponse(content=renderizar_preview_cotizacion(doc, solo_lectura_publico=True))

    svc_ops: ServicioOperativos = _resolver_dep(get_servicio_operativos)
    try:
        op = svc_ops.obtener(documento_id)
    except OperativoNoEncontrado as exc:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado.") from exc
    if not _es_dueno_registro(cuenta, partner_id=op.partner_odoo_id, nombre=op.contraparte_nombre):
        raise HTTPException(status_code=403, detail="No autorizado para este recibo.")
    if op.numero_recibo:
        cta_dict: dict[str, Any] | None = None
        if op.cuenta_tercero_id is not None:
            cta = RepoCuentasTercerosSQL(engine).obtener(op.cuenta_tercero_id)
            if cta is not None:
                cta_dict = cta.a_dict()
        return HTMLResponse(content=renderizar_recibo_operativo(op, cta_dict))
    return HTMLResponse(content=renderizar_preview_operativo(op))


@router.get("/portal/catalogo")
def v1_portal_catalogo(_cuenta: ClientePortalV1Dep) -> dict[str, Any]:
    svc_docs: ServicioDocumentos = _resolver_dep(get_servicio_documentos)
    listas = svc_docs.listas_precio_activas()
    items_out: list[dict[str, Any]] = []
    for it in svc_docs.catalogo_items():
        d = it.a_dict()
        stk = it.stock_disponible
        if stk is None or stk <= Decimal("0"):
            semaforo = "BAJO_PEDIDO"
        elif stk <= Decimal("5"):
            semaforo = "BAJO_STOCK"
        else:
            semaforo = "DISPONIBLE"
        items_out.append(
            {
                "producto_id": d["producto_id"],
                "codigo": d["codigo"],
                "nombre": d["nombre"],
                "marca": d["marca"],
                "presentacion": d["presentacion"],
                "tipo_envase": d["tipo_envase"],
                "litros": d["litros"],
                "precio_usd": d["precio_usd"],
                "precio_ves": d["precio_ves"],
                "precios_por_lista": d["precios_por_lista"],
                "disponibilidad": semaforo,
                "etiqueta": d["etiqueta"],
            }
        )
    return {"listas_precio": listas, "total": len(items_out), "items": items_out}


@router.post("/portal/cotizaciones")
def v1_portal_crear_cotizacion(
    cuenta: ClientePortalV1Dep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> JSONResponse:
    if cuenta.get("rol_portal") not in ("titular", "compras"):
        raise HTTPException(
            status_code=403,
            detail="Tu rol en el portal no permite crear cotizaciones/pedidos.",
        )
    svc_docs: ServicioDocumentos = _resolver_dep(get_servicio_documentos)
    pid = cuenta.get("partner_odoo_id")
    if pid is None and str(cuenta["cliente_id"]).isdigit():
        pid = int(str(cuenta["cliente_id"]))
    datos = {
        **body,
        "tipo": COTIZACION,
        "partner_odoo_id": pid,
        "cliente": cuenta.get("cliente_nombre")
        or body.get("cliente")
        or f"Cliente #{cuenta['cliente_id']}",
    }
    try:
        doc = svc_docs.crear(
            datos,
            origen="portal_cliente",
            actor=f"portal:{cuenta['email']}",
            auto_enviar=False,
        )
    except (DocumentoInvalido, CapturaInvalida) as exc:
        return JSONResponse(
            status_code=422,
            content={"error": str(exc), "faltan": list(getattr(exc, "faltan", []))},
        )
    return JSONResponse(status_code=201, content=doc.a_dict(incluir_lineas=True))


@router.get("/portal/cuentas-pago")
def v1_portal_cuentas_pago(
    _cuenta: ClientePortalV1Dep,
    ruta: str = "",
) -> dict[str, Any]:
    _repo, engine = _repo_y_engine()
    ruta_norm = normalizar_ruta(ruta) if ruta else ""
    cuentas = RepoCuentasReceptorasSQL(engine).listar(solo_activas=True, solo_propias=True)
    filtradas = []
    for c in cuentas:
        if ruta_norm == RUTA_BCV and c.moneda.upper() != "VES":
            continue
        if (
            ruta_norm == RUTA_BINANCE
            and c.moneda.upper() != "USD"
            and c.tipo_canal not in ("binance", "zelle")
        ):
            continue
        filtradas.append(c.a_dict())
    return {"ruta": ruta_norm or "todas", "total": len(filtradas), "items": filtradas}


def _ruta_de_pedido_si_existe(repo: Repository, engine: Engine, pedido_ref: str) -> str:
    ref_up = pedido_ref.strip().upper()
    if not ref_up:
        return ""
    docs, _, _ = RepoDocumentosSQL(engine).listar(q=ref_up, limit=10)
    for d in docs:
        if d.numero.upper() == ref_up or d.odoo_order_name.upper() == ref_up:
            return normalizar_ruta(d.ruta_pago)
    mapeo: dict[str, Any] = {}
    try:
        raw_m = repo.get_config("pricelist_mapeo_unificado")
        if raw_m and isinstance(raw_m, str):
            parsed = json.loads(raw_m)
            if isinstance(parsed, dict):
                mapeo = parsed
    except Exception:
        mapeo = {}
    for o in repo.all_ordenes():
        if o.so_id.strip().upper() == ref_up:
            return normalizar_ruta(getattr(o, "ruta_pago", "")) or ruta_de_lista(
                o.lista_precios, mapeo
            )
    return ""


@router.post("/portal/pagos")
def v1_portal_registrar_pago(
    cuenta: ClientePortalV1Dep,
    body: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> JSONResponse:
    if cuenta.get("rol_portal") not in ("titular", "tesoreria"):
        raise HTTPException(
            status_code=403,
            detail="Tu rol en el portal no permite registrar pagos.",
        )
    repo, engine = _repo_y_engine()
    svc_ops: ServicioOperativos = _resolver_dep(get_servicio_operativos)

    pid = cuenta.get("partner_odoo_id")
    if pid is None and str(cuenta["cliente_id"]).isdigit():
        pid = int(str(cuenta["cliente_id"]))
    datos = {
        **body,
        "tipo": TIPO_PAGO,
        "partner_odoo_id": pid,
        "contraparte_nombre": cuenta.get("cliente_nombre") or f"Cliente #{cuenta['cliente_id']}",
    }

    pedido_ref = str(datos.get("pedido_ref") or "").strip()
    if pedido_ref:
        ruta_ped = _ruta_de_pedido_si_existe(repo, engine, pedido_ref)
        mon = str(datos.get("moneda") or "").strip().upper()
        metodo = str(datos.get("metodo_pago") or "").strip().lower()
        es_pago_binance = mon == "USD" or "binance" in metodo or "zelle" in metodo
        es_pago_bcv = mon == "VES" and "binance" not in metodo and "zelle" not in metodo
        if ruta_ped == RUTA_BINANCE and es_pago_bcv:
            return JSONResponse(
                status_code=422,
                content={
                    "error": (
                        f"El pedido {pedido_ref} pertenece a Ruta Binance (USD) "
                        "y no puede pagarse por Ruta BCV (VES)."
                    )
                },
            )
        if ruta_ped == RUTA_BCV and es_pago_binance:
            return JSONResponse(
                status_code=422,
                content={
                    "error": (
                        f"El pedido {pedido_ref} pertenece a Ruta BCV (VES) "
                        "y no puede pagarse por Ruta Binance (USD)."
                    )
                },
            )

    try:
        val = svc_ops.validar_en_vivo(TIPO_PAGO, datos)
    except (OperativoInvalido, CapturaInvalida) as exc:
        return JSONResponse(
            status_code=422,
            content={"error": str(exc), "faltan": list(getattr(exc, "faltan", []))},
        )

    dest = val.get("destino_validacion") or {}
    if dest.get("bloqueante"):
        return JSONResponse(
            status_code=422,
            content={
                "error": str(dest.get("mensaje") or "La cuenta destino no pertenece a Lubrikca."),
                "destino_validacion": dest,
            },
        )
    dups = list(val.get("posibles_duplicados") or [])
    if dups:
        return JSONResponse(
            status_code=409,
            content={
                "error": str(dups[0].get("detalle") or "Posible referencia de pago duplicada."),
                "posibles_duplicados": dups,
            },
        )

    try:
        op = svc_ops.crear(
            datos,
            origen="portal_cliente",
            actor=f"portal:{cuenta['email']}",
        )
    except (OperativoInvalido, CapturaInvalida) as exc:
        return JSONResponse(
            status_code=422,
            content={"error": str(exc), "faltan": list(getattr(exc, "faltan", []))},
        )
    return JSONResponse(status_code=201, content=op.a_dict())
