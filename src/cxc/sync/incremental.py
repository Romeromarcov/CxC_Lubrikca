"""Sync incremental delta Odoo → Sheets (sección 2 y regla de oro 1.2).

Lee de Odoo SOLO las filas con ``write_date > última_corrida`` y refresca SOLO
las tablas-espejo. NUNCA escribe en Vinculaciones, Bandeja ni SerieTasas: la
implementación se limita a los ``upsert_*`` de espejo del repositorio, que no
tocan las tablas de trabajo humano ni la auditoría inmutable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import LineaOrden, OrdenVenta
from ..odoo.client import OdooReader
from ..repositories import Repository

logger = logging.getLogger("cxc.sync")


@dataclass(frozen=True)
class SyncResult:
    clientes: int
    ordenes: int
    lineas: int
    pagos: int
    lineas_borradas: int
    desde: datetime | None
    hasta: datetime
    facturas: int = 0
    entregas: int = 0
    catalogo: int = 0
    lineas_factura: int = 0
    entregas_lineas: int = 0

    @property
    def total(self) -> int:
        return (
            self.clientes + self.ordenes + self.lineas + self.pagos
            + self.facturas + self.entregas + self.catalogo + self.lineas_factura
            + self.entregas_lineas
        )


class IncrementalSync:
    def __init__(self, repo: Repository, reader: OdooReader) -> None:
        self._repo = repo
        self._reader = reader

    # ``filas``/``upsert_fn`` quedan en ``Any``: este helper es deliberadamente
    # genérico sobre los 5 espejos opcionales (facturas, entregas, catálogo,
    # líneas de factura, líneas de entrega), cada uno con su propio tipo de
    # fila y su propio ``upsert_*``.
    def _sync_opcional(
        self, nombre: str, filas: list[Any], upsert_fn: Callable[[Any], None]
    ) -> int:
        """Upsert best-effort para espejos NUEVOS (Fase 0, agosto 2026) que

        no todos los backends soportan todavía (ej. Sheets, en retiro) --
        si el backend activo no lo implementa, se omite esta tabla sin
        tumbar el resto del sync, que sí es crítico. Ver Repository.
        upsert_facturas/upsert_entregas.
        """
        if not filas:
            return 0
        try:
            upsert_fn(filas)
            return len(filas)
        except NotImplementedError:
            logger.warning(
                "El backend activo no soporta el espejo de %s -- se omite "
                "para esta corrida.",
                nombre,
            )
            return 0

    def run(self, now: datetime, sync_catalogo: bool = True) -> SyncResult:
        """Ejecuta una corrida delta. ``now`` = sello de tiempo del servidor.

        El cursor avanza a ``now`` (inicio de corrida) para no perder filas
        escritas durante la lectura en la próxima corrida.

        ``sync_catalogo=False``: salta la consulta de catálogo (``product.
        template``) en esta corrida -- el catálogo cambia con poca
        frecuencia (precios/nombres de producto, no ventas), así que no
        necesita el mismo ciclo de 5 minutos que clientes/órdenes/pagos. El
        daemon (ver ``run_sync_in_background``) lo pasa en ``False`` en la
        mayoría de los ciclos y en ``True`` una vez al día, reusando el
        mismo patrón de recálculo diario ya construido para las ventanas de
        pago. El sync manual (``/api/sync/manual``) y la primera corrida
        siempre lo dejan en ``True`` (default) -- necesitan el catálogo
        completo desde el arranque.
        """
        since = self._repo.get_last_sync()
        logger.info("Sync delta desde %s", since)

        clientes = self._reader.changed_clientes(since)
        ordenes = self._reader.changed_ordenes(since)
        lineas = self._reader.changed_lineas(since)
        pagos = self._reader.changed_pagos(since)
        facturas = self._reader.changed_facturas(since)
        entregas = self._reader.changed_entregas(since)
        catalogo = self._reader.changed_catalogo(since) if sync_catalogo else []
        lineas_factura = self._reader.changed_lineas_factura(since)
        entregas_lineas = self._reader.changed_entregas_lineas(since)

        # SOLO tablas-espejo. Estas operaciones no tocan Vinculaciones/SerieTasas.
        self._repo.upsert_clientes(clientes)
        self._repo.upsert_ordenes(ordenes)
        self._repo.upsert_lineas(lineas)
        self._repo.upsert_pagos(pagos)

        facturas_sincronizadas = self._sync_opcional(
            "facturas", facturas, self._repo.upsert_facturas
        )
        entregas_sincronizadas = self._sync_opcional(
            "entregas", entregas, self._repo.upsert_entregas
        )
        catalogo_sincronizado = self._sync_opcional(
            "catálogo", catalogo, self._repo.upsert_catalogo
        )
        lineas_factura_sincronizadas = self._sync_opcional(
            "líneas de factura", lineas_factura, self._repo.upsert_lineas_factura
        )
        entregas_lineas_sincronizadas = self._sync_opcional(
            "líneas de entrega", entregas_lineas, self._repo.upsert_entregas_lineas
        )

        lineas_borradas = self.reconciliar_lineas_borradas(ordenes, lineas)

        self._repo.set_last_sync(now)

        result = SyncResult(
            clientes=len(clientes),
            ordenes=len(ordenes),
            lineas=len(lineas),
            pagos=len(pagos),
            lineas_borradas=lineas_borradas,
            desde=since,
            hasta=now,
            facturas=facturas_sincronizadas,
            entregas=entregas_sincronizadas,
            catalogo=catalogo_sincronizado,
            lineas_factura=lineas_factura_sincronizadas,
            entregas_lineas=entregas_lineas_sincronizadas,
        )
        logger.info(
            "Sync delta: %s filas refrescadas, %s líneas huérfanas borradas",
            result.total,
            lineas_borradas,
        )
        return result

    def reconciliar_lineas_borradas(
        self, ordenes: list[OrdenVenta], lineas: list[LineaOrden]
    ) -> int:
        """Borra del espejo local líneas que Odoo confirma que ya no existen.

        ``changed_lineas`` (delta por ``write_date``) nunca puede detectar
        una eliminación -- un registro borrado no deja rastro de
        ``write_date``. Se reconcilian las órdenes tocadas en este ciclo
        (por cambio propio o por cambio en alguna de sus líneas, que en
        Odoo recomputa ``amount_total`` de la orden y bumpea su
        ``write_date``) contra el set de líneas VIGENTES que Odoo reporta
        ahora mismo para ellas; cualquier línea local que ya no esté en ese
        set se borra (hallazgo real orden S00792, agosto 2026: producto
        sacado de la orden seguía apareciendo en el teórico para siempre).
        """
        so_ids = {o.so_id for o in ordenes} | {ln.so_id for ln in lineas if ln.so_id}
        if not so_ids:
            return 0
        vigentes_por_orden = self._reader.lineas_vigentes_por_orden(sorted(so_ids))
        a_borrar: list[str] = []
        for so_id, vigentes in vigentes_por_orden.items():
            for ln in self._repo.lineas_de_orden(so_id):
                if ln.linea_id not in vigentes:
                    a_borrar.append(ln.linea_id)
        if a_borrar:
            self._repo.delete_lineas(a_borrar)
        return len(a_borrar)
