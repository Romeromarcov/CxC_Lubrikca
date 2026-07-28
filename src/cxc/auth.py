"""Módulo de Autenticación Integrada con Odoo y Control de Accesos por Rol."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime
from typing import Any

from cxc.sheets.repository import SheetsRepository

logger = logging.getLogger("cxc.auth")

T_USUARIOS = "UsuariosPlataforma"

# Matriz de Permisos por Rol
ALL_PAGES = [
    "dashboard",
    "facturacion",
    "conciliaciones",
    "cobranza",
    "reporte",
    "auditoria",
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


def obtener_usuarios_plataforma(repo: SheetsRepository) -> list[dict[str, str]]:
    """Lee todos los usuarios registrados en Google Sheets."""
    try:
        return repo._g.read_rows(T_USUARIOS)
    except Exception as e:
        logger.warning("Error leyendo tabla %s: %s", T_USUARIOS, e)
        return []


def buscar_usuario_plataforma(repo: SheetsRepository, email: str) -> dict[str, str] | None:
    """Busca un usuario por correo en Google Sheets."""
    email_clean = email.strip().lower()
    for row in obtener_usuarios_plataforma(repo):
        row_email = str(row.get("email") or "").strip().lower()
        if row_email == email_clean:
            return row
    return None


def registrar_o_actualizar_usuario(
    repo: SheetsRepository,
    email: str,
    password: str | None = None,
    nombre_odoo: str = "",
    rol: str | None = None,
    activo: bool = True,
) -> dict[str, str]:
    """Registra o actualiza un usuario en Google Sheets."""
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

    repo._g.upsert_row(T_USUARIOS, "email", new_row)
    return new_row


def autenticar_usuario(
    execute_fn: Any, repo: SheetsRepository, email: str, password: str
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
