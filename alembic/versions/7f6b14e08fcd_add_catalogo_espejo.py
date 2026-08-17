"""agrega tabla catalogo (espejo de product.product -- Fase 0 del plan de consolidacion de fuentes)

Revision ID: 7f6b14e08fcd
Revises: b80f7bbb6644
Create Date: 2026-08-17 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f6b14e08fcd'
down_revision: Union[str, None] = 'b80f7bbb6644'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'catalogo',
        sa.Column('producto_id', sa.String(), primary_key=True),
        sa.Column('codigo', sa.String(), nullable=False, server_default=''),
        sa.Column('nombre', sa.String(), nullable=False, server_default=''),
        sa.Column('marca', sa.String(), nullable=False, server_default=''),
        sa.Column('volumen', sa.Numeric(18, 4), nullable=False, server_default='0'),
        sa.Column('peso', sa.Numeric(18, 4), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_table('catalogo')
