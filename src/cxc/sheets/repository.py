"""``SheetsRepository`` — implementación de ``Repository`` sobre un ``SheetGateway``.

Traduce cada método del repositorio a lecturas/escrituras de pestañas, usando los
serializadores de ``serde``. Respeta la regla de oro: ``append_serie_tasa`` solo
agrega (SerieTasas es inmutable) y los ``upsert_*`` de espejo no tocan las tablas
de trabajo humano.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..models import (
    BandejaFacturacion,
    Cliente,
    Conciliacion,
    DescuentoBCVCompleto,
    DescuentoMarcaCategoria,
    DescuentoProntoPago,
    DescuentoRecompra,
    DescuentoProducto,
    DescuentoDiferencialCambiario,
    DescuentoVolumen,
    ExclusionRegla,
    Feriado,
    LineaOrden,
    MetodoPago,
    OrdenVenta,
    Pago,
    PromocionPrimeraCompra,
    ReglaRecurrencia,
    SerieTasa,
    Vinculacion,
)
from ..repositories import Repository
from . import gateway as g
from . import serde

_META_LAST_SYNC = "last_sync"


class SheetsRepository(Repository):
    def __init__(self, gateway: g.SheetGateway) -> None:
        self._g = gateway

    # --- SerieTasas ----------------------------------------------------------
    def _serie_rows(self) -> list[SerieTasa]:
        return [serde.serie_from_row(r) for r in self._g.read_rows(g.T_SERIE)]

    def fechas_historicas_map(self) -> dict[str, str]:
        """Retorna {so_id_normalizado: fecha_iso} desde la pestaña FechasHistoricas de Google Sheets."""
        try:
            rows = self._g.read_rows(g.T_FECHAS_HISTORICAS)
            res = {}
            import re
            for r in rows:
                so_id = str(r.get("so_id", "")).strip()
                fecha = str(r.get("fecha_historica", "")).strip()
                if so_id and fecha:
                    digits = re.sub(r"[^\d]", "", so_id)
                    if digits:
                        res[str(int(digits))] = fecha
                    res[so_id.upper()] = fecha
            return res
        except Exception:
            return {}

    def last_serie_tasa(self) -> SerieTasa | None:
        filas = self._serie_rows()
        return filas[-1] if filas else None

    def append_serie_tasa(self, fila: SerieTasa) -> None:
        self._g.append_row(g.T_SERIE, serde.serie_to_row(fila))

    def trailing_failed_captures(self) -> int:
        count = 0
        for fila in reversed(self._serie_rows()):
            if fila.capturada_ok:
                break
            count += 1
        return count

    # --- Cursor --------------------------------------------------------------
    def get_last_sync(self) -> datetime | None:
        raw = self._g.get_meta(_META_LAST_SYNC)
        return serde.p_dt(raw) if raw else None

    def set_last_sync(self, cursor: datetime) -> None:
        self._g.set_meta(_META_LAST_SYNC, serde.s_dt(cursor))

    # --- Espejo (upsert por PK) ---------------------------------------------
    def upsert_clientes(self, filas: list[Cliente]) -> None:
        self._g.upsert_rows(
            g.T_CLIENTES, "cliente_id", [serde.cliente_to_row(c) for c in filas]
        )

    def upsert_ordenes(self, filas: list[OrdenVenta]) -> None:
        self._g.upsert_rows(
            g.T_ORDENES, "so_id", [serde.orden_to_row(o) for o in filas]
        )

    def upsert_lineas(self, filas: list[LineaOrden]) -> None:
        self._g.upsert_rows(
            g.T_LINEAS, "linea_id", [serde.linea_to_row(ln) for ln in filas]
        )

    def upsert_pagos(self, filas: list[Pago]) -> None:
        self._g.upsert_rows(
            g.T_PAGOS, "pago_id", [serde.pago_to_row(p) for p in filas]
        )

    # --- Lecturas ------------------------------------------------------------
    def get_cliente(self, cliente_id: str) -> Cliente | None:
        for r in self._g.read_rows(g.T_CLIENTES):
            if r.get("cliente_id") == cliente_id:
                return serde.cliente_from_row(r)
        return None

    def get_orden(self, so_id: str) -> OrdenVenta | None:
        for r in self._g.read_rows(g.T_ORDENES):
            if r.get("so_id") == so_id:
                return serde.orden_from_row(r)
        return None

    def all_ordenes(self) -> list[OrdenVenta]:
        return [serde.orden_from_row(r) for r in self._g.read_rows(g.T_ORDENES)]

    def lineas_de_orden(self, so_id: str) -> list[LineaOrden]:
        return [
            serde.linea_from_row(r)
            for r in self._g.read_rows(g.T_LINEAS)
            if r.get("so_id") == so_id
        ]

    def get_pago(self, pago_id: str) -> Pago | None:
        for r in self._g.read_rows(g.T_PAGOS):
            if r.get("pago_id") == pago_id:
                return serde.pago_from_row(r)
        return None

    def get_metodo_pago(self, metodo_id: str) -> MetodoPago | None:
        for r in self._g.read_rows(g.T_METODOS):
            if r.get("metodo_id") == metodo_id:
                return serde.metodo_from_row(r)
        return None

    def vinculaciones_de_orden(self, so_id: str) -> list[Vinculacion]:
        return [
            serde.vinculacion_from_row(r)
            for r in self._g.read_rows(g.T_VINCULACIONES)
            if r.get("so_id") == so_id
        ]

    def all_vinculaciones(self) -> list[Vinculacion]:
        return [
            serde.vinculacion_from_row(r)
            for r in self._g.read_rows(g.T_VINCULACIONES)
        ]

    def update_vinculacion(self, vinc: Vinculacion) -> None:
        self._g.upsert_row(
            g.T_VINCULACIONES, "vinc_id", serde.vinculacion_to_row(vinc)
        )

    def descuentos_marca_categoria(self) -> list[DescuentoMarcaCategoria]:
        rows = self._g.read_rows("DescuentosProntoPago")
        if not rows:
            rows = self._g.read_rows(g.T_DESCUENTOS)
        return [serde.pronto_pago_from_row(r) for r in rows]

    def descuentos_pronto_pago(self) -> list[DescuentoProntoPago]:
        return self.descuentos_marca_categoria()

    def descuentos_recompra(self) -> list[DescuentoRecompra]:
        rows = self._g.read_rows("DescuentosRecompra")
        if not rows:
            return [DescuentoRecompra(regla_id="RECOMPRA_DEFAULT", porcentaje=Decimal("0.05"), max_usos_mes=2, dias_ventana=30)]
        return [serde.recompra_from_row(r) for r in rows]

    def descuentos_producto(self) -> list[DescuentoProducto]:
        return [serde.producto_from_row(r) for r in self._g.read_rows("DescuentosProducto")]

    def descuentos_diferencial_cambiario(self) -> list[DescuentoDiferencialCambiario]:
        rows = self._g.read_rows("DescuentosDiferencialCambiario")
        if not rows:
            return [
                DescuentoDiferencialCambiario(regla_id="DIF_35_VES", nombre="35% Fijo VES a USD", tipo_diferencial="fijo_35_ves_usd", tipo_calculo="fijo", porcentaje_fijo=Decimal("0.35"), unidad_medida="USD", monedas_aplicables="USD", listas_aplicables="LISTAS_VES"),
                DescuentoDiferencialCambiario(regla_id="DIF_EQUIPARAR", nombre="Equiparar Binance N/C", tipo_diferencial="equiparar_binance", tipo_calculo="variable", porcentaje_fijo=Decimal("0"), unidad_medida="USD", monedas_aplicables="*", listas_aplicables="LISTAS_VES"),
                DescuentoDiferencialCambiario(regla_id="DIF_BRECHA_CIERRE", nombre="Brecha BCV vs Binance Cierre", tipo_diferencial="diferencial_bcv_binance", tipo_calculo="variable", porcentaje_fijo=Decimal("0"), unidad_medida="USD", monedas_aplicables="*", listas_aplicables="LISTAS_VES")
            ]
        return [serde.diferencial_from_row(r) for r in rows]

    def descuentos_volumen(self) -> list[DescuentoVolumen]:
        return [
            serde.desc_volumen_from_row(r) for r in self._g.read_rows("DescuentosVolumen")
        ]

    def reglas_recurrencia(self) -> list[ReglaRecurrencia]:
        return [serde.regla_from_row(r) for r in self._g.read_rows(g.T_REGLAS)]

    def descuento_bcv_completo(self) -> list[DescuentoBCVCompleto]:
        return [
            serde.bcv_completo_from_row(r)
            for r in self._g.read_rows(g.T_BCV_COMPLETO)
        ]

    def promociones_primera_compra(self) -> list[PromocionPrimeraCompra]:
        return [
            serde.promocion_from_row(r)
            for r in self._g.read_rows(g.T_PROMO_PRIMERA)
        ]

    def feriados(self) -> list[Feriado]:
        return [serde.feriado_from_row(r) for r in self._g.read_rows(g.T_FERIADOS)]

    def exclusiones(self) -> list[ExclusionRegla]:
        return [serde.exclusion_from_row(r) for r in self._g.read_rows("Exclusiones")]

    def save_exclusion(self, rule: ExclusionRegla) -> None:
        ws = self._g._ws("Exclusiones")
        records = ws.get_all_records()
        row_idx = None
        for i, r in enumerate(records):
            if (r.get("regla_tipo_a") == rule.regla_tipo_a and r.get("regla_tipo_b") == rule.regla_tipo_b) or \
               (r.get("regla_tipo_a") == rule.regla_tipo_b and r.get("regla_tipo_b") == rule.regla_tipo_a):
                row_idx = i + 2
                break
        
        row_data = serde.exclusion_to_row(rule)
        if row_idx is not None:
            ws.update(f"A{row_idx}:C{row_idx}", [[row_data["regla_tipo_a"], row_data["regla_tipo_b"], str(row_data["activo"])]])
        else:
            ws.append_row([row_data["regla_tipo_a"], row_data["regla_tipo_b"], str(row_data["activo"])])

    # --- Bandeja -------------------------------------------------------------
    def upsert_bandeja(self, fila: BandejaFacturacion) -> None:
        self._g.upsert_row(g.T_BANDEJA, "so_id", serde.bandeja_to_row(fila))

    def get_bandeja(self, so_id: str) -> BandejaFacturacion | None:
        for r in self._g.read_rows(g.T_BANDEJA):
            if r.get("so_id") == so_id:
                return serde.bandeja_from_row(r)
        return None

    def all_bandeja(self) -> list[BandejaFacturacion]:
        return [serde.bandeja_from_row(r) for r in self._g.read_rows(g.T_BANDEJA)]

    # --- Conciliación --------------------------------------------------------
    def upsert_conciliacion(self, fila: Conciliacion) -> None:
        self._g.upsert_row(g.T_CONCILIACION, "so_id", serde.conciliacion_to_row(fila))

    def all_conciliaciones(self) -> list[Conciliacion]:
        return [
            serde.conciliacion_from_row(r)
            for r in self._g.read_rows(g.T_CONCILIACION)
        ]
