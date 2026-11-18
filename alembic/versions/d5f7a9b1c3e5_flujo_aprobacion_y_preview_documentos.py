"""Preview de cotizaciones, descuentos del motor y flujo de aprobación de pedidos en CxC.

Revision ID: d5f7a9b1c3e5
Revises: c4e6f8a0b2d4
Create Date: 2026-11-18 15:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5f7a9b1c3e5"
down_revision: str | None = "c4e6f8a0b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documentos_comerciales",
        sa.Column("token_preview", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("lista_precios_id", sa.String, nullable=False, server_default=""),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("lista_precios_nombre", sa.String, nullable=False, server_default=""),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("descuentos_json", sa.Text, nullable=False, server_default="[]"),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("validacion_motor_json", sa.Text, nullable=False, server_default="{}"),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("total_descuentos", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("subtotal_neto", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("monto_iva", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("total_con_iva", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("aprobado_por", sa.String, nullable=False, server_default=""),
    )
    op.add_column(
        "documentos_comerciales",
        sa.Column("aprobado_en", sa.DateTime(timezone=False), nullable=True),
    )
    op.create_index(
        "ix_documentos_comerciales_token_preview",
        "documentos_comerciales",
        ["token_preview"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_documentos_comerciales_token_preview",
        table_name="documentos_comerciales",
    )
    for col in (
        "aprobado_en",
        "aprobado_por",
        "total_con_iva",
        "monto_iva",
        "subtotal_neto",
        "total_descuentos",
        "validacion_motor_json",
        "descuentos_json",
        "lista_precios_nombre",
        "lista_precios_id",
        "token_preview",
    ):
        op.drop_column("documentos_comerciales", col)
