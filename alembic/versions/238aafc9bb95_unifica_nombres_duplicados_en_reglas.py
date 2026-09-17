"""Dos pares de campos que decian lo mismo con nombres distintos.

Paso 2 del plan de unificacion de reglas que aprobo el usuario: limpiar
los duplicados ANTES de armar el formulario unico, para que no los
arrastre.

1. descuentos_volumen.litros_minimo era el mismo dato que min_unidades.
El formulario escribia los dos y el motor los desempataba con una cascada
de fallbacks ("es regla de litros si unidad_medida dice LITROS, o si
litros_minimo tiene algo y min_unidades no"). Verificado contra
produccion: los 5 registros tenian el MISMO valor en ambos campos, asi
que el duplicado solo agregaba formas de equivocarse. El tramo queda en
min/max_unidades y ``unidad_medida`` dice en que se cuenta.

2. descuentos_diferencial_cambiario.nombre era el mismo dato que
descripcion, el campo que ya tienen todas las demas reglas. Solo esa
tabla cargaba los dos. DIF_35_VES tenia descripcion vacia y el nombre
"35% Fijo VES a USD", asi que el backfill lo conserva.

No se toca reglas_dias_credito_volumen: ahi litros_minimo/litros_maximo
son genuinamente litros, la tabla no tiene unidad_medida y no hay
ambiguedad que resolver.

Revision ID: 238aafc9bb95
Revises: 434804b8f421
Create Date: 2026-09-08 18:46:36.618166

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '238aafc9bb95'
down_revision: Union[str, None] = '434804b8f421'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # El tramo pasa a vivir solo en min/max_unidades. Backfill defensivo
    # por si algun registro tuviera el dato solo del lado viejo.
    op.execute(
        "UPDATE descuentos_volumen SET min_unidades = litros_minimo "
        "WHERE (min_unidades IS NULL OR min_unidades = 0) AND litros_minimo > 0"
    )
    op.drop_column("descuentos_volumen", "litros_minimo")

    # El nombre viejo se conserva como descripcion donde esta vacia.
    op.execute(
        "UPDATE descuentos_diferencial_cambiario SET descripcion = nombre "
        "WHERE (descripcion IS NULL OR descripcion = '') AND nombre <> ''"
    )
    op.drop_column("descuentos_diferencial_cambiario", "nombre")


def downgrade() -> None:
    op.add_column(
        "descuentos_diferencial_cambiario",
        sa.Column("nombre", sa.String(), nullable=False, server_default=""),
    )
    op.execute("UPDATE descuentos_diferencial_cambiario SET nombre = descripcion")
    op.add_column(
        "descuentos_volumen",
        sa.Column("litros_minimo", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.execute("UPDATE descuentos_volumen SET litros_minimo = min_unidades")
