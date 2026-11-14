"""Permisos por usuario y bitácora de cambios de acceso.

- ``usuarios_permisos``: ajustes de cada usuario sobre la base de su rol (solo la
  diferencia: concedido=True agrega un permiso, False lo quita).
- ``accesos_bitacora``: cada cambio de rol, activo o permisos, con quién lo hizo y el
  antes/después. Solo-append (trigger).

Revision ID: e5f7a9b1c3d5
Revises: d4e6f8a0b2c4
Create Date: 2026-11-13 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f7a9b1c3d5"
down_revision: Union[str, None] = "d4e6f8a0b2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usuarios_permisos",
        sa.Column(
            "email",
            sa.String,
            sa.ForeignKey("usuarios_plataforma.email"),
            primary_key=True,
        ),
        sa.Column("permiso", sa.String, primary_key=True),
        sa.Column("concedido", sa.Boolean, nullable=False),
        sa.Column("actualizado_por", sa.String, nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_table(
        "accesos_bitacora",
        sa.Column("cambio_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("email", sa.String, nullable=False, index=True),
        sa.Column("antes", sa.Text, nullable=False),
        sa.Column("despues", sa.Text, nullable=False),
        sa.Column("comentario", sa.Text, nullable=False, server_default=""),
        sa.Column("usuario", sa.String, nullable=False),
        sa.Column("cambiado_en", sa.DateTime(timezone=False), nullable=False),
    )
    op.execute(
        """
        CREATE FUNCTION accesos_bitacora_inmutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'accesos_bitacora es solo-append: ' || TG_OP || ' rechazado';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accesos_bitacora_inmutable
        BEFORE UPDATE OR DELETE ON accesos_bitacora
        FOR EACH ROW EXECUTE FUNCTION accesos_bitacora_inmutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_accesos_bitacora_inmutable ON accesos_bitacora")
    op.execute("DROP FUNCTION IF EXISTS accesos_bitacora_inmutable()")
    op.drop_table("accesos_bitacora")
    op.drop_table("usuarios_permisos")
