"""agrega vendedores y clasificacion_clientes (item 6, clasificacion comercial/industrial)

Revision ID: 6c1358ecef6a
Revises: d6063556877e
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c1358ecef6a'
down_revision: Union[str, None] = 'd6063556877e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vendedores",
        sa.Column("vendedor_email", sa.String(), primary_key=True),
        sa.Column("nombre", sa.String(), nullable=False, server_default=""),
        sa.Column("es_industrial", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "clasificacion_clientes",
        sa.Column(
            "cliente_id",
            sa.String(),
            sa.ForeignKey("clientes.cliente_id"),
            primary_key=True,
        ),
        sa.Column("es_industrial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("motivo", sa.Text(), nullable=False, server_default=""),
        sa.Column("marcado_por", sa.String(), nullable=False, server_default=""),
        sa.Column("timestamp_marcado", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("clasificacion_clientes")
    op.drop_table("vendedores")
