"""``SheetsRepository`` — implementación de ``Repository`` sobre un ``SheetGateway``.

Traduce cada método del repositorio a lecturas/escrituras de pestañas, usando los
serializadores de ``serde``. Respeta la regla de oro: ``append_serie_tasa`` solo
agrega (SerieTasas es inmutable) y los ``upsert_*`` de espejo no tocan las tablas
de trabajo humano.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..models import (
    BandejaFacturacion,
    Cliente,
    Conciliacion,
    Condicion,
    DescuentoBCVCompleto,
    DescuentoDiferencialCambiario,
    DescuentoMarcaCategoria,
    DescuentoProducto,
    DescuentoProntoPago,
    DescuentoRecompra,
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
    TipoBeneficio,
    VentasTeorico,
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
        """Retorna {so_id_normalizado: fecha_iso} desde la pestaña FechasHistoricas de Google
        Sheets."""
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

    def serie_tasas_del_dia(self, fecha: date) -> list[SerieTasa]:
        return [fila for fila in self._serie_rows() if fila.timestamp.date() == fecha]

    # --- Cursor --------------------------------------------------------------
    def get_last_sync(self) -> datetime | None:
        raw = self._g.get_meta(_META_LAST_SYNC)
        return serde.p_dt(raw) if raw else None

    def set_last_sync(self, cursor: datetime) -> None:
        self._g.set_meta(_META_LAST_SYNC, serde.s_dt(cursor))

    # --- Configuración genérica (_Meta) ---------------------------------------
    def get_config(self, key: str) -> str | None:
        return self._g.get_meta(key)

    def set_config(self, key: str, value: str) -> None:
        self._g.set_meta(key, value)

    def all_config(self) -> dict[str, str]:
        return {
            key: r.get("value", "")
            for r in self._g.read_rows(g.T_META)
            if (key := r.get("key"))
        }

    def invalidate_cache(self, table: str | None = None) -> None:
        self._g.invalidate_cache(table)

    # --- Usuarios de la plataforma ---------------------------------------------
    _T_USUARIOS = "UsuariosPlataforma"

    def get_usuario_plataforma(self, email: str) -> dict[str, str] | None:
        email_clean = email.strip().lower()
        for r in self._g.read_rows(self._T_USUARIOS):
            if str(r.get("email") or "").strip().lower() == email_clean:
                return r
        return None

    def all_usuarios_plataforma(self) -> list[dict[str, str]]:
        return self._g.read_rows(self._T_USUARIOS)

    def upsert_usuario_plataforma(self, row: dict[str, str]) -> None:
        self._g.upsert_row(self._T_USUARIOS, "email", row)

    # --- Espejo (upsert por PK) ---------------------------------------------
    def upsert_clientes(self, filas: list[Cliente]) -> None:
        self._g.upsert_rows(g.T_CLIENTES, "cliente_id", [serde.cliente_to_row(c) for c in filas])

    def upsert_ordenes(self, filas: list[OrdenVenta]) -> None:
        self._g.upsert_rows(g.T_ORDENES, "so_id", [serde.orden_to_row(o) for o in filas])

    def upsert_lineas(self, filas: list[LineaOrden]) -> None:
        self._g.upsert_rows(g.T_LINEAS, "linea_id", [serde.linea_to_row(ln) for ln in filas])

    def upsert_pagos(self, filas: list[Pago]) -> None:
        self._g.upsert_rows(g.T_PAGOS, "pago_id", [serde.pago_to_row(p) for p in filas])

    # --- Lecturas ------------------------------------------------------------
    def get_cliente(self, cliente_id: str) -> Cliente | None:
        for r in self._g.read_rows(g.T_CLIENTES):
            if r.get("cliente_id") == cliente_id:
                return serde.cliente_from_row(r)
        return None

    def all_clientes(self) -> list[Cliente]:
        return [serde.cliente_from_row(r) for r in self._g.read_rows(g.T_CLIENTES)]

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

    def all_lineas(self) -> list[LineaOrden]:
        return [serde.linea_from_row(r) for r in self._g.read_rows(g.T_LINEAS)]

    def get_pago(self, pago_id: str) -> Pago | None:
        for r in self._g.read_rows(g.T_PAGOS):
            if r.get("pago_id") == pago_id:
                return serde.pago_from_row(r)
        return None

    def all_pagos(self) -> list[Pago]:
        return [serde.pago_from_row(r) for r in self._g.read_rows(g.T_PAGOS)]

    def all_pagos_full(self) -> list[dict[str, str]]:
        return self._g.read_rows(g.T_PAGOS)

    def marcar_pagos_recibido(
        self, pago_ids: list[str], numero_recibido: str, fecha_recibido: datetime, recibido_por: str
    ) -> list[dict[str, str]]:
        target = set(pago_ids)
        actualizados = []
        for r in self._g.read_rows(g.T_PAGOS):
            pid = str(r.get("pago_id", "")).strip()
            if pid in target:
                r["recibido"] = "TRUE"
                r["numero_recibido"] = numero_recibido
                r["fecha_recibido"] = fecha_recibido.isoformat()[:19]
                r["recibido_por"] = recibido_por
                self._g.upsert_row(g.T_PAGOS, "pago_id", r)
                actualizados.append(r)
        return actualizados

    def get_metodo_pago(self, metodo_id: str) -> MetodoPago | None:
        for r in self._g.read_rows(g.T_METODOS):
            if r.get("metodo_id") == metodo_id:
                return serde.metodo_from_row(r)
        return None

    def all_serie_tasas(self) -> list[SerieTasa]:
        return self._serie_rows()

    def vinculaciones_de_orden(self, so_id: str) -> list[Vinculacion]:
        return [
            serde.vinculacion_from_row(r)
            for r in self._g.read_rows(g.T_VINCULACIONES)
            if r.get("so_id") == so_id
        ]

    def all_vinculaciones(self) -> list[Vinculacion]:
        return [serde.vinculacion_from_row(r) for r in self._g.read_rows(g.T_VINCULACIONES)]

    def update_vinculacion(self, vinc: Vinculacion) -> None:
        self._g.upsert_row(g.T_VINCULACIONES, "vinc_id", serde.vinculacion_to_row(vinc))

    def update_vinculaciones(self, vincs: list[Vinculacion]) -> None:
        if not vincs:
            return
        self._g.upsert_rows(
            g.T_VINCULACIONES, "vinc_id", [serde.vinculacion_to_row(v) for v in vincs]
        )

    def descuentos_marca_categoria(self) -> list[DescuentoMarcaCategoria]:
        rows = self._g.read_rows("DescuentosProntoPago")
        if not rows:
            rows = self._g.read_rows(g.T_DESCUENTOS)
        return [serde.pronto_pago_from_row(r) for r in rows]

    def descuentos_pronto_pago(self) -> list[DescuentoProntoPago]:
        return self.descuentos_marca_categoria()

    def append_descuento_pronto_pago(self, regla: DescuentoMarcaCategoria) -> None:
        self._g.append_row("DescuentosProntoPago", serde.pronto_pago_to_row(regla))

    def descuentos_recompra(self) -> list[DescuentoRecompra]:
        rows = self._g.read_rows("DescuentosRecompra")
        return [serde.recompra_from_row(r) for r in rows]

    def append_descuento_recompra(self, regla: DescuentoRecompra) -> None:
        self._g.append_row("DescuentosRecompra", serde.recompra_to_row(regla))

    def descuentos_producto(self) -> list[DescuentoProducto]:
        return [serde.producto_from_row(r) for r in self._g.read_rows("DescuentosProducto")]

    def append_descuento_producto(self, regla: DescuentoProducto) -> None:
        self._g.append_row("DescuentosProducto", serde.producto_to_row(regla))

    def descuentos_diferencial_cambiario(self) -> list[DescuentoDiferencialCambiario]:
        rows = self._g.read_rows("DescuentosDiferencialCambiario")
        if not rows:
            return [
                DescuentoDiferencialCambiario(
                    regla_id="DIF_35_VES",
                    nombre="35% Fijo VES a USD",
                    tipo_diferencial="fijo_35_ves_usd",
                    tipo_calculo="fijo",
                    porcentaje_fijo=Decimal("0.35"),
                    unidad_medida="USD",
                    monedas_aplicables="USD",
                    listas_aplicables="LISTAS_VES",
                ),
                DescuentoDiferencialCambiario(
                    regla_id="DIF_EQUIPARAR",
                    nombre="Equiparar Binance N/C",
                    tipo_diferencial="equiparar_binance",
                    tipo_calculo="variable",
                    porcentaje_fijo=Decimal("0"),
                    unidad_medida="USD",
                    monedas_aplicables="*",
                    listas_aplicables="LISTAS_VES",
                ),
                DescuentoDiferencialCambiario(
                    regla_id="DIF_CANDIDATOS_CIERRE",
                    nombre="Candidatos a Cierre de Factura (reporte)",
                    tipo_diferencial="candidato_cierre_factura",
                    tipo_calculo="variable",
                    porcentaje_fijo=Decimal("0"),
                    unidad_medida="USD",
                    monedas_aplicables="*",
                    listas_aplicables="LISTAS_VES",
                    activo=False,
                ),
            ]
        return [serde.diferencial_from_row(r) for r in rows]

    def append_descuento_diferencial_cambiario(
        self, regla: DescuentoDiferencialCambiario
    ) -> None:
        self._g.append_row("DescuentosDiferencialCambiario", serde.diferencial_to_row(regla))

    def descuentos_volumen(self) -> list[DescuentoVolumen]:
        return [serde.desc_volumen_from_row(r) for r in self._g.read_rows("DescuentosVolumen")]

    def append_descuento_volumen(self, regla: DescuentoVolumen) -> None:
        self._g.append_row("DescuentosVolumen", serde.desc_volumen_to_row(regla))

    def reglas_recurrencia(self) -> list[ReglaRecurrencia]:
        return [serde.regla_from_row(r) for r in self._g.read_rows(g.T_REGLAS)]

    def set_regla_recurrencia_porcentaje(self, condicion: str, valor: Decimal) -> None:
        rows = self._g.read_rows(g.T_REGLAS)
        for r in rows:
            if r.get("condicion") == condicion:
                r["porcentaje"] = str(valor)
                r["valor"] = str(valor)
                self._g.upsert_row(g.T_REGLAS, "condicion", r)
                return
        nueva = ReglaRecurrencia(
            condicion=Condicion(condicion),
            tipo_beneficio=TipoBeneficio.PORCENTAJE,
            valor=valor,
            vigencia_desde=date.today(),
        )
        self._g.append_row(g.T_REGLAS, serde.regla_to_row(nueva))

    def descuento_bcv_completo(self) -> list[DescuentoBCVCompleto]:
        return [serde.bcv_completo_from_row(r) for r in self._g.read_rows(g.T_BCV_COMPLETO)]

    def promociones_primera_compra(self) -> list[PromocionPrimeraCompra]:
        return [serde.promocion_from_row(r) for r in self._g.read_rows(g.T_PROMO_PRIMERA)]

    def append_promocion_primera_compra(self, regla: PromocionPrimeraCompra) -> None:
        self._g.append_row(g.T_PROMO_PRIMERA, serde.promocion_to_row(regla))

    def delete_regla(self, tabla: str, regla_id: str) -> bool:
        return self._g.delete_row(tabla, "regla_id", regla_id)

    def set_regla_activo(self, tabla: str, regla_id: str, activo: bool) -> bool:
        for r in self._g.read_rows(tabla):
            if str(r.get("regla_id", "")).strip() == regla_id:
                r["activo"] = "TRUE" if activo else "FALSE"
                self._g.upsert_row(tabla, "regla_id", r)
                return True
        return False

    def all_anomalias_aceptadas(self) -> list[dict[str, str]]:
        return self._g.read_rows("AnomaliasAceptadas")

    def append_anomalia_aceptada(self, row: dict[str, str]) -> None:
        self._g.append_row("AnomaliasAceptadas", row)

    def all_listas_precios_historicas(self) -> list[dict[str, str]]:
        return self._g.read_rows("ListasPreciosHistoricas")

    def replace_listas_precios_historicas(self, rows: list[dict[str, str]]) -> None:
        self._g.replace_rows("ListasPreciosHistoricas", rows)

    def all_tasas_historicas_auditoria(self) -> list[dict[str, str]]:
        return self._g.read_rows("TasasHistoricasAuditoria")

    def replace_tasas_historicas_auditoria(self, rows: list[dict[str, str]]) -> None:
        self._g.replace_rows("TasasHistoricasAuditoria", rows)

    def upsert_tasa_historica_auditoria(self, row: dict[str, str]) -> None:
        self._g.upsert_row("TasasHistoricasAuditoria", "fecha", row)

    def all_pagos_huerfanos_cerrados(self) -> list[dict[str, str]]:
        return self._g.read_rows("PagosHuerfanosCerrados")

    def upsert_pago_huerfano_cerrado(self, row: dict[str, str]) -> None:
        self._g.upsert_row("PagosHuerfanosCerrados", "pago_id", row)

    def all_pagos_tasa_binance_override(self) -> list[dict[str, str]]:
        return self._g.read_rows("PagosTasaBinanceOverride")

    def upsert_pago_tasa_binance_override(self, row: dict[str, str]) -> None:
        self._g.upsert_row("PagosTasaBinanceOverride", "pago_id", row)

    def all_descuentos_sistema_aprobados(self) -> list[dict[str, str]]:
        return self._g.read_rows("DescuentosSistemaAprobados")

    def upsert_descuento_sistema_aprobado(self, row: dict[str, str]) -> None:
        self._g.upsert_row("DescuentosSistemaAprobados", "so_id", row)

    def all_reglas_dias_credito_volumen(self) -> list[dict[str, str]]:
        return self._g.read_rows("ReglasDiasCreditoVolumen")

    def upsert_regla_dias_credito_volumen(self, row: dict[str, str]) -> None:
        self._g.upsert_row("ReglasDiasCreditoVolumen", "regla_id", row)

    def feriados(self) -> list[Feriado]:
        return [serde.feriado_from_row(r) for r in self._g.read_rows(g.T_FERIADOS)]

    def append_feriado(self, feriado: Feriado) -> None:
        self._g.append_row(g.T_FERIADOS, serde.feriado_to_row(feriado))

    def exclusiones(self) -> list[ExclusionRegla]:
        return [serde.exclusion_from_row(r) for r in self._g.read_rows("Exclusiones")]

    def save_exclusion(self, rule: ExclusionRegla) -> None:
        # Requiere acceso directo a la hoja (solo GspreadGateway lo soporta).
        ws = self._g._ws("Exclusiones")  # type: ignore[attr-defined]
        records = ws.get_all_records()
        row_idx = None
        for i, r in enumerate(records):
            if (
                r.get("regla_tipo_a") == rule.regla_tipo_a
                and r.get("regla_tipo_b") == rule.regla_tipo_b
            ) or (
                r.get("regla_tipo_a") == rule.regla_tipo_b
                and r.get("regla_tipo_b") == rule.regla_tipo_a
            ):
                row_idx = i + 2
                break

        row_data = serde.exclusion_to_row(rule)
        if row_idx is not None:
            ws.update(
                f"A{row_idx}:C{row_idx}",
                [[row_data["regla_tipo_a"], row_data["regla_tipo_b"], str(row_data["activo"])]],
            )
        else:
            ws.append_row(
                [row_data["regla_tipo_a"], row_data["regla_tipo_b"], str(row_data["activo"])]
            )

    # --- Bandeja -------------------------------------------------------------
    def upsert_bandeja(self, fila: BandejaFacturacion) -> None:
        self._g.upsert_row(g.T_BANDEJA, "so_id", serde.bandeja_to_row(fila))

    def upsert_bandejas(self, filas: list[BandejaFacturacion]) -> None:
        if not filas:
            return
        self._g.upsert_rows(g.T_BANDEJA, "so_id", [serde.bandeja_to_row(f) for f in filas])

    def get_bandeja(self, so_id: str) -> BandejaFacturacion | None:
        for r in self._g.read_rows(g.T_BANDEJA):
            if r.get("so_id") == so_id:
                return serde.bandeja_from_row(r)
        return None

    def all_bandeja(self) -> list[BandejaFacturacion]:
        return [serde.bandeja_from_row(r) for r in self._g.read_rows(g.T_BANDEJA)]

    # --- Teóricos de Ventas (Fase 10) -----------------------------------------
    def upsert_ventas_teorico(self, fila: VentasTeorico) -> None:
        self._g.upsert_row(g.T_VENTAS_TEORICOS, "so_id", serde.ventas_teorico_to_row(fila))

    def get_ventas_teorico(self, so_id: str) -> VentasTeorico | None:
        for r in self._g.read_rows(g.T_VENTAS_TEORICOS):
            if r.get("so_id") == so_id:
                return serde.ventas_teorico_from_row(r)
        return None

    def all_ventas_teoricos(self) -> list[VentasTeorico]:
        return [serde.ventas_teorico_from_row(r) for r in self._g.read_rows(g.T_VENTAS_TEORICOS)]

    # --- Conciliación --------------------------------------------------------
    def upsert_conciliacion(self, fila: Conciliacion) -> None:
        self._g.upsert_row(g.T_CONCILIACION, "so_id", serde.conciliacion_to_row(fila))

    def upsert_conciliaciones(self, filas: list[Conciliacion]) -> None:
        if not filas:
            return
        self._g.upsert_rows(
            g.T_CONCILIACION, "so_id", [serde.conciliacion_to_row(f) for f in filas]
        )

    def all_conciliaciones(self) -> list[Conciliacion]:
        return [serde.conciliacion_from_row(r) for r in self._g.read_rows(g.T_CONCILIACION)]

    # --- Bandeja de Auditoría de Descuentos (append-only, inmutable) ---------
    def append_auditoria(self, fila: dict[str, Any]) -> None:
        """Registra una discrepancia en la bandeja de auditoría (solo append).

        fila debe contener: audit_id, so_id, tipo_auditoria, motor_calcula_usd,
        odoo_registrado_usd, diferencia_usd, detalle_odoo, detalle_motor,
        estado (pendiente), revisado_por, timestamp_audit.
        """
        row = {k: str(v) if v is not None else "" for k, v in fila.items()}
        self._g.append_row(g.T_BANDEJA_AUDITORIA, row)

    def append_auditoria_rows(self, filas: list[dict[str, Any]]) -> None:
        """Agrega múltiples filas a la bandeja de auditoría en un solo lote (1 escritura)."""
        if not filas:
            return
        rows: list[Mapping[str, str]] = [
            {k: str(v) if v is not None else "" for k, v in f.items()} for f in filas
        ]
        self._g.upsert_rows(g.T_BANDEJA_AUDITORIA, "audit_id", rows)

    def all_auditoria(self) -> list[dict[str, Any]]:
        """Lee todas las filas de la bandeja de auditoría."""
        return self._g.read_rows(g.T_BANDEJA_AUDITORIA)

    def update_auditoria_estado(self, audit_id: str, estado: str, revisado_por: str) -> None:
        """Actualiza el estado de una fila de auditoría (revisado/aprobado)."""
        self._g.upsert_row(
            g.T_BANDEJA_AUDITORIA,
            "audit_id",
            {"audit_id": audit_id, "estado": estado, "revisado_por": revisado_por},
        )
