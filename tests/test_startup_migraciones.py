"""Red de seguridad de arranque: ``_aplicar_migraciones_pendientes``.

El `Procfile` ya declara ``release: alembic upgrade head``, pero un
despliegue real mostró que esa fase puede no correr (o fallar en
silencio): la app siguió sirviendo requests contra un esquema
desactualizado (``UndefinedColumn`` en producción). Esta función corre la
misma migración al arrancar el proceso `web` como respaldo. Debe ser
siempre best-effort: nunca debe tumbar el arranque de la app.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text

from cxc.db import schema as db_schema
from cxc.web.app import _aplicar_migraciones_pendientes

_DATABASE_URL = os.environ.get("DATABASE_URL")


def test_no_propaga_excepcion_si_alembic_falla() -> None:
    """Best-effort: un fallo de Alembic se loggea, nunca tumba el arranque."""
    with (
        patch("alembic.command.upgrade", side_effect=RuntimeError("DB no disponible")),
    ):
        _aplicar_migraciones_pendientes()  # no debe lanzar


@pytest.mark.skipif(not _DATABASE_URL, reason="DATABASE_URL no configurado")
def test_aplica_migraciones_pendientes_contra_postgres_real() -> None:
    """Downgrade a un esquema "viejo" (simula producción sin migrar) y

    confirma que la función lo deja al día -- mismo escenario del log de
    producción real (UndefinedColumn en requiere_pago_previo /
    equivalente_lista_usd)."""
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from sqlalchemy import create_engine

    from cxc.db.engine import to_psycopg_url

    engine = create_engine(to_psycopg_url(_DATABASE_URL))
    db_schema.metadata.create_all(engine)

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", to_psycopg_url(_DATABASE_URL))

    # Simula el estado de producción encontrado en los logs: las
    # migraciones que agregan requiere_pago_previo/equivalente_lista_usd
    # nunca se aplicaron. Se apunta a la revisión explícita anterior a
    # 'e5f6a7b8c9d0' (no un offset relativo tipo "-2") para que este test no
    # se rompa cada vez que se agregue una migración nueva al head.
    command.downgrade(cfg, "d4e5f6a7b8c9")
    with engine.connect() as conn:
        cols = {
            c["name"]
            for c in __import__("sqlalchemy").inspect(engine).get_columns("descuentos_pronto_pago")
        }
        assert "requiere_pago_previo" not in cols
        conn.rollback()

    with patch.dict(os.environ, {"DATABASE_URL": _DATABASE_URL}):
        _aplicar_migraciones_pendientes()

    def _has_column(conn: Any, table: str, column: str) -> bool:
        rows = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).fetchall()
        return len(rows) == 1

    with engine.connect() as conn:
        assert _has_column(conn, "descuentos_pronto_pago", "requiere_pago_previo")
        assert _has_column(conn, "bandeja_facturacion", "equivalente_lista_usd")
