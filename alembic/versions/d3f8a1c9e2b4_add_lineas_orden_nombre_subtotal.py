"""agrega nombre/subtotal a lineas_orden (Fase 4/5 del plan de consolidacion de fuentes)

Revision ID: d3f8a1c9e2b4
Revises: c2a4f9d81b7e
Create Date: 2026-08-17 22:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f8a1c9e2b4'
down_revision: Union[str, None] = 'c2a4f9d81b7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'lineas_orden',
        sa.Column('nombre', sa.String(), nullable=False, server_default=''),
    )
    op.add_column(
        'lineas_orden',
        sa.Column('subtotal', sa.Numeric(18, 4), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('lineas_orden', 'subtotal')
    op.drop_column('lineas_orden', 'nombre')
