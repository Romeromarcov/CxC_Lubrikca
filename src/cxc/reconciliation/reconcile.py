"""Conciliación de facturación (sección 7) — detección de desviaciones.

El motor dice lo que la factura DEBERÍA ser (``total_motor``); Odoo dice lo que
FUE (``monto_facturado`` − NCs). El sistema marca la brecha con un semáforo. No
escribe nada a Odoo (write-back purista).

Bandas del semáforo (interpretación de las tres bandas de la sección 7):
    |dif| <= tolerancia_redondeo            -> VERDE   (cuadra)
    tolerancia_redondeo < |dif| <= roja     -> AMARILLO (revisar)
    |dif| > tolerancia_roja                 -> ROJO    (se facturó distinto)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..config import ReconciliationConfig
from ..decimal_utils import q2
from ..models import Conciliacion, ResultadoConciliacion
from ..odoo.client import map_factura
from ..repositories import Repository


def clasificar_diferencia(
    total_motor: Decimal,
    monto_odoo: Decimal,
    ncs_odoo: Decimal,
    config: ReconciliationConfig,
    so_id: str = "",
) -> Conciliacion:
    """Compara el neto del motor contra el neto real de Odoo y aplica el semáforo."""
    neto_odoo = monto_odoo - ncs_odoo
    diferencia = q2(total_motor - neto_odoo)
    magnitud = abs(diferencia)
    if magnitud <= config.tolerance_rounding:
        resultado = ResultadoConciliacion.VERDE
    elif magnitud <= config.tolerance_red:
        resultado = ResultadoConciliacion.AMARILLO
    else:
        resultado = ResultadoConciliacion.ROJO
    return Conciliacion(
        so_id=so_id,
        total_motor=q2(total_motor),
        monto_odoo=q2(monto_odoo),
        ncs_odoo=q2(ncs_odoo),
        diferencia=diferencia,
        resultado=resultado,
    )


@dataclass(frozen=True)
class NetoFacturado:
    monto_facturado: Decimal
    ncs: Decimal
    facturada: bool


class FacturasOdoo(ABC):
    """Lee de Odoo la factura real + NCs de una orden (solo lectura)."""

    @abstractmethod
    def neto_facturado(self, so_id: str) -> NetoFacturado: ...

    def neto_facturado_batch(self, so_ids: list[str]) -> dict[str, NetoFacturado]:
        """Batch por defecto = N llamadas individuales (compatibilidad hacia

        atrás para implementaciones que no la sobrescriban). ``OdooFacturas
        Reader`` la sobrescribe con UNA sola consulta -- ver su docstring."""
        return {so_id: self.neto_facturado(so_id) for so_id in so_ids}


class OdooFacturasReader(FacturasOdoo):
    """Lee account.move (facturas y NCs) de una orden vía ``execute`` inyectable.

    La compañía factura en VES; se usa ``amount_total_signed_usd`` (equivalente
    USD a la tasa registrada en la factura). La orden se liga por
    ``invoice_origin = SO.name``.
    """

    MODEL = "account.move"
    FIELDS = ["id", "invoice_origin", "amount_total_signed_usd", "move_type", "state"]

    def __init__(
        self,
        execute: Callable[[str, str, list[Any], dict[str, Any]], Any],
    ) -> None:
        self._execute = execute

    def neto_facturado(self, so_id: str) -> NetoFacturado:
        registros: list[dict[str, Any]] = self._execute(
            self.MODEL,
            "search_read",
            [
                [
                    ["invoice_origin", "=", so_id],
                    ["state", "=", "posted"],
                    ["move_type", "in", ["out_invoice", "out_refund"]],
                ]
            ],
            {"fields": self.FIELDS},
        )
        monto = Decimal("0")
        ncs = Decimal("0")
        facturada = False
        for rec in registros:
            _so, m, n = map_factura(rec)
            monto += m
            ncs += n
            facturada = True
        return NetoFacturado(monto_facturado=monto, ncs=ncs, facturada=facturada)

    def neto_facturado_batch(self, so_ids: list[str]) -> dict[str, NetoFacturado]:
        """UNA sola consulta a Odoo para TODAS las órdenes (Fase 2 del plan

        de rendimiento del usuario, agosto 2026 -- hallazgo real: ``Reconciler.
        run()`` llamaba ``neto_facturado`` una vez POR orden en un loop, un
        N+1 clásico que con ~780 órdenes reales explicaba buena parte de los
        ~7 minutos del ciclo "Recálculo completo". Agrupa por ``so_id`` en
        Python en vez de una query por orden -- mismo patrón que ya usa
        ``get_reporte_saldos`` para ``sale.order``.
        """
        vacio = NetoFacturado(Decimal("0"), Decimal("0"), False)
        result: dict[str, NetoFacturado] = dict.fromkeys(so_ids, vacio)
        if not so_ids:
            return result
        registros: list[dict[str, Any]] = self._execute(
            self.MODEL,
            "search_read",
            [
                [
                    ["invoice_origin", "in", so_ids],
                    ["state", "=", "posted"],
                    ["move_type", "in", ["out_invoice", "out_refund"]],
                ]
            ],
            {"fields": self.FIELDS},
        )
        montos: dict[str, Decimal] = {}
        ncs_map: dict[str, Decimal] = {}
        facturadas: set[str] = set()
        for rec in registros:
            so, m, n = map_factura(rec)
            if so not in result:
                continue
            montos[so] = montos.get(so, Decimal("0")) + m
            ncs_map[so] = ncs_map.get(so, Decimal("0")) + n
            facturadas.add(so)
        for so in facturadas:
            result[so] = NetoFacturado(
                monto_facturado=montos.get(so, Decimal("0")),
                ncs=ncs_map.get(so, Decimal("0")),
                facturada=True,
            )
        return result


class Reconciler:
    def __init__(
        self, repo: Repository, facturas: FacturasOdoo, config: ReconciliationConfig
    ) -> None:
        self._repo = repo
        self._facturas = facturas
        self._config = config

    def run(self) -> list[Conciliacion]:
        """Concilia toda la bandeja contra Odoo. Devuelve las filas conciliadas.

        Persiste en UNA sola escritura por lote (no una por orden) -- con
        cientos de órdenes, escribir de a una agota la cuota de la API de
        Sheets casi de inmediato. Lee las facturas de TODAS las órdenes en
        UNA sola consulta (``neto_facturado_batch``) en vez de una por
        orden -- ver su docstring (Fase 2, hallazgo real agosto 2026).
        """
        bandejas = self._repo.all_bandeja()
        netos = self._facturas.neto_facturado_batch([b.so_id for b in bandejas])
        resultados: list[Conciliacion] = []
        for bandeja in bandejas:
            neto = netos.get(bandeja.so_id, NetoFacturado(Decimal("0"), Decimal("0"), False))
            conc = clasificar_diferencia(
                bandeja.total_motor,
                neto.monto_facturado,
                neto.ncs,
                self._config,
                so_id=bandeja.so_id,
            )
            resultados.append(conc)
        self._repo.upsert_conciliaciones(resultados)
        return resultados
