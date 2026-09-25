"""agrega api_keys (acceso de solo lectura para un sistema externo)

Revision ID: dd99c915a9ad
Revises: 6c1358ecef6a
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd99c915a9ad'
down_revision: Union[str, None] = '6c1358ecef6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("key_id", sa.String(), primary_key=True),
        sa.Column("nombre", sa.String(), nullable=False, server_default=""),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("creado_por", sa.String(), nullable=False, server_default=""),
        sa.Column("fecha_creacion", sa.String(), nullable=False, server_default=""),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ultimo_uso", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
