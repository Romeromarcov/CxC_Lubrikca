"""Pruebas unitarias para el módulo de autenticación src/cxc/auth.py."""

from unittest.mock import MagicMock

from cxc.auth import (
    autenticar_usuario,
    buscar_usuario_plataforma,
    crear_session_token,
    hash_password,
    obtener_usuarios_plataforma,
    registrar_o_actualizar_usuario,
    verificar_password,
    verificar_session_token,
    verificar_usuario_odoo_activo,
)
from cxc.repositories import InMemoryRepository


def test_hash_and_verify_password():
    pwd = "mi_clave_secreta_123"
    pwd_hash, salt = hash_password(pwd)
    assert pwd_hash is not None
    assert salt is not None
    assert verificar_password(pwd, pwd_hash, salt) is True
    assert verificar_password("clave_incorrecta", pwd_hash, salt) is False


def test_verificar_usuario_odoo_activo():
    # Test None email
    assert verificar_usuario_odoo_activo(None, "") is None

    # Test active Odoo user found
    mock_execute = MagicMock(
        return_value=[
            {
                "id": 12,
                "name": "Marco Romero",
                "login": "mromero@lubrikca.com",
                "email": "mromero@lubrikca.com",
            }
        ]
    )
    user = verificar_usuario_odoo_activo(mock_execute, "mromero@lubrikca.com")
    assert user is not None
    assert user["user_id"] == 12
    assert user["email"] == "mromero@lubrikca.com"

    # Test user not found
    mock_execute_empty = MagicMock(return_value=[])
    assert verificar_usuario_odoo_activo(mock_execute_empty, "inexistente@lubrikca.com") is None

    # Test Odoo exception handled gracefully
    mock_execute_err = MagicMock(side_effect=Exception("Connection error"))
    assert verificar_usuario_odoo_activo(mock_execute_err, "error@lubrikca.com") is None


def test_session_token():
    secret = "mi_secret_key_2026"
    email = "admin@lubrikca.com"
    token = crear_session_token(email, secret)
    assert token is not None

    decoded_email = verificar_session_token(token, secret)
    assert decoded_email == email

    # Test invalid token or secret
    assert verificar_session_token("invalid_token_str", secret) is None
    assert verificar_session_token(token, "wrong_secret") is None


def test_repo_usuarios_management():
    repo = InMemoryRepository()
    repo.upsert_usuario_plataforma(
        {
            "email": "admin@lubrikca.com",
            "nombre_odoo": "Admin",
            "password_hash": "hash123",
            "salt": "salt123",
            "rol": "admin",
            "activo": "TRUE",
        }
    )

    users = obtener_usuarios_plataforma(repo)
    assert len(users) == 1

    found = buscar_usuario_plataforma(repo, "ADMIN@lubrikca.com")
    assert found is not None
    assert found["email"] == "admin@lubrikca.com"

    # Test registrar_o_actualizar_usuario
    new_u = registrar_o_actualizar_usuario(
        repo,
        email="nuevo@lubrikca.com",
        password="password123",
        nombre_odoo="Nuevo Usuario",
        rol="tesoreria",
    )
    assert new_u["email"] == "nuevo@lubrikca.com"
    assert new_u["rol"] == "tesoreria"
    assert buscar_usuario_plataforma(repo, "nuevo@lubrikca.com") is not None


def test_autenticar_usuario():
    pwd = "password123"
    pwd_hash, salt = hash_password(pwd)

    mock_repo = InMemoryRepository()
    mock_repo.upsert_usuario_plataforma(
        {
            "email": "user@lubrikca.com",
            "nombre_odoo": "User Test",
            "password_hash": pwd_hash,
            "salt": salt,
            "rol": "tesoreria",
            "activo": "TRUE",
        }
    )

    mock_execute = MagicMock(
        return_value=[
            {
                "id": 5,
                "name": "User Test",
                "login": "user@lubrikca.com",
                "email": "user@lubrikca.com",
            }
        ]
    )

    # Successful authentication
    u_info, err = autenticar_usuario(mock_execute, mock_repo, "user@lubrikca.com", pwd)
    assert err is None
    assert u_info is not None
    assert u_info["email"] == "user@lubrikca.com"
    assert u_info["rol"] == "tesoreria"

    # Wrong password
    u_info_err, err_msg = autenticar_usuario(
        mock_execute, mock_repo, "user@lubrikca.com", "wrong_pwd"
    )
    assert u_info_err is None
    assert err_msg == "Contraseña incorrecta."

    # User not in Sheets
    u_info_unreg, err_unreg = autenticar_usuario(mock_execute, mock_repo, "unreg@lubrikca.com", pwd)
    assert u_info_unreg is None
    assert "no registrado" in err_unreg

    # Inactive in Odoo
    mock_execute_inactive = MagicMock(return_value=[])
    u_info_inact, err_inact = autenticar_usuario(
        mock_execute_inactive, mock_repo, "user@lubrikca.com", pwd
    )
    assert u_info_inact is None
    assert "no se encuentra activo en Odoo" in err_inact
