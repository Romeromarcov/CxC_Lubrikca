"""Router FastAPI v1 (/api/v1/*) para App Móvil Offline de Vendedores y Portal de Clientes."""

from __future__ import annotations

import csv
import html
import io
import json
import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
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
from cxc.api_v1.notificaciones import RepoNotificacionesSQL
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
from cxc.engine.saldos_propios import (
    ESTADOS_ORDEN_CONFIRMADA,
    ESTADOS_VINC_CONFIRMADA,
    estado_cuenta_cliente,
)
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


def get_repo_notificaciones() -> RepoNotificacionesSQL:
    _repo, engine = _repo_y_engine()
    return RepoNotificacionesSQL(engine)


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


class PortalInvitarUsuarioIn(BaseModel):
    email: str
    rol_portal: str = "compras"
    ttl_horas: int = 48


class PushTokenUpdateIn(BaseModel):
    device_id: str
    push_token: str


class WebPushSuscripcionIn(BaseModel):
    endpoint: str
    p256dh: str = ""
    auth: str = ""
    user_agent: str = ""


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
        repo_v1.registrar_evento_seguridad_v1(
            email=email_clean,
            evento="v1_vendedor_login_fail",
            detalle={"ip": ip, "motivo": err_msg or "Credenciales inválidas"},
            actor=f"ip:{ip}",
        )
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
    repo_v1.registrar_evento_seguridad_v1(
        email=email_clean,
        evento="v1_vendedor_login_ok",
        detalle={"device_id": disp["device_id"], "plataforma": body.plataforma, "ip": ip},
        actor=email_clean,
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
        if "reuso" in str(exc).lower():
            repo_v1.registrar_evento_seguridad_v1(
                email="desconocido",
                evento="v1_refresh_reuse_alert",
                detalle={"sujeto_tipo": "vendedor", "ip": _client_ip(request), "detalle": str(exc)},
                actor=f"ip:{_client_ip(request)}",
            )
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
    repo_v1.registrar_evento_seguridad_v1(
        email=sujeto_id or "desconocido",
        evento="v1_vendedor_logout",
        detalle={"device_id": body.device_id, "revocados": revocados},
        actor=sujeto_id or "vendedor",
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
    repo_v1.registrar_evento_seguridad_v1(
        email=body.email,
        evento="v1_portal_invitacion_creada",
        detalle={"cliente_id": body.cliente_id, "rol_portal": body.rol_portal},
        actor=str(user.get("email") or user.get("nombre") or "admin"),
    )
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
    user: Annotated[dict[str, Any], Depends(_admin_portal)],
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
    repo_v1.registrar_evento_seguridad_v1(
        email=str(actualizado.get("email") or f"cuenta:{cuenta_id}"),
        evento="v1_portal_cuenta_actualizada",
        detalle={"cuenta_id": cuenta_id, "activo": body.activo, "rol_portal": body.rol_portal},
        actor=str(user.get("email") or user.get("nombre") or "admin"),
    )
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
    repo_v1.registrar_evento_seguridad_v1(
        email=str(cuenta.get("email") or ""),
        evento="v1_portal_activacion_ok",
        detalle={"cuenta_id": cuenta.get("cuenta_id"), "cliente_id": cuenta.get("cliente_id")},
        actor=str(cuenta.get("email") or "portal"),
    )
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
        repo_v1.registrar_evento_seguridad_v1(
            email=email_clean,
            evento="v1_portal_login_fail",
            detalle={"ip": ip, "motivo": str(exc)},
            actor=f"ip:{ip}",
        )
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
    repo_v1.registrar_evento_seguridad_v1(
        email=email_clean,
        evento="v1_portal_login_ok",
        detalle={"cuenta_id": cuenta["cuenta_id"], "cliente_id": cuenta["cliente_id"], "ip": ip},
        actor=email_clean,
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
        if "reuso" in str(exc).lower():
            repo_v1.registrar_evento_seguridad_v1(
                email="desconocido",
                evento="v1_portal_refresh_reuse_alert",
                detalle={
                    "sujeto_tipo": "cliente_portal",
                    "ip": _client_ip(request),
                    "detalle": str(exc),
                },
                actor=f"ip:{_client_ip(request)}",
            )
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


@router.get("/portal/estado-cuenta/export")
def v1_portal_exportar_estado_cuenta(
    cuenta: ClientePortalV1Dep,
    formato: str = "csv",
) -> Response:
    ec = v1_portal_estado_cuenta(cuenta)
    cli_nom = str(ec.get("cliente_nombre") or f"Cliente #{ec['cliente_id']}")
    movs_bcv = list(ec["ruta_bcv"]["movimientos"])
    movs_bin = list(ec["ruta_binance"]["movimientos"])

    if formato.strip().lower() == "html":
        filas_html = []
        for ruta_lbl, movs in (("Ruta BCV", movs_bcv), ("Ruta Binance", movs_bin)):
            for m in movs:
                filas_html.append(
                    "<tr>"
                    f"<td>{html.escape(ruta_lbl)}</td>"
                    f"<td>{html.escape(str(m.get('fecha') or ''))}</td>"
                    f"<td>{html.escape(str(m.get('etiqueta') or m.get('tipo') or ''))}</td>"
                    f"<td>{html.escape(str(m.get('referencia') or ''))}</td>"
                    f"<td style='text-align:right'>${float(m.get('debito_usd') or 0):.2f}</td>"
                    f"<td style='text-align:right'>${float(m.get('credito_usd') or 0):.2f}</td>"
                    "</tr>"
                )
        html_doc = (
            "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<title>Estado de Cuenta · {html.escape(cli_nom)}</title>"
            "<style>body{font-family:system-ui,sans-serif;padding:24px;color:#0f172a}"
            ".cards{display:flex;gap:16px;margin:16px 0}"
            ".card{border:1px solid #cbd5e1;border-radius:10px;padding:12px 16px;flex:1}"
            "table{width:100%;border-collapse:collapse;font-size:13px}"
            "th,td{border-bottom:1px solid #e2e8f0;padding:8px;text-align:left}</style></head>"
            "<body>"
            f"<h1>Estado de Cuenta · {html.escape(cli_nom)}</h1>"
            "<div class='cards'>"
            "<div class='card'><strong>Saldo Ruta BCV</strong><div>"
            f"${float(ec['ruta_bcv']['saldo_usd']):.2f} USD</div></div>"
            "<div class='card'><strong>Saldo Ruta Binance</strong><div>"
            f"${float(ec['ruta_binance']['saldo_usd']):.2f} USD</div></div>"
            "</div>"
            "<table><thead><tr><th>Ruta</th><th>Fecha</th><th>Concepto</th>"
            "<th>Referencia</th><th>Débito USD</th><th>Crédito USD</th></tr></thead>"
            f"<tbody>{''.join(filas_html) or '<tr><td colspan=6>Sin movimientos</td></tr>'}"
            "</tbody></table></body></html>"
        )
        return HTMLResponse(content=html_doc)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Ruta", "Fecha", "Concepto", "Referencia", "Debito USD", "Credito USD"])
    for ruta_lbl, movs in (("BCV", movs_bcv), ("BINANCE", movs_bin)):
        for m in movs:
            writer.writerow(
                [
                    ruta_lbl,
                    str(m.get("fecha") or ""),
                    str(m.get("etiqueta") or m.get("tipo") or ""),
                    str(m.get("referencia") or ""),
                    f"{float(m.get('debito_usd') or 0):.2f}",
                    f"{float(m.get('credito_usd') or 0):.2f}",
                ]
            )
    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="estado_cuenta_lubrikca.csv"'},
    )


def _pasos_trazabilidad(
    *,
    cot_recibida: bool,
    aprobado: bool,
    despachado: bool,
    facturado: bool,
    pagado: bool,
) -> list[dict[str, Any]]:
    return [
        {
            "codigo": "cotizacion_recibida",
            "titulo": "Cotización recibida",
            "completado": cot_recibida,
        },
        {"codigo": "aprobado_ventas", "titulo": "Aprobado por Ventas", "completado": aprobado},
        {"codigo": "despachado", "titulo": "Despachado", "completado": despachado},
        {"codigo": "facturado", "titulo": "Facturado", "completado": facturado},
        {"codigo": "pagado_conciliado", "titulo": "Pagado / Conciliado", "completado": pagado},
    ]


@router.get("/portal/pedidos")
def v1_portal_pedidos(cuenta: ClientePortalV1Dep) -> dict[str, Any]:
    repo, engine = _repo_y_engine()
    svc_docs: ServicioDocumentos = _resolver_dep(get_servicio_documentos)
    cid = str(cuenta["cliente_id"])

    try:
        entregas = repo.all_entregas()
    except NotImplementedError:
        entregas = []
    entregas_done_so = {
        str(e.so_id).upper()
        for e in entregas
        if e.so_id and e.estado == "done" and not e.es_devolucion
    }

    try:
        facturas = repo.all_facturas()
    except NotImplementedError:
        facturas = []
    facturas_so = {
        str(f.so_id).upper()
        for f in facturas
        if f.so_id and f.estado == "posted" and f.move_type == "out_invoice"
    }

    pagos_por_id = {str(p.pago_id): p for p in repo.all_pagos() if str(p.cliente_id) == cid}
    pagado_so: set[str] = set()
    for v in repo.all_vinculaciones():
        if v.estado in ESTADOS_VINC_CONFIRMADA and str(v.pago_id) in pagos_por_id:
            pagado_so.add(str(v.so_id).upper())

    mapeo: dict[str, Any] = {}
    try:
        raw_m = repo.get_config("pricelist_mapeo_unificado")
        if raw_m and isinstance(raw_m, str):
            parsed = json.loads(raw_m)
            if isinstance(parsed, dict):
                mapeo = parsed
    except Exception:
        mapeo = {}

    pedidos_out: list[dict[str, Any]] = []
    sos_vistos: set[str] = set()
    ultimo_recompra: dict[str, Any] | None = None

    docs_cli = [
        d
        for d in RepoDocumentosSQL(engine).listar(limit=500)[0]
        if _es_dueno_registro(cuenta, partner_id=d.partner_odoo_id, nombre=d.cliente_nombre)
    ]
    for d in docs_cli:
        so_up = (d.odoo_order_name or "").strip().upper()
        if so_up:
            sos_vistos.add(so_up)
        aprobado = d.estado in ("APROBADO_AUTO", ENVIADO_ODOO) or bool(so_up)
        despachado = bool(so_up and so_up in entregas_done_so)
        facturado = bool(so_up and so_up in facturas_so)
        pagado = bool(so_up and so_up in pagado_so)
        pasos = _pasos_trazabilidad(
            cot_recibida=True,
            aprobado=aprobado,
            despachado=despachado,
            facturado=facturado,
            pagado=pagado,
        )
        etapa = next(
            (p["titulo"] for p in reversed(pasos) if p["completado"]), "Cotización recibida"
        )
        lineas_dict = [ln.a_dict() for ln in getattr(d, "lineas", ())]
        if ultimo_recompra is None and lineas_dict:
            ultimo_recompra = {
                "referencia": d.odoo_order_name or d.numero,
                "lista_precios_id": str(d.lista_precios_id or ""),
                "ruta_pago": normalizar_ruta(d.ruta_pago) or RUTA_BCV,
                "lineas": [
                    {
                        "producto_id": str(ln.get("producto_id") or ""),
                        "codigo": str(ln.get("codigo") or ""),
                        "descripcion": str(ln.get("descripcion") or ""),
                        "cantidad": float(ln.get("cantidad") or 0),
                        "precio_unitario": float(ln.get("precio_unitario") or 0),
                    }
                    for ln in lineas_dict
                ],
            }
        pedidos_out.append(
            {
                "origen": "cxc",
                "documento_id": d.documento_id,
                "numero": d.numero,
                "odoo_order_name": d.odoo_order_name,
                "tipo": d.tipo,
                "estado": d.estado,
                "fecha": d.creado_en.date().isoformat() if d.creado_en else None,
                "ruta_pago": normalizar_ruta(d.ruta_pago) or RUTA_BCV,
                "total_usd": float(d.total_con_iva or d.subtotal_neto or d.subtotal or 0),
                "etapa_actual": etapa,
                "pasos": pasos,
            }
        )

    catalogo_por_nombre = {
        normalizar_nombre(it.nombre): it for it in svc_docs.catalogo_items()
    }
    ordenes_cli = sorted(
        (
            o
            for o in repo.all_ordenes()
            if str(o.cliente_id) == cid and o.estado_orden != "cancel"
        ),
        key=lambda x: (x.fecha, str(x.so_id)),
        reverse=True,
    )
    for o in ordenes_cli:
        so_up = str(o.so_id).strip().upper()
        if so_up in sos_vistos:
            continue
        ruta_o = normalizar_ruta(getattr(o, "ruta_pago", "")) or ruta_de_lista(
            o.lista_precios, mapeo
        )
        aprobado = o.estado_orden in ESTADOS_ORDEN_CONFIRMADA
        despachado = bool(o.entregada_completa or so_up in entregas_done_so)
        facturado = bool(o.facturada or so_up in facturas_so)
        pagado = so_up in pagado_so
        pasos = _pasos_trazabilidad(
            cot_recibida=True,
            aprobado=aprobado,
            despachado=despachado,
            facturado=facturado,
            pagado=pagado,
        )
        etapa = next(
            (p["titulo"] for p in reversed(pasos) if p["completado"]), "Cotización recibida"
        )
        if ultimo_recompra is None:
            lns_ord = repo.lineas_de_orden(o.so_id)
            lns_rec = []
            for ln in lns_ord:
                it_cat = catalogo_por_nombre.get(normalizar_nombre(ln.producto))
                if it_cat is not None:
                    lns_rec.append(
                        {
                            "producto_id": it_cat.producto_id,
                            "codigo": it_cat.codigo,
                            "descripcion": it_cat.nombre,
                            "cantidad": float(ln.cantidad),
                            "precio_unitario": float(ln.precio_unitario),
                        }
                    )
            if lns_rec:
                ultimo_recompra = {
                    "referencia": o.so_id,
                    "lista_precios_id": str(o.lista_precios or ""),
                    "ruta_pago": ruta_o or RUTA_BCV,
                    "lineas": lns_rec,
                }
        pedidos_out.append(
            {
                "origen": "odoo",
                "documento_id": None,
                "numero": o.so_id,
                "odoo_order_name": o.so_id,
                "tipo": "pedido",
                "estado": o.estado_orden,
                "fecha": o.fecha.isoformat(),
                "ruta_pago": ruta_o or RUTA_BCV,
                "total_usd": float(o.monto_total),
                "etapa_actual": etapa,
                "pasos": pasos,
            }
        )

    return {
        "cliente_id": cid,
        "total": len(pedidos_out),
        "pedidos": pedidos_out,
        "ultimo_pedido_recompra": ultimo_recompra,
    }


@router.get("/portal/usuarios")
def v1_portal_titular_listar_usuarios(
    cuenta: ClientePortalV1Dep,
    repo_v1: RepoV1Dep,
) -> dict[str, Any]:
    if cuenta.get("rol_portal") != "titular":
        raise HTTPException(
            status_code=403,
            detail="Solo el usuario titular puede administrar el equipo del portal.",
        )
    items = repo_v1.listar_cuentas_portal(cliente_id=str(cuenta["cliente_id"]))
    return {"cliente_id": str(cuenta["cliente_id"]), "total": len(items), "items": items}


@router.post("/portal/usuarios")
def v1_portal_titular_invitar_usuario(
    body: PortalInvitarUsuarioIn,
    cuenta: ClientePortalV1Dep,
    repo_v1: RepoV1Dep,
) -> JSONResponse:
    if cuenta.get("rol_portal") != "titular":
        raise HTTPException(
            status_code=403,
            detail="Solo el usuario titular puede invitar miembros del portal.",
        )
    try:
        token_raw, inv = repo_v1.crear_invitacion_portal(
            cliente_id=str(cuenta["cliente_id"]),
            partner_odoo_id=cuenta.get("partner_odoo_id"),
            cliente_nombre=str(cuenta.get("cliente_nombre") or ""),
            email=body.email,
            rol_portal=body.rol_portal,
            creado_por=f"portal:{cuenta['email']}",
            ttl_horas=body.ttl_horas,
        )
    except ApiV1Error as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    repo_v1.registrar_evento_seguridad_v1(
        email=body.email,
        evento="v1_portal_invitacion_creada_por_titular",
        detalle={"cliente_id": cuenta["cliente_id"], "rol_portal": body.rol_portal},
        actor=f"portal:{cuenta['email']}",
    )
    return JSONResponse(
        status_code=201,
        content={
            **inv,
            "token": token_raw,
            "enlace_activacion": f"/portal/activar?token={token_raw}",
        },
    )


@router.put("/portal/usuarios/{cuenta_id}")
def v1_portal_titular_actualizar_usuario(
    cuenta_id: int,
    body: ActualizarCuentaPortalIn,
    cuenta: ClientePortalV1Dep,
    repo_v1: RepoV1Dep,
) -> JSONResponse:
    if cuenta.get("rol_portal") != "titular":
        raise HTTPException(
            status_code=403,
            detail="Solo el usuario titular puede modificar cuentas del portal.",
        )
    objetivo = repo_v1.obtener_cuenta_portal(cuenta_id)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="Cuenta de portal no encontrada.")
    if str(objetivo["cliente_id"]) != str(cuenta["cliente_id"]):
        raise HTTPException(
            status_code=403, detail="No autorizado para modificar cuentas de otra empresa."
        )
    if int(cuenta["cuenta_id"]) == int(cuenta_id) and body.activo is False:
        return JSONResponse(
            status_code=422,
            content={"error": "No puedes desactivar tu propia cuenta titular."},
        )
    try:
        actualizado = repo_v1.actualizar_cuenta_portal(
            cuenta_id,
            activo=body.activo,
            rol_portal=body.rol_portal,
        )
    except ApiV1Error as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    repo_v1.registrar_evento_seguridad_v1(
        email=str(actualizado.get("email") or ""),
        evento="v1_portal_usuario_modificado_por_titular",
        detalle={"cuenta_id": cuenta_id, "activo": body.activo, "rol_portal": body.rol_portal},
        actor=f"portal:{cuenta['email']}",
    )
    return JSONResponse(status_code=200, content=actualizado)


# ── Fase 4: Push Multicanal, Adjuntos R2/S3 Seguros y Observabilidad Piloto ───


@router.post("/dispositivos/push-token")
def v1_registrar_push_token(
    body: PushTokenUpdateIn,
    user: VendedorV1Dep,
    repo_v1: RepoV1Dep,
) -> dict[str, Any]:
    if not body.device_id.strip():
        raise HTTPException(status_code=422, detail="device_id es obligatorio.")
    disp = repo_v1.actualizar_push_token(
        device_id=body.device_id,
        sujeto_tipo="vendedor",
        sujeto_id=str(user["email"]),
        push_token=body.push_token,
    )
    return {"status": "ok", "dispositivo": disp}


@router.get("/notificaciones")
def v1_listar_notificaciones(
    actor: CualquierActorV1Dep,
    limit: int = 50,
) -> dict[str, Any]:
    repo_n = _resolver_dep(get_repo_notificaciones)
    items = repo_n.listar_para_sujeto(
        sujeto_tipo=str(actor["actor_tipo"]),
        sujeto_id=str(actor["actor_id"]),
        limit=limit,
    )
    return {
        "actor_tipo": actor["actor_tipo"],
        "total": len(items),
        "no_leidas": sum(1 for x in items if not x.get("leida_en")),
        "items": items,
    }


@router.post("/notificaciones/{notificacion_id}/leer")
def v1_marcar_notificacion_leida(
    notificacion_id: int,
    actor: CualquierActorV1Dep,
) -> dict[str, Any]:
    repo_n = _resolver_dep(get_repo_notificaciones)
    act = repo_n.marcar_leida(
        notificacion_id,
        sujeto_tipo=str(actor["actor_tipo"]),
        sujeto_id=str(actor["actor_id"]),
    )
    if act is None:
        raise HTTPException(status_code=404, detail="Notificación no encontrada.")
    return {"status": "ok", "notificacion": act}


@router.post("/portal/push/suscribir")
def v1_portal_suscribir_web_push(
    body: WebPushSuscripcionIn,
    cuenta: ClientePortalV1Dep,
) -> JSONResponse:
    if not body.endpoint.strip().startswith("https://"):
        return JSONResponse(
            status_code=422, content={"error": "El endpoint Web Push debe iniciar con https://."}
        )
    repo_n = _resolver_dep(get_repo_notificaciones)
    sub = repo_n.registrar_web_push(
        sujeto_tipo="cliente_portal",
        sujeto_id=str(cuenta["cuenta_id"]),
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        user_agent=body.user_agent,
    )
    return JSONResponse(status_code=201, content={"status": "suscrita", "suscripcion": sub})


@router.get("/adjuntos/{adjunto_id}/url")
def v1_obtener_url_adjunto(
    adjunto_id: int,
    actor: CualquierActorV1Dep,
    storage: StorageV1Dep,
) -> dict[str, Any]:
    try:
        info = storage.preparar_descarga_segura(adjunto_id, expires=300)
    except AdjuntoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if info is None:
        raise HTTPException(status_code=404, detail="Adjunto no encontrado.")
    if actor["actor_tipo"] == "cliente_portal" and (
        info.get("subido_por_tipo") != "cliente_portal"
        or str(info.get("subido_por_id")) != str(actor["actor_id"])
    ):
        raise HTTPException(
            status_code=403, detail="No autorizado para acceder a este comprobante."
        )
    return {
        "adjunto_id": info["adjunto_id"],
        "storage_backend": info["storage_backend"],
        "mime": info["mime"],
        "bytes": info["bytes"],
        "expires_in": info["expires_in"],
        "download_url": info["download_url"],
        "descarga_local": info["descarga_local"],
    }


@router.get("/observabilidad/metricas-piloto")
def v1_observabilidad_metricas_piloto(
    repo_v1: RepoV1Dep,
    storage: StorageV1Dep,
    _user: Annotated[dict[str, Any], Depends(_admin_portal)],
) -> dict[str, Any]:
    repo_n = _resolver_dep(get_repo_notificaciones)
    metricas = repo_v1.metricas_piloto()
    metricas["storage"] = storage.estado_storage()
    metricas["push_outbox"] = repo_n.resumen_salud()
    return metricas
