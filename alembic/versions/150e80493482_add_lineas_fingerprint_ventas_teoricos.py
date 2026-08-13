"""agrega lineas_fingerprint a ventas_teoricos

Revision ID: 150e80493482
Revises: e440941fae83
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '150e80493482'
down_revision: Union[str, None] = 'e440941fae83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ventas_teoricos',
        sa.Column('lineas_fingerprint', sa.String(), server_default='', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('ventas_teoricos', 'lineas_fingerprint')
