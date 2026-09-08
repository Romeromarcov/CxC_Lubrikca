"""renombra anomalias_aceptadas a discrepancias_aceptadas y le agrega huella

Revision ID: d4f5a6b7c8e9
Revises: c9e1f2a3b4d5
Create Date: 2026-09-07 00:00:00.000000

Dos pedidos del usuario al revisar la página de Auditoría (septiembre 2026).

1. EL NOMBRE. "No lo llames anomalías, llámalas discrepancias." Una
   anomalía suena a error del sistema; una discrepancia es lo que en verdad
   se muestra: dos fuentes que no coinciden, y alguien tiene que decidir
   cuál vale.

2. LA HUELLA. Aceptar una discrepancia la silenciaba PARA SIEMPRE, aunque
   después cambiaran los datos que la originaron: si se acepta una
   diferencia de 264,58 y mañana esa orden pasa a diferir en 3.000, la
   aceptación vieja la seguiría tapando. Eso es exactamente el "caso
   oculto" que el usuario no quiere.

   ``huella`` guarda un hash de los valores que definen la discrepancia. El
   detector compara: si coincide, sigue aceptada; si cambió, vuelve a
   aparecer como discrepancia nueva. Mismo patrón que
   ``VentasTeorico.lineas_fingerprint`` ya usa para los teóricos.

``detalle`` completa la trazabilidad que pidió: qué decía exactamente la
discrepancia cuando se aceptó, no solo su tipo.

La tabla está vacía en producción, así que el renombrado no arrastra datos.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4f5a6b7c8e9'
down_revision: Union[str, None] = 'c9e1f2a3b4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('anomalias_aceptadas', 'discrepancias_aceptadas')
    op.alter_column(
        'discrepancias_aceptadas', 'anomalia_id', new_column_name='discrepancia_id'
    )
    op.alter_column(
        'discrepancias_aceptadas', 'tipo_anomalia', new_column_name='tipo_discrepancia'
    )
    op.add_column(
        'discrepancias_aceptadas',
        sa.Column('huella', sa.String(), server_default='', nullable=False),
    )
    op.add_column(
        'discrepancias_aceptadas',
        sa.Column('detalle', sa.Text(), server_default='', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('discrepancias_aceptadas', 'detalle')
    op.drop_column('discrepancias_aceptadas', 'huella')
    op.alter_column(
        'discrepancias_aceptadas', 'tipo_discrepancia', new_column_name='tipo_anomalia'
    )
    op.alter_column(
        'discrepancias_aceptadas', 'discrepancia_id', new_column_name='anomalia_id'
    )
    op.rename_table('discrepancias_aceptadas', 'anomalias_aceptadas')
