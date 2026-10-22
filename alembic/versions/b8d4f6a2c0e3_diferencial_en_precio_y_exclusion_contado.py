"""El diferencial cambiario ya metido en el precio, y su par con el contado en VES.

Caso que lo motivo (S01226, 22-oct-2026): la linea llevaba 31,45 % de descuento puesto a mano
para llevar el monto en VES a la tasa Binance, y el motor ademas le calculaba el 20 % de pronto
pago. Si aplica el diferencial no aplica el contado.

- ``bandeja_facturacion.diferencial_en_precio``: monto del diferencial que la orden ya tiene en
  el precio de sus lineas. No entra al total de descuentos (ya esta en la base); solo sirve para
  que la validacion contra Odoo lo reconozca.
- Siembra el par contado_ves <-> bcv_completo en ``exclusiones``: con el diferencial el pago en VES
  se evalua a tasa Binance, o sea como pago en USD. Entran las reglas de contado en USD (8, 6, 3 %)
  y salen las del camino BCV en VES (20 % y 15 %). Visible y editable en Configuracion.

Revision ID: b8d4f6a2c0e3
Revises: a7c3e9b1d2f4
Create Date: 2026-10-22 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8d4f6a2c0e3"
down_revision: Union[str, None] = "a7c3e9b1d2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bandeja_facturacion",
        sa.Column(
            "diferencial_en_precio",
            sa.Numeric(18, 4),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        """
        INSERT INTO exclusiones (regla_tipo_a, regla_tipo_b, activo)
        SELECT 'contado_ves', 'bcv_completo', true
        WHERE NOT EXISTS (
            SELECT 1 FROM exclusiones
            WHERE (regla_tipo_a = 'contado_ves' AND regla_tipo_b = 'bcv_completo')
               OR (regla_tipo_a = 'bcv_completo' AND regla_tipo_b = 'contado_ves')
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM exclusiones WHERE regla_tipo_a = 'contado_ves' AND regla_tipo_b = 'bcv_completo'"
    )
    op.drop_column("bandeja_facturacion", "diferencial_en_precio")
