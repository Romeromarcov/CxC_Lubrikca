"""Modelo de datos — tablas de la sección 3.x de la especificación.

Convención:
    - Dinero y tasas: ``Decimal`` (nunca float, para no arrastrar error de redondeo).
    - Fechas: ``date`` / ``datetime``.
    - Enumerados: ``Enum`` de str para que serialicen legible a Sheets.

Las dataclasses son el contrato entre las piezas. Los repositorios
(``cxc.repositories``) las leen/escriben; la lógica de negocio opera sobre ellas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Moneda(StrEnum):
    USD = "USD"
    VES = "VES"


class TipoTasa(StrEnum):
    BCV = "BCV"
    BINANCE = "Binance"
    N_A = "N_A"


class TipoDescuento(StrEnum):
    CONTADO = "contado"


class Condicion(StrEnum):
    PRIMERA_COMPRA = "primera_compra"
    RECOMPRA = "recompra"


class TipoBeneficio(StrEnum):
    NOTA_CREDITO = "nota_credito"
    PORCENTAJE = "porcentaje"


class TipoFeriado(StrEnum):
    NACIONAL = "nacional"
    REGIONAL = "regional"
    BANCARIO = "bancario"


class EstadoVinculacion(StrEnum):
    PENDIENTE = "pendiente"
    APROBADO = "aprobado"
    FACTURADO = "facturado"
    CONCILIADO = "conciliado"


class EstadoBandeja(StrEnum):
    CALCULADO = "calculado"
    APROBADO = "aprobado"
    FACTURADO = "facturado"


class ResultadoConciliacion(StrEnum):
    VERDE = "verde"
    AMARILLO = "amarillo"
    ROJO = "rojo"


# --- 3.1 Clientes (espejo) ---------------------------------------------------
@dataclass
class Cliente:
    cliente_id: str
    nombre: str
    vendedor_email: str
    wh_iva_agent: bool = False
    wh_iva_rate: float = 75.0


# --- 3.2 OrdenesVenta (espejo) ----------------------------------------------
@dataclass
class OrdenVenta:
    so_id: str
    cliente_id: str
    fecha: date
    # fecha_entrega = fecha de la ENTREGA COMPLETA (despacho). El plazo de contado
    # solo arranca cuando la orden está entregada completa; si no, va None.
    fecha_entrega: date | None
    monto_total: Decimal
    lista_precios: str
    vendedor_email: str
    es_primera_compra: bool
    facturada: bool = False
    factura_id: str | None = None
    monto_facturado: Decimal | None = None
    # Seguimiento de entrega/devoluciones y estado de orden en Odoo.
    estado_orden: str = "sale"  # Odoo state: draft/sent/sale/done/cancel
    estado_entrega: str = ""  # delivery_status de Odoo: pending/partial/full
    entregada_completa: bool = False
    tiene_devolucion: bool = False
    # Días de crédito reales otorgados por Odoo (payment_term_id), en el
    # momento en que la orden nació -- usado por Recompra (ventana de
    # días de crédito + gracia desde la orden anterior) y por la alerta
    # de días de crédito máximo por volumen.
    dias_credito: int = 0


# Fallback de marca configurable (Configuración > Ajustes generales, clave
# "marca_fallback"): no todos los productos tienen brand_id asignado en Odoo
# (los SINOCO sí, muchos GLOBAL OIL no) -- ``resolved_marca`` usa este valor
# cuando la línea no trae marca. Mutable a propósito: se actualiza una vez
# por request desde el config guardado (ver web/app.py y engine/runner.py),
# sin tener que enhebrar el repo por todo el motor solo para esto.
_MARCA_FALLBACK_DEFAULT = "GLOBAL OIL"


def set_marca_fallback(valor: str) -> None:
    global _MARCA_FALLBACK_DEFAULT
    if not isinstance(valor, str) or not valor.strip():
        return
    _MARCA_FALLBACK_DEFAULT = valor.strip()


def get_marca_fallback() -> str:
    return _MARCA_FALLBACK_DEFAULT


# --- 3.3 LineasOrden (espejo) -----------------------------------------------
@dataclass
class LineaOrden:
    linea_id: str
    so_id: str
    producto: str
    marca: str
    categoria: str
    cantidad: Decimal
    precio_unitario: Decimal
    # Cantidad realmente entregada (neta de devoluciones) — seguimiento visual.
    cantidad_entregada: Decimal = Decimal("0")
    descuento: Decimal = Decimal("0")
    # Subcategoría real de Odoo (2do nivel del path de categ_id, ej.
    # "Comercial/Elite" -> "Elite"). Vacío si el producto no tiene ese nivel.
    # Ver OdooClient._productos.
    subcategoria: str = ""
    # Presentación/envase REAL, parseada del NOMBRE del producto en Odoo (el
    # contenido entre paréntesis al final, ej. "... (1x6)" -> "1X6", "...
    # (Tambor)" -> "TAMBOR"). Vacío si el producto no la trae en el nombre
    # (ítems que no son de venta de lubricante). Ver OdooClient._productos.
    presentacion_odoo: str = ""

    @property
    def resolved_marca(self) -> str:
        """Marca de la línea, o el fallback configurable si viene vacía/'*'."""
        m = str(self.marca or "").strip()
        if m and m != "*":
            return m
        return get_marca_fallback()

    @property
    def presentacion(self) -> str:
        """Presentación/envase de la línea -- prioriza el dato REAL traído

        de Odoo (``presentacion_odoo``); si no está disponible (línea sin
        ese dato, ej. backend Sheets legado), cae al guess binario anterior
        por categoría (Comercial -> "CAJA", Industrial -> "PAILA")."""
        if self.presentacion_odoo:
            return self.presentacion_odoo
        return "CAJA" if str(self.categoria).upper() == "COMERCIAL" else "PAILA"

    @property
    def categoria_madre(self) -> str:
        """Categoría madre ('Comercial' o 'Industrial') de la línea -- ya es

        el dato real traído de Odoo en ``categoria`` (ver
        OdooClient._productos), esta propiedad solo lo expone con el nombre
        que usa el motor de matching."""
        return self.categoria or "Comercial"


# --- 3.4 Pagos (espejo) ------------------------------------------------------
@dataclass
class Pago:
    pago_id: str
    cliente_id: str
    monto: Decimal
    moneda: Moneda
    metodo_pago: str  # Ref -> MetodosPago.metodo_id
    fecha_pago: datetime
    vendedor_email: str


# --- 3.5 MetodosPago (catálogo) ---------------------------------------------
@dataclass
class MetodoPago:
    metodo_id: str
    nombre: str
    moneda: Moneda
    tipo_tasa: TipoTasa
    es_contado: bool


# --- 3.6 SerieTasas (auditoría inmutable, append-only) ----------------------
@dataclass
class SerieTasa:
    timestamp: datetime
    tasa_bcv: Decimal
    tasa_binance: Decimal
    fuente: str
    es_heredada: bool = False
    capturada_ok: bool = True
    tasa_binance_manana: Decimal | None = None
    tasa_binance_tarde: Decimal | None = None
    tasa_binance_diario: Decimal | None = None
    diferencial_bcv_binance_pct: Decimal | None = None
    tasa_bcv_euro: Decimal | None = None


# --- 3.7 DescuentosProntoPago (configurable, pronto pago con días de gracia) ---
@dataclass
class DescuentoProntoPago:
    regla_id: str
    marca: str = "*"
    categoria: str = "*"
    min_cantidad: Decimal = Decimal("0")
    max_cantidad: Decimal = Decimal("999999")
    unidad_medida: str = "USD"
    tipo_beneficio: str = "descuento"
    # "Ventana de pago" (reemplaza "Días de gracia"): desde cuándo se
    # cuentan los `ventana_pago_dias` de margen -- ver
    # engine/discounts.py::ventana_pago_vigente. Default "entrega" (no
    # "vencimiento"): el pronto pago premia pagar CERCA de la entrega, no
    # cerca del vencimiento del crédito -- usar "vencimiento" aquí anularía
    # el propósito del descuento por contado.
    ventana_pago_tipo: str = "entrega"
    ventana_pago_dias: int = 3
    porcentaje: Decimal = Decimal("0.05")
    monedas_aplicables: str = "*"  # "USD", "VES", "*"
    listas_aplicables: str = "*"  # "4", "5", "*"
    vigencia_desde: date = date(2026, 1, 1)
    vigencia_hasta: date | None = None
    activo: bool = True
    tipo_descuento: TipoDescuento = TipoDescuento.CONTADO
    # Pronto pago solo tiene sentido si ya existe al menos un abono
    # vinculado a la orden/factura (ver EngineInputs.abonos).
    requiere_pago_previo: bool = True
    # "linea" (% solo sobre las líneas que hacen match) o "subtotal" (mismo %
    # sobre el subtotal completo de la orden) -- ver engine/discounts.py.
    aplica_a: str = "linea"
    descripcion: str = ""


# Legacy Alias for backward compatibility
DescuentoMarcaCategoria = DescuentoProntoPago


# --- 3.7_vol DescuentosVolumen (configurable, volume pricing) ----------------
@dataclass
class DescuentoVolumen:
    regla_id: str
    marca: str = "*"
    categoria: str = "*"
    litros_minimo: Decimal = Decimal("0")
    porcentaje: Decimal = Decimal("0.05")
    min_cantidad: Decimal = Decimal("0")
    max_cantidad: Decimal = Decimal("999999")
    unidad_medida: str = "UNIDADES"
    tipo_beneficio: str = "descuento"
    tipo_evaluacion: str = "orden"  # "orden" o "acumulado"
    dias_evaluacion: int = 30  # días para acumulado (0 = histórico total)
    vigencia_desde: date = date(2026, 1, 1)
    vigencia_hasta: date | None = None
    listas_aplicables: str = "*"
    activo: bool = True
    # Descuento por volumen depende de la cantidad de la orden, no de pagos.
    requiere_pago_previo: bool = False
    aplica_a: str = "linea"
    descripcion: str = ""


# --- 3.7c PromocionPrimeraCompra (configurable, effective dating) -----------
@dataclass
class PromocionPrimeraCompra:
    regla_id: str
    tipo_beneficio: str = "producto"  # "producto" o "porcentaje"
    productos: str = ""  # Comma-separated list of product IDs/names
    valor: Decimal = Decimal("1")  # quantity for product, or percentage for porcentaje (e.g. 0.02)
    compra_minima: Decimal = Decimal("3")  # Comercial units threshold
    regalo_tipo: str = "solo_uno"  # "conjunto" o "solo_uno"
    vigencia_desde: date = date(2026, 1, 1)
    vigencia_hasta: date | None = None
    descuento_fallback: Decimal = Decimal("0.02")
    categorias_aplica: str = "Comercial"
    marca: str = "GLOBAL OIL"
    categoria: str = "CAJA"
    min_cantidad: Decimal = Decimal("3")
    max_cantidad: Decimal = Decimal("999999")
    unidad_medida: str = "CAJAS"
    listas_aplicables: str = "*"
    solo_primera_compra: bool = (
        False  # False = Recurrente (cada compra >= min), True = Solo 1era compra
    )
    activo: bool = True
    # Promoción de primera compra depende del historial del cliente, no de pagos.
    requiere_pago_previo: bool = False
    # No cambia el cálculo (ya opera sobre "todas las líneas" o solo
    # Industrial según otra lógica) -- se guarda por consistencia de esquema.
    aplica_a: str = "linea"
    descripcion: str = ""


# --- 3.7d DescuentoRecompra (configurable, recompra/recurrencia) -----------
@dataclass
class DescuentoRecompra:
    regla_id: str
    marca: str = "GLOBAL OIL"
    categoria: str = "CAJA"
    min_cajas: int = 2
    max_cajas: int = 4
    min_cantidad: Decimal = Decimal("2")
    max_cantidad: Decimal = Decimal("4")
    unidad_medida: str = "CAJAS"
    tipo_beneficio: str = "descuento"
    porcentaje: Decimal = Decimal("0.03")
    listas_aplicables: str = "*"
    vigencia_desde: date = date(2026, 4, 1)
    vigencia_hasta: date | None = None
    activo: bool = True
    # Recompra depende del historial de compras del cliente, no de pagos.
    requiere_pago_previo: bool = False
    aplica_a: str = "linea"
    descripcion: str = ""
    # Ventana de recompra: aplica si la orden anterior del cliente está
    # totalmente pagada y la nueva orden llega dentro de la "Ventana de
    # pago" configurada (por defecto, "vencimiento" -- días de crédito
    # reales de esa orden anterior + ventana_pago_dias de margen). Ver
    # engine/discounts.py::ventana_pago_vigente. Reemplaza "Días de
    # gracia" (dias_gracia); "Días Ventana Recompra (legado)"
    # (dias_ventana) y "Máximo Usos / Mes" (max_usos_mes) se eliminan --
    # ya no aplican con la lógica actual (pedido explícito del usuario).
    ventana_pago_tipo: str = "vencimiento"
    ventana_pago_dias: int = 3


# --- 3.7g DescuentoFidelizacion (fidelización por litros acumulados) ---------
@dataclass
class DescuentoFidelizacion:
    regla_id: str
    nombre: str
    marca: str = "*"
    min_litros_acumulados: Decimal = Decimal("0")
    porcentaje: Decimal = Decimal("0.05")
    categoria: str = "*"
    min_cantidad: Decimal = Decimal("0")
    max_cantidad: Decimal = Decimal("999999")
    unidad_medida: str = "LITROS"
    tipo_beneficio: str = "descuento"
    listas_aplicables: str = "*"
    ventana_dias: int = 90
    vigencia_desde: date = date(2026, 1, 1)
    vigencia_hasta: date | None = None
    activo: bool = True
    # Fidelización depende de litros acumulados, no de pagos.
    requiere_pago_previo: bool = False


# --- 3.7e DescuentoProducto (configurable, promoción específica por producto) -
@dataclass
class DescuentoProducto:
    regla_id: str
    productos: str = "*"  # CSV de SKUs/IDs de producto o '*'
    marca: str = "*"
    categoria: str = "*"
    min_cantidad: Decimal = Decimal("0")
    max_cantidad: Decimal = Decimal("999999")
    unidad_medida: str = "CAJAS"
    tipo_beneficio: str = "descuento"
    porcentaje: Decimal = Decimal("0.05")
    monedas_aplicables: str = "*"
    listas_aplicables: str = "*"
    vigencia_desde: date = date(2026, 1, 1)
    vigencia_hasta: date | None = None
    activo: bool = True
    # Descuento por producto depende del producto/orden, no de pagos.
    requiere_pago_previo: bool = False
    aplica_a: str = "linea"
    descripcion: str = ""


# --- 3.7f DescuentoDiferencialCambiario (configurable, diferencial camb) ----
@dataclass
class DescuentoDiferencialCambiario:
    regla_id: str
    nombre: str
    tipo_diferencial: str = (
        "fijo_35_ves_usd"  # 'fijo_35_ves_usd' | 'equiparar_binance' | 'candidato_cierre_factura'
    )
    tipo_calculo: str = "fijo"  # 'fijo' | 'variable'
    porcentaje_fijo: Decimal = Decimal("0.35")
    marca: str = "*"
    categoria: str = "*"
    min_cantidad: Decimal = Decimal("0")
    max_cantidad: Decimal = Decimal("999999")
    unidad_medida: str = "USD"
    tipo_beneficio: str = "descuento"
    monedas_aplicables: str = "*"
    listas_aplicables: str = "*"
    vigencia_desde: date = date(2026, 1, 1)
    vigencia_hasta: date | None = None
    activo: bool = True
    # Diferencial cambiario: por definición se calcula sobre un abono ya
    # vinculado (tasa del abono), requiere pago previo.
    requiere_pago_previo: bool = True
    # No cambia el cálculo (se calcula por abono, no por línea) -- se guarda
    # por consistencia de esquema.
    aplica_a: str = "linea"
    descripcion: str = ""


# --- 3.8 ReglasRecurrencia (configurable, effective dating) ------------------
@dataclass
class ReglaRecurrencia:
    condicion: Condicion
    tipo_beneficio: TipoBeneficio
    valor: Decimal
    vigencia_desde: date
    vigencia_hasta: date | None = None
    activo: bool = True
    # Legado: recurrencia por historial, no depende de pagos.
    requiere_pago_previo: bool = False
    aplica_a: str = "linea"
    descripcion: str = ""


# --- 3.8b Feriados (configurable) -------------------------------------------
@dataclass
class Feriado:
    fecha: date
    descripcion: str
    tipo: TipoFeriado


# --- 3.9 Vinculaciones (trabajo humano; el sync NUNCA la toca, salvo la
# excepción de abajo) --------------------------------------------------------
# Excepción: si el pago de una Vinculacion ya está reconciliado en Odoo
# contra una orden DISTINTA a `so_id` (caso simple, sin ambigüedad -- ver
# `cxc.web.app._resincronizar_vinculaciones_con_odoo`), el ciclo de sync SÍ
# actualiza `so_id` para igualar a Odoo -- Odoo siempre prevalece sobre una
# asignación manual vieja. `monto_aplicado` NO se toca (evita introducir
# discrepancias financieras derivadas; solo corrige a qué orden cuenta el
# pago). Cada corrección deja un rastro en BandejaAuditoria (qué decía
# antes, qué dice ahora).
@dataclass
class Vinculacion:
    vinc_id: str
    pago_id: str
    so_id: str
    monto_aplicado: Decimal
    hora_pago_confirmada: datetime
    tasa_bcv_aplicada: Decimal
    tasa_binance_aplicada: Decimal
    es_tasa_heredada: bool
    # Equivalentes congelados (3.9b) — calculados UNA vez, nunca recalculados.
    equiv_usd_bcv: Decimal | None = None
    equiv_usd_binance: Decimal | None = None
    equiv_ves_bcv: Decimal | None = None
    equiv_ves_binance: Decimal | None = None
    confirmado_por: str = ""
    timestamp_registro: datetime | None = None
    estado: EstadoVinculacion = EstadoVinculacion.PENDIENTE
    # Moneda del abono (derivada del Pago) — necesaria para la regla de mezcla.
    moneda_abono: Moneda = Moneda.VES
    # Ruta real estampada del abono (BCV / Binance / USD) — para mezcla y cierre.
    tipo_tasa_abono: TipoTasa = TipoTasa.N_A
    # Variante de tasa BCV usada (oficial USD vs. cruce EUR) cuando
    # tipo_tasa_abono es BCV — independiente de tipo_tasa_abono (esa distingue
    # BCV vs Binance como FUENTE; esta distingue cuál BCV).
    bcv_variante: str = "USD"


# --- 3.10 BandejaFacturacion (salida del motor + trabajo humano) ------------
@dataclass
class BandejaFacturacion:
    so_id: str
    lista_aplicada: str
    precio_base_calculado: Decimal
    descuentos_detalle: list[DescuentoAplicado] = field(default_factory=list)
    total_descuentos: Decimal = Decimal("0")
    ncs_calculadas: Decimal = Decimal("0")
    total_motor: Decimal = Decimal("0")
    requiere_revision: bool = False
    candidata_a_cierre: bool = False
    aprobado_por: str | None = None
    estado: EstadoBandeja = EstadoBandeja.CALCULADO
    # Tarea 3 (rediseño de Ventas) -- equivalentes/teóricos por lista,
    # calculados una vez por el motor (mismo precio_resolver que
    # precio_base_calculado) para que Ventas/Auditoría los lean sin
    # recalcular. Ver docs/REDISENO_DESCUENTOS_UNIFICADOS.md.
    equivalente_lista_usd: Decimal = Decimal("0")
    teorico_lista_ves: Decimal = Decimal("0")
    teorico_lista_usd: Decimal = Decimal("0")
    descuentos_teorico_ves: Decimal = Decimal("0")
    descuentos_teorico_usd: Decimal = Decimal("0")


@dataclass
class VentasTeorico:
    """Teórico VES/USD de una orden -- Fase 10, módulo Ventas.

    Punto de comparación FIJO (a diferencia de BandejaFacturacion, que se
    recalcula constantemente y salta órdenes ya facturadas): se calcula UNA
    vez por orden y no cambia salvo que ``usa_fallback_ves``/``_usd`` sea
    True (algún producto no tenía precio fijo en esa lista específica y se
    resolvió por fallback -- señal de que hay que re-verificar cuando esa
    lista se complete) O que ``lineas_fingerprint`` ya no coincida con las
    líneas actuales de la orden (alguien editó cantidades/productos en Odoo
    DESPUÉS de que se calculó el teórico -- hallazgo real, agosto 2026:
    orden S00792, el teórico quedó mostrando una línea de producto que ya
    no existía en la orden real, y cantidades viejas, porque nada disparaba
    un recalculo cuando la orden en sí cambiaba, solo cuando faltaba precio
    en una lista).
    """

    so_id: str
    teorico_ves: Decimal = Decimal("0")
    teorico_usd: Decimal = Decimal("0")
    descuentos_teorico_ves: Decimal = Decimal("0")
    descuentos_teorico_usd: Decimal = Decimal("0")
    lista_ves_id: str = ""
    lista_usd_id: str = ""
    usa_fallback_ves: bool = False
    usa_fallback_usd: bool = False
    calculado_en: datetime = field(default_factory=datetime.now)
    # Huella de las líneas de la orden al momento del cálculo (ver
    # ``engine.runner.fingerprint_lineas``) -- "" en filas viejas
    # (calculadas antes de este campo), que se tratan como desactualizadas
    # y se recalculan en el próximo ciclo para poblarla.
    lineas_fingerprint: str = ""


@dataclass
class DescuentoAplicado:
    """Un componente del desglose de descuentos (apilamiento aditivo)."""

    origen: str  # 'recurrencia' | 'contado' | 'bcv_completo'
    descripcion: str
    monto: Decimal


# --- 3.11 Conciliacion (computada por la pieza 5) ---------------------------
@dataclass
class Conciliacion:
    so_id: str
    total_motor: Decimal
    monto_odoo: Decimal
    ncs_odoo: Decimal
    diferencia: Decimal
    resultado: ResultadoConciliacion
    revisado_por: str | None = None


# --- 3.12 ExclusionRegla (pares de descuentos mutuamente excluyentes) --------
@dataclass
class ExclusionRegla:
    """Par de tipos de descuento/promoción que no pueden aplicarse simultáneamente.

    Cuando ambos tienen valor > 0, se aplica el de mayor valor y el otro
    se anula para el cálculo del total de la bandeja de facturación.

    Valores válidos para regla_tipo_a / regla_tipo_b:
      'primera_compra', 'recurrencia', 'contado', 'volumen', 'bcv_completo'
    """

    regla_tipo_a: str
    regla_tipo_b: str
    activo: bool = True
