"""agrega subcategoria/presentacion_odoo a lineas_orden

Revision ID: e440941fae83
Revises: 81abc1bd7672
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e440941fae83'
down_revision: Union[str, None] = '81abc1bd7672'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'lineas_orden',
        sa.Column('subcategoria', sa.String(), server_default='', nullable=False),
    )
    op.add_column(
        'lineas_orden',
        sa.Column('presentacion_odoo', sa.String(), server_default='', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('lineas_orden', 'presentacion_odoo')
    op.drop_column('lineas_orden', 'subcategoria')
