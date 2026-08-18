"""agrega producto_id a lineas_factura (Fase 4/5 del plan de consolidacion de fuentes)

Revision ID: f9a3d5e7c1b2
Revises: e7c2b4d6f8a1
Create Date: 2026-08-17 22:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9a3d5e7c1b2'
down_revision: Union[str, None] = 'e7c2b4d6f8a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'lineas_factura',
        sa.Column('producto_id', sa.String(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_column('lineas_factura', 'producto_id')
