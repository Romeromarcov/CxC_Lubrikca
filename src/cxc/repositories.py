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
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .models import (
    BandejaFacturacion,
    Cliente,
    Conciliacion,
    Condicion,
    DescuentoDiferencialCambiario,
    DescuentoMarcaCategoria,
    DescuentoProducto,
    DescuentoRecompra,
    DescuentoVolumen,
    Entrega,
    ExclusionRegla,
    Factura,
    Feriado,
    LineaOrden,
    MetodoPago,
    OrdenVenta,
    Pago,
    PromocionPrimeraCompra,
    ReglaRecurrencia,
    SerieTasa,
    TipoBeneficio,
    VentasTeorico,
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

    @abstractmethod
    def serie_tasas_del_dia(self, fecha: date) -> list[SerieTasa]:
        """Todas las capturas de SerieTasas del día indicado (para validar

        min/max histórico al editar una tasa manualmente).
        """

    # --- Cursor de sync ------------------------------------------------------
    @abstractmethod
    def get_last_sync(self) -> datetime | None: ...

    @abstractmethod
    def set_last_sync(self, cursor: datetime) -> None: ...

    # --- Configuración genérica clave/valor (_Meta en Sheets, app_settings en
    # Postgres) -- separado del cursor de sync (get_last_sync/set_last_sync,
    # que usan la misma tabla pero con una clave reservada). ------------------
    @abstractmethod
    def get_config(self, key: str) -> str | None: ...

    @abstractmethod
    def set_config(self, key: str, value: str) -> None: ...

    @abstractmethod
    def all_config(self) -> dict[str, str]: ...

    def invalidate_cache(self, table: str | None = None) -> None:
        """No-op por defecto -- solo ``SheetsRepository`` tiene una caché de

        lectura (``GspreadGateway``, TTL 120s) que a veces hay que forzar a
        refrescar tras un escritura; Postgres siempre lee en vivo.
        """
        return None

    # --- Usuarios de la plataforma (login) -- filas crudas email/nombre/
    # rol/password_hash/salt/activo/fecha_registro, igual forma en ambos
    # backends (ver cxc.auth, que las consume como dict). ---------------------
    @abstractmethod
    def get_usuario_plataforma(self, email: str) -> dict[str, str] | None: ...

    @abstractmethod
    def all_usuarios_plataforma(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def upsert_usuario_plataforma(self, row: dict[str, str]) -> None: ...

    # --- Tablas-espejo (solo el sync escribe) --------------------------------
    @abstractmethod
    def upsert_clientes(self, filas: list[Cliente]) -> None: ...

    @abstractmethod
    def upsert_ordenes(self, filas: list[OrdenVenta]) -> None: ...

    @abstractmethod
    def upsert_lineas(self, filas: list[LineaOrden]) -> None: ...

    @abstractmethod
    def delete_lineas(self, linea_ids: list[str]) -> None:
        """Borra líneas del espejo local por ``linea_id`` (agosto 2026: una

        línea eliminada en Odoo nunca aparece en ``changed_lineas`` -- no hay
        ``write_date`` de un registro que ya no existe -- así que el sync
        necesita poder borrar explícitamente lo que Odoo confirma que ya no
        existe. Ver ``sync.incremental.IncrementalSync`` y hallazgo real
        orden S00792."""
        ...

    @abstractmethod
    def upsert_pagos(self, filas: list[Pago]) -> None: ...

    # Facturas (Fase 0 del plan de consolidación de fuentes, agosto 2026):
    # espejo INMUTABLE de account.move (facturas/NC/ND) -- NO amount_residual
    # (eso sigue siendo dominio de Cobranza, en vivo). Métodos concretos (no
    # @abstractmethod) con default que avisa "no implementado" en vez de
    # forzar a todo backend a soportarlo -- hoy solo PostgresRepository lo
    # implementa; Sheets se está retirando como backend, no vale la pena
    # construirle esta tabla nueva.
    def upsert_facturas(self, filas: list[Factura]) -> None:
        raise NotImplementedError(
            "upsert_facturas solo está implementado en PostgresRepository."
        )

    def all_facturas(self) -> list[Factura]:
        raise NotImplementedError("all_facturas solo está implementado en PostgresRepository.")

    def upsert_entregas(self, filas: list[Entrega]) -> None:
        raise NotImplementedError(
            "upsert_entregas solo está implementado en PostgresRepository."
        )

    def all_entregas(self) -> list[Entrega]:
        raise NotImplementedError("all_entregas solo está implementado en PostgresRepository.")

    # --- Lecturas para el motor ---------------------------------------------
    @abstractmethod
    def get_cliente(self, cliente_id: str) -> Cliente | None: ...

    @abstractmethod
    def all_clientes(self) -> list[Cliente]: ...

    @abstractmethod
    def get_orden(self, so_id: str) -> OrdenVenta | None: ...

    @abstractmethod
    def all_ordenes(self) -> list[OrdenVenta]: ...

    @abstractmethod
    def lineas_de_orden(self, so_id: str) -> list[LineaOrden]: ...

    @abstractmethod
    def all_lineas(self) -> list[LineaOrden]: ...

    @abstractmethod
    def get_pago(self, pago_id: str) -> Pago | None: ...

    @abstractmethod
    def all_pagos(self) -> list[Pago]: ...

    # --- Pagos + columnas humanas de cobranza (recibido/numero_recibido/
    # fecha_recibido/recibido_por) -- fuera del dataclass Pago porque el
    # sync (upsert_pagos) nunca las toca; solo /api/cobranza/marcar-recibido
    # las escribe. Fila cruda dict[str,str], mismo shape en ambos backends
    # (igual que lo que daba GspreadGateway.read_rows("Pagos")). ------------
    @abstractmethod
    def all_pagos_full(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def marcar_pagos_recibido(
        self, pago_ids: list[str], numero_recibido: str, fecha_recibido: datetime, recibido_por: str
    ) -> list[dict[str, str]]: ...

    @abstractmethod
    def get_metodo_pago(self, metodo_id: str) -> MetodoPago | None: ...

    @abstractmethod
    def all_serie_tasas(self) -> list[SerieTasa]: ...

    @abstractmethod
    def vinculaciones_de_orden(self, so_id: str) -> list[Vinculacion]: ...

    @abstractmethod
    def all_vinculaciones(self) -> list[Vinculacion]: ...

    @abstractmethod
    def update_vinculacion(self, vinc: Vinculacion) -> None: ...

    @abstractmethod
    def update_vinculaciones(self, vincs: list[Vinculacion]) -> None:
        """Igual que update_vinculacion pero en una sola escritura por lote.

        Usar cuando se actualizan muchas Vinculaciones a la vez (ej. un
        recálculo completo del motor) para no gastar la cuota de la API de
        Sheets con N escrituras individuales.
        """

    @abstractmethod
    def descuentos_marca_categoria(self) -> list[DescuentoMarcaCategoria]: ...

    @abstractmethod
    def descuentos_volumen(self) -> list[DescuentoVolumen]: ...

    @abstractmethod
    def descuentos_recompra(self) -> list[DescuentoRecompra]: ...

    @abstractmethod
    def descuentos_diferencial_cambiario(self) -> list[DescuentoDiferencialCambiario]: ...

    @abstractmethod
    def descuentos_producto(self) -> list[DescuentoProducto]: ...

    # --- Altas de reglas de descuento (config; panel de administración) -----
    @abstractmethod
    def append_descuento_pronto_pago(self, regla: DescuentoMarcaCategoria) -> None: ...

    @abstractmethod
    def append_descuento_volumen(self, regla: DescuentoVolumen) -> None: ...

    @abstractmethod
    def append_descuento_recompra(self, regla: DescuentoRecompra) -> None: ...

    @abstractmethod
    def append_descuento_producto(self, regla: DescuentoProducto) -> None: ...

    @abstractmethod
    def append_descuento_diferencial_cambiario(
        self, regla: DescuentoDiferencialCambiario
    ) -> None: ...

    @abstractmethod
    def append_promocion_primera_compra(self, regla: PromocionPrimeraCompra) -> None: ...

    # --- Borrado / activación de reglas por nombre de tabla lógica (panel de
    # administración -- "tabla" es uno de: DescuentosRecompra,
    # DescuentosProntoPago (alias legacy DescuentosMarcaCategoria),
    # DescuentosVolumen, PromocionPrimeraCompra, DescuentosProducto,
    # DescuentosDiferencialCambiario). ----------------------------------------
    @abstractmethod
    def delete_regla(self, tabla: str, regla_id: str) -> bool: ...

    @abstractmethod
    def set_regla_activo(self, tabla: str, regla_id: str, activo: bool) -> bool: ...

    # --- Tablas de auditoría/histórico -- filas crudas dict[str,str], mismo
    # shape en ambos backends (equivalentes a lo que daba
    # GspreadGateway.read_rows para cada pestaña). ---------------------------
    @abstractmethod
    def all_anomalias_aceptadas(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def append_anomalia_aceptada(self, row: dict[str, str]) -> None: ...

    @abstractmethod
    def all_listas_precios_historicas(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def replace_listas_precios_historicas(self, rows: list[dict[str, str]]) -> None: ...

    @abstractmethod
    def all_tasas_historicas_auditoria(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def all_pagos_huerfanos_cerrados(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def upsert_pago_huerfano_cerrado(self, row: dict[str, str]) -> None: ...

    @abstractmethod
    def all_pagos_tasa_binance_override(self) -> list[dict[str, str]]:
        """Correcciones manuales de tasa Binance para pagos AÚN PENDIENTES

        (sin Vinculación real todavía). Una vez el pago se vincula a una
        orden, la Vinculación real toma el control -- este registro
        simplemente deja de leerse para ese pago_id.
        """

    @abstractmethod
    def upsert_pago_tasa_binance_override(self, row: dict[str, str]) -> None: ...

    @abstractmethod
    def all_descuentos_sistema_aprobados(self) -> list[dict[str, str]]:
        """Descuentos aprobados manualmente desde la Bandeja 1 de

        Facturación -- nunca se escriben a Odoo, solo ajustan los saldos
        internos de CxC en /api/ventas.
        """

    @abstractmethod
    def upsert_descuento_sistema_aprobado(self, row: dict[str, str]) -> None: ...

    @abstractmethod
    def all_reglas_dias_credito_volumen(self) -> list[dict[str, str]]:
        """Rangos de litros -> máximo de días de crédito permitido.

        Config pura, usada SOLO como validación/alerta en Ventas contra el
        plazo de pago real que Odoo otorgó -- no alimenta la fórmula de
        recompra (esa usa el plazo REAL de la orden anterior).
        """

    @abstractmethod
    def upsert_regla_dias_credito_volumen(self, row: dict[str, str]) -> None: ...

    @abstractmethod
    def replace_tasas_historicas_auditoria(self, rows: list[dict[str, str]]) -> None:
        """Reemplaza la tabla completa (scripts/cargar_tasas_historicas.py

        regenera el archivo diario entero desde Odoo + SerieTasas cada vez
        que corre -- no es un upsert incremental).
        """

    @abstractmethod
    def upsert_tasa_historica_auditoria(self, row: dict[str, str]) -> None:
        """Upsert de UNA fila por ``fecha`` (clave natural) -- a diferencia

        de ``replace_tasas_historicas_auditoria`` (reemplazo completo), esto
        es incremental: usado por el scraper para grabar el promedio Binance
        definitivo + diferencial de cierre de día, sin tocar el resto de la
        tabla.
        """

    @abstractmethod
    def reglas_recurrencia(self) -> list[ReglaRecurrencia]: ...

    @abstractmethod
    def set_regla_recurrencia_porcentaje(self, condicion: str, valor: Decimal) -> None:
        """Actualiza el porcentaje de la regla de recurrencia con esa

        condición (p.ej. "recompra"); si no existe ninguna, crea una nueva
        vigente desde hoy. Usado por ``/api/config/meta`` (ajuste global de
        % de descuento por recompra).
        """

    @abstractmethod
    def promociones_primera_compra(self) -> list[PromocionPrimeraCompra]: ...

    @abstractmethod
    def feriados(self) -> list[Feriado]: ...

    @abstractmethod
    def append_feriado(self, feriado: Feriado) -> None: ...

    @abstractmethod
    def exclusiones(self) -> list[ExclusionRegla]: ...

    @abstractmethod
    def save_exclusion(self, rule: ExclusionRegla) -> None: ...

    # --- Bandeja de facturación (salida del motor) ---------------------------
    @abstractmethod
    def upsert_bandeja(self, fila: BandejaFacturacion) -> None: ...

    @abstractmethod
    def upsert_bandejas(self, filas: list[BandejaFacturacion]) -> None:
        """Igual que upsert_bandeja pero en una sola escritura por lote."""

    @abstractmethod
    def get_bandeja(self, so_id: str) -> BandejaFacturacion | None: ...

    @abstractmethod
    def all_bandeja(self) -> list[BandejaFacturacion]: ...

    # --- Teóricos de Ventas (Fase 10 -- fijos, fuera de Bandeja) --------------
    @abstractmethod
    def upsert_ventas_teorico(self, fila: VentasTeorico) -> None: ...

    @abstractmethod
    def get_ventas_teorico(self, so_id: str) -> VentasTeorico | None: ...

    @abstractmethod
    def all_ventas_teoricos(self) -> list[VentasTeorico]: ...

    # --- Conciliación --------------------------------------------------------
    @abstractmethod
    def upsert_conciliacion(self, fila: Conciliacion) -> None: ...

    @abstractmethod
    def upsert_conciliaciones(self, filas: list[Conciliacion]) -> None:
        """Igual que upsert_conciliacion pero en una sola escritura por lote."""

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
        self._facturas: dict[str, Factura] = {}
        self._entregas: dict[str, Entrega] = {}
        self._metodos: dict[str, MetodoPago] = {}
        self._vinculaciones: dict[str, Vinculacion] = {}
        self._descuentos: list[DescuentoMarcaCategoria] = []
        self._descuentos_volumen: list[DescuentoVolumen] = []
        self._reglas: list[ReglaRecurrencia] = []
        self._promos: list[PromocionPrimeraCompra] = []
        self._feriados: list[Feriado] = []
        self._exclusiones: list[ExclusionRegla] = []
        self._bandeja: dict[str, BandejaFacturacion] = {}
        self._ventas_teoricos: dict[str, VentasTeorico] = {}
        self._conciliaciones: dict[str, Conciliacion] = {}
        self._config: dict[str, str] = {}
        self._descuentos_producto: list[DescuentoProducto] = []
        self._descuentos_recompra: list[DescuentoRecompra] = []
        self._descuentos_diferencial: list[DescuentoDiferencialCambiario] = []
        self._usuarios: dict[str, dict[str, str]] = {}
        self._pagos_human: dict[str, dict[str, str]] = {}
        self._anomalias_aceptadas: list[dict[str, str]] = []
        self._listas_precios_historicas: list[dict[str, str]] = []
        self._tasas_historicas_auditoria: list[dict[str, str]] = []
        self._pagos_huerfanos_cerrados: dict[str, dict[str, str]] = {}
        self._pagos_tasa_binance_override: dict[str, dict[str, str]] = {}
        self._descuentos_sistema_aprobados: dict[str, dict[str, str]] = {}
        self._reglas_dias_credito_volumen: dict[str, dict[str, str]] = {}

    # --- Configuración genérica -----------------------------------------------
    def get_config(self, key: str) -> str | None:
        return self._config.get(key)

    def set_config(self, key: str, value: str) -> None:
        self._config[key] = value

    def all_config(self) -> dict[str, str]:
        return dict(self._config)

    def get_usuario_plataforma(self, email: str) -> dict[str, str] | None:
        return self._usuarios.get(email.strip().lower())

    def all_usuarios_plataforma(self) -> list[dict[str, str]]:
        return [dict(u) for u in self._usuarios.values()]

    def upsert_usuario_plataforma(self, row: dict[str, str]) -> None:
        self._usuarios[row["email"].strip().lower()] = dict(row)

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

    def serie_tasas_del_dia(self, fecha: date) -> list[SerieTasa]:
        return [fila for fila in self._serie if fila.timestamp.date() == fecha]

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

    def delete_lineas(self, linea_ids: list[str]) -> None:
        for lid in linea_ids:
            self._lineas.pop(lid, None)

    def upsert_pagos(self, filas: list[Pago]) -> None:
        for p in filas:
            self._pagos[p.pago_id] = p

    def upsert_facturas(self, filas: list[Factura]) -> None:
        for f in filas:
            self._facturas[f.factura_id] = f

    def all_facturas(self) -> list[Factura]:
        return list(self._facturas.values())

    def upsert_entregas(self, filas: list[Entrega]) -> None:
        for e in filas:
            self._entregas[e.entrega_id] = e

    def all_entregas(self) -> list[Entrega]:
        return list(self._entregas.values())

    # --- Lecturas ------------------------------------------------------------
    def get_cliente(self, cliente_id: str) -> Cliente | None:
        return self._clientes.get(cliente_id)

    def all_clientes(self) -> list[Cliente]:
        return list(self._clientes.values())

    def get_orden(self, so_id: str) -> OrdenVenta | None:
        return self._ordenes.get(so_id)

    def all_ordenes(self) -> list[OrdenVenta]:
        return list(self._ordenes.values())

    def lineas_de_orden(self, so_id: str) -> list[LineaOrden]:
        return [ln for ln in self._lineas.values() if ln.so_id == so_id]

    def all_lineas(self) -> list[LineaOrden]:
        return list(self._lineas.values())

    def get_pago(self, pago_id: str) -> Pago | None:
        return self._pagos.get(pago_id)

    def all_pagos(self) -> list[Pago]:
        return list(self._pagos.values())

    def all_pagos_full(self) -> list[dict[str, str]]:
        out = []
        for p in self._pagos.values():
            row = {
                "pago_id": p.pago_id,
                "cliente_id": p.cliente_id,
                "monto": str(p.monto),
                "moneda": p.moneda.value,
                "metodo_pago": p.metodo_pago,
                "fecha_pago": p.fecha_pago.isoformat(),
                "vendedor_email": p.vendedor_email,
            }
            row.update(
                self._pagos_human.get(
                    p.pago_id,
                    {
                        "recibido": "FALSE",
                        "numero_recibido": "",
                        "fecha_recibido": "",
                        "recibido_por": "",
                    },
                )
            )
            out.append(row)
        return out

    def marcar_pagos_recibido(
        self, pago_ids: list[str], numero_recibido: str, fecha_recibido: datetime, recibido_por: str
    ) -> list[dict[str, str]]:
        actualizados = []
        target = set(pago_ids)
        for pid in target:
            if pid not in self._pagos:
                continue
            self._pagos_human[pid] = {
                "recibido": "TRUE",
                "numero_recibido": numero_recibido,
                "fecha_recibido": fecha_recibido.isoformat()[:19],
                "recibido_por": recibido_por,
            }
        for row in self.all_pagos_full():
            if row["pago_id"] in target:
                actualizados.append(row)
        return actualizados

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

    def update_vinculaciones(self, vincs: list[Vinculacion]) -> None:
        for v in vincs:
            self._vinculaciones[v.vinc_id] = v

    def descuentos_marca_categoria(self) -> list[DescuentoMarcaCategoria]:
        return list(self._descuentos)

    def add_descuento(self, regla: DescuentoMarcaCategoria) -> None:
        self.append_descuento_pronto_pago(regla)

    def append_descuento_pronto_pago(self, regla: DescuentoMarcaCategoria) -> None:
        self._descuentos.append(regla)

    def descuentos_volumen(self) -> list[DescuentoVolumen]:
        return list(self._descuentos_volumen)

    def descuentos_recompra(self) -> list[DescuentoRecompra]:
        return list(self._descuentos_recompra)

    def append_descuento_recompra(self, regla: DescuentoRecompra) -> None:
        self._descuentos_recompra.append(regla)

    def descuentos_diferencial_cambiario(self) -> list[DescuentoDiferencialCambiario]:
        return list(self._descuentos_diferencial)

    def append_descuento_diferencial_cambiario(
        self, regla: DescuentoDiferencialCambiario
    ) -> None:
        self._descuentos_diferencial.append(regla)

    def descuentos_producto(self) -> list[DescuentoProducto]:
        return list(self._descuentos_producto)

    def add_descuento_producto(self, regla: DescuentoProducto) -> None:
        self.append_descuento_producto(regla)

    def append_descuento_producto(self, regla: DescuentoProducto) -> None:
        self._descuentos_producto.append(regla)

    def add_descuento_volumen(self, regla: DescuentoVolumen) -> None:
        self.append_descuento_volumen(regla)

    def append_descuento_volumen(self, regla: DescuentoVolumen) -> None:
        self._descuentos_volumen.append(regla)

    def reglas_recurrencia(self) -> list[ReglaRecurrencia]:
        return list(self._reglas)

    def add_regla_recurrencia(self, regla: ReglaRecurrencia) -> None:
        self._reglas.append(regla)

    def set_regla_recurrencia_porcentaje(self, condicion: str, valor: Decimal) -> None:
        cond = Condicion(condicion)
        for r in self._reglas:
            if r.condicion == cond:
                r.valor = valor
                return
        self._reglas.append(
            ReglaRecurrencia(
                condicion=cond,
                tipo_beneficio=TipoBeneficio.PORCENTAJE,
                valor=valor,
                vigencia_desde=date.today(),
            )
        )

    def promociones_primera_compra(self) -> list[PromocionPrimeraCompra]:
        return list(self._promos)

    def add_promocion_primera_compra(self, promo: PromocionPrimeraCompra) -> None:
        self.append_promocion_primera_compra(promo)

    def append_promocion_primera_compra(self, promo: PromocionPrimeraCompra) -> None:
        self._promos.append(promo)

    def _regla_list(self, tabla: str) -> list[Any] | None:
        mapa: dict[str, list[Any]] = {
            "DescuentosRecompra": self._descuentos_recompra,
            "DescuentosProntoPago": self._descuentos,
            "DescuentosMarcaCategoria": self._descuentos,
            "DescuentosVolumen": self._descuentos_volumen,
            "PromocionPrimeraCompra": self._promos,
            "DescuentosProducto": self._descuentos_producto,
            "DescuentosDiferencialCambiario": self._descuentos_diferencial,
        }
        return mapa.get(tabla)

    def delete_regla(self, tabla: str, regla_id: str) -> bool:
        lst = self._regla_list(tabla)
        if lst is None:
            return False
        for i, r in enumerate(lst):
            if r.regla_id == regla_id:
                del lst[i]
                return True
        return False

    def set_regla_activo(self, tabla: str, regla_id: str, activo: bool) -> bool:
        lst = self._regla_list(tabla)
        if lst is None:
            return False
        for r in lst:
            if r.regla_id == regla_id:
                r.activo = activo
                return True
        return False

    def all_anomalias_aceptadas(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._anomalias_aceptadas]

    def append_anomalia_aceptada(self, row: dict[str, str]) -> None:
        self._anomalias_aceptadas.append(dict(row))

    def all_listas_precios_historicas(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._listas_precios_historicas]

    def replace_listas_precios_historicas(self, rows: list[dict[str, str]]) -> None:
        self._listas_precios_historicas = [dict(r) for r in rows]

    def all_tasas_historicas_auditoria(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._tasas_historicas_auditoria]

    def replace_tasas_historicas_auditoria(self, rows: list[dict[str, str]]) -> None:
        self._tasas_historicas_auditoria = [dict(r) for r in rows]

    def upsert_tasa_historica_auditoria(self, row: dict[str, str]) -> None:
        fecha = str(row.get("fecha", ""))[:10]
        for i, r in enumerate(self._tasas_historicas_auditoria):
            if str(r.get("fecha", ""))[:10] == fecha:
                self._tasas_historicas_auditoria[i] = dict(row)
                return
        self._tasas_historicas_auditoria.append(dict(row))

    def all_pagos_huerfanos_cerrados(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._pagos_huerfanos_cerrados.values()]

    def upsert_pago_huerfano_cerrado(self, row: dict[str, str]) -> None:
        self._pagos_huerfanos_cerrados[row["pago_id"]] = dict(row)

    def all_pagos_tasa_binance_override(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._pagos_tasa_binance_override.values()]

    def upsert_pago_tasa_binance_override(self, row: dict[str, str]) -> None:
        self._pagos_tasa_binance_override[row["pago_id"]] = dict(row)

    def all_descuentos_sistema_aprobados(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._descuentos_sistema_aprobados.values()]

    def upsert_descuento_sistema_aprobado(self, row: dict[str, str]) -> None:
        self._descuentos_sistema_aprobados[row["so_id"]] = dict(row)

    def all_reglas_dias_credito_volumen(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._reglas_dias_credito_volumen.values()]

    def upsert_regla_dias_credito_volumen(self, row: dict[str, str]) -> None:
        self._reglas_dias_credito_volumen[row["regla_id"]] = dict(row)

    def feriados(self) -> list[Feriado]:
        return list(self._feriados)

    def add_feriado(self, feriado: Feriado) -> None:
        self.append_feriado(feriado)

    def append_feriado(self, feriado: Feriado) -> None:
        self._feriados = [f for f in self._feriados if f.fecha != feriado.fecha]
        self._feriados.append(feriado)

    def exclusiones(self) -> list[ExclusionRegla]:
        return list(self._exclusiones)

    def save_exclusion(self, rule: ExclusionRegla) -> None:
        # Check either order of rule type
        for i, r in enumerate(self._exclusiones):
            if (r.regla_tipo_a == rule.regla_tipo_a and r.regla_tipo_b == rule.regla_tipo_b) or (
                r.regla_tipo_a == rule.regla_tipo_b and r.regla_tipo_b == rule.regla_tipo_a
            ):
                self._exclusiones[i] = rule
                return
        self._exclusiones.append(rule)

    # --- Bandeja -------------------------------------------------------------
    def upsert_bandeja(self, fila: BandejaFacturacion) -> None:
        self._bandeja[fila.so_id] = fila

    def upsert_bandejas(self, filas: list[BandejaFacturacion]) -> None:
        for fila in filas:
            self._bandeja[fila.so_id] = fila

    def get_bandeja(self, so_id: str) -> BandejaFacturacion | None:
        return self._bandeja.get(so_id)

    def all_bandeja(self) -> list[BandejaFacturacion]:
        return list(self._bandeja.values())

    # --- Teóricos de Ventas (Fase 10) -----------------------------------------
    def upsert_ventas_teorico(self, fila: VentasTeorico) -> None:
        self._ventas_teoricos[fila.so_id] = fila

    def get_ventas_teorico(self, so_id: str) -> VentasTeorico | None:
        return self._ventas_teoricos.get(so_id)

    def all_ventas_teoricos(self) -> list[VentasTeorico]:
        return list(self._ventas_teoricos.values())

    # --- Conciliación --------------------------------------------------------
    def upsert_conciliacion(self, fila: Conciliacion) -> None:
        self._conciliaciones[fila.so_id] = fila

    def upsert_conciliaciones(self, filas: list[Conciliacion]) -> None:
        for fila in filas:
            self._conciliaciones[fila.so_id] = fila

    def all_conciliaciones(self) -> list[Conciliacion]:
        return list(self._conciliaciones.values())
