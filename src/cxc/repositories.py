"""Acceso a datos — interfaz abstracta + implementación en memoria.

La lógica de negocio (motor, conciliación, etc.) NO conoce Google Sheets ni
Odoo: opera sobre estas interfaces. En producción se usa ``SheetsRepository``
(cxc.sheets); en tests, ``InMemoryRepository``. Eso permite probar todo sin red.

Regla de oro de la plomería (sección 1.2): los métodos de escritura de las
tablas-espejo (clientes, órdenes, líneas, pagos, estado factura) están separados
de las tablas de trabajo humano (Vinculaciones, Bandeja) y de la auditoría
inmutable (SerieTasas, solo append). El sync usa SOLO los `replace/upsert` de
espejo; nunca toca las demás.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from .models import (
    BandejaFacturacion,
    Cliente,
    Conciliacion,
    DescuentoBCVCompleto,
    DescuentoMarcaCategoria,
    Feriado,
    LineaOrden,
    MetodoPago,
    OrdenVenta,
    Pago,
    ReglaRecurrencia,
    SerieTasa,
    Vinculacion,
)


class Repository(ABC):
    """Contrato de persistencia para todas las piezas backend."""

    # --- Auditoría inmutable: SerieTasas (APPEND ONLY) -----------------------
    @abstractmethod
    def last_serie_tasa(self) -> SerieTasa | None: ...

    @abstractmethod
    def append_serie_tasa(self, fila: SerieTasa) -> None: ...

    @abstractmethod
    def trailing_failed_captures(self) -> int:
        """Nº de capturas fallidas consecutivas al final de la serie."""

    # --- Cursor de sync ------------------------------------------------------
    @abstractmethod
    def get_last_sync(self) -> datetime | None: ...

    @abstractmethod
    def set_last_sync(self, cursor: datetime) -> None: ...

    # --- Tablas-espejo (solo el sync escribe) --------------------------------
    @abstractmethod
    def upsert_clientes(self, filas: list[Cliente]) -> None: ...

    @abstractmethod
    def upsert_ordenes(self, filas: list[OrdenVenta]) -> None: ...

    @abstractmethod
    def upsert_lineas(self, filas: list[LineaOrden]) -> None: ...

    @abstractmethod
    def upsert_pagos(self, filas: list[Pago]) -> None: ...

    # --- Lecturas para el motor ---------------------------------------------
    @abstractmethod
    def get_cliente(self, cliente_id: str) -> Cliente | None: ...

    @abstractmethod
    def get_orden(self, so_id: str) -> OrdenVenta | None: ...

    @abstractmethod
    def lineas_de_orden(self, so_id: str) -> list[LineaOrden]: ...

    @abstractmethod
    def get_pago(self, pago_id: str) -> Pago | None: ...

    @abstractmethod
    def get_metodo_pago(self, metodo_id: str) -> MetodoPago | None: ...

    @abstractmethod
    def vinculaciones_de_orden(self, so_id: str) -> list[Vinculacion]: ...

    @abstractmethod
    def all_vinculaciones(self) -> list[Vinculacion]: ...

    @abstractmethod
    def update_vinculacion(self, vinc: Vinculacion) -> None: ...

    @abstractmethod
    def descuentos_marca_categoria(self) -> list[DescuentoMarcaCategoria]: ...

    @abstractmethod
    def reglas_recurrencia(self) -> list[ReglaRecurrencia]: ...

    @abstractmethod
    def descuento_bcv_completo(self) -> list[DescuentoBCVCompleto]: ...

    @abstractmethod
    def feriados(self) -> list[Feriado]: ...

    # --- Bandeja de facturación (salida del motor) ---------------------------
    @abstractmethod
    def upsert_bandeja(self, fila: BandejaFacturacion) -> None: ...

    @abstractmethod
    def get_bandeja(self, so_id: str) -> BandejaFacturacion | None: ...

    @abstractmethod
    def all_bandeja(self) -> list[BandejaFacturacion]: ...

    # --- Conciliación --------------------------------------------------------
    @abstractmethod
    def upsert_conciliacion(self, fila: Conciliacion) -> None: ...

    @abstractmethod
    def all_conciliaciones(self) -> list[Conciliacion]: ...


class InMemoryRepository(Repository):
    """Implementación en memoria — usada en tests y como referencia semántica."""

    def __init__(self) -> None:
        self._serie: list[SerieTasa] = []
        self._last_sync: datetime | None = None
        self._clientes: dict[str, Cliente] = {}
        self._ordenes: dict[str, OrdenVenta] = {}
        self._lineas: dict[str, LineaOrden] = {}
        self._pagos: dict[str, Pago] = {}
        self._metodos: dict[str, MetodoPago] = {}
        self._vinculaciones: dict[str, Vinculacion] = {}
        self._descuentos: list[DescuentoMarcaCategoria] = []
        self._reglas: list[ReglaRecurrencia] = []
        self._bcv_diario: list[DescuentoBCVCompleto] = []
        self._feriados: list[Feriado] = []
        self._bandeja: dict[str, BandejaFacturacion] = {}
        self._conciliaciones: dict[str, Conciliacion] = {}

    # --- SerieTasas ----------------------------------------------------------
    def last_serie_tasa(self) -> SerieTasa | None:
        return self._serie[-1] if self._serie else None

    def append_serie_tasa(self, fila: SerieTasa) -> None:
        self._serie.append(fila)

    def trailing_failed_captures(self) -> int:
        count = 0
        for fila in reversed(self._serie):
            if fila.capturada_ok:
                break
            count += 1
        return count

    def all_serie_tasas(self) -> list[SerieTasa]:
        return list(self._serie)

    # --- Cursor --------------------------------------------------------------
    def get_last_sync(self) -> datetime | None:
        return self._last_sync

    def set_last_sync(self, cursor: datetime) -> None:
        self._last_sync = cursor

    # --- Espejo (upsert por PK) ---------------------------------------------
    def upsert_clientes(self, filas: list[Cliente]) -> None:
        for c in filas:
            self._clientes[c.cliente_id] = c

    def upsert_ordenes(self, filas: list[OrdenVenta]) -> None:
        for o in filas:
            self._ordenes[o.so_id] = o

    def upsert_lineas(self, filas: list[LineaOrden]) -> None:
        for ln in filas:
            self._lineas[ln.linea_id] = ln

    def upsert_pagos(self, filas: list[Pago]) -> None:
        for p in filas:
            self._pagos[p.pago_id] = p

    # --- Lecturas ------------------------------------------------------------
    def get_cliente(self, cliente_id: str) -> Cliente | None:
        return self._clientes.get(cliente_id)

    def get_orden(self, so_id: str) -> OrdenVenta | None:
        return self._ordenes.get(so_id)

    def all_ordenes(self) -> list[OrdenVenta]:
        return list(self._ordenes.values())

    def lineas_de_orden(self, so_id: str) -> list[LineaOrden]:
        return [ln for ln in self._lineas.values() if ln.so_id == so_id]

    def get_pago(self, pago_id: str) -> Pago | None:
        return self._pagos.get(pago_id)

    def get_metodo_pago(self, metodo_id: str) -> MetodoPago | None:
        return self._metodos.get(metodo_id)

    def add_metodo_pago(self, metodo: MetodoPago) -> None:
        self._metodos[metodo.metodo_id] = metodo

    def vinculaciones_de_orden(self, so_id: str) -> list[Vinculacion]:
        return [v for v in self._vinculaciones.values() if v.so_id == so_id]

    def all_vinculaciones(self) -> list[Vinculacion]:
        return list(self._vinculaciones.values())

    def add_vinculacion(self, vinc: Vinculacion) -> None:
        self._vinculaciones[vinc.vinc_id] = vinc

    def update_vinculacion(self, vinc: Vinculacion) -> None:
        self._vinculaciones[vinc.vinc_id] = vinc

    def descuentos_marca_categoria(self) -> list[DescuentoMarcaCategoria]:
        return list(self._descuentos)

    def add_descuento(self, regla: DescuentoMarcaCategoria) -> None:
        self._descuentos.append(regla)

    def reglas_recurrencia(self) -> list[ReglaRecurrencia]:
        return list(self._reglas)

    def add_regla_recurrencia(self, regla: ReglaRecurrencia) -> None:
        self._reglas.append(regla)

    def descuento_bcv_completo(self) -> list[DescuentoBCVCompleto]:
        return list(self._bcv_diario)

    def add_descuento_bcv_completo(self, regla: DescuentoBCVCompleto) -> None:
        self._bcv_diario.append(regla)

    def feriados(self) -> list[Feriado]:
        return list(self._feriados)

    def add_feriado(self, feriado: Feriado) -> None:
        self._feriados.append(feriado)

    # --- Bandeja -------------------------------------------------------------
    def upsert_bandeja(self, fila: BandejaFacturacion) -> None:
        self._bandeja[fila.so_id] = fila

    def get_bandeja(self, so_id: str) -> BandejaFacturacion | None:
        return self._bandeja.get(so_id)

    def all_bandeja(self) -> list[BandejaFacturacion]:
        return list(self._bandeja.values())

    # --- Conciliación --------------------------------------------------------
    def upsert_conciliacion(self, fila: Conciliacion) -> None:
        self._conciliaciones[fila.so_id] = fila

    def all_conciliaciones(self) -> list[Conciliacion]:
        return list(self._conciliaciones.values())
