"""agrega pago_id a bandeja_auditoria (traza reasignado_por_odoo, dedup del resync)

Revision ID: d1e2f3a4b5c6
Revises: c9d8e7f6a5b4
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c9d8e7f6a5b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bandeja_auditoria', sa.Column('pago_id', sa.String(), nullable=True))
    op.create_index(
        'ix_bandeja_auditoria_pago_id', 'bandeja_auditoria', ['pago_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_bandeja_auditoria_pago_id', table_name='bandeja_auditoria')
    op.drop_column('bandeja_auditoria', 'pago_id')
