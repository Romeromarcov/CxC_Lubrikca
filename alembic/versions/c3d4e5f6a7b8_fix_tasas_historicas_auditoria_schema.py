"""tasas_historicas_auditoria: columnas reales (archivo diario BCV/Binance)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RATE = sa.Numeric(14, 6)


def upgrade() -> None:
    # El esquema original (PR1) modelaba esta tabla como un log de
    # ediciones manuales (timestamp/campo/valor_anterior/valor_nuevo); la
    # hoja real de Sheets es un archivo diario BCV/Binance regenerado por
    # scripts/cargar_tasas_historicas.py (fecha/tasa_bcv_usd/tasa_bcv_euro/
    # tasa_binance_promedio_diario/diferencial_bcv_binance_pct/fuente/
    # notas) -- tabla nunca tuvo backfill, se recrea sin perdida real.
    op.drop_table('tasas_historicas_auditoria')
    op.create_table(
        'tasas_historicas_auditoria',
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('tasa_bcv_usd', RATE, nullable=True),
        sa.Column('tasa_bcv_euro', RATE, nullable=True),
        sa.Column('tasa_binance_promedio_diario', RATE, nullable=True),
        sa.Column('diferencial_bcv_binance_pct', sa.String(), nullable=True),
        sa.Column('fuente', sa.String(), server_default='', nullable=False),
        sa.Column('notas', sa.String(), server_default='', nullable=False),
        sa.PrimaryKeyConstraint('fecha'),
    )


def downgrade() -> None:
    op.drop_table('tasas_historicas_auditoria')
    op.create_table(
        'tasas_historicas_auditoria',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('campo', sa.String(), server_default='', nullable=False),
        sa.Column('valor_anterior', sa.String(), nullable=True),
        sa.Column('valor_nuevo', sa.String(), nullable=True),
        sa.Column('editado_por', sa.String(), server_default='', nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
