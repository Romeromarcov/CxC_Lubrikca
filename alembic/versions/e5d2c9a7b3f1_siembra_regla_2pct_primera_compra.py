"""Siembra la regla PRIMERA_COMPRA_COMERCIAL_2PCT si no existe.

El 11-sep-2026 el 2 % de primera compra dejó de ser un respaldo cableado en el
motor y pasó a ser una regla de la tabla (``scripts/configurar_2pct_primera_compra.py``,
decisión del usuario: «crea la regla nueva y elimina la vieja»). El propio script
avisa: «sin el respaldo, si la regla NO está creada en la base, esas 119 órdenes
dejan de recibir el 2 % y su deuda sube 742,24 USD. El script tiene que correr con
el despliegue, no después».

Esta migración es eso: corre con ``release: alembic upgrade head`` (Procfile), así
que el despliegue no depende de que alguien se acuerde. Es idempotente -- si la
regla ya existe, con cualquier valor, no la toca: una regla que administración ya
editó no se pisa desde una migración.

Los valores son los del script, verificados el 11-sep contra la copia de
producción: 742,24 USD por las dos vías, cero órdenes que difieran.

Revision ID: e5d2c9a7b3f1
Revises: d4b7e2a9c6f3
Create Date: 2026-09-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5d2c9a7b3f1"
down_revision: str | None = "d4b7e2a9c6f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REGLA_ID = "PRIMERA_COMPRA_COMERCIAL_2PCT"


def upgrade() -> None:
    con = op.get_bind()
    existe = con.execute(
        sa.text("SELECT 1 FROM promocion_primera_compra WHERE regla_id = :id"), {"id": REGLA_ID}
    ).first()
    if existe:
        return
    con.execute(
        sa.text(
            """
            INSERT INTO promocion_primera_compra (
                regla_id, tipo_beneficio, productos, valor, compra_minima, regalo_tipo,
                vigencia_desde, vigencia_hasta, descuento_fallback, categorias_aplica,
                categorias_descuento, marca, categoria, unidad_medida, listas_aplicables,
                listas_excluidas, monedas_excluidas, solo_primera_compra, activo,
                requiere_pago_previo, aplica_a, descripcion
            ) VALUES (
                :id, 'porcentaje', '', 0.02, 0, 'solo_uno',
                '2026-02-01', NULL, 0.02, 'Comercial',
                'COMERCIAL', '*', '*', 'UNIDADES', '*',
                '', '', true, true,
                false, 'linea', '2 % de primera compra sobre lineas Comercial'
            )
            """
        ),
        {"id": REGLA_ID},
    )


def downgrade() -> None:
    # No se borra: si la regla ya existía antes de esta migración, borrarla al bajar
    # dejaría a las 119 órdenes sin su 2 %. Bajar la migración no deshace datos.
    pass
