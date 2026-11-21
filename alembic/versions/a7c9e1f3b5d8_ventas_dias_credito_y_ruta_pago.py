"""Agrega días de crédito, ruta de pago y descuentos especiales en documentos comerciales.

Revision ID: a7c9e1f3b5d8
Revises: f6b8d0e2a4c7
Create Date: 2026-11-21 12:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1f3b5d8"
down_revision: str | None = "f6b8d0e2a4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("documentos_comerciales"):
        return

    cols = {c["name"] for c in inspector.get_columns("documentos_comerciales")}
    if "dias_credito" not in cols:
        op.add_column(
            "documentos_comerciales",
            sa.Column("dias_credito", sa.Integer, nullable=False, server_default="0"),
        )
    if "ruta_pago" not in cols:
        op.add_column(
            "documentos_comerciales",
            sa.Column("ruta_pago", sa.String(24), nullable=False, server_default=""),
        )
    if "descuentos_especiales_json" not in cols:
        op.add_column(
            "documentos_comerciales",
            sa.Column("descuentos_especiales_json", sa.Text, nullable=False, server_default="[]"),
        )

    op.execute(
        sa.text(
            "UPDATE documentos_comerciales "
            "SET ruta_pago = CASE "
            "  WHEN UPPER(COALESCE(moneda_lista, '')) = 'USD' "
            "    OR UPPER(COALESCE(lista_precios_nombre, '')) LIKE '%USD%' THEN 'binance' "
            "  ELSE 'bcv' "
            "END "
            "WHERE COALESCE(ruta_pago, '') = ''"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("documentos_comerciales"):
        return

    cols = {c["name"] for c in inspector.get_columns("documentos_comerciales")}
    if "descuentos_especiales_json" in cols:
        op.drop_column("documentos_comerciales", "descuentos_especiales_json")
    if "ruta_pago" in cols:
        op.drop_column("documentos_comerciales", "ruta_pago")
    if "dias_credito" in cols:
        op.drop_column("documentos_comerciales", "dias_credito")
