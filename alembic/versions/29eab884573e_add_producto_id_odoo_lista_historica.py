"""agrega producto_id_odoo a listas_precios_historicas (crosswalk codigo->Odoo)

Revision ID: 29eab884573e
Revises: f7a8b9c0d1e2
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '29eab884573e'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'listas_precios_historicas',
        sa.Column('producto_id_odoo', sa.Integer(), nullable=True),
    )
    op.create_index(
        'ix_listas_precios_historicas_producto_id_odoo',
        'listas_precios_historicas',
        ['producto_id_odoo'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_listas_precios_historicas_producto_id_odoo',
        table_name='listas_precios_historicas',
    )
    op.drop_column('listas_precios_historicas', 'producto_id_odoo')
