"""Construcción del engine sincrono de SQLAlchemy a partir de DATABASE_URL.

Sincrono a propósito: ``Repository`` (``cxc.repositories``) es una interfaz
sincrona hoy, consumida sin ``await`` por el motor de descuentos, el sync
incremental y el reconciliador. Un driver async forzaría a reescribir esas
piezas -- fuera de alcance por ahora.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine


def to_psycopg_url(url: str) -> str:
    """Normaliza un DATABASE_URL (Railway da ``postgres://`` o

    ``postgresql://``) al esquema que SQLAlchemy+psycopg necesita
    (``postgresql+psycopg://``).
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def make_engine(database_url: str) -> Engine:
    """Engine con pool -- una instancia por proceso, reutilizada entre

    requests (igual que el singleton ``_repo_cache``/``get_repo()`` en
    ``web/app.py``). ``pool_pre_ping`` evita usar una conexión muerta tras
    un failover/reinicio del servidor Postgres (Railway puede reciclar la
    instancia).
    """
    return create_engine(to_psycopg_url(database_url), pool_pre_ping=True, pool_size=5)
