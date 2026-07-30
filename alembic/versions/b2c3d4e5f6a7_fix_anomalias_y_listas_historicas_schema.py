"""anomalias_aceptadas y listas_precios_historicas: columnas reales de Sheets

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-29 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MONEY = sa.Numeric(18, 4)


def upgrade() -> None:
    # anomalias_aceptadas: el esquema original (PR1) no coincidia con las
    # columnas reales que escribe/lee la app (ver AceptarAnomaliaRequest en
    # web/app.py) -- tabla nunca tuvo backfill, se recrea sin perdida real.
    op.drop_table('anomalias_aceptadas')
    op.create_table(
        'anomalias_aceptadas',
        sa.Column('anomalia_id', sa.String(), nullable=False),
        sa.Column('so_id', sa.String(), nullable=False),
        sa.Column('factura_id', sa.String(), server_default='', nullable=False),
        sa.Column('tipo_anomalia', sa.String(), server_default='', nullable=False),
        sa.Column('motivo_aceptacion', sa.String(), server_default='', nullable=False),
        sa.Column('aprobado_por', sa.String(), server_default='', nullable=False),
        sa.Column('timestamp_aprobacion', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('anomalia_id'),
    )
    op.create_index(
        op.f('ix_anomalias_aceptadas_so_id'), 'anomalias_aceptadas', ['so_id'], unique=False
    )

    # listas_precios_historicas: idem -- columnas reales son codigo/
    # producto_nombre/precio_usd/precio_bcv_euro.
    op.drop_table('listas_precios_historicas')
    op.create_table(
        'listas_precios_historicas',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('codigo', sa.String(), nullable=False),
        sa.Column('producto_nombre', sa.String(), server_default='', nullable=False),
        sa.Column('precio_usd', MONEY, server_default='0', nullable=False),
        sa.Column('precio_bcv_euro', MONEY, server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_listas_precios_historicas_codigo'),
        'listas_precios_historicas',
        ['codigo'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_listas_precios_historicas_codigo'), table_name='listas_precios_historicas'
    )
    op.drop_table('listas_precios_historicas')
    op.create_table(
        'listas_precios_historicas',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('producto_id', sa.String(), nullable=False),
        sa.Column('lista_precios', sa.String(), nullable=False),
        sa.Column('precio', MONEY, nullable=False),
        sa.Column('vigencia_desde', sa.Date(), nullable=True),
        sa.Column('vigencia_hasta', sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_listas_precios_historicas_producto_id'),
        'listas_precios_historicas',
        ['producto_id'],
        unique=False,
    )

    op.drop_index(op.f('ix_anomalias_aceptadas_so_id'), table_name='anomalias_aceptadas')
    op.drop_table('anomalias_aceptadas')
    op.create_table(
        'anomalias_aceptadas',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('so_id', sa.String(), nullable=False),
        sa.Column('motivo', sa.String(), server_default='', nullable=False),
        sa.Column('aceptado_por', sa.String(), server_default='', nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_anomalias_aceptadas_so_id'), 'anomalias_aceptadas', ['so_id'], unique=False
    )
