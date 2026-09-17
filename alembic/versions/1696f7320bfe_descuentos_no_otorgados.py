"""Registra las ordenes cuyo descuento NO se le prometio al cliente.

Decision del usuario (septiembre 2026): el descuento que calcula el motor
se asume COMPROMETIDO por defecto -- "casi siempre los vendedores dan el
descuento al cliente" -- y baja la cuenta por cobrar aunque administracion
todavia no haya emitido la nota de credito. Sin eso la CxC queda inflada
por un tramite administrativo: medido contra produccion, $14.482,08 en 154
ordenes, el 5,8% de los $251.380,75 que se persiguen.

Esta tabla guarda las EXCEPCIONES. La que lo motivo es TERA: el motor le
calcula $3.949,79 entre S00010 y S00584, pero "a ellos no se les dio ese
descuento, pagaron completo y ya".

Existe porque el usuario no permite que los vendedores toquen precios ni
descuentos en Odoo (hay desajustes historicos, intencionales o no), asi
que el descuento vive en el motor hasta que se emita la NC.

Revision ID: 1696f7320bfe
Revises: d4f5a6b7c8e9
Create Date: 2026-09-08 05:41:57.025291

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1696f7320bfe'
down_revision: Union[str, None] = 'd4f5a6b7c8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "descuentos_no_otorgados",
        sa.Column("so_id", sa.String(), primary_key=True),
        sa.Column("motivo", sa.Text(), nullable=False, server_default=""),
        sa.Column("marcado_por", sa.String(), nullable=False, server_default=""),
        sa.Column("timestamp_marcado", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("descuentos_no_otorgados")
