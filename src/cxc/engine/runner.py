"""Runner del motor — cablea ``Repository`` + ``PriceResolver`` con el cálculo puro.

Arma los ``EngineInputs`` de cada orden a partir del repositorio (órdenes,
líneas, vinculaciones, métodos, tablas de descuento, feriados), corre
``calcular_factura`` y persiste la fila en BandejaFacturacion. También estampa
los equivalentes congelados de cada abono (una sola vez).
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from datetime import date

from ..models import (
    BandejaFacturacion,
    Entrega,
    EstadoVinculacion,
    LineaOrden,
    MetodoPago,
    Moneda,
    OrdenVenta,
    TipoTasa,
    VentasTeorico,
    Vinculacion,
    set_marca_fallback,
)
from ..repositories import Repository
from .discounts import EngineInputs, calcular_factura, calcular_teorico_orden_con_fallback
from .historical_pricing import cargar_mapa_historico, es_orden_historica
from .price_resolver import PriceResolver

logger = logging.getLogger("cxc.engine")


def fingerprint_lineas(lineas: list[LineaOrden]) -> str:
    """Huella determinista de las líneas de una orden (agosto 2026, hallazgo

    real orden S00792: el teórico quedó mostrando una línea de producto que
    ya no existía y cantidades viejas porque nada disparaba un recálculo
    cuando la orden en sí cambiaba en Odoo, solo cuando faltaba precio en
    una lista -- ver ``VentasTeorico.lineas_fingerprint``). Cambia si Odoo
    edita cantidades, precios o el set de productos de la orden."""
    partes = sorted(
        f"{ln.linea_id}|{ln.producto}|{ln.cantidad}|{ln.precio_unitario}|{ln.descuento}"
        for ln in lineas
    )
    return hashlib.sha256("\n".join(partes).encode("utf-8")).hexdigest()[:16]


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
        """Abonos "reales" para el motor -- Vinculaciones ``PENDIENTE`` (aún

        sin confirmar por Odoo, sea por vinculación manual reciente o por
        una sugerencia FIFO auto-vinculada) NO cuentan aquí. Fase 0 del plan
        de arquitectura de pagos (agosto 2026, pedido explícito del
        usuario): antes CUALQUIER Vinculación destrababa reglas con
        ``requiere_pago_previo=True`` (Contado, Recompra, Diferencial
        Cambiario) sin importar si Odoo ya la había reconciliado -- riesgo
        real una vez se automatice la vinculación FIFO (una sugerencia
        equivocada aprobaría un descuento real antes de que Odoo confirme
        nada). El filtro es sobre TODO ``inp.abonos``, no solo las reglas
        con pago previo, porque ninguna regla del motor debería tratar
        dinero sin confirmar como dinero real -- las reglas sin pago previo
        (Volumen, fallback de Primera Compra) no leen ``inp.abonos`` de
        todas formas, así que no se ven afectadas.

        La ventana de pago (Contado) sigue evaluándose correctamente de
        forma retroactiva una vez la Vinculación se promueve a
        ``CONCILIADO``: ``within_window`` compara la fecha REAL del abono
        (``Vinculacion.hora_pago_confirmada``, que viene de ``Pago.
        fecha_pago``), nunca la fecha en que se confirmó -- ver
        ``_resincronizar_vinculaciones_con_odoo`` en ``app.py``, que dispara
        el recálculo de la orden al promover el estado.
        """
        vincs_confirmadas = [v for v in vincs if v.estado == EstadoVinculacion.CONCILIADO]
        abonos: list[tuple[Vinculacion, MetodoPago]] = []
        for v in vincs_confirmadas:
            pago = self._repo.get_pago(v.pago_id)
            if pago is None:
                logger.warning("Vinculación %s sin pago %s; se omite", v.vinc_id, v.pago_id)
                continue
            metodo = self._repo.get_metodo_pago(pago.metodo_pago)
            if metodo is None:
                # Bug real corregido (agosto 2026, orden S00817/Michele
                # Carfora Vigliotti): ``metodos_pago`` -- tabla de
                # referencia sin panel propio en Configuración, nunca
                # sembrada -- estaba COMPLETAMENTE VACÍA en producción, así
                # que ESTE lookup fallaba para TODO pago, sin excepción, y
                # la Vinculación (ya CONCILIADO, confirmada por Odoo) se
                # descartaba entera de ``inp.abonos``. Eso apagaba, para
                # TODA orden del sistema, cualquier regla con
                # ``requiere_pago_previo=True`` (Contado/Pronto Pago,
                # Diferencial Cambiario) -- nunca había abonos que
                # evaluar. Ninguno de los campos de ``MetodoPago``
                # (moneda/tipo_tasa/es_contado) se lee en ningún otro
                # punto del motor (``Vinculacion.moneda_abono`` ya cubre
                # la moneda real del abono, con datos siempre presentes) --
                # el lookup solo servía, sin querer, como un gate que
                # excluía el pago cuando fallaba. Se construye un
                # MetodoPago de reserva a partir de la propia Vinculación
                # en vez de descartar el abono.
                logger.warning(
                    "Pago %s con método %s sin fila en metodos_pago; "
                    "se usa un MetodoPago de reserva (no se descarta el abono)",
                    pago.pago_id,
                    pago.metodo_pago,
                )
                metodo = MetodoPago(
                    metodo_id=str(pago.metodo_pago),
                    nombre="",
                    moneda=v.moneda_abono or Moneda.USD,
                    tipo_tasa=v.tipo_tasa_abono or TipoTasa.N_A,
                    es_contado=True,
                )
            abonos.append((v, metodo))
        return abonos

    def build_inputs(
        self,
        so_id: str,
        fecha_calculo: date,
        *,
        lineas_index: dict[str, list[LineaOrden]] | None = None,
    ) -> EngineInputs | None:
        """Arma ``EngineInputs`` para una orden, SIN correr ``calcular_factura``.

        Extraído de ``_calcular`` para que llamadores que solo necesitan los
        inputs (ej. ``/api/ventas/{so_id}/detalle`` -- Fase 5, desglose por
        línea vía ``discounts.lineas_con_precio``) no dupliquen esta lógica
        de cableo repo -> EngineInputs.

        ``lineas_index`` (opcional, ``{so_id: [LineaOrden]}`` ya cargado en
        memoria por el llamador): evita volver a golpear la DB por cada
        orden -- ``run_all()`` lo arma UNA vez (``self._repo.all_lineas()``)
        y lo pasa aquí. Bug real de rendimiento (agosto 2026): sin esto, el
        historial "acumulado" de Volumen (ver más abajo) hacía una query
        ``lineas_de_orden`` POR CADA orden anterior del mismo cliente, POR
        CADA orden que ``run_all()`` procesaba -- para un cliente con N
        órdenes eso es O(N²) queries en vez de O(N), y con cientos de
        órdenes reales en producción el recálculo pasó de minutos a
        20-35+ minutos por ciclo, monopolizando el único worker/pool de
        conexiones y causando 502 "upstream error" en requests normales
        como ``/reporte`` mientras el ciclo corría.
        """
        orden = self._repo.get_orden(so_id)
        if orden is None:
            logger.warning("Orden %s inexistente", so_id)
            return None
        lineas = (
            lineas_index.get(so_id, [])
            if lineas_index is not None
            else self._repo.lineas_de_orden(so_id)
        )
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

        # Fallback de marca configurable (no todos los productos traen
        # brand_id de Odoo) -- ver LineaOrden.resolved_marca.
        try:
            marca_fallback = self._repo.get_config("marca_fallback")
            if marca_fallback:
                set_marca_fallback(marca_fallback)
        except Exception as e:
            logger.warning("Error al leer marca_fallback de _Meta: %s", e)

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
            orden.fecha,
            orden.lista_precios,
            historical_enabled,
            lista_es_usd_valida=str(orden.lista_precios or "").strip() in valid_usd,
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
        # Fase 0 (mismo criterio que ``_abonos``): "orden anterior pagada
        # completo" -- el gate de Recompra -- tampoco debe contar
        # Vinculaciones sin confirmar por Odoo.
        orden_anterior_cliente_vincs = (
            [
                v
                for v in self._repo.vinculaciones_de_orden(orden_anterior_cliente.so_id)
                if v.estado == EstadoVinculacion.CONCILIADO
            ]
            if orden_anterior_cliente is not None
            else []
        )

        # Volumen "acumulado" (ver LineaOrden/discounts.py): líneas de las
        # DEMÁS órdenes del mismo cliente, para sumar litros/cajas dentro de
        # la ventana de cada regla "acumulado". Acotado al cliente (no un
        # full-scan de líneas) -- reutiliza `todas_ordenes` ya cargado.
        historial_cliente_lineas: list[tuple[OrdenVenta, list[LineaOrden]]] = []
        try:
            for o in todas_ordenes:
                if o.cliente_id != orden.cliente_id or o.so_id == orden.so_id:
                    continue
                o_lineas = (
                    lineas_index.get(o.so_id, [])
                    if lineas_index is not None
                    else self._repo.lineas_de_orden(o.so_id)
                )
                historial_cliente_lineas.append((o, o_lineas))
        except Exception as e:
            logger.warning("Error al leer historial de volumen del cliente: %s", e)

        inputs = EngineInputs(
            orden=orden,
            lineas=lineas,
            abonos=abonos,
            descuentos=self._repo.descuentos_marca_categoria(),
            descuentos_volumen=self._repo.descuentos_volumen(),
            reglas_recurrencia=self._repo.reglas_recurrencia(),
            promociones_primera_compra=self._repo.promociones_primera_compra(),
            feriados_tabla=self._repo.feriados(),
            price_resolver=self._resolver,
            engine_config=self._cfg,
            fecha_calculo=fecha_calculo,
            all_ordenes=todas_ordenes,
            exclusiones=self._repo.exclusiones(),
            descuentos_recompra=self._repo.descuentos_recompra(),
            descuentos_diferencial=self._repo.descuentos_diferencial_cambiario(),
            descuentos_producto=self._repo.descuentos_producto(),
            valid_ves=valid_ves,
            valid_usd=valid_usd,
            orden_es_historica=orden_es_historica,
            historical_price_map=historical_price_map,
            orden_anterior_cliente=orden_anterior_cliente,
            orden_anterior_cliente_vincs=orden_anterior_cliente_vincs,
            historial_cliente_lineas=historial_cliente_lineas,
        )
        return inputs

    def _calcular(
        self,
        so_id: str,
        fecha_calculo: date,
        *,
        lineas_index: dict[str, list[LineaOrden]] | None = None,
    ) -> tuple[BandejaFacturacion, list[Vinculacion]] | None:
        """Calcula la bandeja de una orden SIN persistir (para batchear en run_all)."""
        inputs = self.build_inputs(so_id, fecha_calculo, lineas_index=lineas_index)
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
        # Prefetch UNA sola vez -- ver docstring de build_inputs (bug de
        # rendimiento real, agosto 2026): sin esto, el historial "acumulado"
        # de Volumen hace una query lineas_de_orden por cada orden anterior
        # del cliente, POR CADA orden de este loop (O(N²)).
        lineas_index: dict[str, list[LineaOrden]] = {}
        for ln in self._repo.all_lineas():
            if ln.so_id:
                lineas_index.setdefault(ln.so_id, []).append(ln)
        for o in ordenes:
            st = str(getattr(o, "estado_orden", "sale") or "").strip().lower()
            if st in ("cancel", "cancelled", "draft", "sent"):
                continue
            if o.facturada:
                continue
            resultado = self._calcular(o.so_id, fecha_calculo, lineas_index=lineas_index)
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

        tienen, que sí lo tienen pero quedaron marcadas
        ``usa_fallback_ves``/``_usd`` (su precio no estaba en la lista
        específica -- se re-verifica por si esa lista ya se completó), o
        cuyas líneas cambiaron desde el último cálculo (``lineas_
        fingerprint`` ya no coincide -- alguien editó cantidades/productos
        de la orden en Odoo DESPUÉS de calcular su teórico; ver hallazgo
        real orden S00792 y docstring de ``VentasTeorico``).

        A diferencia de ``run_all``, procesa órdenes SIN importar si ya
        están facturadas -- el teórico es precisamente el punto de
        comparación que más se necesita para órdenes ya facturadas (ver
        docstring de la tabla en ``db/schema.py``). Órdenes canceladas/
        borrador se saltan igual que ``run_all`` (no tiene sentido un
        teórico para una orden que nunca se concretó) -- EXCEPTO
        (corrección del usuario, artefacto de verificación, agosto 2026)
        si la mercancía YA fue entregada (``Entrega`` outgoing "done") y
        NO hay ninguna devolución registrada: cancelar la orden en Odoo
        después de despachada no revierte la venta real, así que se sigue
        tratando como una venta real y se recalcula igual que cualquier
        otra orden.

        ``limite``: tope de órdenes a procesar en esta corrida (cada una
        implica varias llamadas a Odoo vía el price resolver) -- None
        procesa todas las pendientes.
        """
        existentes = {v.so_id: v for v in self._repo.all_ventas_teoricos()}
        # Prefetch UNA sola vez (mismo patrón que run_all, ver su
        # docstring) -- necesitamos las líneas de CADA orden para calcular
        # su huella, incluso las que terminan sin recalcularse.
        lineas_index: dict[str, list[LineaOrden]] = {}
        for ln in self._repo.all_lineas():
            if ln.so_id:
                lineas_index.setdefault(ln.so_id, []).append(ln)
        entregas_index: dict[str, list[Entrega]] = {}
        for e in self._repo.all_entregas():
            if e.so_id:
                entregas_index.setdefault(e.so_id, []).append(e)
        procesadas = 0
        for o in self._repo.all_ordenes():
            if limite is not None and procesadas >= limite:
                break
            st = str(getattr(o, "estado_orden", "sale") or "").strip().lower()
            if st in ("cancel", "cancelled", "draft"):
                entregada_sin_devolver = (
                    not o.tiene_devolucion
                    and any(
                        e.tipo == "outgoing" and e.estado == "done"
                        for e in entregas_index.get(o.so_id, [])
                    )
                )
                if not entregada_sin_devolver:
                    continue
            existente = existentes.get(o.so_id)
            fingerprint_actual = fingerprint_lineas(lineas_index.get(o.so_id, []))
            if (
                existente is not None
                and not (existente.usa_fallback_ves or existente.usa_fallback_usd)
                and existente.lineas_fingerprint == fingerprint_actual
                and existente.lineas_fingerprint != ""
            ):
                continue  # ya calculado, sin fallback, y las líneas no cambiaron

            inputs = self.build_inputs(o.so_id, fecha_calculo, lineas_index=lineas_index)
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
                    lineas_fingerprint=fingerprint_actual,
                )
            )
            procesadas += 1
        return procesadas
