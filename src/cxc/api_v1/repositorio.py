"""Repositorio SQL transaccional para Fase 1 API v1."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from cxc.auth import hash_password, password_token_version, verificar_password
from cxc.db.schema import (
    adjuntos_objeto,
    auth_refresh_tokens,
    clientes_portal_cuentas,
    clientes_portal_invitaciones,
    dispositivos_moviles,
    sync_idempotency_keys,
)

from .jwt_tokens import (
    DEFAULT_REFRESH_TTL_SECONDS,
    generar_refresh_token,
    hash_payload,
    hash_token,
)

ROLES_PORTAL_VALIDOS = ("titular", "compras", "tesoreria")


class ApiV1Error(Exception):
    """Error base de negocio/autorización para API v1."""


class ApiV1NoAutorizado(ApiV1Error):
    """Credenciales inválidas, token revocado o reuso detectado."""


class ApiV1NoEncontrado(ApiV1Error):
    """Entidad solicitada inexistente."""


class ApiV1Conflicto(ApiV1Error):
    """Conflicto de unicidad, expiración o idempotencia con distinto payload."""


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


def _cuenta_portal_publica(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "cuenta_id": int(row["cuenta_id"]),
        "cliente_id": str(row["cliente_id"]),
        "partner_odoo_id": (
            int(row["partner_odoo_id"]) if row.get("partner_odoo_id") is not None else None
        ),
        "cliente_nombre": str(row.get("cliente_nombre") or ""),
        "email": str(row["email"]),
        "telefono": str(row.get("telefono") or ""),
        "rol_portal": str(row.get("rol_portal") or "titular"),
        "activo": bool(row.get("activo", True)),
        "verificado_en": (row["verificado_en"].isoformat() if row.get("verificado_en") else None),
        "ultimo_acceso_en": (
            row["ultimo_acceso_en"].isoformat() if row.get("ultimo_acceso_en") else None
        ),
        "creado_por": str(row.get("creado_por") or ""),
        "creado_en": (row["creado_en"].isoformat() if row.get("creado_en") else None),
    }


class RepoApiV1SQL:
    """Operaciones de persistencia SQL para API v1."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── Dispositivos ──────────────────────────────────────────────────────────

    def registrar_dispositivo(
        self,
        *,
        device_id: str,
        sujeto_tipo: str,
        sujeto_id: str,
        plataforma: str = "android",
        push_token: str = "",
        app_version: str = "",
    ) -> dict[str, Any]:
        did = device_id.strip()[:120] or f"dev-{secrets.token_hex(8)}"
        ahora = datetime.now()
        with self._engine.begin() as conn:
            existe = conn.execute(
                select(dispositivos_moviles).where(dispositivos_moviles.c.device_id == did)
            ).one_or_none()
            if existe is None:
                vals: dict[str, Any] = {
                    "device_id": did,
                    "sujeto_tipo": sujeto_tipo,
                    "sujeto_id": sujeto_id,
                    "plataforma": (plataforma or "android")[:32],
                    "push_token": push_token[:255],
                    "app_version": app_version[:40],
                    "ultimo_sync_en": None,
                    "ultimo_cursor": "",
                    "activo": True,
                    "actualizado_en": ahora,
                }
                row = conn.execute(
                    insert(dispositivos_moviles).values(**vals).returning(dispositivos_moviles)
                ).one()
            else:
                cambios: dict[str, Any] = {
                    "sujeto_tipo": sujeto_tipo,
                    "sujeto_id": sujeto_id,
                    "plataforma": (plataforma or existe.plataforma or "android")[:32],
                    "app_version": (app_version or existe.app_version or "")[:40],
                    "activo": True,
                    "actualizado_en": ahora,
                }
                if push_token:
                    cambios["push_token"] = push_token[:255]
                row = conn.execute(
                    update(dispositivos_moviles)
                    .where(dispositivos_moviles.c.device_id == did)
                    .values(**cambios)
                    .returning(dispositivos_moviles)
                ).one()
        return _row_dict(row)

    def actualizar_sync_dispositivo(
        self, *, device_id: str, cursor: str, cuando: datetime | None = None
    ) -> None:
        if not device_id.strip():
            return
        ahora = cuando or datetime.now()
        with self._engine.begin() as conn:
            conn.execute(
                update(dispositivos_moviles)
                .where(dispositivos_moviles.c.device_id == device_id.strip())
                .values(ultimo_sync_en=ahora, ultimo_cursor=cursor[:64], actualizado_en=ahora)
            )

    # ── Refresh Tokens con rotación y detección de reuso ──────────────────────

    def emitir_refresh_token(
        self,
        *,
        sujeto_tipo: str,
        sujeto_id: str,
        device_id: str = "",
        password_version: str = "",
        ip_origen: str = "",
        familia_id: str | None = None,
        ttl_seconds: int = DEFAULT_REFRESH_TTL_SECONDS,
    ) -> tuple[str, dict[str, Any]]:
        raw = generar_refresh_token()
        ahora = datetime.now()
        fam = familia_id or secrets.token_hex(16)
        vals = {
            "familia_id": fam,
            "token_hash": hash_token(raw),
            "sujeto_tipo": sujeto_tipo,
            "sujeto_id": sujeto_id,
            "device_id": device_id[:120],
            "password_version": password_version[:32],
            "ip_origen": ip_origen[:64],
            "creado_en": ahora,
            "expira_en": ahora + timedelta(seconds=max(300, int(ttl_seconds))),
            "rotado_en": None,
            "revocado_en": None,
        }
        with self._engine.begin() as conn:
            row = conn.execute(
                insert(auth_refresh_tokens).values(**vals).returning(auth_refresh_tokens)
            ).one()
        return raw, _row_dict(row)

    def rotar_refresh_token(
        self,
        *,
        raw_refresh_token: str,
        expected_sujeto_tipo: str,
        ip_origen: str = "",
        pwd_version_actual: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        th = hash_token(raw_refresh_token)
        ahora = datetime.now()
        error_auth: str | None = None
        nuevo_raw = ""
        nueva_fila: Any = None
        with self._engine.begin() as conn:
            fila = conn.execute(
                select(auth_refresh_tokens).where(auth_refresh_tokens.c.token_hash == th)
            ).one_or_none()
            if fila is None:
                error_auth = "Refresh token inválido."
            elif fila.sujeto_tipo != expected_sujeto_tipo:
                error_auth = "Audiencia de refresh token inválida."
            elif fila.revocado_en is not None:
                error_auth = "La sesión fue revocada. Inicia sesión nuevamente."
            elif fila.rotado_en is not None:
                # Reuso detectado: persistir revocación de TODA la familia al hacer commit.
                conn.execute(
                    update(auth_refresh_tokens)
                    .where(
                        and_(
                            auth_refresh_tokens.c.familia_id == fila.familia_id,
                            auth_refresh_tokens.c.revocado_en.is_(None),
                        )
                    )
                    .values(revocado_en=ahora)
                )
                error_auth = (
                    "Reuso de refresh token detectado; familia de sesión revocada por seguridad."
                )
            elif fila.expira_en < ahora:
                conn.execute(
                    update(auth_refresh_tokens)
                    .where(auth_refresh_tokens.c.token_id == fila.token_id)
                    .values(revocado_en=ahora)
                )
                error_auth = "Refresh token expirado."
            elif (
                pwd_version_actual is not None
                and fila.password_version
                and fila.password_version != pwd_version_actual[:32]
            ):
                conn.execute(
                    update(auth_refresh_tokens)
                    .where(auth_refresh_tokens.c.familia_id == fila.familia_id)
                    .values(revocado_en=ahora)
                )
                error_auth = "Sesión invalidada tras cambio de contraseña."
            else:
                conn.execute(
                    update(auth_refresh_tokens)
                    .where(auth_refresh_tokens.c.token_id == fila.token_id)
                    .values(rotado_en=ahora)
                )
                nuevo_raw = generar_refresh_token()
                nuevo_vals = {
                    "familia_id": fila.familia_id,
                    "token_hash": hash_token(nuevo_raw),
                    "sujeto_tipo": fila.sujeto_tipo,
                    "sujeto_id": fila.sujeto_id,
                    "device_id": fila.device_id,
                    "password_version": (
                        pwd_version_actual[:32]
                        if pwd_version_actual is not None
                        else fila.password_version
                    ),
                    "ip_origen": ip_origen[:64] or fila.ip_origen,
                    "creado_en": ahora,
                    "expira_en": ahora + timedelta(seconds=DEFAULT_REFRESH_TTL_SECONDS),
                    "rotado_en": None,
                    "revocado_en": None,
                }
                nueva_fila = conn.execute(
                    insert(auth_refresh_tokens)
                    .values(**nuevo_vals)
                    .returning(auth_refresh_tokens)
                ).one()
        if error_auth is not None:
            raise ApiV1NoAutorizado(error_auth)
        return nuevo_raw, _row_dict(nueva_fila)

    def revocar_refresh_tokens_sujeto(
        self,
        *,
        sujeto_tipo: str,
        sujeto_id: str,
        device_id: str | None = None,
        raw_refresh_token: str | None = None,
    ) -> int:
        ahora = datetime.now()
        conds: list[Any] = [
            auth_refresh_tokens.c.sujeto_tipo == sujeto_tipo,
            auth_refresh_tokens.c.sujeto_id == sujeto_id,
            auth_refresh_tokens.c.revocado_en.is_(None),
        ]
        if raw_refresh_token:
            th = hash_token(raw_refresh_token)
            with self._engine.begin() as conn:
                ref = conn.execute(
                    select(auth_refresh_tokens.c.familia_id).where(
                        auth_refresh_tokens.c.token_hash == th
                    )
                ).one_or_none()
                if ref is not None:
                    res = conn.execute(
                        update(auth_refresh_tokens)
                        .where(
                            and_(
                                auth_refresh_tokens.c.familia_id == ref.familia_id,
                                auth_refresh_tokens.c.revocado_en.is_(None),
                            )
                        )
                        .values(revocado_en=ahora)
                    )
                    return int(res.rowcount or 0)
        if device_id:
            conds.append(auth_refresh_tokens.c.device_id == device_id.strip())
        with self._engine.begin() as conn:
            res = conn.execute(
                update(auth_refresh_tokens).where(and_(*conds)).values(revocado_en=ahora)
            )
            return int(res.rowcount or 0)

    # ── Portal de Clientes (invitaciones y cuentas) ───────────────────────────

    def crear_invitacion_portal(
        self,
        *,
        cliente_id: str,
        partner_odoo_id: int | None,
        cliente_nombre: str,
        email: str,
        rol_portal: str = "titular",
        creado_por: str = "sistema",
        ttl_horas: int = 48,
    ) -> tuple[str, dict[str, Any]]:
        email_norm = email.strip().lower()
        if not email_norm or "@" not in email_norm:
            raise ApiV1Error("Email de invitación inválido.")
        rol = rol_portal.strip().lower() if rol_portal else "titular"
        if rol not in ROLES_PORTAL_VALIDOS:
            raise ApiV1Error(f"Rol de portal inválido: {rol!r}.")
        token_raw = "inv1_" + secrets.token_urlsafe(32)
        ahora = datetime.now()
        vals = {
            "token_hash": hash_token(token_raw),
            "cliente_id": str(cliente_id).strip(),
            "partner_odoo_id": partner_odoo_id,
            "cliente_nombre": cliente_nombre.strip()[:200],
            "email": email_norm[:160],
            "rol_portal": rol,
            "creado_por": creado_por.strip()[:160] or "sistema",
            "creado_en": ahora,
            "expira_en": ahora + timedelta(hours=max(1, int(ttl_horas))),
            "consumido_en": None,
        }
        with self._engine.begin() as conn:
            row = conn.execute(
                insert(clientes_portal_invitaciones)
                .values(**vals)
                .returning(clientes_portal_invitaciones)
            ).one()
        d = _row_dict(row)
        return token_raw, {
            "invitacion_id": int(d["invitacion_id"]),
            "cliente_id": d["cliente_id"],
            "partner_odoo_id": d["partner_odoo_id"],
            "cliente_nombre": d["cliente_nombre"],
            "email": d["email"],
            "rol_portal": d["rol_portal"],
            "expira_en": d["expira_en"].isoformat(),
        }

    def activar_invitacion_portal(
        self,
        *,
        token: str,
        password: str,
        telefono: str = "",
    ) -> dict[str, Any]:
        if len(password or "") < 8:
            raise ApiV1Error("La contraseña debe tener al menos 8 caracteres.")
        th = hash_token(token)
        ahora = datetime.now()
        pwd_hash, salt = hash_password(password)
        with self._engine.begin() as conn:
            inv = conn.execute(
                select(clientes_portal_invitaciones).where(
                    clientes_portal_invitaciones.c.token_hash == th
                )
            ).one_or_none()
            if inv is None:
                raise ApiV1NoEncontrado("Invitación no encontrada o inválida.")
            if inv.consumido_en is not None:
                raise ApiV1Conflicto("Esta invitación ya fue utilizada.")
            if inv.expira_en < ahora:
                raise ApiV1Conflicto("Esta invitación ha expirado. Solicita una nueva.")

            existente = conn.execute(
                select(clientes_portal_cuentas).where(clientes_portal_cuentas.c.email == inv.email)
            ).one_or_none()
            if existente is not None:
                cuenta_row = conn.execute(
                    update(clientes_portal_cuentas)
                    .where(clientes_portal_cuentas.c.cuenta_id == existente.cuenta_id)
                    .values(
                        cliente_id=inv.cliente_id,
                        partner_odoo_id=inv.partner_odoo_id,
                        cliente_nombre=inv.cliente_nombre,
                        telefono=telefono.strip()[:40] or existente.telefono,
                        password_hash=pwd_hash,
                        salt=salt,
                        rol_portal=inv.rol_portal,
                        activo=True,
                        verificado_en=ahora,
                        actualizado_en=ahora,
                    )
                    .returning(clientes_portal_cuentas)
                ).one()
            else:
                cuenta_row = conn.execute(
                    insert(clientes_portal_cuentas)
                    .values(
                        cliente_id=inv.cliente_id,
                        partner_odoo_id=inv.partner_odoo_id,
                        cliente_nombre=inv.cliente_nombre,
                        email=inv.email,
                        telefono=telefono.strip()[:40],
                        password_hash=pwd_hash,
                        salt=salt,
                        rol_portal=inv.rol_portal,
                        activo=True,
                        verificado_en=ahora,
                        ultimo_acceso_en=None,
                        creado_por=inv.creado_por,
                        creado_en=ahora,
                        actualizado_en=ahora,
                    )
                    .returning(clientes_portal_cuentas)
                ).one()

            conn.execute(
                update(clientes_portal_invitaciones)
                .where(clientes_portal_invitaciones.c.invitacion_id == inv.invitacion_id)
                .values(consumido_en=ahora)
            )
        return _cuenta_portal_publica(_row_dict(cuenta_row))

    def autenticar_cuenta_portal(self, *, email: str, password: str) -> dict[str, Any]:
        email_norm = email.strip().lower()
        with self._engine.begin() as conn:
            row = conn.execute(
                select(clientes_portal_cuentas).where(clientes_portal_cuentas.c.email == email_norm)
            ).one_or_none()
            if row is None or not bool(row.activo):
                raise ApiV1NoAutorizado("Credenciales inválidas o cuenta de cliente desactivada.")
            if not verificar_password(password, row.password_hash, row.salt):
                raise ApiV1NoAutorizado("Credenciales inválidas.")
            ahora = datetime.now()
            upd = conn.execute(
                update(clientes_portal_cuentas)
                .where(clientes_portal_cuentas.c.cuenta_id == row.cuenta_id)
                .values(ultimo_acceso_en=ahora, actualizado_en=ahora)
                .returning(clientes_portal_cuentas)
            ).one()
        d = _row_dict(upd)
        d["_pwd_ver"] = password_token_version(d.get("password_hash"))
        return d

    def obtener_cuenta_portal(self, cuenta_id: int) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(clientes_portal_cuentas).where(
                    clientes_portal_cuentas.c.cuenta_id == cuenta_id
                )
            ).one_or_none()
        if row is None:
            return None
        d = _row_dict(row)
        d["_pwd_ver"] = password_token_version(d.get("password_hash"))
        return d

    def listar_cuentas_portal(self, *, cliente_id: str | None = None) -> list[dict[str, Any]]:
        stmt = select(clientes_portal_cuentas).order_by(clientes_portal_cuentas.c.cuenta_id.desc())
        if cliente_id:
            stmt = stmt.where(clientes_portal_cuentas.c.cliente_id == str(cliente_id).strip())
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_cuenta_portal_publica(_row_dict(r)) for r in rows]

    def actualizar_cuenta_portal(
        self,
        cuenta_id: int,
        *,
        activo: bool | None = None,
        rol_portal: str | None = None,
    ) -> dict[str, Any]:
        cambios: dict[str, Any] = {}
        if activo is not None:
            cambios["activo"] = bool(activo)
        if rol_portal is not None:
            rol = rol_portal.strip().lower()
            if rol not in ROLES_PORTAL_VALIDOS:
                raise ApiV1Error(f"Rol de portal inválido: {rol!r}.")
            cambios["rol_portal"] = rol
        if not cambios:
            actual = self.obtener_cuenta_portal(cuenta_id)
            if actual is None:
                raise ApiV1NoEncontrado("Cuenta de portal no encontrada.")
            return _cuenta_portal_publica(actual)
        cambios["actualizado_en"] = datetime.now()
        with self._engine.begin() as conn:
            row = conn.execute(
                update(clientes_portal_cuentas)
                .where(clientes_portal_cuentas.c.cuenta_id == cuenta_id)
                .values(**cambios)
                .returning(clientes_portal_cuentas)
            ).one_or_none()
        if row is None:
            raise ApiV1NoEncontrado("Cuenta de portal no encontrada.")
        if activo is False:
            self.revocar_refresh_tokens_sujeto(
                sujeto_tipo="cliente_portal",
                sujeto_id=str(cuenta_id),
            )
        return _cuenta_portal_publica(_row_dict(row))

    # ── Idempotencia Push Sync ────────────────────────────────────────────────

    def obtener_o_reservar_idempotencia(
        self,
        *,
        idempotency_key: str,
        actor_tipo: str,
        actor_id: str,
        endpoint: str,
        payload: Any,
    ) -> tuple[bool, int, dict[str, Any] | None]:
        """Devuelve ``(ya_existia, status_code, response_json)``; rechaza cambio de payload."""
        ph = hash_payload(payload)
        with self._engine.connect() as conn:
            row = conn.execute(
                select(sync_idempotency_keys).where(
                    sync_idempotency_keys.c.idempotency_key == idempotency_key
                )
            ).one_or_none()
        if row is not None:
            if row.payload_hash != ph:
                raise ApiV1Conflicto(
                    f"La clave idempotente {idempotency_key!r} ya fue usada con otro payload."
                )
            try:
                parsed = json.loads(row.response_json or "{}")
            except ValueError:
                parsed = {}
            return True, int(row.status_code), parsed if isinstance(parsed, dict) else {}
        return False, 200, None

    def guardar_idempotencia(
        self,
        *,
        idempotency_key: str,
        actor_tipo: str,
        actor_id: str,
        endpoint: str,
        payload: Any,
        status_code: int,
        response_data: dict[str, Any],
    ) -> None:
        ph = hash_payload(payload)
        ahora = datetime.now()
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert(sync_idempotency_keys).values(
                        idempotency_key=idempotency_key[:128],
                        actor_tipo=actor_tipo[:32],
                        actor_id=actor_id[:160],
                        endpoint=endpoint[:120],
                        payload_hash=ph,
                        status_code=int(status_code),
                        response_json=json.dumps(response_data, ensure_ascii=False, default=str),
                        creado_en=ahora,
                    )
                )
        except IntegrityError:
            pass

    # ── Adjuntos ──────────────────────────────────────────────────────────────

    def buscar_adjunto_por_sha256(self, sha256_hex: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(adjuntos_objeto)
                .where(adjuntos_objeto.c.sha256 == sha256_hex.strip().lower())
                .order_by(adjuntos_objeto.c.adjunto_id.asc())
            ).first()
        return _row_dict(row) if row is not None else None

    def registrar_adjunto(
        self,
        *,
        sha256_hex: str,
        storage_key: str,
        storage_backend: str,
        mime: str,
        bytes_len: int,
        nombre_archivo: str,
        subido_por_tipo: str,
        subido_por_id: str,
        entidad_tipo: str = "",
        entidad_id: str = "",
    ) -> dict[str, Any]:
        ahora = datetime.now()
        with self._engine.begin() as conn:
            row = conn.execute(
                insert(adjuntos_objeto)
                .values(
                    sha256=sha256_hex.strip().lower()[:64],
                    storage_key=storage_key[:255],
                    storage_backend=storage_backend[:32],
                    mime=mime[:80],
                    bytes=int(bytes_len),
                    nombre_archivo=nombre_archivo[:200],
                    subido_por_tipo=subido_por_tipo[:32],
                    subido_por_id=subido_por_id[:160],
                    entidad_tipo=entidad_tipo[:40],
                    entidad_id=entidad_id[:64],
                    creado_en=ahora,
                )
                .returning(adjuntos_objeto)
            ).one()
        return _row_dict(row)
