"""agrega requiere_pago_previo a las tablas de reglas de descuento

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Clasificación de si una regla requiere al menos un pago (Vinculacion) ya
# asociado a la orden/factura para poder aplicarse. Ver
# src/cxc/engine/discounts.py:_filtrar_por_pago_previo.
_TABLES_DEFAULT_TRUE = (
    "descuentos_pronto_pago",  # pronto pago -- se calcula sobre el abono
    "descuento_bcv_completo",  # diferencial cambiario -- se calcula por abono
    "descuentos_diferencial_cambiario",  # diferencial cambiario (config, no vive en el motor aún)
)
_TABLES_DEFAULT_FALSE = (
    "descuentos_volumen",
    "promocion_primera_compra",
    "descuentos_recompra",
    "descuentos_producto",
    "reglas_recurrencia",
)


def upgrade() -> None:
    for table in _TABLES_DEFAULT_TRUE:
        op.add_column(
            table,
            sa.Column(
                "requiere_pago_previo",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )
    for table in _TABLES_DEFAULT_FALSE:
        op.add_column(
            table,
            sa.Column(
                "requiere_pago_previo",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    for table in _TABLES_DEFAULT_TRUE + _TABLES_DEFAULT_FALSE:
        op.drop_column(table, "requiere_pago_previo")
