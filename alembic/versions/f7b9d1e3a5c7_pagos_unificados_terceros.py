"""Pagos unificados, recibos CxC y cuentas Zelle de terceros.

Revision ID: f7b9d1e3a5c7
Revises: e6a8c0d2f4b6
Create Date: 2026-11-19 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7b9d1e3a5c7"
down_revision: str | None = "e6a8c0d2f4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _cols_existentes(inspector: sa.Inspector, tabla: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(tabla)}


def _indices_existentes(inspector: sa.Inspector, tabla: str) -> set[str]:
    return {ix["name"] for ix in inspector.get_indexes(tabla) if ix.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("cuentas_terceros"):
        op.create_table(
            "cuentas_terceros",
            sa.Column("cuenta_id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("alias", sa.String, nullable=False, unique=True),
            sa.Column("titular", sa.String, nullable=False, server_default=""),
            sa.Column("correo_telefono", sa.String, nullable=False, server_default=""),
            sa.Column("proveedor_odoo_id", sa.Integer, nullable=False),
            sa.Column("proveedor_nombre", sa.String, nullable=False, server_default=""),
            sa.Column("diario_odoo_id", sa.Integer, nullable=True),
            sa.Column("diario_odoo_nombre", sa.String, nullable=False, server_default=""),
            sa.Column("cuenta_cxp_override_id", sa.Integer, nullable=True),
            sa.Column("cuenta_cxc_override_id", sa.Integer, nullable=True),
            sa.Column("moneda", sa.String, nullable=False, server_default="USD"),
            sa.Column("notas", sa.Text, nullable=False, server_default=""),
            sa.Column("activo", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("creado_por", sa.String, nullable=False, server_default="sistema"),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index(
            "ix_cuentas_terceros_proveedor_odoo_id",
            "cuentas_terceros",
            ["proveedor_odoo_id"],
        )

    if not inspector.has_table("pagos_proveedores_odoo"):
        op.create_table(
            "pagos_proveedores_odoo",
            sa.Column("pago_id", sa.String, primary_key=True),
            sa.Column("numero_odoo", sa.String, nullable=False, server_default=""),
            sa.Column("partner_odoo_id", sa.Integer, nullable=True),
            sa.Column("proveedor_nombre", sa.String, nullable=False, server_default=""),
            sa.Column("rif", sa.String, nullable=False, server_default=""),
            sa.Column("monto", sa.Numeric(18, 4), nullable=False),
            sa.Column("moneda", sa.String, nullable=False, server_default="USD"),
            sa.Column("diario_id", sa.Integer, nullable=True),
            sa.Column("diario_nombre", sa.String, nullable=False, server_default=""),
            sa.Column("referencia", sa.String, nullable=False, server_default=""),
            sa.Column("fecha_pago", sa.Date, nullable=False),
            sa.Column("estado_odoo", sa.String, nullable=False, server_default="posted"),
            sa.Column("reconciliado", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("move_id", sa.Integer, nullable=True),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index(
            "ix_pagos_proveedores_odoo_partner_odoo_id",
            "pagos_proveedores_odoo",
            ["partner_odoo_id"],
        )
        op.create_index(
            "ix_pagos_proveedores_odoo_fecha_pago",
            "pagos_proveedores_odoo",
            ["fecha_pago"],
        )

    if inspector.has_table("documentos_operativos"):
        cols_op = _cols_existentes(inspector, "documentos_operativos")
        idxs_op = _indices_existentes(inspector, "documentos_operativos")
        nuevas_op: list[sa.Column[sa.Any]] = [
            sa.Column("es_zelle_tercero", sa.Boolean, nullable=False, server_default="false"),
            sa.Column(
                "cuenta_tercero_id",
                sa.Integer,
                sa.ForeignKey("cuentas_terceros.cuenta_id"),
                nullable=True,
            ),
            sa.Column("beneficiario_tercero", sa.String, nullable=False, server_default=""),
            sa.Column("confirmado_cxc", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("confirmado_cxc_por", sa.String, nullable=False, server_default=""),
            sa.Column("confirmado_cxc_en", sa.DateTime(timezone=False), nullable=True),
            sa.Column("numero_recibo", sa.String, nullable=False, server_default=""),
            sa.Column("recibo_emitido_en", sa.DateTime(timezone=False), nullable=True),
        ]
        for col in nuevas_op:
            if col.name not in cols_op:
                op.add_column("documentos_operativos", col)
        if "ix_documentos_operativos_cuenta_tercero_id" not in idxs_op:
            op.create_index(
                "ix_documentos_operativos_cuenta_tercero_id",
                "documentos_operativos",
                ["cuenta_tercero_id"],
            )
        if "ix_documentos_operativos_numero_recibo" not in idxs_op:
            op.create_index(
                "ix_documentos_operativos_numero_recibo",
                "documentos_operativos",
                ["numero_recibo"],
            )

    if inspector.has_table("pagos"):
        cols_pagos = _cols_existentes(inspector, "pagos")
        for col in (
            sa.Column("numero_odoo", sa.String, nullable=False, server_default=""),
            sa.Column("referencia", sa.String, nullable=False, server_default=""),
        ):
            if col.name not in cols_pagos:
                op.add_column("pagos", col)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("pagos"):
        cols_pagos = _cols_existentes(inspector, "pagos")
        for nombre in ("referencia", "numero_odoo"):
            if nombre in cols_pagos:
                op.drop_column("pagos", nombre)

    if inspector.has_table("documentos_operativos"):
        idxs_op = _indices_existentes(inspector, "documentos_operativos")
        for ix in (
            "ix_documentos_operativos_numero_recibo",
            "ix_documentos_operativos_cuenta_tercero_id",
        ):
            if ix in idxs_op:
                op.drop_index(ix, table_name="documentos_operativos")
        cols_op = _cols_existentes(inspector, "documentos_operativos")
        for nombre in (
            "recibo_emitido_en",
            "numero_recibo",
            "confirmado_cxc_en",
            "confirmado_cxc_por",
            "confirmado_cxc",
            "beneficiario_tercero",
            "cuenta_tercero_id",
            "es_zelle_tercero",
        ):
            if nombre in cols_op:
                op.drop_column("documentos_operativos", nombre)

    if inspector.has_table("pagos_proveedores_odoo"):
        op.drop_index(
            "ix_pagos_proveedores_odoo_fecha_pago",
            table_name="pagos_proveedores_odoo",
        )
        op.drop_index(
            "ix_pagos_proveedores_odoo_partner_odoo_id",
            table_name="pagos_proveedores_odoo",
        )
        op.drop_table("pagos_proveedores_odoo")

    if inspector.has_table("cuentas_terceros"):
        op.drop_index("ix_cuentas_terceros_proveedor_odoo_id", table_name="cuentas_terceros")
        op.drop_table("cuentas_terceros")
