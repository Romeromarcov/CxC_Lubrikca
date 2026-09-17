"""unifica los rangos de cantidad de las reglas en min_unidades/max_unidades

Revision ID: a1b2c3d4e5f6
Revises: d1e2f3a4b5c6
Create Date: 2026-09-07 00:00:00.000000

Auditoría de reglas pedida por el usuario (septiembre 2026). Tres problemas
distintos, una sola migración.

1. CAMPOS DUPLICADOS. ``descuentos_recompra`` tenía DOS pares para el mismo
   concepto: ``min_cajas``/``max_cajas`` (enteros, los que el motor lee) y
   ``min_cantidad``/``max_cantidad`` (decimales, los que la vista
   consolidada de reglas muestra). Divergieron en producción:
   REC_GLOBAL_TRAMO2 tenía cajas 5..999999 -- el tramo real -- y cantidad
   2..4, así que la pantalla mostraba un tramo que no era el que se
   aplicaba. Se conserva el valor de ``cajas`` porque es el que el motor
   venía usando.

2. EL NOMBRE. "cajas" quedó chico: el desplegable de la regla ofrece
   Unidades / Litros / USD, y un entero no puede expresar litros ni dólares
   con decimales. Pasa a ``min_unidades``/``max_unidades`` NUMERIC.

3. CAMPOS MUERTOS. ``min_cantidad``/``max_cantidad`` existían también en
   pronto pago, primera compra y diferencial cambiario, donde el motor
   NUNCA los lee -- el formulario dejaba configurar un rango que no surtía
   ningún efecto. Se eliminan de esas tres. Primera compra conserva
   ``compra_minima``, que es su criterio real y el que el usuario confirmó
   ("gana la regla de compra mínima más alta que se cumpla").

``descuentos_producto`` y ``descuentos_volumen`` conservan y renombran su
par: producto lo va a necesitar cuando se use, y volumen ya lo honra.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9e1f2a3b4d5'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tablas que conservan el rango, con su par renombrado a "unidades".
_CON_RANGO = ('descuentos_recompra', 'descuentos_volumen', 'descuentos_producto')
# Tablas donde el motor nunca leyó el rango: se elimina.
_SIN_RANGO = (
    'descuentos_pronto_pago',
    'promocion_primera_compra',
    'descuentos_diferencial_cambiario',
)


def upgrade() -> None:
    for tabla in _CON_RANGO:
        op.alter_column(tabla, 'min_cantidad', new_column_name='min_unidades')
        op.alter_column(tabla, 'max_cantidad', new_column_name='max_unidades')

    # Recompra: el valor bueno vive en cajas (es el que el motor aplicaba),
    # así que se copia encima del renombrado antes de tirar el par viejo.
    op.execute(
        'UPDATE descuentos_recompra '
        'SET min_unidades = min_cajas, max_unidades = max_cajas'
    )
    op.drop_column('descuentos_recompra', 'min_cajas')
    op.drop_column('descuentos_recompra', 'max_cajas')

    for tabla in _SIN_RANGO:
        op.drop_column(tabla, 'min_cantidad')
        op.drop_column(tabla, 'max_cantidad')


def downgrade() -> None:
    for tabla in _SIN_RANGO:
        op.add_column(
            tabla,
            sa.Column('min_cantidad', sa.Numeric(18, 4), server_default='0', nullable=False),
        )
        op.add_column(
            tabla,
            sa.Column('max_cantidad', sa.Numeric(18, 4), server_default='999999', nullable=False),
        )

    op.add_column(
        'descuentos_recompra',
        sa.Column('min_cajas', sa.Integer(), server_default='2', nullable=False),
    )
    op.add_column(
        'descuentos_recompra',
        sa.Column('max_cajas', sa.Integer(), server_default='4', nullable=False),
    )
    op.execute(
        'UPDATE descuentos_recompra '
        'SET min_cajas = min_unidades, max_cajas = max_unidades'
    )

    for tabla in _CON_RANGO:
        op.alter_column(tabla, 'min_unidades', new_column_name='min_cantidad')
        op.alter_column(tabla, 'max_unidades', new_column_name='max_cantidad')
