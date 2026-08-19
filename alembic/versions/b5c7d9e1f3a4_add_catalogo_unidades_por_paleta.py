"""agrega catalogo.unidades_por_paleta (product.packaging 'Paleta' -- Fase B, plan de Inventario/Catalogo)

Revision ID: b5c7d9e1f3a4
Revises: a4e6c8d1f3b5
Create Date: 2026-08-19 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c7d9e1f3a4'
down_revision: Union[str, None] = 'a4e6c8d1f3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'catalogo',
        sa.Column('unidades_por_paleta', sa.Numeric(18, 4), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('catalogo', 'unidades_por_paleta')
