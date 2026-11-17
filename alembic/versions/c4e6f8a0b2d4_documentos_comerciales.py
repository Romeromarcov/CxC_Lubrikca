"""Documentos comerciales de Cotizaciones y Pedidos en CxC.

- ``documentos_comerciales``: encabezado de cada cotización o pedido creado desde el bot de
  Telegram o desde la sección de Ventas en CxC, con vínculo opcional a ``capturas_bot`` y a
  la orden de venta (``sale.order``) generada en Odoo.
- ``documentos_comerciales_lineas``: detalle de líneas vinculado al catálogo real de Odoo.

Revision ID: c4e6f8a0b2d4
Revises: b2d4f6a8c0e2
Create Date: 2026-11-17 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e6f8a0b2d4"
down_revision: str | None = "b2d4f6a8c0e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documentos_comerciales",
        sa.Column("documento_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("numero", sa.String, nullable=False, unique=True),
        sa.Column("tipo", sa.String, nullable=False),
        sa.Column("estado", sa.String, nullable=False, server_default="BORRADOR"),
        sa.Column("origen", sa.String, nullable=False, server_default="web"),
        sa.Column("captura_id", sa.Integer, nullable=True),
        sa.Column("partner_odoo_id", sa.Integer, nullable=True),
        sa.Column("tipo_contacto", sa.String, nullable=False, server_default="prospecto"),
        sa.Column("cliente_nombre", sa.String, nullable=False),
        sa.Column("rif", sa.String, nullable=False, server_default=""),
        sa.Column("email", sa.String, nullable=False, server_default=""),
        sa.Column("telefono", sa.String, nullable=False, server_default=""),
        sa.Column("direccion", sa.Text, nullable=False, server_default=""),
        sa.Column("plus_code", sa.String, nullable=False, server_default=""),
        sa.Column("latitud", sa.Numeric(12, 6), nullable=True),
        sa.Column("longitud", sa.Numeric(12, 6), nullable=True),
        sa.Column("es_primera_compra", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("moneda_lista", sa.String, nullable=False, server_default="USD"),
        sa.Column("vendedor_email", sa.String, nullable=False, server_default=""),
        sa.Column("fecha_entrega", sa.Date, nullable=True),
        sa.Column("condiciones_negociacion", sa.Text, nullable=False, server_default=""),
        sa.Column("observaciones", sa.Text, nullable=False, server_default=""),
        sa.Column("subtotal", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("total_litros", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("odoo_order_id", sa.Integer, nullable=True),
        sa.Column("odoo_order_name", sa.String, nullable=False, server_default=""),
        sa.Column("error_odoo", sa.Text, nullable=False, server_default=""),
        sa.Column("creado_por", sa.String, nullable=False, server_default="sistema"),
        sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_index("ix_documentos_comerciales_tipo", "documentos_comerciales", ["tipo"])
    op.create_index("ix_documentos_comerciales_estado", "documentos_comerciales", ["estado"])
    op.create_index(
        "ix_documentos_comerciales_captura_id", "documentos_comerciales", ["captura_id"]
    )

    op.create_table(
        "documentos_comerciales_lineas",
        sa.Column("linea_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "documento_id",
            sa.Integer,
            sa.ForeignKey("documentos_comerciales.documento_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("orden", sa.Integer, nullable=False, server_default="1"),
        sa.Column("producto_id", sa.String, nullable=False, server_default=""),
        sa.Column("codigo", sa.String, nullable=False, server_default=""),
        sa.Column("descripcion", sa.String, nullable=False),
        sa.Column("presentacion", sa.String, nullable=False, server_default=""),
        sa.Column("litros_unidad", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("cantidad", sa.Numeric(18, 4), nullable=False),
        sa.Column("precio_unitario", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("descuento_pct", sa.Numeric(7, 4), nullable=False, server_default="0"),
        sa.Column("subtotal", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_documentos_comerciales_lineas_documento_id",
        "documentos_comerciales_lineas",
        ["documento_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_documentos_comerciales_lineas_documento_id",
        table_name="documentos_comerciales_lineas",
    )
    op.drop_table("documentos_comerciales_lineas")
    op.drop_index("ix_documentos_comerciales_captura_id", table_name="documentos_comerciales")
    op.drop_index("ix_documentos_comerciales_estado", table_name="documentos_comerciales")
    op.drop_index("ix_documentos_comerciales_tipo", table_name="documentos_comerciales")
    op.drop_table("documentos_comerciales")
