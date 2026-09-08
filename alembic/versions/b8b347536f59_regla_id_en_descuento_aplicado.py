"""Guarda QUE REGLA produjo cada descuento, con su porcentaje y su base.

Sin esto no se puede auditar el motor. Lo pidio el usuario (septiembre
2026) al revisar por que TERA recibia un descuento de volumen que nadie le
otorgo: el detalle solo guardaba el ``origen`` ("volumen") y, con cinco
reglas de volumen activas en produccion, era imposible decir cual lo
produjo sin recalcular a mano.

``componentes`` cubre el caso de un descuento que suma VARIAS reglas --
volumen y producto apilan una regla por linea o por subtotal -- con el
aporte de cada una en monto y en porcentaje.

Revision ID: b8b347536f59
Revises: 1696f7320bfe
Create Date: 2026-09-08 07:01:02.434635

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8b347536f59'
down_revision: Union[str, None] = '1696f7320bfe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "descuento_aplicado",
        sa.Column("regla_id", sa.String(), nullable=False, server_default=""),
    )
    op.add_column("descuento_aplicado", sa.Column("porcentaje", sa.Numeric(18, 4), nullable=True))
    op.add_column("descuento_aplicado", sa.Column("base", sa.Numeric(18, 4), nullable=True))
    op.add_column(
        "descuento_aplicado",
        sa.Column("componentes", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("descuento_aplicado", "componentes")
    op.drop_column("descuento_aplicado", "base")
    op.drop_column("descuento_aplicado", "porcentaje")
    op.drop_column("descuento_aplicado", "regla_id")
