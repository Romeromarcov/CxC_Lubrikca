"""Las reglas ahora fijan a que NO aplican, no solo a que si.

Criterio del usuario (septiembre 2026): "cuando dice que aplica a las
listas VES, quiere decir que nunca debe aplicar a orden nacida con lista
USD, porque aplicaria dos veces el 35%, pero no que aplique a todas las
ordenes en lista VES, para eso el motor debe evaluar cada caso concreto".
Y: "para evitar problemas hacia el futuro podemos modificar los
formularios para incluir los tipos de lista a los que NO aplica ese
descuento, en lugar de establecerlo como que aplica a todas las ordenes de
una lista. Igualmente con las monedas".

Decir "aplica a X" obliga a enumerar todo lo permitido, y una lista nueva
entra sin querer. Decir "nunca a Y" fija la prohibicion, que es lo que de
verdad protege.

Revision ID: 434804b8f421
Revises: b8b347536f59
Create Date: 2026-09-08 09:13:53.927194

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '434804b8f421'
down_revision: Union[str, None] = 'b8b347536f59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLAS = (
    "descuentos_pronto_pago",
    "descuentos_recompra",
    "descuentos_volumen",
    "descuentos_producto",
    "promocion_primera_compra",
    "descuentos_diferencial_cambiario",
)


def upgrade() -> None:
    for tabla in TABLAS:
        op.add_column(
            tabla,
            sa.Column("listas_excluidas", sa.String(), nullable=False, server_default=""),
        )
        op.add_column(
            tabla,
            sa.Column("monedas_excluidas", sa.String(), nullable=False, server_default=""),
        )
    # El diferencial cambiario NUNCA debe aplicar a una orden nacida en
    # lista USD: ese precio ya viene con el descuento y aplicarlo otra vez
    # da el 35% dos veces. Lo destapo el usuario mirando S00010 (TERA).
    op.execute(
        "UPDATE descuentos_diferencial_cambiario SET listas_excluidas = 'LISTAS_USD'"
    )


def downgrade() -> None:
    for tabla in TABLAS:
        op.drop_column(tabla, "monedas_excluidas")
        op.drop_column(tabla, "listas_excluidas")
