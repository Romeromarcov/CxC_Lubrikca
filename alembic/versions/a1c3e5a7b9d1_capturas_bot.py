"""Capturas de los bots de Telegram y su bitácora.

- ``capturas_bot``: contactos, pedidos y pagos que llegan por Telegram y su estado hacia Odoo.
  Único por ``idempotency_key``. El sync de Odoo nunca la toca.
- ``capturas_bot_eventos``: bitácora solo-append (trigger) de cada captura.

Revision ID: a1c3e5a7b9d1
Revises: e5f7a9b1c3d5
Create Date: 2026-11-14 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c3e5a7b9d1"
down_revision: Union[str, None] = "e5f7a9b1c3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "capturas_bot",
        sa.Column("captura_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("idempotency_key", sa.String, nullable=False),
        sa.Column("estado", sa.String, nullable=False, server_default="PENDIENTE"),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("fuente", sa.Text, nullable=False, server_default="{}"),
        sa.Column("auto", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("incompleto", sa.Text, nullable=False, server_default="[]"),
        sa.Column("resultado", sa.Text, nullable=False, server_default="{}"),
        sa.Column("odoo_ref", sa.String, nullable=False, server_default=""),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("intentos", sa.Integer, nullable=False, server_default="0"),
        sa.Column("creada_en", sa.DateTime(timezone=False), nullable=False),
        sa.Column("actualizada_en", sa.DateTime(timezone=False), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_capturas_bot_idempotency_key"),
    )
    op.create_index("ix_capturas_bot_kind", "capturas_bot", ["kind"])
    op.create_index("ix_capturas_bot_estado", "capturas_bot", ["estado"])
    op.create_table(
        "capturas_bot_eventos",
        sa.Column("evento_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "captura_id", sa.Integer, sa.ForeignKey("capturas_bot.captura_id"), nullable=False
        ),
        sa.Column("evento", sa.String, nullable=False),
        sa.Column("detalle", sa.Text, nullable=False, server_default=""),
        sa.Column("actor", sa.String, nullable=False, server_default="sistema"),
        sa.Column("en", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_index("ix_capturas_bot_eventos_captura_id", "capturas_bot_eventos", ["captura_id"])
    op.execute(
        """
        CREATE FUNCTION capturas_bot_eventos_inmutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'capturas_bot_eventos es solo-append: no se modifica ni se borra';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_capturas_bot_eventos_inmutable
        BEFORE UPDATE OR DELETE ON capturas_bot_eventos
        FOR EACH ROW EXECUTE FUNCTION capturas_bot_eventos_inmutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_capturas_bot_eventos_inmutable ON capturas_bot_eventos")
    op.execute("DROP FUNCTION IF EXISTS capturas_bot_eventos_inmutable()")
    op.drop_table("capturas_bot_eventos")
    op.drop_table("capturas_bot")
