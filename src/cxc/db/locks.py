"""Locks transaccionales y de concurrencia distribuidos sobre PostgreSQL.

En PostgreSQL usa ``pg_advisory_xact_lock`` / ``pg_try_advisory_lock`` con dos enteros
de 32 bits derivados determinísticamente de ``(namespace, clave)``. En SQLite u otros
motores locales hace no-op con un lock en memoria del proceso para pruebas unitarias.
"""

from __future__ import annotations

import contextlib
import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, text

_LOCAL_GUARD = threading.Lock()
_LOCAL_KEYS: dict[str, threading.RLock] = {}


def _par_llaves(namespace: str, clave: str) -> tuple[int, int]:
    """Convierte ``(namespace, clave)`` en un par de enteros signed int32 estables."""

    def _i32(txt: str) -> int:
        raw = int.from_bytes(hashlib.sha256(txt.encode("utf-8")).digest()[:4], "big", signed=False)
        return raw - 2**32 if raw >= 2**31 else raw

    return _i32(namespace), _i32(clave)


def _local_lock(namespace: str, clave: str) -> threading.RLock:
    full = f"{namespace}:{clave}"
    with _LOCAL_GUARD:
        lk = _LOCAL_KEYS.get(full)
        if lk is None:
            lk = threading.RLock()
            _LOCAL_KEYS[full] = lk
        return lk


def advisory_lock_conn(conn: Any, namespace: str, clave: str) -> None:
    """Adquiere ``pg_advisory_xact_lock`` dentro de la transacción activa en PostgreSQL."""
    dialect = getattr(getattr(conn, "dialect", None), "name", "")
    if dialect != "postgresql":
        return
    k1, k2 = _par_llaves(namespace, str(clave))
    conn.execute(text("SELECT pg_advisory_xact_lock(:k1, :k2)"), {"k1": k1, "k2": k2})


@contextmanager
def advisory_lock_engine(
    engine: Engine | None, namespace: str, clave: str
) -> Iterator[None]:
    """Serializa una sección crítica entre workers sobre el mismo ``namespace:clave``."""
    if engine is None or getattr(engine.dialect, "name", "") != "postgresql":
        lk = _local_lock(namespace, str(clave))
        with lk:
            yield
        return

    with engine.begin() as conn:
        advisory_lock_conn(conn, namespace, str(clave))
        yield


@contextmanager
def try_advisory_session_lock(
    engine: Engine | None, namespace: str, clave: str
) -> Iterator[bool]:
    """Lock no bloqueante a nivel sesión/conexión para daemons en múltiples workers."""
    if engine is None or getattr(engine.dialect, "name", "") != "postgresql":
        lk = _local_lock(namespace, str(clave))
        adq = lk.acquire(blocking=False)
        try:
            yield adq
        finally:
            if adq:
                lk.release()
        return

    k1, k2 = _par_llaves(namespace, str(clave))
    with engine.connect() as conn:
        adq = bool(
            conn.execute(
                text("SELECT pg_try_advisory_lock(:k1, :k2)"),
                {"k1": k1, "k2": k2},
            ).scalar()
        )
        try:
            yield adq
        finally:
            if adq:
                with contextlib.suppress(Exception):
                    conn.execute(
                        text("SELECT pg_advisory_unlock(:k1, :k2)"),
                        {"k1": k1, "k2": k2},
                    )
