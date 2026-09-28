"""agrega so_id_linea a lineas_factura (facturas que consolidan varias ordenes)

Revision ID: c6eb23e61d5b
Revises: dd99c915a9ad
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c6eb23e61d5b'
down_revision: Union[str, None] = 'dd99c915a9ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lineas_factura", sa.Column("so_id_linea", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("lineas_factura", "so_id_linea")
