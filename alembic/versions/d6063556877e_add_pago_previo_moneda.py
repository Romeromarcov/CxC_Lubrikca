"""agrega pago_previo_moneda a las tablas de reglas con requiere_pago_previo

Revision ID: d6063556877e
Revises: e5d2c9a7b3f1
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6063556877e'
down_revision: Union[str, None] = 'e5d2c9a7b3f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Cuál teórico (VES/USD) debe estar cubierto por lo pagado para que la
# regla aplique -- "ves" | "usd" | "cualquiera". Ver
# src/cxc/engine/discounts.py:_filtrar_por_pago_previo. Todas las tablas
# arrancan en "cualquiera": mismo comportamiento que hoy (cualquier abono
# cuenta) hasta que alguien la configure distinto desde el formulario.
_TABLES = (
    "descuentos_pronto_pago",
    "descuentos_volumen",
    "promocion_primera_compra",
    "descuentos_recompra",
    "descuentos_producto",
    "descuentos_diferencial_cambiario",
    "reglas_recurrencia",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "pago_previo_moneda",
                sa.String(),
                nullable=False,
                server_default="cualquiera",
            ),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "pago_previo_moneda")
