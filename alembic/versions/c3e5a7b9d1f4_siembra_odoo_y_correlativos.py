"""Tablas correlativos_sistema y siembra_bitacora para siembra historica Odoo.

Revision ID: c3e5a7b9d1f4
Revises: b2d4f6a8c0e3
Create Date: 2026-11-19 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a7b9d1f4"
down_revision: str | None = "b2d4f6a8c0e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("correlativos_sistema"):
        op.create_table(
            "correlativos_sistema",
            sa.Column("entidad", sa.String, primary_key=True),
            sa.Column("prefijo", sa.String, primary_key=True),
            sa.Column("ultimo_numero", sa.Integer, nullable=False, server_default="0"),
            sa.Column("ejemplo_maximo", sa.String, nullable=False, server_default=""),
            sa.Column("fuente", sa.String, nullable=False, server_default="odoo"),
            sa.Column("total_registros", sa.Integer, nullable=False, server_default="0"),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )

    if not inspector.has_table("siembra_bitacora"):
        op.create_table(
            "siembra_bitacora",
            sa.Column("siembra_id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tipo", sa.String, nullable=False, server_default="completa"),
            sa.Column(
                "estado",
                sa.String,
                nullable=False,
                server_default="EN_PROGRESO",
                index=True,
            ),
            sa.Column("actor", sa.String, nullable=False, server_default="sistema"),
            sa.Column("iniciada_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("finalizada_en", sa.DateTime(timezone=False), nullable=True),
            sa.Column("duracion_seg", sa.Numeric(10, 2), nullable=True),
            sa.Column("conteos_json", sa.Text, nullable=False, server_default="{}"),
            sa.Column("correlativos_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("avisos_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("error", sa.Text, nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("siembra_bitacora"):
        op.drop_table("siembra_bitacora")
    if inspector.has_table("correlativos_sistema"):
        op.drop_table("correlativos_sistema")
