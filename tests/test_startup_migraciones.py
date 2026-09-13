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


BASE_DESECHABLE = "cxc_mig_test"


@pytest.fixture
def url_desechable():
    """Una base vacía que se crea y se borra para este test.

    **Por qué no la de CI.** El test hace ``downgrade`` (DROP COLUMN) y después
    ``upgrade`` (ADD COLUMN), y **Postgres nunca reusa el slot de una columna
    borrada**: cada corrida quema un slot por columna y por tabla, para siempre.
    El límite es 1.600.

    Contra la base de CI eso reventó de verdad: después de un día de correr la
    suite, ``descuentos_recompra`` tenía **1.578 columnas muertas** y 19 vivas, y
    el test empezó a fallar con ``TooManyColumns``. Detrás venían cinco tablas
    más (``descuentos_pronto_pago`` con 1.213, ``descuentos_diferencial_cambiario``
    con 969...). Un test que degrada la base contra la que corre acaba rompiendo
    para todos, y el síntoma no se parece en nada a la causa.

    Con una base desechable, los slots quemados se van con ella.

    **Y el parche va en el entorno, no en la config de Alembic.**
    ``alembic/env.py`` dice textualmente que «DATABASE_URL siempre gana sobre lo
    que haya en alembic.ini», así que un ``cfg.set_main_option("sqlalchemy.url",
    ...)`` se ignora. Por eso el ``downgrade`` de este test corría contra la base
    de CI aunque la config apuntara a otra parte: ése era el mecanismo de la fuga.
    """
    from sqlalchemy import create_engine
    from sqlalchemy import text as _text

    from cxc.db.engine import to_psycopg_url

    base_url = to_psycopg_url(_DATABASE_URL)
    admin_url = base_url.rsplit("/", 1)[0] + "/postgres"
    destino = base_url.rsplit("/", 1)[0] + "/" + BASE_DESECHABLE

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as con:
            con.execute(_text(f'DROP DATABASE IF EXISTS "{BASE_DESECHABLE}"'))
            con.execute(_text(f'CREATE DATABASE "{BASE_DESECHABLE}"'))
    finally:
        admin.dispose()
    try:
        with patch.dict(os.environ, {"DATABASE_URL": destino}):
            yield destino
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as con:
                # El test deja su propio pool abierto y Postgres se niega a
                # borrar una base en uso ("is being accessed by other users").
                # Se cierran las sesiones ajenas antes de borrar: es una base
                # desechable, no hay nada que preservar.
                con.execute(
                    _text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :d AND pid <> pg_backend_pid()"
                    ),
                    {"d": BASE_DESECHABLE},
                )
                con.execute(_text(f'DROP DATABASE IF EXISTS "{BASE_DESECHABLE}"'))
        finally:
            admin.dispose()


@pytest.mark.skipif(not _DATABASE_URL, reason="DATABASE_URL no configurado")
def test_aplica_migraciones_pendientes_contra_postgres_real(url_desechable) -> None:
    """Downgrade a un esquema "viejo" (simula producción sin migrar) y

    confirma que la función lo deja al día -- mismo escenario del log de
    producción real (UndefinedColumn en requiere_pago_previo /
    equivalente_lista_usd)."""
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from sqlalchemy import create_engine

    engine = create_engine(url_desechable)
    db_schema.metadata.create_all(engine)

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url_desechable)
    command.stamp(cfg, "head")

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

    # El fixture ya dejó DATABASE_URL apuntando a la base desechable, que es lo
    # que ``alembic/env.py`` mira -- ver su docstring.
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
