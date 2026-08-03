"""agrega equivalentes/teoricos por lista a bandeja_facturacion

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-03 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tarea 3 (rediseño de Ventas) -- columnas nuevas calculadas por el motor
# (ver src/cxc/engine/discounts.py:_teoricos_por_lista), no editables a mano.
_COLUMNS = (
    "equivalente_lista_usd",
    "teorico_lista_ves",
    "teorico_lista_usd",
    "descuentos_teorico_ves",
    "descuentos_teorico_usd",
)


def upgrade() -> None:
    for col in _COLUMNS:
        op.add_column(
            "bandeja_facturacion",
            sa.Column(col, sa.Numeric(18, 4), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for col in _COLUMNS:
        op.drop_column("bandeja_facturacion", col)
