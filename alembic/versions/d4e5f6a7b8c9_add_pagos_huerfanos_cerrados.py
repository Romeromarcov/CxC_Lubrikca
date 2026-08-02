"""agrega pagos_huerfanos_cerrados

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-02 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pagos_huerfanos_cerrados',
        sa.Column('pago_id', sa.String(), nullable=False),
        sa.Column('motivo', sa.String(), server_default='', nullable=False),
        sa.Column('cerrado_por', sa.String(), server_default='', nullable=False),
        sa.Column('timestamp_cierre', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('pago_id'),
    )


def downgrade() -> None:
    op.drop_table('pagos_huerfanos_cerrados')
