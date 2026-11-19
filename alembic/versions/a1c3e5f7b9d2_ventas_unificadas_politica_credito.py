"""Ventas unificadas y política de crédito / auto-envío inteligente.

Revision ID: a1c3e5f7b9d2
Revises: f7b9d1e3a5c7
Create Date: 2026-11-19 14:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c3e5f7b9d2"
down_revision: str | None = "f7b9d1e3a5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _cols_existentes(inspector: sa.Inspector, tabla: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(tabla)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("documentos_comerciales"):
        cols = _cols_existentes(inspector, "documentos_comerciales")
        nuevas: list[sa.Column[sa.Any]] = [
            sa.Column("auto_enviado", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("motivos_retencion_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("evaluacion_politica_json", sa.Text, nullable=False, server_default="{}"),
        ]
        for col in nuevas:
            if col.name not in cols:
                op.add_column("documentos_comerciales", col)

    if not inspector.has_table("politica_credito_cliente"):
        op.create_table(
            "politica_credito_cliente",
            sa.Column("partner_odoo_id", sa.Integer, primary_key=True, autoincrement=False),
            sa.Column("cliente_nombre", sa.String, nullable=False, server_default=""),
            sa.Column("limite_credito_usd", sa.Numeric(18, 2), nullable=True),
            sa.Column("limite_facturas_pendientes", sa.Integer, nullable=True),
            sa.Column("dias_mora_max", sa.Integer, nullable=True),
            sa.Column("bloquear_auto_envio", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("notas", sa.Text, nullable=False, server_default=""),
            sa.Column("actualizado_por", sa.String, nullable=False, server_default="sistema"),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("politica_credito_cliente"):
        op.drop_table("politica_credito_cliente")

    if inspector.has_table("documentos_comerciales"):
        cols = _cols_existentes(inspector, "documentos_comerciales")
        for nombre in (
            "evaluacion_politica_json",
            "motivos_retencion_json",
            "auto_enviado",
        ):
            if nombre in cols:
                op.drop_column("documentos_comerciales", nombre)
