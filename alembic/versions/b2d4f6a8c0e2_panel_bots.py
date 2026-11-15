"""Panel de bots de Telegram: registro, usuarios autorizados, latidos y bitácora.

- ``bots_registro``: un renglón por bot (pedidos, cobranza, compras, eventos, asana) con su
  configuración y su último latido. No guarda tokens ni API keys.
- ``bots_usuarios``: usuarios de Telegram autorizados y los bots que pueden usar.
- ``bots_latidos``: contadores por bot y día (retención corta).
- ``bots_bitacora``: cambios hechos desde el panel; solo-append (trigger).

Revision ID: b2d4f6a8c0e2
Revises: a1c3e5a7b9d1
Create Date: 2026-11-14 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d4f6a8c0e2"
down_revision: Union[str, None] = "a1c3e5a7b9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bots_registro",
        sa.Column("bot", sa.String, primary_key=True),
        sa.Column("nombre", sa.String, nullable=False, server_default=""),
        sa.Column("habilitado", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("parametros", sa.Text, nullable=False, server_default="{}"),
        sa.Column("chats", sa.Text, nullable=False, server_default="{}"),
        sa.Column("url_panel", sa.String, nullable=False, server_default=""),
        sa.Column("mensaje", sa.String, nullable=False, server_default=""),
        sa.Column("version", sa.String, nullable=False, server_default=""),
        sa.Column("estado", sa.Text, nullable=False, server_default="{}"),
        sa.Column("ultimo_latido", sa.DateTime(timezone=False), nullable=True),
        sa.Column("actualizado_por", sa.String, nullable=False, server_default="sistema"),
        sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_table(
        "bots_usuarios",
        sa.Column("telegram_id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("email", sa.String, nullable=False, server_default=""),
        sa.Column("nombre", sa.String, nullable=False, server_default=""),
        sa.Column("bots", sa.Text, nullable=False, server_default="[]"),
        sa.Column("creado_por", sa.String, nullable=False, server_default="sistema"),
        sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_table(
        "bots_latidos",
        sa.Column("bot", sa.String, primary_key=True),
        sa.Column("dia", sa.Date, primary_key=True),
        sa.Column("latidos", sa.Integer, nullable=False, server_default="0"),
        sa.Column("mensajes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errores", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ultimo", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_table(
        "bots_bitacora",
        sa.Column("cambio_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("entidad", sa.String, nullable=False),
        sa.Column("accion", sa.String, nullable=False),
        sa.Column("antes", sa.Text, nullable=False, server_default="{}"),
        sa.Column("despues", sa.Text, nullable=False, server_default="{}"),
        sa.Column("usuario", sa.String, nullable=False),
        sa.Column("cambiado_en", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_index("ix_bots_bitacora_entidad", "bots_bitacora", ["entidad"])
    op.execute(
        """
        CREATE FUNCTION bots_bitacora_inmutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'bots_bitacora es solo-append: no se modifica ni se borra';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_bots_bitacora_inmutable
        BEFORE UPDATE OR DELETE ON bots_bitacora
        FOR EACH ROW EXECUTE FUNCTION bots_bitacora_inmutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_bots_bitacora_inmutable ON bots_bitacora")
    op.execute("DROP FUNCTION IF EXISTS bots_bitacora_inmutable()")
    op.drop_table("bots_bitacora")
    op.drop_table("bots_latidos")
    op.drop_table("bots_usuarios")
    op.drop_table("bots_registro")
