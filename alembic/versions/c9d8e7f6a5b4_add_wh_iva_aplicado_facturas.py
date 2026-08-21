"""agrega wh_iva_aplicado a facturas (Fase 3 del plan de arquitectura de pagos)

Revision ID: c9d8e7f6a5b4
Revises: b5c7d9e1f3a4
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d8e7f6a5b4'
down_revision: Union[str, None] = 'b5c7d9e1f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'facturas',
        sa.Column('wh_iva_aplicado', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    op.drop_column('facturas', 'wh_iva_aplicado')
