"""Módulo de Autenticación Integrada con Odoo y Control de Accesos por Rol."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime
from typing import Any

from cxc.permisos import BASE_POR_ROL, NOMBRES_ROLES, PAGINAS, efectivos
from cxc.repositories import Repository

logger = logging.getLogger("cxc.auth")

# Matriz de Permisos por Rol. La fuente es ``cxc.permisos`` (base de cada rol, que el
# administrador ajusta persona por persona); esto queda por compatibilidad: lo que
# cada rol tiene SIN ajustes, en el orden del menú.
ALL_PAGES = [clave for clave, _ in PAGINAS]

ROLES_PERMISOS: dict[str, list[str]] = {rol: efectivos(rol) for rol in BASE_POR_ROL}


_SCRYPT_PREFIX = "scrypt$"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DEFAULT_SESSION_TTL_SECONDS = 86400 * 7


def _hash_legacy_sha256(password: str, salt: str) -> str:
    salted = (password + salt).encode("utf-8")
    return hashlib.sha256(salted).hexdigest()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Genera un hash scrypt con salt para una contraseña."""
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return f"{_SCRYPT_PREFIX}{dk.hex()}", salt


def verificar_password(password: str, pwd_hash: str, salt: str) -> bool:
    """Verifica si una contraseña coincide con su hash (scrypt o SHA-256 legado)."""
    if not pwd_hash or not salt:
        return False
    if pwd_hash.startswith(_SCRYPT_PREFIX):
        expected, _ = hash_password(password, salt)
        return hmac.compare_digest(expected, pwd_hash)
    legacy = _hash_legacy_sha256(password, salt)
    return hmac.compare_digest(legacy, pwd_hash)


def password_token_version(pwd_hash: str | None) -> str:
    """Huella corta estable del hash vigente para revocar sesiones si cambia la clave."""
    if not pwd_hash:
        return ""
    return hashlib.sha256(str(pwd_hash).encode("utf-8")).hexdigest()[:12]


# --- Llaves de API de solo lectura (septiembre 2026) -------------------------
#
# Pedido del usuario: dar acceso a un sistema externo sin crearle un usuario
# de Odoo. Mismo principio que las contraseñas -- la llave en crudo se
# muestra UNA sola vez al crearla y nunca se vuelve a guardar, solo su hash.
# El middleware que la valida (``web/app.py::exigir_sesion_en_api``) la
# acepta SOLO en rutas GET, sin importar el endpoint: una llave de API nunca
# puede escribir, por diseño.

_API_KEY_PREFIX = "cxc_live_"


def generar_api_key() -> str:
    """Una llave nueva en crudo -- se muestra una sola vez a quien la crea."""
    return _API_KEY_PREFIX + secrets.token_hex(24)


def hash_api_key(api_key: str) -> str:
    """Hash sin sal -- la propia llave YA es aleatoria de sobra (24 bytes);

    una llave de API se compara en cada request, no vale la pena guardar
    ni recalcular una sal para eso.
    """
    return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()


def verificar_api_key(api_key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(api_key), key_hash)


def _verificar_usuario_odoo_con_estado(
    execute_fn: Any, email: str
) -> tuple[dict[str, Any] | None, bool]:
    """Consulta a Odoo indicando ``(usuario_activo_o_None, odoo_disponible)``."""
    if not email or not callable(execute_fn):
        return None, execute_fn is not None and bool(email)
    email_clean = email.strip().lower()
    try:
        users = execute_fn(
            "res.users",
            "search_read",
            [
                [
                    ["active", "=", True],
                    "|",
                    ["login", "=", email_clean],
                    ["email", "=", email_clean],
                ]
            ],
            {"fields": ["id", "name", "login", "email"]},
        )
        if users and len(users) > 0:
            u = users[0]
            return (
                {
                    "user_id": u.get("id"),
                    "name": u.get("name") or email_clean,
                    "login": u.get("login") or email_clean,
                    "email": u.get("email") or u.get("login") or email_clean,
                },
                True,
            )
        return None, True
    except Exception as e:
        logger.warning("Error verificando usuario %s en Odoo: %s", email_clean, e)
        return None, False


def verificar_usuario_odoo_activo(execute_fn: Any, email: str) -> dict[str, Any] | None:
    """Consulta a Odoo para verificar que el correo corresponda a un usuario activo."""
    user, _ok = _verificar_usuario_odoo_con_estado(execute_fn, email)
    return user


def obtener_usuarios_plataforma(repo: Repository) -> list[dict[str, str]]:
    """Lee todos los usuarios registrados en la plataforma."""
    try:
        return repo.all_usuarios_plataforma()
    except Exception as e:
        logger.warning("Error leyendo usuarios de la plataforma: %s", e)
        return []


def buscar_usuario_plataforma(repo: Repository, email: str) -> dict[str, str] | None:
    """Busca un usuario por correo."""
    return repo.get_usuario_plataforma(email.strip().lower())


def registrar_o_actualizar_usuario(
    repo: Repository,
    email: str,
    password: str | None = None,
    nombre_odoo: str = "",
    rol: str | None = None,
    activo: bool = True,
) -> dict[str, str]:
    """Registra o actualiza un usuario de la plataforma."""
    email_clean = email.strip().lower()
    existente = buscar_usuario_plataforma(repo, email_clean) or {}

    current_pwd_hash = existente.get("password_hash", "")
    current_salt = existente.get("salt", "")

    if password:
        current_pwd_hash, current_salt = hash_password(password)

    # El primer usuario registrado o admins reconocidos obtienen rol 'admin' por defecto
    current_rol = rol or existente.get("rol")
    if not current_rol:
        all_users = obtener_usuarios_plataforma(repo)
        current_rol = "admin" if len(all_users) == 0 else "ventas"

    new_row = {
        "email": email_clean,
        "nombre_odoo": nombre_odoo or existente.get("nombre_odoo") or email_clean,
        "password_hash": current_pwd_hash,
        "salt": current_salt,
        "rol": current_rol,
        "activo": "TRUE" if activo else "FALSE",
        "fecha_registro": existente.get("fecha_registro") or datetime.now().isoformat()[:19],
    }

    repo.upsert_usuario_plataforma(new_row)
    return new_row


def autenticar_usuario(
    execute_fn: Any, repo: Repository, email: str, password: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Valida la contraseña y verifica estado en Odoo (con respaldo si Odoo no responde)."""
    email_clean = email.strip().lower()

    u_row = buscar_usuario_plataforma(repo, email_clean)
    if not u_row or not u_row.get("password_hash"):
        return None, "Usuario no registrado. Regístrate por primera vez para crear tu contraseña."

    if u_row.get("activo") == "FALSE":
        return None, "Usuario desactivado en la plataforma."

    if not verificar_password(password, u_row["password_hash"], u_row.get("salt", "")):
        return None, "Contraseña incorrecta."

    # Migración transparente de hashes SHA-256 legados a scrypt tras login válido
    if not str(u_row["password_hash"]).startswith(_SCRYPT_PREFIX):
        try:
            nuevo_hash, nuevo_salt = hash_password(password)
            repo.upsert_usuario_plataforma(
                {**u_row, "password_hash": nuevo_hash, "salt": nuevo_salt}
            )
            u_row = {**u_row, "password_hash": nuevo_hash, "salt": nuevo_salt}
        except Exception as e_mig:
            logger.debug("No se pudo re-hashear contraseña legada de %s: %s", email_clean, e_mig)

    odoo_user, odoo_disponible = _verificar_usuario_odoo_con_estado(execute_fn, email_clean)
    if not odoo_user:
        if odoo_disponible:
            return None, "Acceso denegado: Tu usuario no se encuentra activo en Odoo."
        logger.warning(
            "Odoo no respondió durante login de %s; se usa respaldo local activo=%s.",
            email_clean,
            u_row.get("activo"),
        )
        odoo_user = {
            "user_id": None,
            "name": u_row.get("nombre_odoo") or email_clean,
            "login": email_clean,
            "email": email_clean,
        }

    rol = u_row.get("rol", "ventas")
    user_info = {
        "email": email_clean,
        "nombre": odoo_user.get("name") or u_row.get("nombre_odoo") or email_clean,
        "rol": rol,
        "permisos": efectivos(rol),
        "nombre_rol": NOMBRES_ROLES.get(rol, "Ventas"),
        "_pwd_ver": password_token_version(u_row.get("password_hash")),
    }
    return user_info, None


def crear_session_token(
    email: str,
    secret_key: str,
    *,
    ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
    pwd_version: str = "",
) -> str:
    """Crea un token de sesión firmado (HMAC-SHA256 completo, con expiración y versión)."""
    exp = int(time.time()) + max(60, int(ttl_seconds))
    raw_payload = f"{email.strip().lower()}|{exp}|{pwd_version[:12]}"
    payload_b64 = base64.urlsafe_b64encode(raw_payload.encode("utf-8")).decode("ascii")
    signature = hmac.new(
        secret_key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def verificar_session_token(
    token: str,
    secret_key: str,
    *,
    expected_pwd_version: str | None = None,
) -> str | None:
    """Verifica firma, expiración y versión de contraseña del token de sesión."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, signature = token.split(".", 1)
        expected_sig = hmac.new(
            secret_key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        decoded = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        parts = decoded.split("|")
        if len(parts) < 2:
            return None
        email = parts[0].strip().lower()
        exp = int(parts[1])
        pwd_ver = parts[2] if len(parts) >= 3 else ""
        if exp < int(time.time()):
            return None
        if expected_pwd_version is not None and pwd_ver and pwd_ver != expected_pwd_version[:12]:
            return None
        return email or None
    except Exception:
        return None


# --- quien hizo una accion, sin creerle al navegador -------------------------

# Los nombres que el formulario ofrece por defecto. No identifican a nadie: son
# roles, y estan pre-cargados en un `prompt`, asi que llegan tal cual cuando la
# persona aprieta Enter sin escribir.
DECLARADOS_GENERICOS = frozenset(
    {
        "",
        "direccion / administracion",
        "dirección / administración",
        "direccion / auditor",
        "dirección / auditor",
        "direccion / facturacion",
        "dirección / facturación",
        # El default de `MarcarRecibidoRequest`, a secas.
        "administracion",
        "administración",
        "desconocido",
    }
)


def identidad_de_sesion(usuario: dict[str, Any] | None) -> str:
    """Como se llama quien esta logueado, o cadena vacia si no hay sesion."""
    if not usuario:
        return ""
    return str(usuario.get("nombre") or usuario.get("email") or "").strip()


def actor_de_la_accion(usuario: dict[str, Any] | None, declarado: str) -> str:
    """Quien hizo una accion: la sesion manda, y lo declarado se conserva.

    Tres endpoints que cambian decisiones de dinero --aceptar una discrepancia,
    marcar un descuento como no otorgado, aprobar un descuento del sistema-- tomaban
    al actor del CUERPO del request. Y el formulario lo pide con un
    ``prompt("¿Quien lo marca?", "Direccion / Administracion")``: la persona **tipea**
    quien es, con un default que no identifica a nadie, mientras la sesion ya lo sabe.
    Comparar con ``patch_auditoria_estado``, en el mismo archivo, que si lo lee de la
    cookie.

    **No se descarta lo declarado.** Que alguien escriba "Direccion / Administracion"
    puede ser deliberado --actua en nombre de ese rol-- y borrarlo perderia la
    intencion. Lo que no puede pasar es que la identidad real no quede, asi que:

      - sin sesion, se conserva lo declarado (es todo lo que hay) o "desconocido"
      - con sesion y un declarado generico o vacio, se guarda la identidad sola
      - con sesion y un declarado distinto, se guardan las dos:
        ``"Ana Perez en nombre de Direccion / Auditor"``

    Nunca devuelve cadena vacia: una fila de auditoria sin actor no sirve para
    auditar.
    """
    sesion = identidad_de_sesion(usuario)
    limpio = (declarado or "").strip()
    if not sesion:
        return limpio or "desconocido"
    if limpio.lower() in DECLARADOS_GENERICOS or limpio.lower() == sesion.lower():
        return sesion
    return f"{sesion} en nombre de {limpio}"
