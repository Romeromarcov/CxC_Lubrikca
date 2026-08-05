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

from ..models import BandejaFacturacion, MetodoPago, VentasTeorico, Vinculacion
from ..repositories import Repository
from .discounts import EngineInputs, calcular_factura, calcular_teorico_orden_con_fallback
from .historical_pricing import cargar_mapa_historico, es_orden_historica
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

    def build_inputs(self, so_id: str, fecha_calculo: date) -> EngineInputs | None:
        """Arma ``EngineInputs`` para una orden, SIN correr ``calcular_factura``.

        Extraído de ``_calcular`` para que llamadores que solo necesitan los
        inputs (ej. ``/api/ventas/{so_id}/detalle`` -- Fase 5, desglose por
        línea vía ``discounts.lineas_con_precio``) no dupliquen esta lógica
        de cableo repo -> EngineInputs.
        """
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
            valor = self._repo.get_config("cash_window_business_days")
            if valor:
                self._cfg = dataclasses.replace(
                    self._cfg, cash_window_business_days=int(valor)
                )
        except Exception as e:
            logger.warning("Error al leer cash_window_business_days de _Meta: %s", e)

        # Tarea 3 (auditoria reglas por lista): listas de precio configuradas
        # en Configuración -- sin esto, el matching de "LISTAS_VES"/
        # "LISTAS_USD" en listas_aplicables usa el default hardcodeado de
        # effective_dating._match_lista en vez de lo que el usuario tildó.
        valid_ves: list[str] = []
        valid_usd: list[str] = []
        try:
            ves_str = self._repo.get_config("valid_pricelists_ves")
            usd_str = self._repo.get_config("valid_pricelists_usd")
            valid_ves = [x.strip() for x in (ves_str or "").split(",") if x.strip()]
            valid_usd = [x.strip() for x in (usd_str or "").split(",") if x.strip()]
        except Exception as e:
            logger.warning("Error al leer listas de precio validas de _Meta: %s", e)

        # Tarea 2 (Lista Histórica de Auditoría): sin esto BandejaFacturacion
        # (lo que alimenta /api/ventas) nunca sabia de la excepcion historica
        # y mostraba teorico $0.00 para ordenes sin lista o del periodo
        # 20-feb al 12-mar-2026 -- solo los endpoints de reporte la conocian.
        historical_enabled = True
        historical_price_map: dict[str, dict[str, object]] = {}
        try:
            toggle = self._repo.get_config("historical_pricelist_enabled")
            historical_enabled = toggle is None or toggle.strip().lower() not in (
                "false",
                "0",
                "no",
            )
            historical_price_map = cargar_mapa_historico(
                self._repo.all_listas_precios_historicas()
            )
        except Exception as e:
            logger.warning("Error al leer Lista Historica de Auditoria: %s", e)
        orden_es_historica = es_orden_historica(
            orden.fecha, orden.lista_precios, historical_enabled
        )

        # Recompra (ventana = días de crédito reales de la orden anterior +
        # dias_gracia): la orden anterior del cliente es la de fecha más
        # reciente ANTES de esta (mismo cliente_id), con desempate por
        # so_id para fechas iguales (mismo criterio que usaba el gate
        # "primera del mes" que esta ventana reemplaza).
        todas_ordenes = self._repo.all_ordenes()
        orden_anterior_cliente = None
        for o in todas_ordenes:
            if o.cliente_id != orden.cliente_id or o.so_id == orden.so_id:
                continue
            es_anterior = o.fecha < orden.fecha or (
                o.fecha == orden.fecha and o.so_id < orden.so_id
            )
            if es_anterior and (
                orden_anterior_cliente is None
                or o.fecha > orden_anterior_cliente.fecha
                or (
                    o.fecha == orden_anterior_cliente.fecha
                    and o.so_id > orden_anterior_cliente.so_id
                )
            ):
                orden_anterior_cliente = o
        orden_anterior_cliente_vincs = (
            self._repo.vinculaciones_de_orden(orden_anterior_cliente.so_id)
            if orden_anterior_cliente is not None
            else []
        )

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
            all_ordenes=todas_ordenes,
            exclusiones=self._repo.exclusiones(),
            descuentos_recompra=self._repo.descuentos_recompra(),
            descuentos_diferencial=self._repo.descuentos_diferencial_cambiario(),
            valid_ves=valid_ves,
            valid_usd=valid_usd,
            orden_es_historica=orden_es_historica,
            historical_price_map=historical_price_map,
            orden_anterior_cliente=orden_anterior_cliente,
            orden_anterior_cliente_vincs=orden_anterior_cliente_vincs,
        )
        return inputs

    def _calcular(
        self, so_id: str, fecha_calculo: date
    ) -> tuple[BandejaFacturacion, list[Vinculacion]] | None:
        """Calcula la bandeja de una orden SIN persistir (para batchear en run_all)."""
        inputs = self.build_inputs(so_id, fecha_calculo)
        if inputs is None:
            return None
        bandeja = calcular_factura(inputs)
        # Equivalentes congelados estampados durante el cálculo -- se
        # devuelven junto a la bandeja para que el llamador decida cómo
        # persistirlos (uno por uno o en lote).
        return bandeja, [v for v, _ in inputs.abonos]

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

    def run_teoricos_pendientes(self, fecha_calculo: date, limite: int | None = None) -> int:
        """Calcula ``ventas_teoricos`` (Fase 10) para órdenes que AÚN no lo

        tienen, o que sí lo tienen pero quedaron marcadas
        ``usa_fallback_ves``/``_usd`` (su precio no estaba en la lista
        específica -- se re-verifica por si esa lista ya se completó).

        A diferencia de ``run_all``, procesa órdenes SIN importar si ya
        están facturadas -- el teórico es precisamente el punto de
        comparación que más se necesita para órdenes ya facturadas (ver
        docstring de la tabla en ``db/schema.py``). Órdenes canceladas/
        borrador se saltan igual que ``run_all`` (no tiene sentido un
        teórico para una orden que nunca se concretó).

        ``limite``: tope de órdenes a procesar en esta corrida (cada una
        implica varias llamadas a Odoo vía el price resolver) -- None
        procesa todas las pendientes.
        """
        existentes = {v.so_id: v for v in self._repo.all_ventas_teoricos()}
        procesadas = 0
        for o in self._repo.all_ordenes():
            if limite is not None and procesadas >= limite:
                break
            st = str(getattr(o, "estado_orden", "sale") or "").strip().lower()
            if st in ("cancel", "cancelled", "draft"):
                continue
            existente = existentes.get(o.so_id)
            if existente is not None and not (
                existente.usa_fallback_ves or existente.usa_fallback_usd
            ):
                continue  # ya calculado y sin fallback -- el teórico es fijo, no se toca

            inputs = self.build_inputs(o.so_id, fecha_calculo)
            if inputs is None:
                continue
            resultado = calcular_teorico_orden_con_fallback(inputs)
            self._repo.upsert_ventas_teorico(
                VentasTeorico(
                    so_id=o.so_id,
                    teorico_ves=resultado["teorico_ves"],
                    teorico_usd=resultado["teorico_usd"],
                    descuentos_teorico_ves=resultado["descuentos_teorico_ves"],
                    descuentos_teorico_usd=resultado["descuentos_teorico_usd"],
                    lista_ves_id=resultado["lista_ves_id"],
                    lista_usd_id=resultado["lista_usd_id"],
                    usa_fallback_ves=resultado["usa_fallback_ves"],
                    usa_fallback_usd=resultado["usa_fallback_usd"],
                )
            )
            procesadas += 1
        return procesadas
