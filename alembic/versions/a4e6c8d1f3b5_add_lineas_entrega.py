"""agrega tabla lineas_entrega (espejo de stock.move.line -- Fase 5 del plan de consolidacion de fuentes)

Revision ID: a4e6c8d1f3b5
Revises: f9a3d5e7c1b2
Create Date: 2026-08-18 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4e6c8d1f3b5'
down_revision: Union[str, None] = 'f9a3d5e7c1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lineas_entrega',
        sa.Column('linea_id', sa.String(), primary_key=True),
        sa.Column('entrega_id', sa.String(), nullable=False),
        sa.Column('producto_id', sa.String(), nullable=False, server_default=''),
    )
    op.create_index(
        'ix_lineas_entrega_entrega_id', 'lineas_entrega', ['entrega_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_lineas_entrega_entrega_id', table_name='lineas_entrega')
    op.drop_table('lineas_entrega')
