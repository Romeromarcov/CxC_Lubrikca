"""Runner del motor — cablea ``Repository`` + ``PriceResolver`` con el cálculo puro.

Arma los ``EngineInputs`` de cada orden a partir del repositorio (órdenes,
líneas, vinculaciones, métodos, tablas de descuento, feriados), corre
``calcular_factura`` y persiste la fila en BandejaFacturacion. También estampa
los equivalentes congelados de cada abono (una sola vez).
"""

from __future__ import annotations

import dataclasses
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

    def _abonos(self, vincs: list[Vinculacion]) -> list[tuple[Vinculacion, MetodoPago]]:
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
                    pago.pago_id,
                    pago.metodo_pago,
                )
                continue
            abonos.append((v, metodo))
        return abonos

    def _calcular(
        self, so_id: str, fecha_calculo: date
    ) -> tuple[BandejaFacturacion, list[Vinculacion]] | None:
        """Calcula la bandeja de una orden SIN persistir (para batchear en run_all)."""
        orden = self._repo.get_orden(so_id)
        if orden is None:
            logger.warning("Orden %s inexistente", so_id)
            return None
        lineas = self._repo.lineas_de_orden(so_id)
        vincs = self._repo.vinculaciones_de_orden(so_id)
        abonos = self._abonos(vincs)

        from ..config import EngineConfig  # local para evitar ciclo de tipos

        assert isinstance(self._cfg, EngineConfig)

        # Override cash_window_business_days with value from _Meta if available
        try:
            if hasattr(self._repo, "_g"):
                rows = self._repo._g.read_rows("_Meta")
                for r in rows:
                    if r.get("key") == "cash_window_business_days" and r.get("value"):
                        self._cfg = dataclasses.replace(
                            self._cfg, cash_window_business_days=int(r.get("value"))
                        )
                        break
        except Exception as e:
            logger.warning("Error al leer cash_window_business_days de _Meta: %s", e)

        inputs = EngineInputs(
            orden=orden,
            lineas=lineas,
            abonos=abonos,
            descuentos=self._repo.descuentos_marca_categoria(),
            descuentos_volumen=self._repo.descuentos_volumen(),
            reglas_recurrencia=self._repo.reglas_recurrencia(),
            descuento_bcv_diario=self._repo.descuento_bcv_completo(),
            promociones_primera_compra=self._repo.promociones_primera_compra(),
            feriados_tabla=self._repo.feriados(),
            price_resolver=self._resolver,
            engine_config=self._cfg,
            fecha_calculo=fecha_calculo,
            all_ordenes=self._repo.all_ordenes(),
            exclusiones=self._repo.exclusiones(),
            descuentos_recompra=self._repo.descuentos_recompra(),
            descuentos_diferencial=self._repo.descuentos_diferencial_cambiario(),
        )
        bandeja = calcular_factura(inputs)
        # Equivalentes congelados estampados durante el cálculo -- se
        # devuelven junto a la bandeja para que el llamador decida cómo
        # persistirlos (uno por uno o en lote).
        return bandeja, [v for v, _ in abonos]

    def run_orden(self, so_id: str, fecha_calculo: date) -> BandejaFacturacion | None:
        resultado = self._calcular(so_id, fecha_calculo)
        if resultado is None:
            return None
        bandeja, vincs_actualizadas = resultado
        self._repo.upsert_bandeja(bandeja)
        for v in vincs_actualizadas:
            self._repo.update_vinculacion(v)
        return bandeja

    def run_all(self, fecha_calculo: date) -> list[BandejaFacturacion]:
        """Calcula la bandeja de toda orden activa no facturada.

        Persiste en LOTE (una sola escritura por tabla) en vez de una
        escritura por orden -- con cientos de órdenes, escribir de a una
        agota la cuota de la API de Sheets casi de inmediato.
        """
        resultados: list[BandejaFacturacion] = []
        todas_vincs: list[Vinculacion] = []
        ordenes = self._repo.all_ordenes()
        for o in ordenes:
            st = str(getattr(o, "estado_orden", "sale") or "").strip().lower()
            if st in ("cancel", "cancelled", "draft", "sent"):
                continue
            if o.facturada:
                continue
            resultado = self._calcular(o.so_id, fecha_calculo)
            if resultado is None:
                continue
            bandeja, vincs_actualizadas = resultado
            resultados.append(bandeja)
            todas_vincs.extend(vincs_actualizadas)

        self._repo.upsert_bandejas(resultados)
        self._repo.update_vinculaciones(todas_vincs)
        return resultados
