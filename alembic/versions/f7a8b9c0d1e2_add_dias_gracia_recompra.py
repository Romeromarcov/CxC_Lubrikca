"""agrega dias_gracia a descuentos_recompra (ventana de recompra)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'descuentos_recompra',
        sa.Column('dias_gracia', sa.Integer(), server_default='3', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('descuentos_recompra', 'dias_gracia')
