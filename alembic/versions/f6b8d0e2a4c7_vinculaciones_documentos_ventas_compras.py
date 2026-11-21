"""Vinculaciones bidireccionales de pagos en ventas (operativos) y compras/gastos.

Revision ID: f6b8d0e2a4c7
Revises: e5a7c9d1f3b6
Create Date: 2026-11-21 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b8d0e2a4c7"
down_revision: str | None = "e5a7c9d1f3b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("vinculaciones_ventas_op"):
        op.create_table(
            "vinculaciones_ventas_op",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("pago_operativo_id", sa.Integer, nullable=True, index=True),
            sa.Column("pago_odoo_id", sa.String(64), nullable=True, index=True),
            sa.Column("so_id", sa.String(64), nullable=False, index=True),
            sa.Column("documento_comercial_id", sa.Integer, nullable=True, index=True),
            sa.Column("partner_odoo_id", sa.Integer, nullable=True, index=True),
            sa.Column("monto_aplicado", sa.Numeric(14, 2), nullable=False),
            sa.Column("moneda", sa.String(8), nullable=False, server_default="USD"),
            sa.Column("equiv_usd_bcv", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("tasa_bcv", sa.Numeric(14, 6), nullable=True),
            sa.Column("origen_vinculacion", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("estado", sa.String(32), nullable=False, server_default="VINCULADO_CXC"),
            sa.Column("odoo_reconciliado", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("odoo_move_id", sa.Integer, nullable=True),
            sa.Column("odoo_detalle", sa.Text, nullable=False, server_default=""),
            sa.Column("vinculado_por", sa.String(160), nullable=False, server_default=""),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
        )

    if not inspector.has_table("vinculaciones_compras"):
        op.create_table(
            "vinculaciones_compras",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("proveedor_odoo_id", sa.Integer, nullable=True, index=True),
            sa.Column("proveedor_nombre", sa.String(255), nullable=False, server_default=""),
            sa.Column("pago_operativo_id", sa.Integer, nullable=True, index=True),
            sa.Column("pago_odoo_id", sa.String(64), nullable=True, index=True),
            sa.Column("pago_ref", sa.String(100), nullable=False, server_default=""),
            sa.Column("compra_operativo_id", sa.Integer, nullable=True, index=True),
            sa.Column("compra_odoo_move_id", sa.String(64), nullable=True, index=True),
            sa.Column("compra_ref", sa.String(100), nullable=False, server_default=""),
            sa.Column("monto_aplicado", sa.Numeric(14, 2), nullable=False),
            sa.Column("moneda", sa.String(8), nullable=False, server_default="USD"),
            sa.Column("equiv_usd_bcv", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("tasa_bcv", sa.Numeric(14, 6), nullable=True),
            sa.Column("origen_vinculacion", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("estado", sa.String(32), nullable=False, server_default="VINCULADO_CXC"),
            sa.Column("odoo_reconciliado", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("odoo_move_id", sa.Integer, nullable=True),
            sa.Column("odoo_detalle", sa.Text, nullable=False, server_default=""),
            sa.Column("vinculado_por", sa.String(160), nullable=False, server_default=""),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("vinculaciones_compras"):
        op.drop_table("vinculaciones_compras")
    if inspector.has_table("vinculaciones_ventas_op"):
        op.drop_table("vinculaciones_ventas_op")
