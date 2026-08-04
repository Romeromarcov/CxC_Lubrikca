"""agrega tabla ventas_teoricos (Fase 10 -- teoricos fuera de Bandeja)

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f7
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ventas_teoricos',
        sa.Column('so_id', sa.String(), nullable=False),
        sa.Column('teorico_ves', sa.Numeric(18, 4), server_default='0', nullable=False),
        sa.Column('teorico_usd', sa.Numeric(18, 4), server_default='0', nullable=False),
        sa.Column('descuentos_teorico_ves', sa.Numeric(18, 4), server_default='0', nullable=False),
        sa.Column('descuentos_teorico_usd', sa.Numeric(18, 4), server_default='0', nullable=False),
        sa.Column('lista_ves_id', sa.String(), server_default='', nullable=False),
        sa.Column('lista_usd_id', sa.String(), server_default='', nullable=False),
        sa.Column('usa_fallback_ves', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('usa_fallback_usd', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('calculado_en', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['so_id'], ['ordenes_venta.so_id']),
        sa.PrimaryKeyConstraint('so_id'),
    )


def downgrade() -> None:
    op.drop_table('ventas_teoricos')
