"""Tablas Fase 1 API v1: tokens móvil/portal, dispositivos, portal clientes, idempotencia y adjuntos.

Revision ID: c9e2f4a6b8d0
Revises: b8d1e3f5a7c9
Create Date: 2026-11-22 07:28:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e2f4a6b8d0"
down_revision: str | None = "b8d1e3f5a7c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _crear_indice_si_falta(
    inspector: sa.Inspector, tabla: str, nombre: str, cols: list[str], *, unique: bool = False
) -> None:
    existentes = {i["name"] for i in inspector.get_indexes(tabla)}
    if nombre not in existentes:
        op.create_index(nombre, tabla, cols, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("auth_refresh_tokens"):
        op.create_table(
            "auth_refresh_tokens",
            sa.Column("token_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("familia_id", sa.String(64), nullable=False),
            sa.Column("token_hash", sa.String(128), nullable=False),
            sa.Column("sujeto_tipo", sa.String(32), nullable=False),
            sa.Column("sujeto_id", sa.String(160), nullable=False),
            sa.Column("device_id", sa.String(120), nullable=False, server_default=""),
            sa.Column("password_version", sa.String(32), nullable=False, server_default=""),
            sa.Column("ip_origen", sa.String(64), nullable=False, server_default=""),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("expira_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("rotado_en", sa.DateTime(timezone=False), nullable=True),
            sa.Column("revocado_en", sa.DateTime(timezone=False), nullable=True),
        )
        inspector = sa.inspect(bind)
    _crear_indice_si_falta(
        inspector, "auth_refresh_tokens", "ix_auth_refresh_tokens_familia_id", ["familia_id"]
    )
    _crear_indice_si_falta(
        inspector,
        "auth_refresh_tokens",
        "ix_auth_refresh_tokens_token_hash",
        ["token_hash"],
        unique=True,
    )
    _crear_indice_si_falta(
        inspector, "auth_refresh_tokens", "ix_auth_refresh_tokens_sujeto_tipo", ["sujeto_tipo"]
    )
    _crear_indice_si_falta(
        inspector, "auth_refresh_tokens", "ix_auth_refresh_tokens_sujeto_id", ["sujeto_id"]
    )
    _crear_indice_si_falta(
        inspector, "auth_refresh_tokens", "ix_auth_refresh_tokens_device_id", ["device_id"]
    )
    _crear_indice_si_falta(
        inspector, "auth_refresh_tokens", "ix_auth_refresh_tokens_expira_en", ["expira_en"]
    )

    if not inspector.has_table("dispositivos_moviles"):
        op.create_table(
            "dispositivos_moviles",
            sa.Column("device_id", sa.String(120), primary_key=True),
            sa.Column("sujeto_tipo", sa.String(32), nullable=False),
            sa.Column("sujeto_id", sa.String(160), nullable=False),
            sa.Column("plataforma", sa.String(32), nullable=False, server_default="android"),
            sa.Column("push_token", sa.String(255), nullable=False, server_default=""),
            sa.Column("app_version", sa.String(40), nullable=False, server_default=""),
            sa.Column("ultimo_sync_en", sa.DateTime(timezone=False), nullable=True),
            sa.Column("ultimo_cursor", sa.String(64), nullable=False, server_default=""),
            sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )
        inspector = sa.inspect(bind)
    _crear_indice_si_falta(
        inspector, "dispositivos_moviles", "ix_dispositivos_moviles_sujeto_tipo", ["sujeto_tipo"]
    )
    _crear_indice_si_falta(
        inspector, "dispositivos_moviles", "ix_dispositivos_moviles_sujeto_id", ["sujeto_id"]
    )
    _crear_indice_si_falta(
        inspector, "dispositivos_moviles", "ix_dispositivos_moviles_activo", ["activo"]
    )

    if not inspector.has_table("clientes_portal_cuentas"):
        op.create_table(
            "clientes_portal_cuentas",
            sa.Column("cuenta_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("cliente_id", sa.String(64), nullable=False),
            sa.Column("partner_odoo_id", sa.Integer(), nullable=True),
            sa.Column("cliente_nombre", sa.String(200), nullable=False, server_default=""),
            sa.Column("email", sa.String(160), nullable=False),
            sa.Column("telefono", sa.String(40), nullable=False, server_default=""),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("salt", sa.String(64), nullable=False),
            sa.Column("rol_portal", sa.String(32), nullable=False, server_default="titular"),
            sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("verificado_en", sa.DateTime(timezone=False), nullable=True),
            sa.Column("ultimo_acceso_en", sa.DateTime(timezone=False), nullable=True),
            sa.Column("creado_por", sa.String(160), nullable=False, server_default="sistema"),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )
        inspector = sa.inspect(bind)
    _crear_indice_si_falta(
        inspector,
        "clientes_portal_cuentas",
        "ix_clientes_portal_cuentas_cliente_id",
        ["cliente_id"],
    )
    _crear_indice_si_falta(
        inspector,
        "clientes_portal_cuentas",
        "ix_clientes_portal_cuentas_partner_odoo_id",
        ["partner_odoo_id"],
    )
    _crear_indice_si_falta(
        inspector,
        "clientes_portal_cuentas",
        "ix_clientes_portal_cuentas_email",
        ["email"],
        unique=True,
    )
    _crear_indice_si_falta(
        inspector, "clientes_portal_cuentas", "ix_clientes_portal_cuentas_activo", ["activo"]
    )

    if not inspector.has_table("clientes_portal_invitaciones"):
        op.create_table(
            "clientes_portal_invitaciones",
            sa.Column("invitacion_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.String(128), nullable=False),
            sa.Column("cliente_id", sa.String(64), nullable=False),
            sa.Column("partner_odoo_id", sa.Integer(), nullable=True),
            sa.Column("cliente_nombre", sa.String(200), nullable=False, server_default=""),
            sa.Column("email", sa.String(160), nullable=False),
            sa.Column("rol_portal", sa.String(32), nullable=False, server_default="titular"),
            sa.Column("creado_por", sa.String(160), nullable=False, server_default="sistema"),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("expira_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("consumido_en", sa.DateTime(timezone=False), nullable=True),
        )
        inspector = sa.inspect(bind)
    _crear_indice_si_falta(
        inspector,
        "clientes_portal_invitaciones",
        "ix_clientes_portal_invitaciones_token_hash",
        ["token_hash"],
        unique=True,
    )
    _crear_indice_si_falta(
        inspector,
        "clientes_portal_invitaciones",
        "ix_clientes_portal_invitaciones_cliente_id",
        ["cliente_id"],
    )
    _crear_indice_si_falta(
        inspector,
        "clientes_portal_invitaciones",
        "ix_clientes_portal_invitaciones_partner_odoo_id",
        ["partner_odoo_id"],
    )
    _crear_indice_si_falta(
        inspector,
        "clientes_portal_invitaciones",
        "ix_clientes_portal_invitaciones_email",
        ["email"],
    )

    if not inspector.has_table("sync_idempotency_keys"):
        op.create_table(
            "sync_idempotency_keys",
            sa.Column("idempotency_key", sa.String(128), primary_key=True),
            sa.Column("actor_tipo", sa.String(32), nullable=False),
            sa.Column("actor_id", sa.String(160), nullable=False),
            sa.Column("endpoint", sa.String(120), nullable=False, server_default=""),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False, server_default="200"),
            sa.Column("response_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
        )
        inspector = sa.inspect(bind)
    _crear_indice_si_falta(
        inspector, "sync_idempotency_keys", "ix_sync_idempotency_keys_actor_tipo", ["actor_tipo"]
    )
    _crear_indice_si_falta(
        inspector, "sync_idempotency_keys", "ix_sync_idempotency_keys_actor_id", ["actor_id"]
    )
    _crear_indice_si_falta(
        inspector, "sync_idempotency_keys", "ix_sync_idempotency_keys_creado_en", ["creado_en"]
    )

    if not inspector.has_table("adjuntos_objeto"):
        op.create_table(
            "adjuntos_objeto",
            sa.Column("adjunto_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("storage_key", sa.String(255), nullable=False, unique=True),
            sa.Column("storage_backend", sa.String(32), nullable=False, server_default="local"),
            sa.Column("mime", sa.String(80), nullable=False),
            sa.Column("bytes", sa.Integer(), nullable=False),
            sa.Column("nombre_archivo", sa.String(200), nullable=False, server_default=""),
            sa.Column("subido_por_tipo", sa.String(32), nullable=False, server_default=""),
            sa.Column("subido_por_id", sa.String(160), nullable=False, server_default=""),
            sa.Column("entidad_tipo", sa.String(40), nullable=False, server_default=""),
            sa.Column("entidad_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
        )
        inspector = sa.inspect(bind)
    _crear_indice_si_falta(inspector, "adjuntos_objeto", "ix_adjuntos_objeto_sha256", ["sha256"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for tabla in (
        "adjuntos_objeto",
        "sync_idempotency_keys",
        "clientes_portal_invitaciones",
        "clientes_portal_cuentas",
        "dispositivos_moviles",
        "auth_refresh_tokens",
    ):
        if inspector.has_table(tabla):
            op.drop_table(tabla)
