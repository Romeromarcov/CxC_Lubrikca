"""agrega tabla reglas_dias_credito_volumen (config, alerta de dias de credito)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'reglas_dias_credito_volumen',
        sa.Column('regla_id', sa.String(), nullable=False),
        sa.Column('litros_minimo', sa.Numeric(18, 4), server_default='0', nullable=False),
        sa.Column('litros_maximo', sa.Numeric(18, 4), nullable=True),
        sa.Column('dias_credito_max', sa.Integer(), nullable=False),
        sa.Column('descripcion', sa.Text(), server_default='', nullable=False),
        sa.Column('activo', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.PrimaryKeyConstraint('regla_id'),
    )
    reglas_dias_credito_volumen = sa.table(
        'reglas_dias_credito_volumen',
        sa.column('regla_id', sa.String),
        sa.column('litros_minimo', sa.Numeric),
        sa.column('litros_maximo', sa.Numeric),
        sa.column('dias_credito_max', sa.Integer),
        sa.column('descripcion', sa.Text),
    )
    op.bulk_insert(
        reglas_dias_credito_volumen,
        [
            {
                'regla_id': 'CREDITO_0_500L',
                'litros_minimo': 0,
                'litros_maximo': 500,
                'dias_credito_max': 21,
                'descripcion': 'De 0 a 500 Lts: máximo 21 días de crédito.',
            },
            {
                'regla_id': 'CREDITO_501L_2500L',
                'litros_minimo': 501,
                'litros_maximo': 2500,
                'dias_credito_max': 30,
                'descripcion': 'De 501 a 2500 Lts: máximo 30 días de crédito.',
            },
            {
                'regla_id': 'CREDITO_NEGOCIACION_ESPECIAL',
                'litros_minimo': 2501,
                'litros_maximo': None,
                'dias_credito_max': 45,
                'descripcion': (
                    'Negociaciones especiales, mayores a 2500 Lts: '
                    'máximo 45 días de crédito.'
                ),
            },
        ],
    )


def downgrade() -> None:
    op.drop_table('reglas_dias_credito_volumen')
