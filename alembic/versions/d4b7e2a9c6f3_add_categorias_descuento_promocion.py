"""agrega categorias_descuento a promocion_primera_compra

Sobre qué líneas se aplica el porcentaje de una promoción, que es una pregunta
distinta de ``categorias_aplica`` — ése dice qué unidades CALIFICAN para el
mínimo de compra.

Existe por el 2 % de primera compra. Vivía como respaldo cableado en el motor y
sumaba solo las líneas Comercial; configurarlo como regla de la tabla ensanchaba
la base a TODAS las líneas, porque la rama de reglas configuradas no filtraba por
categoría. Medido: 122 órdenes de la copia de producción tienen líneas de las dos
categorías, así que la diferencia no es teórica.

El default es vacío, que significa «todas las líneas» — exactamente lo que la
rama hacía antes. Ninguna regla existente cambia de comportamiento al migrar.

Revision ID: d4b7e2a9c6f3
Revises: f1e2d3c4b5a6
Create Date: 2026-09-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b7e2a9c6f3"
down_revision: str | None = "f1e2d3c4b5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "promocion_primera_compra",
        sa.Column("categorias_descuento", sa.String(), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("promocion_primera_compra", "categorias_descuento")
