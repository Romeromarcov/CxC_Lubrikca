"""Registra las decisiones comerciales sobre el descuento de una orden.

Caso que lo motivo (7-oct-2026): S00913. La regla da 8% de pronto pago y comercialmente se
dio 6%. La tabla no cambia ningun monto: documenta que la NC difiere del motor a proposito,
para que la comparacion NC-contra-motor no la trate como anomalia.

Revision ID: a7c3e9b1d2f4
Revises: c6eb23e61d5b
Create Date: 2026-10-07 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c3e9b1d2f4"
down_revision: Union[str, None] = "c6eb23e61d5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decisiones_comerciales",
        sa.Column("so_id", sa.String(), primary_key=True),
        sa.Column("motivo", sa.Text(), nullable=False, server_default=""),
        sa.Column("marcado_por", sa.String(), nullable=False, server_default=""),
        sa.Column("timestamp_marcado", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("decisiones_comerciales")
