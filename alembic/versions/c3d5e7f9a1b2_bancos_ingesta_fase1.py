"""Ingesta bancaria, fase 1: archivos crudos inmutables y movimientos canónicos.

- ``bancos_archivos``: cada estado de cuenta tal cual llegó (bytes + sha256), con el
  resultado de validar su cadena de saldos. Solo-append: un trigger rechaza UPDATE y
  DELETE, porque es la evidencia contra la que se audita todo lo demás.
- ``bancos_movimientos``: el modelo canónico, una fila por movimiento real,
  deduplicado por ``huella``. ``unique_import_id`` es la clave de idempotencia que
  viaja a Odoo en ``account.bank.statement.line``.

Revision ID: c3d5e7f9a1b2
Revises: b8d4f6a2c0e3
Create Date: 2026-11-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, None] = "b8d4f6a2c0e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MONEY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.create_table(
        "bancos_archivos",
        sa.Column("archivo_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("banco", sa.String, nullable=False, index=True),
        sa.Column("cuenta", sa.String, nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("nombre_archivo", sa.String, nullable=False),
        sa.Column("contenido", sa.LargeBinary, nullable=False),
        sa.Column("cargado_en", sa.DateTime(timezone=False), nullable=False),
        sa.Column("cargado_por", sa.String, nullable=False, server_default=""),
        sa.Column("n_movimientos", sa.Integer, nullable=False),
        sa.Column("n_nuevos", sa.Integer, nullable=False),
        sa.Column("fecha_desde", sa.Date, nullable=True),
        sa.Column("fecha_hasta", sa.Date, nullable=True),
        sa.Column("saldo_inicial", MONEY, nullable=True),
        sa.Column("saldo_final", MONEY, nullable=True),
        sa.Column("saldo_inicial_derivado", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("cuadra", sa.Boolean, nullable=False),
        sa.Column("observaciones", sa.Text, nullable=False, server_default=""),
    )
    op.create_table(
        "bancos_movimientos",
        sa.Column("huella", sa.String(24), primary_key=True),
        sa.Column("unique_import_id", sa.String, nullable=False, unique=True),
        sa.Column("banco", sa.String, nullable=False),
        sa.Column("cuenta", sa.String, nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("referencia", sa.String, nullable=False, server_default=""),
        sa.Column("correlativo", sa.String, nullable=False, server_default=""),
        sa.Column("descripcion", sa.Text, nullable=False, server_default=""),
        sa.Column("codigo", sa.String, nullable=False, server_default=""),
        sa.Column("debito", MONEY, nullable=False),
        sa.Column("credito", MONEY, nullable=False),
        sa.Column("saldo", MONEY, nullable=True),
        sa.Column("orden", sa.Integer, nullable=False),
        sa.Column("categoria", sa.String, nullable=False),
        sa.Column("regla", sa.String, nullable=False),
        sa.Column(
            "archivo_id",
            sa.Integer,
            sa.ForeignKey("bancos_archivos.archivo_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
        sa.CheckConstraint("debito >= 0", name="ck_bmov_debito_no_negativo"),
        sa.CheckConstraint("credito >= 0", name="ck_bmov_credito_no_negativo"),
    )
    op.create_index("ix_bmov_banco_fecha", "bancos_movimientos", ["banco", "fecha"])
    # El mensaje se arma concatenando, sin el placeholder de RAISE: el driver
    # tomaria el signo de porcentaje como un parametro de la consulta.
    op.execute(
        """
        CREATE FUNCTION bancos_archivos_inmutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'bancos_archivos es solo-append: ' || TG_OP || ' rechazado';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_bancos_archivos_inmutable
        BEFORE UPDATE OR DELETE ON bancos_archivos
        FOR EACH ROW EXECUTE FUNCTION bancos_archivos_inmutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_bancos_archivos_inmutable ON bancos_archivos")
    op.execute("DROP FUNCTION IF EXISTS bancos_archivos_inmutable()")
    op.drop_index("ix_bmov_banco_fecha", table_name="bancos_movimientos")
    op.drop_table("bancos_movimientos")
    op.drop_table("bancos_archivos")
