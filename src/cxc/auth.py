"""Módulo de Autenticación Integrada con Odoo y Control de Accesos por Rol."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime
from typing import Any

from cxc.repositories import Repository

logger = logging.getLogger("cxc.auth")

# Matriz de Permisos por Rol
ALL_PAGES = [
    "dashboard",
    "facturacion",
    "conciliaciones",
    "cobranza",
    "ventas",
    "reporte",
    "auditoria",
    "inventario",
    "configuracion",
]

ROLES_PERMISOS: dict[str, list[str]] = {
    "admin": ALL_PAGES,
    "gerente_ventas": ALL_PAGES,
    "tesoreria": ALL_PAGES,
    "auditor": ALL_PAGES,
    "ventas": ALL_PAGES,
}

NOMBRES_ROLES: dict[str, str] = {
    "admin": "Administrador / Gerencia",
    "gerente_ventas": "Supervisor / Gerente de Ventas",
    "tesoreria": "Tesorería y Cobranza",
    "auditor": "Auditoría y Contabilidad",
    "ventas": "Ventas y Comercial",
}


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Genera un hash SHA-256 con salt para una contraseña."""
    if not salt:
        salt = secrets.token_hex(16)
    salted = (password + salt).encode("utf-8")
    pwd_hash = hashlib.sha256(salted).hexdigest()
    return pwd_hash, salt


def verificar_password(password: str, pwd_hash: str, salt: str) -> bool:
    """Verifica si una contraseña coincide con su hash almacenado."""
    calculated_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(calculated_hash, pwd_hash)


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


def verificar_usuario_odoo_activo(execute_fn: Any, email: str) -> dict[str, Any] | None:
    """Consulta a Odoo para verificar que el correo corresponda a un usuario activo."""
    if not email:
        return None
    email_clean = email.strip().lower()
    try:
        # Buscar en res.users usuarios activos
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
            return {
                "user_id": u.get("id"),
                "name": u.get("name") or email_clean,
                "login": u.get("login") or email_clean,
                "email": u.get("email") or u.get("login") or email_clean,
            }
    except Exception as e:
        logger.warning("Error verificando usuario %s en Odoo: %s", email_clean, e)
    return None


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
    """Valida la contraseña y que el usuario siga activo en Odoo."""
    email_clean = email.strip().lower()

    # 1. Verificar existencia en Google Sheets
    u_row = buscar_usuario_plataforma(repo, email_clean)
    if not u_row or not u_row.get("password_hash"):
        return None, "Usuario no registrado. Regístrate por primera vez para crear tu contraseña."

    if u_row.get("activo") == "FALSE":
        return None, "Usuario desactivado en la plataforma."

    # 2. Verificar contraseña
    if not verificar_password(password, u_row["password_hash"], u_row["salt"]):
        return None, "Contraseña incorrecta."

    # 3. Verificar que siga activo en Odoo
    odoo_user = verificar_usuario_odoo_activo(execute_fn, email_clean)
    if not odoo_user:
        return None, "Acceso denegado: Tu usuario no se encuentra activo en Odoo."

    user_info = {
        "email": email_clean,
        "nombre": odoo_user.get("name") or u_row.get("nombre_odoo") or email_clean,
        "rol": u_row.get("rol", "ventas"),
        "permisos": ROLES_PERMISOS.get(u_row.get("rol", "ventas"), ["reporte"]),
        "nombre_rol": NOMBRES_ROLES.get(u_row.get("rol", "ventas"), "Ventas"),
    }
    return user_info, None


def crear_session_token(email: str, secret_key: str) -> str:
    """Crea un token de sesión firmado."""
    email_b64 = base64.urlsafe_b64encode(email.strip().lower().encode()).decode()
    signature = hmac.new(secret_key.encode(), email_b64.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{email_b64}.{signature}"


def verificar_session_token(token: str, secret_key: str) -> str | None:
    """Verifica y decodifica un token de sesión."""
    if not token or "." not in token:
        return None
    try:
        email_b64, signature = token.split(".", 1)
        expected_sig = hmac.new(
            secret_key.encode(), email_b64.encode(), hashlib.sha256
        ).hexdigest()[:16]
        if hmac.compare_digest(signature, expected_sig):
            return base64.urlsafe_b64decode(email_b64.encode()).decode()
    except Exception:
        pass
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
