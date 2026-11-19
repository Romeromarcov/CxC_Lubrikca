"""Compras unificadas Odoo, asientos de gastos sin factura y lotes de caja chica.

Revision ID: b2d4f6a8c0e3
Revises: a1c3e5f7b9d2
Create Date: 2026-11-19 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2d4f6a8c0e3"
down_revision: str | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _cols_existentes(inspector: sa.Inspector, tabla: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(tabla)}


def _indices_existentes(inspector: sa.Inspector, tabla: str) -> set[str]:
    return {ix["name"] for ix in inspector.get_indexes(tabla) if ix.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("compras_gastos_odoo"):
        op.create_table(
            "compras_gastos_odoo",
            sa.Column("move_id", sa.String, primary_key=True),
            sa.Column("numero_odoo", sa.String, nullable=False, server_default=""),
            sa.Column("move_type", sa.String, nullable=False, server_default="in_invoice"),
            sa.Column("tipo_cxc", sa.String, nullable=False, server_default="factura_proveedor"),
            sa.Column("partner_odoo_id", sa.Integer, nullable=True),
            sa.Column("proveedor_nombre", sa.String, nullable=False, server_default=""),
            sa.Column("rif", sa.String, nullable=False, server_default=""),
            sa.Column("monto_total", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("moneda", sa.String, nullable=False, server_default="VES"),
            sa.Column("diario_id", sa.Integer, nullable=True),
            sa.Column("diario_nombre", sa.String, nullable=False, server_default=""),
            sa.Column("referencia", sa.String, nullable=False, server_default=""),
            sa.Column("fecha_documento", sa.Date, nullable=False),
            sa.Column("estado_odoo", sa.String, nullable=False, server_default="posted"),
            sa.Column("estado_pago", sa.String, nullable=False, server_default=""),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index(
            "ix_compras_gastos_odoo_partner_odoo_id",
            "compras_gastos_odoo",
            ["partner_odoo_id"],
        )
        op.create_index(
            "ix_compras_gastos_odoo_fecha_documento",
            "compras_gastos_odoo",
            ["fecha_documento"],
        )

    if not inspector.has_table("lotes_caja_chica"):
        op.create_table(
            "lotes_caja_chica",
            sa.Column("lote_id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("codigo", sa.String, nullable=False, unique=True),
            sa.Column("responsable", sa.String, nullable=False, server_default=""),
            sa.Column("moneda", sa.String, nullable=False, server_default="USD"),
            sa.Column("monto_total", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("estado", sa.String, nullable=False, server_default="ABIERTO"),
            sa.Column("notas", sa.Text, nullable=False, server_default=""),
            sa.Column("creado_por", sa.String, nullable=False, server_default="sistema"),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("cerrado_en", sa.DateTime(timezone=False), nullable=True),
        )

    if inspector.has_table("documentos_operativos"):
        cols_op = _cols_existentes(inspector, "documentos_operativos")
        idxs_op = _indices_existentes(inspector, "documentos_operativos")
        nuevas_op: list[sa.Column[sa.Any]] = [
            sa.Column(
                "lote_caja_chica_id",
                sa.Integer,
                sa.ForeignKey("lotes_caja_chica.lote_id"),
                nullable=True,
            ),
            sa.Column("cuenta_contrapartida_id", sa.Integer, nullable=True),
            sa.Column("conciliado_banco_huella", sa.String(64), nullable=False, server_default=""),
        ]
        for col in nuevas_op:
            if col.name not in cols_op:
                op.add_column("documentos_operativos", col)
        if "ix_documentos_operativos_lote_caja_chica_id" not in idxs_op:
            op.create_index(
                "ix_documentos_operativos_lote_caja_chica_id",
                "documentos_operativos",
                ["lote_caja_chica_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("documentos_operativos"):
        idxs_op = _indices_existentes(inspector, "documentos_operativos")
        if "ix_documentos_operativos_lote_caja_chica_id" in idxs_op:
            op.drop_index(
                "ix_documentos_operativos_lote_caja_chica_id",
                table_name="documentos_operativos",
            )
        cols_op = _cols_existentes(inspector, "documentos_operativos")
        for nombre in (
            "conciliado_banco_huella",
            "cuenta_contrapartida_id",
            "lote_caja_chica_id",
        ):
            if nombre in cols_op:
                op.drop_column("documentos_operativos", nombre)

    if inspector.has_table("lotes_caja_chica"):
        op.drop_table("lotes_caja_chica")

    if inspector.has_table("compras_gastos_odoo"):
        idxs_cg = _indices_existentes(inspector, "compras_gastos_odoo")
        for ix in (
            "ix_compras_gastos_odoo_fecha_documento",
            "ix_compras_gastos_odoo_partner_odoo_id",
        ):
            if ix in idxs_cg:
                op.drop_index(ix, table_name="compras_gastos_odoo")
        op.drop_table("compras_gastos_odoo")
