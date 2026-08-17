"""agrega monto_total_signed_usd/monto_sin_impuestos_signed_usd a facturas (Fase 0 del plan de consolidacion de fuentes)

Revision ID: c2a4f9d81b7e
Revises: 7f6b14e08fcd
Create Date: 2026-08-17 05:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2a4f9d81b7e'
down_revision: Union[str, None] = '7f6b14e08fcd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'facturas',
        sa.Column('monto_total_signed_usd', sa.Numeric(18, 4), nullable=False, server_default='0'),
    )
    op.add_column(
        'facturas',
        sa.Column(
            'monto_sin_impuestos_signed_usd', sa.Numeric(18, 4), nullable=False, server_default='0'
        ),
    )


def downgrade() -> None:
    op.drop_column('facturas', 'monto_sin_impuestos_signed_usd')
    op.drop_column('facturas', 'monto_total_signed_usd')
