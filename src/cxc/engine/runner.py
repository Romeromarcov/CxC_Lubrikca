"""Runner del motor — cablea ``Repository`` + ``PriceResolver`` con el cálculo puro.

Arma los ``EngineInputs`` de cada orden a partir del repositorio (órdenes,
líneas, vinculaciones, métodos, tablas de descuento, feriados), corre
``calcular_factura`` y persiste la fila en BandejaFacturacion. También estampa
los equivalentes congelados de cada abono (una sola vez).
"""

from __future__ import annotations

import logging
from datetime import date

from ..models import BandejaFacturacion, MetodoPago, Vinculacion
from ..repositories import Repository
from .discounts import EngineInputs, calcular_factura
from .price_resolver import PriceResolver

logger = logging.getLogger("cxc.engine")


class EngineRunner:
    def __init__(
        self,
        repo: Repository,
        price_resolver: PriceResolver,
        engine_config: object,
    ) -> None:
        self._repo = repo
        self._resolver = price_resolver
        self._cfg = engine_config

    def _abonos(
        self, vincs: list[Vinculacion]
    ) -> list[tuple[Vinculacion, MetodoPago]]:
        abonos: list[tuple[Vinculacion, MetodoPago]] = []
        for v in vincs:
            pago = self._repo.get_pago(v.pago_id)
            if pago is None:
                logger.warning("Vinculación %s sin pago %s; se omite", v.vinc_id, v.pago_id)
                continue
            metodo = self._repo.get_metodo_pago(pago.metodo_pago)
            if metodo is None:
                logger.warning(
                    "Pago %s con método %s inexistente; se omite",
                    pago.pago_id, pago.metodo_pago,
                )
                continue
            abonos.append((v, metodo))
        return abonos

    def run_orden(self, so_id: str, fecha_calculo: date) -> BandejaFacturacion | None:
        orden = self._repo.get_orden(so_id)
        if orden is None:
            logger.warning("Orden %s inexistente", so_id)
            return None
        lineas = self._repo.lineas_de_orden(so_id)
        vincs = self._repo.vinculaciones_de_orden(so_id)
        abonos = self._abonos(vincs)

        from ..config import EngineConfig  # local para evitar ciclo de tipos

        assert isinstance(self._cfg, EngineConfig)
        inputs = EngineInputs(
            orden=orden,
            lineas=lineas,
            abonos=abonos,
            descuentos=self._repo.descuentos_marca_categoria(),
            reglas_recurrencia=self._repo.reglas_recurrencia(),
            feriados_tabla=self._repo.feriados(),
            price_resolver=self._resolver,
            engine_config=self._cfg,
            fecha_calculo=fecha_calculo,
        )
        bandeja = calcular_factura(inputs)
        self._repo.upsert_bandeja(bandeja)
        # Persistir los equivalentes congelados estampados durante el cálculo.
        for v, _ in abonos:
            self._repo.update_vinculacion(v)
        return bandeja

    def run_all(self, fecha_calculo: date) -> list[BandejaFacturacion]:
        """Calcula la bandeja de toda orden no facturada con al menos un abono."""
        resultados: list[BandejaFacturacion] = []
        so_con_abonos = {v.so_id for v in self._repo.all_vinculaciones()}
        for so_id in sorted(so_con_abonos):
            orden = self._repo.get_orden(so_id)
            if orden is None or orden.facturada:
                continue
            bandeja = self.run_orden(so_id, fecha_calculo)
            if bandeja is not None:
                resultados.append(bandeja)
        return resultados
