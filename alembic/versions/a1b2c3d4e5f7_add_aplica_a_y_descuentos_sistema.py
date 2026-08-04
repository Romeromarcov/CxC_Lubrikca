"""agrega aplica_a a reglas de descuento y tabla descuentos_sistema_aprobados

Revision ID: a1b2c3d4e5f7
Revises: f6a7b8c9d0e1
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# "linea" (default, preserva el comportamiento actual del motor) vs
# "subtotal" -- ver src/cxc/engine/discounts.py. Mismas 8 tablas que ya
# tienen requiere_pago_previo (ver e5f6a7b8c9d0_add_requiere_pago_previo.py).
_TABLES = (
    "descuentos_pronto_pago",
    "descuento_bcv_completo",
    "descuentos_diferencial_cambiario",
    "descuentos_volumen",
    "promocion_primera_compra",
    "descuentos_recompra",
    "descuentos_producto",
    "reglas_recurrencia",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "aplica_a",
                sa.String(),
                nullable=False,
                server_default="linea",
            ),
        )

    op.create_table(
        'descuentos_sistema_aprobados',
        sa.Column('so_id', sa.String(), nullable=False),
        sa.Column('monto', sa.Numeric(18, 4), server_default='0', nullable=False),
        sa.Column('motivo', sa.String(), server_default='', nullable=False),
        sa.Column('aprobado_por', sa.String(), server_default='', nullable=False),
        sa.Column('timestamp_aprobacion', sa.DateTime(), nullable=False),
        sa.Column('activo', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(['so_id'], ['ordenes_venta.so_id']),
        sa.PrimaryKeyConstraint('so_id'),
    )


def downgrade() -> None:
    op.drop_table('descuentos_sistema_aprobados')
    for table in _TABLES:
        op.drop_column(table, "aplica_a")
