"""Sync incremental delta Odoo → Sheets (sección 2 y regla de oro 1.2).

Lee de Odoo SOLO las filas con ``write_date > última_corrida`` y refresca SOLO
las tablas-espejo. NUNCA escribe en Vinculaciones, Bandeja ni SerieTasas: la
implementación se limita a los ``upsert_*`` de espejo del repositorio, que no
tocan las tablas de trabajo humano ni la auditoría inmutable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..odoo.client import OdooReader
from ..repositories import Repository

logger = logging.getLogger("cxc.sync")


@dataclass(frozen=True)
class SyncResult:
    clientes: int
    ordenes: int
    lineas: int
    pagos: int
    desde: datetime | None
    hasta: datetime

    @property
    def total(self) -> int:
        return self.clientes + self.ordenes + self.lineas + self.pagos


class IncrementalSync:
    def __init__(self, repo: Repository, reader: OdooReader) -> None:
        self._repo = repo
        self._reader = reader

    def run(self, now: datetime) -> SyncResult:
        """Ejecuta una corrida delta. ``now`` = sello de tiempo del servidor.

        El cursor avanza a ``now`` (inicio de corrida) para no perder filas
        escritas durante la lectura en la próxima corrida.
        """
        since = self._repo.get_last_sync()
        logger.info("Sync delta desde %s", since)

        clientes = self._reader.changed_clientes(since)
        ordenes = self._reader.changed_ordenes(since)
        lineas = self._reader.changed_lineas(since)
        pagos = self._reader.changed_pagos(since)

        # SOLO tablas-espejo. Estas operaciones no tocan Vinculaciones/SerieTasas.
        self._repo.upsert_clientes(clientes)
        self._repo.upsert_ordenes(ordenes)
        self._repo.upsert_lineas(lineas)
        self._repo.upsert_pagos(pagos)

        self._repo.set_last_sync(now)

        result = SyncResult(
            clientes=len(clientes),
            ordenes=len(ordenes),
            lineas=len(lineas),
            pagos=len(pagos),
            desde=since,
            hasta=now,
        )
        logger.info("Sync delta: %s filas refrescadas", result.total)
        return result
