"""Tabla contactos_maestro para Maestro Único de Contactos y motor anti-duplicados.

Revision ID: d4f6a8c0e2a5
Revises: c3e5a7b9d1f4
Create Date: 2026-11-19 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6a8c0e2a5"
down_revision: str | None = "c3e5a7b9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("contactos_maestro"):
        op.create_table(
            "contactos_maestro",
            sa.Column("contacto_id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("odoo_partner_id", sa.Integer, unique=True, nullable=True, index=True),
            sa.Column("origen", sa.String, nullable=False, server_default="cxc", index=True),
            sa.Column("rol", sa.String, nullable=False, server_default="prospecto", index=True),
            sa.Column("nombre", sa.String, nullable=False, server_default=""),
            sa.Column(
                "nombre_normalizado", sa.String, nullable=False, server_default="", index=True
            ),
            sa.Column("rif", sa.String, nullable=False, server_default=""),
            sa.Column("rif_normalizado", sa.String, nullable=False, server_default="", index=True),
            sa.Column("telefono", sa.String, nullable=False, server_default=""),
            sa.Column(
                "telefono_normalizado", sa.String, nullable=False, server_default="", index=True
            ),
            sa.Column("email", sa.String, nullable=False, server_default=""),
            sa.Column("direccion", sa.Text, nullable=False, server_default=""),
            sa.Column("ciudad", sa.String, nullable=False, server_default=""),
            sa.Column("latitud", sa.Numeric(10, 7), nullable=True),
            sa.Column("longitud", sa.Numeric(10, 7), nullable=True),
            sa.Column("plus_code", sa.String, nullable=False, server_default=""),
            sa.Column("vendedor_email", sa.String, nullable=False, server_default=""),
            sa.Column(
                "estado_validacion",
                sa.String,
                nullable=False,
                server_default="PENDIENTE",
                index=True,
            ),
            sa.Column("completitud_pct", sa.Integer, nullable=False, server_default="0"),
            sa.Column("campos_faltantes_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("duplicado_de_id", sa.Integer, nullable=True, index=True),
            sa.Column("posibles_duplicados_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("es_borrador_telegram", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("captura_id", sa.Integer, nullable=True, index=True),
            sa.Column("creado_por", sa.String, nullable=False, server_default="sistema"),
            sa.Column("validado_por", sa.String, nullable=False, server_default=""),
            sa.Column("notas", sa.Text, nullable=False, server_default=""),
            sa.Column("activo", sa.Boolean, nullable=False, server_default="true", index=True),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("contactos_maestro"):
        op.drop_table("contactos_maestro")
