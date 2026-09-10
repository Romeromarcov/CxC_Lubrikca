#!/usr/bin/env python3
"""Entorno de pruebas: espejo local apuntado al Odoo de QA.

Base del banco de escenarios de la Fase 3. Levanta una base Postgres
separada (``cxc_qa`` por defecto, NUNCA la de produccion), le corre las
migraciones y sincroniza el espejo desde el Odoo de prueba.

    python scripts/qa_entorno.py crear      # crea la base y migra
    python scripts/qa_entorno.py sync       # corrida completa desde cero
    python scripts/qa_entorno.py resync     # corrida delta (lo que cambio)
    python scripts/qa_entorno.py estado     # que hay en el espejo

La configuracion sale de ``.env.qa`` (ver ``.env.qa.example``). El script se
niega a correr si ``DATABASE_URL`` apunta a algo que no sea local o si el
``ODOO_URL`` no lleva ``.dev.odoo.com``: el objetivo entero de este entorno
es que un error de dedo no pueda tocar produccion.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

RUTA_ENV_QA = RAIZ / ".env.qa"

# Hosts donde vive el Postgres de pruebas. Cualquier otro es sospechoso de
# ser produccion y aborta la corrida.
HOSTS_PERMITIDOS = {"localhost", "127.0.0.1", "::1", "postgres", ""}


def cargar_env_qa() -> None:
    """Carga ``.env.qa`` PISANDO lo que haya en el entorno.

    Pisar es deliberado: si la terminal ya tiene el ``DATABASE_URL`` de
    produccion exportado, un ``setdefault`` dejaria el script apuntando
    ahi. Se prefiere que el archivo de QA gane siempre.
    """
    if not RUTA_ENV_QA.exists():
        sys.exit(
            f"Falta {RUTA_ENV_QA.name}. Copia .env.qa.example y completa las "
            "credenciales del Odoo de prueba."
        )
    for linea in RUTA_ENV_QA.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ[clave.strip()] = valor.strip()


def verificar_que_es_qa() -> None:
    """Las dos barreras que impiden tocar produccion desde aca."""
    url_db = os.environ.get("DATABASE_URL", "")
    host = urlparse(url_db).hostname or ""
    if host not in HOSTS_PERMITIDOS:
        sys.exit(
            f"DATABASE_URL apunta a '{host}', que no es local. Este script solo "
            "trabaja contra una base de pruebas en esta maquina."
        )
    url_odoo = os.environ.get("ODOO_URL", "")
    if ".dev.odoo.com" not in url_odoo:
        sys.exit(
            f"ODOO_URL es '{url_odoo}', que no parece un entorno de prueba de "
            "Odoo (.dev.odoo.com). Abortado."
        )


def _url_admin(url_db: str) -> tuple[str, str]:
    """Separa la URL en (conexion a 'postgres', nombre de la base)."""
    partes = urlparse(url_db)
    nombre = (partes.path or "/").lstrip("/")
    admin = url_db.rsplit("/", 1)[0] + "/postgres"
    return admin, nombre


def _motor(url: str):
    import sqlalchemy as sa

    # psycopg3: el paquete instalado es ``psycopg``, no ``psycopg2``.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return sa.create_engine(url)


def crear() -> int:
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    url_db = os.environ["DATABASE_URL"]
    admin, nombre = _url_admin(url_db)
    motor = _motor(admin)
    with motor.connect() as con:
        con.execution_options(isolation_level="AUTOCOMMIT")
        existe = con.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": nombre}
        ).scalar()
        if existe:
            print(f"La base '{nombre}' ya existe.")
        else:
            con.execute(sa.text(f'CREATE DATABASE "{nombre}"'))
            print(f"Base '{nombre}' creada.")

    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url_db)
    command.upgrade(cfg, "head")
    print("Migraciones al dia.")
    return 0


def _repo_y_reader():
    from cxc.config import AppConfig
    from cxc.db.postgres_repository import PostgresRepository
    from cxc.odoo.client import OdooXmlRpcReader

    config = AppConfig.from_env()
    repo = PostgresRepository.from_url(os.environ["DATABASE_URL"])
    reader = OdooXmlRpcReader(config.odoo)
    return repo, reader


def _borrar_cursor() -> None:
    """Saca el cursor de sync para que la proxima corrida lea todo.

    ``set_last_sync`` solo acepta un datetime, asi que el borrado va por
    SQL directo contra ``app_settings``. Es la unica forma de pedir una
    corrida completa sin recrear la base.
    """
    import sqlalchemy as sa

    motor = _motor(os.environ["DATABASE_URL"])
    with motor.begin() as con:
        con.execute(sa.text("DELETE FROM app_settings WHERE key = 'last_sync'"))


def sync(desde_cero: bool) -> int:
    from cxc.sync.incremental import IncrementalSync

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    repo, reader = _repo_y_reader()
    if desde_cero:
        _borrar_cursor()
        print("Cursor borrado: la corrida lee el historico completo.")
    inicio = datetime.now()
    resultado = IncrementalSync(repo, reader).run(inicio, sync_catalogo=True)
    print(
        f"\nSync {'completo' if desde_cero else 'delta'} en "
        f"{(datetime.now() - inicio).total_seconds():.0f}s"
    )
    for campo in (
        "clientes",
        "ordenes",
        "lineas",
        "pagos",
        "facturas",
        "entregas",
        "catalogo",
        "lineas_factura",
        "entregas_lineas",
        "lineas_borradas",
    ):
        print(f"  {campo:<18} {getattr(resultado, campo)}")
    print(f"  {'TOTAL':<18} {resultado.total}")
    return 0


def estado() -> int:
    import sqlalchemy as sa

    motor = _motor(os.environ["DATABASE_URL"])
    with motor.connect() as con:
        tablas = con.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name"
            )
        ).scalars().all()
        print(f"{'tabla':<34} {'filas':>10}")
        print("-" * 46)
        for tabla in tablas:
            n = con.execute(sa.text(f'SELECT count(*) FROM "{tabla}"')).scalar()
            if n:
                print(f"{tabla:<34} {n:>10}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accion", choices=["crear", "sync", "resync", "estado"])
    args = parser.parse_args()

    cargar_env_qa()
    verificar_que_es_qa()

    if args.accion == "crear":
        return crear()
    if args.accion == "sync":
        return sync(desde_cero=True)
    if args.accion == "resync":
        return sync(desde_cero=False)
    return estado()


if __name__ == "__main__":
    sys.exit(main())
