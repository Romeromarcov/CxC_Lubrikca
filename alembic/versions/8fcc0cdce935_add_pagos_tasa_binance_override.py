"""agrega tabla pagos_tasa_binance_override (correccion manual de tasa Binance para pagos pendientes)

Revision ID: 8fcc0cdce935
Revises: 29eab884573e
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8fcc0cdce935'
down_revision: Union[str, None] = '29eab884573e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pagos_tasa_binance_override',
        sa.Column('pago_id', sa.String(), primary_key=True),
        sa.Column('tasa_binance', sa.Numeric(18, 4), nullable=False),
        sa.Column('editado_por', sa.String(), nullable=False, server_default=''),
        sa.Column('timestamp_edicion', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('pagos_tasa_binance_override')
