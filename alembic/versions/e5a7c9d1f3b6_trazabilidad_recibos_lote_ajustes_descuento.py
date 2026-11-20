"""Trazabilidad Odoo en órdenes, recibos de efectivo por lote y ajustes de descuento.

Revision ID: e5a7c9d1f3b6
Revises: d4f6a8c0e2a5
Create Date: 2026-11-20 14:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7c9d1f3b6"
down_revision: str | None = "d4f6a8c0e2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(18, 4)


def _cols_de(inspector: sa.Inspector, tabla: str) -> set[str]:
    if not inspector.has_table(tabla):
        return set()
    return {c["name"] for c in inspector.get_columns(tabla)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "odoo_order_id" not in _cols_de(inspector, "ordenes_venta"):
        op.add_column("ordenes_venta", sa.Column("odoo_order_id", sa.Integer, nullable=True))
        op.create_index(
            "ix_ordenes_venta_odoo_order_id",
            "ordenes_venta",
            ["odoo_order_id"],
            unique=False,
        )

    if "lote_recibo_id" not in _cols_de(inspector, "documentos_operativos"):
        op.add_column(
            "documentos_operativos", sa.Column("lote_recibo_id", sa.Integer, nullable=True)
        )
        op.create_index(
            "ix_documentos_operativos_lote_recibo_id",
            "documentos_operativos",
            ["lote_recibo_id"],
            unique=False,
        )

    if not inspector.has_table("lotes_recibo_efectivo"):
        op.create_table(
            "lotes_recibo_efectivo",
            sa.Column("lote_id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("numero_recibo", sa.String, unique=True, nullable=False, index=True),
            sa.Column("vendedor_email", sa.String, nullable=False, server_default="", index=True),
            sa.Column("vendedor_nombre", sa.String, nullable=False, server_default=""),
            sa.Column("fecha_lote", sa.Date, nullable=False, index=True),
            sa.Column("cantidad_pagos", sa.Integer, nullable=False, server_default="0"),
            sa.Column("total_usd", _MONEY, nullable=False, server_default="0"),
            sa.Column("total_ves", _MONEY, nullable=False, server_default="0"),
            sa.Column("total_eur", _MONEY, nullable=False, server_default="0"),
            sa.Column("total_equivalente_usd", _MONEY, nullable=False, server_default="0"),
            sa.Column("pagos_ref_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("recibido_por", sa.String, nullable=False, server_default=""),
            sa.Column("recibido_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("observaciones", sa.Text, nullable=False, server_default=""),
            sa.Column("estado", sa.String, nullable=False, server_default="EMITIDO", index=True),
        )

    if not inspector.has_table("orden_descuentos_ajustes"):
        op.create_table(
            "orden_descuentos_ajustes",
            sa.Column("ajuste_id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("so_id", sa.String, nullable=False, index=True),
            sa.Column("accion", sa.String, nullable=False, index=True),
            sa.Column("descuento_origen", sa.String, nullable=False, server_default=""),
            sa.Column("regla_id", sa.String, nullable=False, server_default=""),
            sa.Column("tipo_valor", sa.String, nullable=False, server_default="porcentaje"),
            sa.Column("porcentaje", sa.Numeric(8, 4), nullable=True),
            sa.Column("monto_usd", _MONEY, nullable=True),
            sa.Column("descripcion", sa.Text, nullable=False, server_default=""),
            sa.Column("motivo", sa.Text, nullable=False, server_default=""),
            sa.Column("activo", sa.Boolean, nullable=False, server_default="true", index=True),
            sa.Column("creado_por", sa.String, nullable=False, server_default=""),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("anulado_por", sa.String, nullable=False, server_default=""),
            sa.Column("anulado_en", sa.DateTime(timezone=False), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("orden_descuentos_ajustes"):
        op.drop_table("orden_descuentos_ajustes")
    if inspector.has_table("lotes_recibo_efectivo"):
        op.drop_table("lotes_recibo_efectivo")
    if "lote_recibo_id" in _cols_de(inspector, "documentos_operativos"):
        op.drop_column("documentos_operativos", "lote_recibo_id")
    if "odoo_order_id" in _cols_de(inspector, "ordenes_venta"):
        op.drop_column("ordenes_venta", "odoo_order_id")
