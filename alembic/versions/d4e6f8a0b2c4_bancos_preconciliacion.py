"""Bancos, fase 2: propuestas de pre-conciliación y bitácora de decisiones.

- ``bancos_propuestas``: una fila por movimiento con lo que proponen las reglas
  (un pago de Odoo, una cuenta, ambiguo o nada) y su estado de revisión.
- ``bancos_decisiones``: cada aprobación, rechazo o corrección, con quién y cuándo.
  Solo-append (trigger), como ``bancos_archivos``: es el log de auditoría de quién
  aprobó qué.

Revision ID: d4e6f8a0b2c4
Revises: c3d5e7f9a1b2
Create Date: 2026-11-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e6f8a0b2c4"
down_revision: Union[str, None] = "c3d5e7f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bancos_propuestas",
        sa.Column(
            "huella",
            sa.String(24),
            sa.ForeignKey("bancos_movimientos.huella"),
            primary_key=True,
        ),
        sa.Column("tipo", sa.String, nullable=False),
        sa.Column("regla", sa.String, nullable=False),
        sa.Column("motivo", sa.Text, nullable=False, server_default=""),
        sa.Column("pago_id", sa.Integer, nullable=True),
        sa.Column("pago_nombre", sa.String, nullable=False, server_default=""),
        sa.Column("cuenta", sa.String, nullable=False, server_default=""),
        sa.Column("puntaje", sa.Integer, nullable=False, server_default="0"),
        sa.Column("candidatos", sa.Text, nullable=False, server_default="[]"),
        sa.Column("generada_en", sa.DateTime(timezone=False), nullable=False),
        sa.Column("estado", sa.String, nullable=False, server_default="pendiente", index=True),
        sa.Column("decidida_por", sa.String, nullable=False, server_default=""),
        sa.Column("decidida_en", sa.DateTime(timezone=False), nullable=True),
    )
    op.create_table(
        "bancos_decisiones",
        sa.Column("decision_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "huella",
            sa.String(24),
            sa.ForeignKey("bancos_movimientos.huella"),
            nullable=False,
            index=True,
        ),
        sa.Column("accion", sa.String, nullable=False),
        sa.Column("antes", sa.Text, nullable=False),
        sa.Column("despues", sa.Text, nullable=False),
        sa.Column("comentario", sa.Text, nullable=False, server_default=""),
        sa.Column("usuario", sa.String, nullable=False),
        sa.Column("decidida_en", sa.DateTime(timezone=False), nullable=False),
    )
    op.execute(
        """
        CREATE FUNCTION bancos_decisiones_inmutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'bancos_decisiones es solo-append: ' || TG_OP || ' rechazado';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_bancos_decisiones_inmutable
        BEFORE UPDATE OR DELETE ON bancos_decisiones
        FOR EACH ROW EXECUTE FUNCTION bancos_decisiones_inmutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_bancos_decisiones_inmutable ON bancos_decisiones")
    op.execute("DROP FUNCTION IF EXISTS bancos_decisiones_inmutable()")
    op.drop_table("bancos_decisiones")
    op.drop_table("bancos_propuestas")
