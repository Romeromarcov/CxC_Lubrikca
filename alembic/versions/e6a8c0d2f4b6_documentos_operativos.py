"""Documentos operativos (pagos recibidos, facturas de proveedor, pagos a proveedor y gastos).

Revision ID: e6a8c0d2f4b6
Revises: d5f7a9b1c3e5
Create Date: 2026-11-19 05:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6a8c0d2f4b6"
down_revision: str | None = "d5f7a9b1c3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("documentos_operativos"):
        return

    op.create_table(
        "documentos_operativos",
        sa.Column("documento_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("numero", sa.String, nullable=False, unique=True),
        sa.Column("token_preview", sa.String(64), nullable=False, unique=True),
        sa.Column("tipo", sa.String, nullable=False),
        sa.Column(
            "estado",
            sa.String,
            nullable=False,
            server_default="PENDIENTE_APROBACION",
        ),
        sa.Column("origen", sa.String, nullable=False, server_default="web"),
        sa.Column("captura_id", sa.Integer, nullable=True),
        sa.Column("partner_odoo_id", sa.Integer, nullable=True),
        sa.Column("contraparte_nombre", sa.String, nullable=False, server_default=""),
        sa.Column("rif", sa.String, nullable=False, server_default=""),
        sa.Column("monto", sa.Numeric(18, 4), nullable=True),
        sa.Column("base_imponible", sa.Numeric(18, 4), nullable=True),
        sa.Column("iva", sa.Numeric(18, 4), nullable=True),
        sa.Column("moneda", sa.String, nullable=False, server_default="VES"),
        sa.Column("metodo_pago", sa.String, nullable=False, server_default=""),
        sa.Column("banco", sa.String, nullable=False, server_default=""),
        sa.Column("diario_id", sa.Integer, nullable=True),
        sa.Column("diario_nombre", sa.String, nullable=False, server_default=""),
        sa.Column("cuenta_contable_id", sa.Integer, nullable=True),
        sa.Column("cuenta_contable_codigo", sa.String, nullable=False, server_default=""),
        sa.Column("cuenta_contable_nombre", sa.String, nullable=False, server_default=""),
        sa.Column("referencia", sa.String, nullable=False, server_default=""),
        sa.Column("numero_documento", sa.String, nullable=False, server_default=""),
        sa.Column("numero_control", sa.String, nullable=False, server_default=""),
        sa.Column("pedido_ref", sa.String, nullable=False, server_default=""),
        sa.Column("factura_ref", sa.String, nullable=False, server_default=""),
        sa.Column("orden_ref", sa.String, nullable=False, server_default=""),
        sa.Column("categoria", sa.String, nullable=False, server_default=""),
        sa.Column("concepto", sa.Text, nullable=False, server_default=""),
        sa.Column("sin_factura", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("fecha_documento", sa.Date, nullable=True),
        sa.Column("fecha_vencimiento", sa.Date, nullable=True),
        sa.Column("proveedor_telefono", sa.String, nullable=False, server_default=""),
        sa.Column("proveedor_email", sa.String, nullable=False, server_default=""),
        sa.Column("proveedor_direccion", sa.Text, nullable=False, server_default=""),
        sa.Column("notas", sa.Text, nullable=False, server_default=""),
        sa.Column("validacion_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("faltantes_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("aprobado_por", sa.String, nullable=False, server_default=""),
        sa.Column("aprobado_en", sa.DateTime(timezone=False), nullable=True),
        sa.Column("rechazo_motivo", sa.Text, nullable=False, server_default=""),
        sa.Column("odoo_model", sa.String, nullable=False, server_default=""),
        sa.Column("odoo_res_id", sa.Integer, nullable=True),
        sa.Column("odoo_ref", sa.String, nullable=False, server_default=""),
        sa.Column("error_odoo", sa.Text, nullable=False, server_default=""),
        sa.Column("creado_por", sa.String, nullable=False, server_default="sistema"),
        sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_index("ix_documentos_operativos_tipo", "documentos_operativos", ["tipo"])
    op.create_index("ix_documentos_operativos_estado", "documentos_operativos", ["estado"])
    op.create_index("ix_documentos_operativos_captura_id", "documentos_operativos", ["captura_id"])
    op.create_index(
        "ix_documentos_operativos_token_preview",
        "documentos_operativos",
        ["token_preview"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_documentos_operativos_token_preview", table_name="documentos_operativos")
    op.drop_index("ix_documentos_operativos_captura_id", table_name="documentos_operativos")
    op.drop_index("ix_documentos_operativos_estado", table_name="documentos_operativos")
    op.drop_index("ix_documentos_operativos_tipo", table_name="documentos_operativos")
    op.drop_table("documentos_operativos")
