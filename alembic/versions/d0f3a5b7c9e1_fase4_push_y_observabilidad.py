"""Tablas Fase 4: notificaciones push outbox y suscripciones web push.

Revision ID: d0f3a5b7c9e1
Revises: c9e2f4a6b8d0
Create Date: 2026-11-22 19:48:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d0f3a5b7c9e1"
down_revision: str | None = "c9e2f4a6b8d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _crear_indice_si_falta(
    inspector: sa.Inspector, tabla: str, nombre: str, cols: list[str], *, unique: bool = False
) -> None:
    existentes = {i["name"] for i in inspector.get_indexes(tabla)}
    if nombre not in existentes:
        op.create_index(nombre, tabla, cols, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("notificaciones_push_outbox"):
        op.create_table(
            "notificaciones_push_outbox",
            sa.Column("notificacion_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("dedupe_key", sa.String(160), nullable=False),
            sa.Column("canal", sa.String(32), nullable=False),
            sa.Column("sujeto_tipo", sa.String(32), nullable=False),
            sa.Column("sujeto_id", sa.String(160), nullable=False),
            sa.Column("evento_tipo", sa.String(64), nullable=False),
            sa.Column("titulo", sa.String(180), nullable=False),
            sa.Column("cuerpo", sa.Text(), nullable=False),
            sa.Column("datos_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("estado", sa.String(32), nullable=False, server_default="PENDIENTE"),
            sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_intentos", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("ultimo_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("leida_en", sa.DateTime(timezone=False), nullable=True),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("enviado_en", sa.DateTime(timezone=False), nullable=True),
        )
        inspector = sa.inspect(bind)
    _crear_indice_si_falta(
        inspector,
        "notificaciones_push_outbox",
        "ix_notificaciones_push_outbox_dedupe_key",
        ["dedupe_key"],
        unique=True,
    )
    _crear_indice_si_falta(
        inspector,
        "notificaciones_push_outbox",
        "ix_notificaciones_push_outbox_sujeto_tipo",
        ["sujeto_tipo"],
    )
    _crear_indice_si_falta(
        inspector,
        "notificaciones_push_outbox",
        "ix_notificaciones_push_outbox_sujeto_id",
        ["sujeto_id"],
    )
    _crear_indice_si_falta(
        inspector,
        "notificaciones_push_outbox",
        "ix_notificaciones_push_outbox_evento_tipo",
        ["evento_tipo"],
    )
    _crear_indice_si_falta(
        inspector,
        "notificaciones_push_outbox",
        "ix_notificaciones_push_outbox_estado",
        ["estado"],
    )

    if not inspector.has_table("web_push_suscripciones"):
        op.create_table(
            "web_push_suscripciones",
            sa.Column("suscripcion_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("sujeto_tipo", sa.String(32), nullable=False),
            sa.Column("sujeto_id", sa.String(160), nullable=False),
            sa.Column("endpoint", sa.String(500), nullable=False),
            sa.Column("p256dh", sa.String(255), nullable=False, server_default=""),
            sa.Column("auth", sa.String(255), nullable=False, server_default=""),
            sa.Column("user_agent", sa.String(200), nullable=False, server_default=""),
            sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("creado_en", sa.DateTime(timezone=False), nullable=False),
            sa.Column("actualizado_en", sa.DateTime(timezone=False), nullable=False),
        )
        inspector = sa.inspect(bind)
    _crear_indice_si_falta(
        inspector,
        "web_push_suscripciones",
        "ix_web_push_suscripciones_endpoint",
        ["endpoint"],
        unique=True,
    )
    _crear_indice_si_falta(
        inspector,
        "web_push_suscripciones",
        "ix_web_push_suscripciones_sujeto_tipo",
        ["sujeto_tipo"],
    )
    _crear_indice_si_falta(
        inspector,
        "web_push_suscripciones",
        "ix_web_push_suscripciones_sujeto_id",
        ["sujeto_id"],
    )
    _crear_indice_si_falta(
        inspector,
        "web_push_suscripciones",
        "ix_web_push_suscripciones_activa",
        ["activa"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("web_push_suscripciones"):
        op.drop_table("web_push_suscripciones")
    if inspector.has_table("notificaciones_push_outbox"):
        op.drop_table("notificaciones_push_outbox")
