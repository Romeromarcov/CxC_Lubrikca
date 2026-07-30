"""usuarios_plataforma: add salt/activo/fecha_registro

Revision ID: a1b2c3d4e5f6
Revises: 1fc70f5694c1
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '1fc70f5694c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('usuarios_plataforma', sa.Column('salt', sa.String(), nullable=True))
    op.add_column(
        'usuarios_plataforma',
        sa.Column('activo', sa.Boolean(), server_default='true', nullable=False),
    )
    op.add_column(
        'usuarios_plataforma',
        sa.Column('fecha_registro', sa.String(), server_default='', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('usuarios_plataforma', 'fecha_registro')
    op.drop_column('usuarios_plataforma', 'activo')
    op.drop_column('usuarios_plataforma', 'salt')
