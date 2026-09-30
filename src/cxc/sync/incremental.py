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

from ..models import EstadoVinculacion, LineaOrden, OrdenVenta
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

    # Tablas del barrido y su tope de seguridad: si "sobran" más que esto, lo más
    # probable es que la consulta a Odoo vino incompleta, no que se borró todo.
    TABLAS_BARRIDO = ("pagos", "facturas", "lineas_factura", "lineas_entrega")
    BARRIDO_MAX_ABSOLUTO = 500
    BARRIDO_MAX_FRACCION = 0.10

    def barrer_borrados(self) -> dict[str, Any]:
        """Borra del espejo las filas que Odoo ya no tiene.

        El delta por ``write_date`` no puede ver una eliminación (auditoría de
        septiembre 2026: 8 pagos, 5 facturas y 1.184 líneas de factura seguían en
        el espejo sin existir en Odoo). Por tabla: toma los ids del espejo, luego
        los ids vigentes de Odoo (en ESE orden, para que una fila nacida entre las
        dos lecturas no parezca borrada), y borra la diferencia.

        Salvaguardas: si Odoo devuelve vacío o el lector no sabe contestar, la
        tabla se omite; si la diferencia supera el tope, no se borra nada y queda
        en ``omitidas``. Un pago con Vinculaciones NO se borra -- las Vinculaciones
        son trabajo humano que el sync no toca -- y queda en ``pagos_bloqueados``
        para depurarlo a mano.
        """
        borradas: dict[str, int] = {}
        omitidas: dict[str, str] = {}
        pagos_bloqueados: list[str] = []
        for tabla in self.TABLAS_BARRIDO:
            try:
                en_espejo = self._repo.ids_espejo(tabla)
                vigentes = self._reader.ids_vigentes(tabla)
            except NotImplementedError:
                omitidas[tabla] = "el backend no soporta el barrido"
                continue
            if vigentes is None:
                omitidas[tabla] = "el lector no sabe listar los ids de Odoo"
                continue
            if not vigentes:
                omitidas[tabla] = "Odoo devolvio cero registros; no se borra nada"
                logger.warning("Barrido de borrados: %s omitida (Odoo devolvio vacio).", tabla)
                continue
            sobran = sorted(en_espejo - vigentes)
            tope = max(self.BARRIDO_MAX_ABSOLUTO, int(len(en_espejo) * self.BARRIDO_MAX_FRACCION))
            if len(sobran) > tope:
                omitidas[tabla] = f"{len(sobran)} filas sobran, por encima del tope de {tope}"
                logger.warning("Barrido de borrados: %s omitida: %s", tabla, omitidas[tabla])
                continue
            if tabla == "pagos" and sobran:
                con_vinculaciones = self._repo.pago_ids_con_vinculaciones(sobran)
                # Odoo manda: un pago que Odoo ya no tiene y cuyas vinculaciones son
                # todas PENDIENTE (propuestas sin confirmar) se retira junto con ellas.
                # Si alguna esta CONCILIADA (Odoo la habia confirmado) se conserva y se
                # reporta: eso es plata aplicada y lo decide una persona.
                por_pago: dict[str, list[Any]] = {}
                for v in self._repo.all_vinculaciones():
                    if str(v.pago_id) in con_vinculaciones:
                        por_pago.setdefault(str(v.pago_id), []).append(v)
                solo_pendientes = {
                    pid
                    for pid, vs in por_pago.items()
                    if all(v.estado == EstadoVinculacion.PENDIENTE for v in vs)
                }
                if solo_pendientes:
                    self._repo.delete_vinculaciones(
                        [v.vinc_id for pid in solo_pendientes for v in por_pago[pid]]
                    )
                    con_vinculaciones = con_vinculaciones - solo_pendientes
                pagos_bloqueados = sorted(con_vinculaciones)
                sobran = [i for i in sobran if i not in con_vinculaciones]
                if pagos_bloqueados:
                    logger.warning(
                        "Barrido de borrados: %s pago(s) ya no existen en Odoo pero tienen "
                        "Vinculaciones y se conservan: %s",
                        len(pagos_bloqueados),
                        ", ".join(pagos_bloqueados),
                    )
            if sobran:
                borradas[tabla] = self._repo.borrar_espejo(tabla, sobran)
        if borradas:
            logger.info("Barrido de borrados: %s", borradas)
        return {"borradas": borradas, "omitidas": omitidas, "pagos_bloqueados": pagos_bloqueados}

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
