"""agrega tabla facturas (espejo inmutable de account.move -- Fase 0 del plan de consolidacion de fuentes)

Revision ID: b28cfe376b63
Revises: f1a2b3c4d5e6
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b28cfe376b63'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'facturas',
        sa.Column('factura_id', sa.String(), primary_key=True),
        sa.Column('numero', sa.String(), nullable=False, server_default=''),
        sa.Column('so_id', sa.String(), nullable=True),
        sa.Column('move_type', sa.String(), nullable=False, server_default='out_invoice'),
        sa.Column('es_nota_debito', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('moneda', sa.String(), nullable=False, server_default='USD'),
        sa.Column('monto_total', sa.Numeric(18, 4), nullable=False),
        sa.Column('monto_sin_impuestos', sa.Numeric(18, 4), nullable=False),
        sa.Column('estado', sa.String(), nullable=False, server_default='posted'),
        sa.Column('factura_origen_id', sa.String(), nullable=True),
    )
    op.create_index('ix_facturas_so_id', 'facturas', ['so_id'])


def downgrade() -> None:
    op.drop_index('ix_facturas_so_id', table_name='facturas')
    op.drop_table('facturas')
