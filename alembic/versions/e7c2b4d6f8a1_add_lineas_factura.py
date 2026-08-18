"""agrega tabla lineas_factura (espejo de account.move.line -- Fase 4/5 del plan de consolidacion de fuentes)

Revision ID: e7c2b4d6f8a1
Revises: d3f8a1c9e2b4
Create Date: 2026-08-17 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c2b4d6f8a1'
down_revision: Union[str, None] = 'd3f8a1c9e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lineas_factura',
        sa.Column('linea_id', sa.String(), primary_key=True),
        sa.Column('factura_id', sa.String(), nullable=False),
        sa.Column('nombre', sa.String(), nullable=False, server_default=''),
        sa.Column('cantidad', sa.Numeric(18, 4), nullable=False, server_default='0'),
        sa.Column('precio_unitario', sa.Numeric(18, 4), nullable=False, server_default='0'),
        sa.Column('descuento', sa.Numeric(18, 4), nullable=False, server_default='0'),
        sa.Column('subtotal', sa.Numeric(18, 4), nullable=False, server_default='0'),
    )
    op.create_index(
        'ix_lineas_factura_factura_id', 'lineas_factura', ['factura_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_lineas_factura_factura_id', table_name='lineas_factura')
    op.drop_table('lineas_factura')
