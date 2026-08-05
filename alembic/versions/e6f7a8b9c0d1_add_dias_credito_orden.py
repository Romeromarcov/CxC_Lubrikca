"""agrega dias_credito a ordenes_venta (plazo real otorgado por Odoo)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ordenes_venta',
        sa.Column('dias_credito', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('ordenes_venta', 'dias_credito')
