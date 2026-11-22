"""Cuentas receptoras propias, Binance en vinculaciones ventas, vencimiento CxP e índice recibos.

Revision ID: b8d1e3f5a7c9
Revises: a7c9e1f3b5d8
Create Date: 2026-11-21 18:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d1e3f5a7c9"
down_revision: str | None = "a7c9e1f3b5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("compras_gastos_odoo"):
        cols = {c["name"] for c in inspector.get_columns("compras_gastos_odoo")}
        if "fecha_vencimiento" not in cols:
            op.add_column(
                "compras_gastos_odoo",
                sa.Column("fecha_vencimiento", sa.Date(), nullable=True),
            )

    if inspector.has_table("vinculaciones_ventas_op"):
        cols = {c["name"] for c in inspector.get_columns("vinculaciones_ventas_op")}
        if "tasa_binance_aplicada" not in cols:
            op.add_column(
                "vinculaciones_ventas_op",
                sa.Column("tasa_binance_aplicada", sa.Numeric(14, 6), nullable=True),
            )
        if "equiv_usd_binance" not in cols:
            op.add_column(
                "vinculaciones_ventas_op",
                sa.Column("equiv_usd_binance", sa.Numeric(14, 2), nullable=True),
            )

    if inspector.has_table("documentos_operativos"):
        idxs = {i["name"] for i in inspector.get_indexes("documentos_operativos")}
        if "uq_documentos_operativos_numero_recibo" not in idxs:
            # Limpia eventuales colisiones legadas dejando el más antiguo con su número
            # antes de imponer el índice único parcial en producción/staging.
            op.execute(
                sa.text(
                    "UPDATE documentos_operativos d "
                    "SET numero_recibo = '' "
                    "WHERE COALESCE(numero_recibo, '') <> '' "
                    "AND documento_id <> ("
                    "  SELECT MIN(d2.documento_id) FROM documentos_operativos d2 "
                    "  WHERE d2.numero_recibo = d.numero_recibo"
                    ")"
                )
            )
            op.create_index(
                "uq_documentos_operativos_numero_recibo",
                "documentos_operativos",
                ["numero_recibo"],
                unique=True,
                postgresql_where=sa.text("numero_recibo <> ''"),
                sqlite_where=sa.text("numero_recibo <> ''"),
            )

    if not inspector.has_table("cuentas_receptoras_lubrikca"):
        op.create_table(
            "cuentas_receptoras_lubrikca",
            sa.Column("cuenta_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("nombre", sa.String(120), nullable=False, server_default=""),
            sa.Column("tipo_canal", sa.String(32), nullable=False, server_default="banco_ves"),
            sa.Column("banco_codigo_sudeban", sa.String(4), nullable=False, server_default=""),
            sa.Column("banco_nombre", sa.String(120), nullable=False, server_default=""),
            sa.Column("numero_cuenta", sa.String(40), nullable=False, server_default=""),
            sa.Column("numero_cuenta_norm", sa.String(40), nullable=False, server_default=""),
            sa.Column("titular_nombre", sa.String(160), nullable=False, server_default=""),
            sa.Column("titular_rif", sa.String(32), nullable=False, server_default=""),
            sa.Column("titular_rif_norm", sa.String(32), nullable=False, server_default=""),
            sa.Column("telefono", sa.String(32), nullable=False, server_default=""),
            sa.Column("telefono_norm", sa.String(32), nullable=False, server_default=""),
            sa.Column("email_zelle", sa.String(160), nullable=False, server_default=""),
            sa.Column("email_norm", sa.String(160), nullable=False, server_default=""),
            sa.Column("binance_id", sa.String(120), nullable=False, server_default=""),
            sa.Column("moneda", sa.String(10), nullable=False, server_default="VES"),
            sa.Column("diario_odoo_id", sa.Integer(), nullable=True),
            sa.Column("diario_odoo_codigo", sa.String(32), nullable=False, server_default=""),
            sa.Column("diario_odoo_nombre", sa.String(120), nullable=False, server_default=""),
            sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("es_propia", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("notas", sa.Text(), nullable=False, server_default=""),
            sa.Column("creado_por", sa.String(160), nullable=False, server_default="sistema"),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )
        for col in (
            "tipo_canal",
            "banco_codigo_sudeban",
            "numero_cuenta_norm",
            "titular_rif_norm",
            "telefono_norm",
            "email_norm",
            "binance_id",
            "diario_odoo_id",
            "activa",
        ):
            op.create_index(
                f"ix_cuentas_receptoras_lubrikca_{col}",
                "cuentas_receptoras_lubrikca",
                [col],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("cuentas_receptoras_lubrikca"):
        op.drop_table("cuentas_receptoras_lubrikca")

    if inspector.has_table("documentos_operativos"):
        idxs = {i["name"] for i in inspector.get_indexes("documentos_operativos")}
        if "uq_documentos_operativos_numero_recibo" in idxs:
            op.drop_index(
                "uq_documentos_operativos_numero_recibo", table_name="documentos_operativos"
            )

    if inspector.has_table("vinculaciones_ventas_op"):
        cols = {c["name"] for c in inspector.get_columns("vinculaciones_ventas_op")}
        if "equiv_usd_binance" in cols:
            op.drop_column("vinculaciones_ventas_op", "equiv_usd_binance")
        if "tasa_binance_aplicada" in cols:
            op.drop_column("vinculaciones_ventas_op", "tasa_binance_aplicada")

    if inspector.has_table("compras_gastos_odoo"):
        cols = {c["name"] for c in inspector.get_columns("compras_gastos_odoo")}
        if "fecha_vencimiento" in cols:
            op.drop_column("compras_gastos_odoo", "fecha_vencimiento")
