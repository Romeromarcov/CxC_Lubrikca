"""elimina la tabla legacy descuento_bcv_completo (mecanismo retirado, ver TODO.md)

Revision ID: f1a2b3c4d5e6
Revises: 150e80493482
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '150e80493482'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('descuento_bcv_completo')


def downgrade() -> None:
    op.create_table(
        'descuento_bcv_completo',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('vigencia_desde', sa.Date(), nullable=False, unique=True),
        sa.Column('porcentaje', sa.Numeric(7, 4), nullable=False),
        sa.Column('vigencia_hasta', sa.Date(), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column(
            'requiere_pago_previo', sa.Boolean(), nullable=False, server_default='true'
        ),
        sa.Column('aplica_a', sa.String(), nullable=False, server_default='linea'),
        sa.Column('descripcion', sa.Text(), nullable=False, server_default=''),
    )
