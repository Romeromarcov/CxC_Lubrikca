"""Chequeo combinado de salud de Staging: Postgres + compare_backends.py.

Pensado para correrse periódicamente (tarea local de Windows y/o routine en
la nube) durante la ventana de validación de Fase 4. Hace dos cosas:

1. Un chequeo directo de Postgres (conexiones, locks, queries colgadas) vía
   DATABASE_URL -- no necesita el CLI de Railway, solo la connection string.
2. Corre ``compare_backends.py`` contra Sheets y reporta si hay diferencias
   de VALOR reales (columna "difieren" > 0), que es la única señal que
   indica un problema real -- filas de más/menos por drift normal (nuevas
   órdenes/pagos desde el último check, o los 3 cliente_id placeholder
   conocidos) no cuentan como hallazgo.

Salida: imprime un resumen; exit code 0 si todo OK, 1 si hay algo para
revisar (útil para que el script que lo invoca decida si notificar).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import text  # noqa: E402

from cxc.db.engine import make_engine  # noqa: E402

# cliente_id sintéticos creados a propósito por migrate_sheets_to_postgres.py
# para huérfanos reales de producción (ver _ensure_clientes_placeholder) --
# no son un hallazgo, se descartan del reporte.
_PLACEHOLDER_CLIENTE_IDS = {"", "351", "354"}


def _check_postgres(database_url: str) -> tuple[bool, list[str]]:
    problemas: list[str] = []
    engine = make_engine(database_url)
    with engine.connect() as conn:
        long_running = conn.execute(
            text(
                """
                SELECT pid, state, now() - query_start AS duracion, left(query, 100) AS query
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND state != 'idle'
                  AND pid != pg_backend_pid()
                  AND now() - query_start > interval '2 minutes'
                """
            )
        ).all()
        for row in long_running:
            problemas.append(f"Query larga (pid={row.pid}, {row.duracion}): {row.query}")

        idle_in_tx = conn.execute(
            text(
                """
                SELECT count(*) FROM pg_stat_activity
                WHERE datname = current_database() AND state = 'idle in transaction'
                """
            )
        ).scalar()
        if idle_in_tx:
            problemas.append(f"{idle_in_tx} conexión(es) idle-in-transaction")

        blocked = conn.execute(text("SELECT count(*) FROM pg_locks WHERE NOT granted")).scalar()
        if blocked:
            problemas.append(f"{blocked} lock(s) no otorgados (posible bloqueo)")

        used = conn.execute(text("SELECT count(*) FROM pg_stat_activity")).scalar()
        max_conn = int(conn.execute(text("SHOW max_connections")).scalar())
        if used > max_conn * 0.8:
            problemas.append(f"Conexiones altas: {used}/{max_conn}")

    return (len(problemas) == 0, problemas)


def _check_compare_backends(database_url: str) -> tuple[bool, str]:
    script = os.path.join(os.path.dirname(__file__), "compare_backends.py")
    proc = subprocess.run(
        [sys.executable, script, "--database-url", database_url],
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = proc.stdout + proc.stderr

    # La unica senal real de problema: valores que DIFIEREN (no filas de
    # mas/menos por drift normal). Se busca la tabla resumen y se filtra.
    hallazgos_reales = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 6 and parts[0] not in ("tabla",):
            try:
                difieren = int(parts[5])
            except ValueError:
                continue
            if difieren > 0:
                hallazgos_reales.append(line.strip())

    ok = len(hallazgos_reales) == 0
    resumen = output if not ok else "compare_backends.py: sin valores que difieran."
    return (ok, resumen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("Falta --database-url o la variable de entorno DATABASE_URL")

    print("=== Chequeo de Postgres (staging) ===")
    pg_ok, pg_problemas = _check_postgres(database_url)
    if pg_ok:
        print("OK -- sin conexiones colgadas, locks, ni queries largas.")
    else:
        for p in pg_problemas:
            print(f"PROBLEMA: {p}")

    print("\n=== Chequeo Sheets vs Postgres (compare_backends.py) ===")
    cb_ok, cb_resumen = _check_compare_backends(database_url)
    print(cb_resumen)

    todo_ok = pg_ok and cb_ok
    print(f"\n=== Resultado: {'OK' if todo_ok else 'REVISAR'} ===")
    return 0 if todo_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
