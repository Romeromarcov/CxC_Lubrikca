"""agrega descripcion a las 8 tablas de reglas de descuento

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLAS = [
    'descuentos_pronto_pago',
    'descuentos_volumen',
    'descuento_bcv_completo',
    'promocion_primera_compra',
    'descuentos_recompra',
    'descuentos_producto',
    'descuentos_diferencial_cambiario',
    'reglas_recurrencia',
]


def upgrade() -> None:
    for tabla in TABLAS:
        op.add_column(
            tabla,
            sa.Column('descripcion', sa.Text(), server_default='', nullable=False),
        )


def downgrade() -> None:
    for tabla in TABLAS:
        op.drop_column(tabla, 'descripcion')
