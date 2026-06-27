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
from enum import Enum


class Moneda(str, Enum):
    USD = "USD"
    VES = "VES"


class TipoTasa(str, Enum):
    BCV = "BCV"
    BINANCE = "Binance"
    N_A = "N_A"


class TipoDescuento(str, Enum):
    CONTADO = "contado"


class Condicion(str, Enum):
    PRIMERA_COMPRA = "primera_compra"
    RECOMPRA = "recompra"


class TipoBeneficio(str, Enum):
    NOTA_CREDITO = "nota_credito"
    PORCENTAJE = "porcentaje"


class TipoFeriado(str, Enum):
    NACIONAL = "nacional"
    REGIONAL = "regional"
    BANCARIO = "bancario"


class EstadoVinculacion(str, Enum):
    PENDIENTE = "pendiente"
    APROBADO = "aprobado"
    FACTURADO = "facturado"
    CONCILIADO = "conciliado"


class EstadoBandeja(str, Enum):
    CALCULADO = "calculado"
    APROBADO = "aprobado"
    FACTURADO = "facturado"


class ResultadoConciliacion(str, Enum):
    VERDE = "verde"
    AMARILLO = "amarillo"
    ROJO = "rojo"


# --- 3.1 Clientes (espejo) ---------------------------------------------------
@dataclass
class Cliente:
    cliente_id: str
    nombre: str
    vendedor_email: str


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
    # Seguimiento de entrega/devoluciones (sección 4.6 — ampliación).
    estado_entrega: str = ""  # delivery_status de Odoo: pending/partial/full
    entregada_completa: bool = False
    tiene_devolucion: bool = False


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


# --- 3.7 DescuentosMarcaCategoria (configurable, effective dating) -----------
@dataclass
class DescuentoMarcaCategoria:
    regla_id: str
    marca: str  # '*' = todas
    categoria: str  # '*' = todas las de esa marca
    tipo_descuento: TipoDescuento
    porcentaje: Decimal
    vigencia_desde: date
    vigencia_hasta: date | None = None
    activo: bool = True


# --- 3.7b DescuentoBCVCompleto (configurable, effective dating) --------------
@dataclass
class DescuentoBCVCompleto:
    """Tasa de descuento BCV-completo que fija la gerencia (por fecha).

    El descuento aplicado por abono es ``min(porcentaje, diferencial_real)``,
    donde el diferencial real es ``(binance − bcv)/binance`` del bucket del abono.
    Nunca excede el diferencial; la gerencia puede otorgar menos.
    """

    vigencia_desde: date
    porcentaje: Decimal
    vigencia_hasta: date | None = None
    activo: bool = True


# --- 3.7c PromocionPrimeraCompra (configurable, effective dating) -----------
@dataclass
class PromocionPrimeraCompra:
    """Producto de regalo por primera compra (ej. caja de liga de frenos).

    La NC vale el precio de ``producto`` en la lista de nacimiento de la orden
    (``OrdenVenta.lista_precios``), tomada a la fecha de la SO. Configurable con
    vigencia: solo aplica si la SO cae dentro del período de la promoción.
    """

    producto: str
    vigencia_desde: date
    vigencia_hasta: date | None = None
    activo: bool = True


# --- 3.8 ReglasRecurrencia (configurable, effective dating) ------------------
@dataclass
class ReglaRecurrencia:
    condicion: Condicion
    tipo_beneficio: TipoBeneficio
    valor: Decimal
    vigencia_desde: date
    vigencia_hasta: date | None = None
    activo: bool = True


# --- 3.8b Feriados (configurable) -------------------------------------------
@dataclass
class Feriado:
    fecha: date
    descripcion: str
    tipo: TipoFeriado


# --- 3.9 Vinculaciones (trabajo humano; el sync NUNCA la toca) --------------
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
