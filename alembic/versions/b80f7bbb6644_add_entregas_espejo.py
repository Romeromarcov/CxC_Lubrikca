"""agrega tabla entregas (espejo inmutable de stock.picking -- Fase 0 del plan de consolidacion de fuentes)

Revision ID: b80f7bbb6644
Revises: b28cfe376b63
Create Date: 2026-08-17 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b80f7bbb6644'
down_revision: Union[str, None] = 'b28cfe376b63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'entregas',
        sa.Column('entrega_id', sa.String(), primary_key=True),
        sa.Column('so_id', sa.String(), nullable=True),
        sa.Column('tipo', sa.String(), nullable=False, server_default='outgoing'),
        sa.Column('fecha', sa.Date(), nullable=True),
        sa.Column('estado', sa.String(), nullable=False, server_default='draft'),
        sa.Column('es_devolucion', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('entrega_origen_id', sa.String(), nullable=True),
    )
    op.create_index('ix_entregas_so_id', 'entregas', ['so_id'])


def downgrade() -> None:
    op.drop_index('ix_entregas_so_id', table_name='entregas')
    op.drop_table('entregas')
