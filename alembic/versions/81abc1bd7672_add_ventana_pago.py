"""reemplaza dias_gracia/max_usos_mes/dias_ventana por ventana_pago_tipo+dias

Revision ID: 81abc1bd7672
Revises: 8fcc0cdce935
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '81abc1bd7672'
down_revision: Union[str, None] = '8fcc0cdce935'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'descuentos_pronto_pago',
        sa.Column('ventana_pago_tipo', sa.String(), server_default='vencimiento', nullable=False),
    )
    op.add_column(
        'descuentos_pronto_pago',
        sa.Column('ventana_pago_dias', sa.Integer(), server_default='3', nullable=False),
    )
    op.execute(
        "UPDATE descuentos_pronto_pago SET ventana_pago_dias = dias_gracia "
        "WHERE dias_gracia IS NOT NULL"
    )
    op.drop_column('descuentos_pronto_pago', 'dias_gracia')

    op.add_column(
        'descuentos_recompra',
        sa.Column('ventana_pago_tipo', sa.String(), server_default='vencimiento', nullable=False),
    )
    op.add_column(
        'descuentos_recompra',
        sa.Column('ventana_pago_dias', sa.Integer(), server_default='3', nullable=False),
    )
    op.execute(
        "UPDATE descuentos_recompra SET ventana_pago_dias = dias_gracia "
        "WHERE dias_gracia IS NOT NULL"
    )
    op.drop_column('descuentos_recompra', 'dias_gracia')
    op.drop_column('descuentos_recompra', 'max_usos_mes')
    op.drop_column('descuentos_recompra', 'dias_ventana')


def downgrade() -> None:
    op.add_column(
        'descuentos_recompra',
        sa.Column('dias_ventana', sa.Integer(), server_default='30', nullable=False),
    )
    op.add_column(
        'descuentos_recompra',
        sa.Column('max_usos_mes', sa.Integer(), server_default='1', nullable=False),
    )
    op.add_column(
        'descuentos_recompra',
        sa.Column('dias_gracia', sa.Integer(), server_default='3', nullable=False),
    )
    op.execute(
        "UPDATE descuentos_recompra SET dias_gracia = ventana_pago_dias"
    )
    op.drop_column('descuentos_recompra', 'ventana_pago_dias')
    op.drop_column('descuentos_recompra', 'ventana_pago_tipo')

    op.add_column(
        'descuentos_pronto_pago',
        sa.Column('dias_gracia', sa.Integer(), server_default='3', nullable=False),
    )
    op.execute(
        "UPDATE descuentos_pronto_pago SET dias_gracia = ventana_pago_dias"
    )
    op.drop_column('descuentos_pronto_pago', 'ventana_pago_dias')
    op.drop_column('descuentos_pronto_pago', 'ventana_pago_tipo')
