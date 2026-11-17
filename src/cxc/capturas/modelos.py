"""Modelo y validación de las capturas que llegan de los bots de Telegram.

La validación es pura (sin red ni base). Hay dos niveles:

- **Inválido de fondo** (``CapturaInvalida``): no se puede registrar (monto negativo, RIF mal
  formado, pedido sin productos...). El bot muestra el mensaje y conserva el borrador.
- **Incompleto tolerable** (lista de faltantes): solo en pagos. Se registra con estado
  ``INCOMPLETO`` y se completa después, desde el grupo o desde la interfaz de CxC.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

CONTACTO = "contacto"
CONTACTO_EDITAR = "contacto_editar"
COTIZACION = "cotizacion"
PEDIDO = "pedido"
PAGO = "pago"
PAGO_PROVEEDOR = "pago_proveedor"
PROVEEDOR = "proveedor"
FACTURA_PROVEEDOR = "factura_proveedor"
GASTO = "gasto"  # compra menor / gasto de consumo ya pagado al contado (sin cuenta por pagar)
KINDS: tuple[str, ...] = (
    CONTACTO,
    CONTACTO_EDITAR,
    COTIZACION,
    PEDIDO,
    PAGO,
    PAGO_PROVEEDOR,
    PROVEEDOR,
    FACTURA_PROVEEDOR,
    GASTO,
)

PENDIENTE = "PENDIENTE"
INCOMPLETO = "INCOMPLETO"
ENVIADO = "ENVIADO"
ERROR = "ERROR"
ESTADOS: tuple[str, ...] = (PENDIENTE, INCOMPLETO, ENVIADO, ERROR)

# Campos de un pago que se pueden corregir con /completar.
CAMPOS_PAGO = frozenset(
    {
        "cliente",
        "rif",
        "monto",
        "moneda",
        "metodo_pago",
        "referencia",
        "banco",
        "fecha_pago",
        "pedido_ref",
        "proveedor",
        "factura_ref",
    }
)

# Campos de una factura de proveedor que se pueden corregir con /completar.
CAMPOS_FACTURA = frozenset(
    {
        "proveedor",
        "rif",
        "numero_factura",
        "numero_control",
        "fecha_emision",
        "fecha_vencimiento",
        "moneda",
        "base_imponible",
        "iva",
        "monto_total",
        "concepto",
        "orden_ref",
        "notas",
        # datos del proveedor tal como salen del documento (sirven para crearlo si no existe)
        "proveedor_direccion",
        "proveedor_telefono",
        "proveedor_email",
        # preparado para cuestionario futuro de selección de diario (por defecto va al diario VES)
        "diario_id",
        "diario_codigo",
    }
)

# Campos de un gasto (compra menor ya pagada) que se pueden corregir con /completar.
CAMPOS_GASTO = CAMPOS_FACTURA | {"categoria"}

# Qué tipos se completan después de enviados y con qué campos.
CAMPOS_COMPLETABLES: dict[str, frozenset[str]] = {
    PAGO: CAMPOS_PAGO,
    PAGO_PROVEEDOR: CAMPOS_PAGO,
    FACTURA_PROVEEDOR: CAMPOS_FACTURA,
    GASTO: CAMPOS_GASTO,
}
KINDS_COMPLETABLES = frozenset(CAMPOS_COMPLETABLES)


class CapturaInvalida(ValueError):
    """La captura no se puede registrar. ``faltan`` nombra los campos que lo causan."""

    def __init__(self, error: str, faltan: list[str] | None = None) -> None:
        super().__init__(error)
        self.error = error
        self.faltan = faltan or []


@dataclass(frozen=True)
class Captura:
    captura_id: int
    kind: str
    idempotency_key: str
    estado: str
    payload: dict[str, Any]
    fuente: dict[str, Any]
    auto: bool
    incompleto: list[str]
    resultado: dict[str, Any]
    odoo_ref: str
    error: str
    intentos: int
    creada_en: datetime
    actualizada_en: datetime

    def a_dict(self) -> dict[str, Any]:
        """Forma pública (contrato de la API)."""
        return {
            "id": self.captura_id,
            "kind": self.kind,
            "estado": self.estado,
            "incompleto": self.incompleto,
            "resultado": self.resultado,
            "odoo_ref": self.odoo_ref,
            "error": self.error,
            "auto": self.auto,
            "payload": self.payload,
            "idempotency_key": self.idempotency_key,
            "fecha_creacion": self.creada_en.isoformat(),
            "fecha_actualizacion": self.actualizada_en.isoformat(),
        }


# ── utilidades de normalización ──────────────────────────────────────────────


def vacio(valor: Any) -> bool:
    return valor is None or valor == "" or valor == [] or valor == {}


def a_decimal(valor: Any, nombre: str) -> Decimal:
    try:
        d = Decimal(str(valor).strip())
    except (InvalidOperation, ValueError) as e:
        raise CapturaInvalida(f"{nombre} inválido: {valor!r}.", [nombre.lower()]) from e
    if not d.is_finite():
        raise CapturaInvalida(f"{nombre} inválido: {valor!r}.", [nombre.lower()])
    return d


_RIF = re.compile(r"^\s*([VJEGBPvjegbp])[-\s]?([0-9][0-9\-\s]*)\s*$")


def normalizar_rif(rif: str) -> str:
    """``j-12345678-9`` o ``J 123456789`` → ``J-123456789``; ``CapturaInvalida`` si no es un RIF."""
    m = _RIF.match(rif or "")
    if not m:
        raise CapturaInvalida(
            "RIF inválido: usa letra (V/J/E/G/B/P) y números, p. ej. J-123456789.", ["rif"]
        )
    return f"{m.group(1).upper()}-{re.sub(r'[^0-9]', '', m.group(2))}"


def variantes_rif(rif: str) -> list[str]:
    """Formas en que Odoo puede tener guardado un RIF (``vat``): con y sin guiones, con prefijo."""
    n = normalizar_rif(rif)
    letra, digs = n.split("-", 1)
    return sorted({n, f"{letra}{digs}", f"{letra}-{digs[:-1]}-{digs[-1]}", digs, rif.strip()})


def validar_gps(ubicacion: Any, campo: str = "ubicacion") -> None:
    """Misma regla que Omni (``_gps``): latitud entre -90 y 90, longitud entre -180 y 180."""
    if not isinstance(ubicacion, dict):
        raise CapturaInvalida("Ubicación GPS inválida.", [campo])
    try:
        lat = Decimal(str(ubicacion["latitud"]))
        lon = Decimal(str(ubicacion["longitud"]))
        prec = ubicacion.get("precision_m")
        if prec is not None:
            Decimal(str(prec))
    except (KeyError, InvalidOperation) as e:
        raise CapturaInvalida("Ubicación GPS inválida.", [campo]) from e
    if not (lat.is_finite() and lon.is_finite()):
        raise CapturaInvalida("Ubicación GPS inválida.", [campo])
    if lat == Decimal(0) and lon == Decimal(0):
        raise CapturaInvalida(
            "Envía una ubicación GPS real (coordenadas distintas de 0, 0).", [campo]
        )
    if not (Decimal(-90) <= lat <= 90 and Decimal(-180) <= lon <= 180):
        raise CapturaInvalida("Ubicación GPS fuera de rango.", [campo])


def digitos(telefono: str) -> str:
    return re.sub(r"\D", "", telefono or "")


# ── validación por tipo ──────────────────────────────────────────────────────


def _exigir(draft: dict[str, Any], campos: list[str]) -> None:
    faltan = [c for c in campos if vacio(draft.get(c))]
    if faltan:
        raise CapturaInvalida(f"Faltan datos obligatorios: {', '.join(faltan)}.", faltan)


def validar(kind: str, draft: dict[str, Any]) -> list[str]:
    """Valida un borrador: devuelve los faltantes tolerables o lanza ``CapturaInvalida``."""
    if kind not in KINDS:
        raise CapturaInvalida(f"Tipo de captura desconocido: {kind!r}.")
    if kind == CONTACTO:
        _exigir(draft, ["nombre", "telefono", "ubicacion"])
        if not 7 <= len(digitos(str(draft["telefono"]))) <= 15:
            raise CapturaInvalida("Teléfono inválido (7 a 15 dígitos).", ["telefono"])
        if not vacio(draft.get("rif")):
            normalizar_rif(str(draft["rif"]))
        validar_gps(draft["ubicacion"])
        return []
    if kind == CONTACTO_EDITAR:
        return _validar_contacto_editar(draft)
    if kind == COTIZACION:
        return _validar_cotizacion(draft)
    if kind == PEDIDO:
        return _validar_pedido(draft)
    if kind == PROVEEDOR:
        return _validar_proveedor(draft)
    if kind == FACTURA_PROVEEDOR:
        return _validar_factura(draft)
    if kind == GASTO:
        return _validar_gasto(draft)
    return _validar_pago(kind, draft)


def _validar_proveedor(draft: dict[str, Any]) -> list[str]:
    _exigir(draft, ["nombre", "rif", "telefono"])
    normalizar_rif(str(draft["rif"]))
    if not 7 <= len(digitos(str(draft["telefono"]))) <= 15:
        raise CapturaInvalida("Teléfono inválido (7 a 15 dígitos).", ["telefono"])
    if not vacio(draft.get("ubicacion")):
        validar_gps(draft["ubicacion"])
    dias = draft.get("dias_credito")
    if dias is not None and (not str(dias).isdigit() or int(str(dias)) > 365):
        raise CapturaInvalida(
            "Los días de crédito deben ser un número entre 0 y 365.", ["dias_credito"]
        )
    return []


def fecha_iso_valida(valor: Any, nombre: str) -> str:
    """``YYYY-MM-DD`` exacto; lanza ``CapturaInvalida`` si el formato o la fecha no son válidos."""
    try:
        return date.fromisoformat(str(valor).strip()).isoformat()
    except ValueError as e:
        raise CapturaInvalida(f"{nombre} inválida (usa AAAA-MM-DD).", [nombre.lower()]) from e


def _validar_factura(draft: dict[str, Any]) -> list[str]:
    if not vacio(draft.get("rif")):
        normalizar_rif(str(draft["rif"]))
    if not vacio(draft.get("monto_total")) and a_decimal(draft["monto_total"], "monto_total") <= 0:
        raise CapturaInvalida("El monto total debe ser mayor que cero.", ["monto_total"])
    for campo in ("base_imponible", "iva"):
        if not vacio(draft.get(campo)) and a_decimal(draft[campo], campo) < 0:
            raise CapturaInvalida(f"{campo} no puede ser negativo.", [campo])
    emision = (
        fecha_iso_valida(draft["fecha_emision"], "fecha_emision")
        if not vacio(draft.get("fecha_emision"))
        else None
    )
    if not vacio(draft.get("fecha_vencimiento")):
        venc = fecha_iso_valida(draft["fecha_vencimiento"], "fecha_vencimiento")
        if emision and venc < emision:
            raise CapturaInvalida(
                "La fecha de vencimiento es anterior a la de emisión.", ["fecha_vencimiento"]
            )
    faltan = [
        c
        for c in ("numero_factura", "fecha_emision", "moneda", "monto_total")
        if vacio(draft.get(c))
    ]
    if vacio(draft.get("proveedor")) and vacio(draft.get("rif")):
        faltan.append("proveedor (nombre o RIF)")
    return faltan


# Obligatorios de un gasto: los mismos que Omni (función ``gasto`` en bots_ingest/handlers.py).
REQ_GASTO = ("proveedor", "fecha_emision", "moneda", "monto_total")


def _validar_gasto(draft: dict[str, Any]) -> list[str]:
    """Compra menor ya pagada. Inválido de fondo → ``CapturaInvalida``; faltantes → ``INCOMPLETO``.

    El número de factura es opcional (un recibo o una nota de contado puede no traerlo). El RIF
    también: si falta, el proveedor se identifica por su nombre normalizado.
    """
    if not vacio(draft.get("rif")):
        normalizar_rif(str(draft["rif"]))
    if not vacio(draft.get("monto_total")) and a_decimal(draft["monto_total"], "monto_total") <= 0:
        raise CapturaInvalida("El monto total debe ser mayor que cero.", ["monto_total"])
    # Igual que Omni: solo el IVA se valida (la base no se usa para registrar el gasto allá).
    if not vacio(draft.get("iva")) and a_decimal(draft["iva"], "iva") < 0:
        raise CapturaInvalida("iva no puede ser negativo.", ["iva"])
    if not vacio(draft.get("fecha_emision")):
        fecha_iso_valida(draft["fecha_emision"], "fecha_emision")
    return [c for c in REQ_GASTO if vacio(draft.get(c))]


def normalizar_nombre(nombre: str) -> str:
    """Nombre comparable de un proveedor: sin tildes, en minúsculas y con espacios simples."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", nombre or "") if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_tildes).strip().lower()


# Obligatorios de un pedido de primera compra / ficha completa (paridad con Omni).
REQ_PEDIDO = ["cliente", "lineas", "rif", "email", "direccion_fiscal", "ubicacion"]


def es_efectivo(metodo_pago: Any) -> bool:
    """True si el método de pago es efectivo (no lleva referencia en el memo de Odoo)."""
    if vacio(metodo_pago):
        return False
    m = str(metodo_pago).strip().lower()
    return m == "cash" or "efectivo" in m


def _validar_lineas_comerciales(lineas: Any) -> None:
    if not isinstance(lineas, list) or not lineas or not all(isinstance(ln, dict) for ln in lineas):
        raise CapturaInvalida("Las líneas de productos son inválidas.", ["lineas"])
    for ln in lineas:
        if vacio(ln.get("producto")) and vacio(ln.get("producto_id")):
            raise CapturaInvalida("Hay una línea sin producto.", ["lineas"])
        if a_decimal(ln.get("cantidad"), "Cantidad") <= 0:
            etiqueta = ln.get("producto") or ln.get("producto_id")
            raise CapturaInvalida(f"Cantidad inválida para «{etiqueta}».", ["lineas"])
        if not vacio(ln.get("precio_unitario")) and a_decimal(ln["precio_unitario"], "Precio") < 0:
            raise CapturaInvalida(f"Precio inválido para «{ln.get('producto')}».", ["lineas"])


def _validar_contacto_editar(draft: dict[str, Any]) -> list[str]:
    if all(vacio(draft.get(c)) for c in ("partner_odoo_id", "rif", "nombre_buscar", "nombre")):
        raise CapturaInvalida(
            "Indica qué contacto deseas editar (ID de Odoo, RIF o nombre).", ["nombre"]
        )
    campos_editables = ("nombre", "telefono", "email", "rif", "direccion", "ubicacion", "notas")
    if all(vacio(draft.get(c)) for c in campos_editables):
        raise CapturaInvalida("Indica al menos un dato nuevo para actualizar en el contacto.")
    if not vacio(draft.get("telefono")) and not 7 <= len(digitos(str(draft["telefono"]))) <= 15:
        raise CapturaInvalida("Teléfono inválido (7 a 15 dígitos).", ["telefono"])
    if not vacio(draft.get("rif")):
        normalizar_rif(str(draft["rif"]))
    if not vacio(draft.get("ubicacion")):
        validar_gps(draft["ubicacion"])
    return []


def _validar_cotizacion(draft: dict[str, Any]) -> list[str]:
    _exigir(draft, ["cliente", "lineas"])
    if not vacio(draft.get("rif")):
        normalizar_rif(str(draft["rif"]))
    if not vacio(draft.get("ubicacion")):
        validar_gps(draft["ubicacion"])
    _validar_lineas_comerciales(draft["lineas"])
    return []


def _validar_pedido(draft: dict[str, Any]) -> list[str]:
    primera = draft.get("es_primera_compra")
    ya_localizado = primera is False or not vacio(draft.get("partner_odoo_id"))
    if ya_localizado:
        # Cliente ya registrado o seleccionado de Odoo: basta identificar cliente y líneas.
        _exigir(draft, ["cliente", "lineas"])
        if not vacio(draft.get("rif")):
            normalizar_rif(str(draft["rif"]))
        if not vacio(draft.get("ubicacion")):
            validar_gps(draft["ubicacion"])
    else:
        _exigir(draft, REQ_PEDIDO)
        # En el flujo nuevo (es_primera_compra=True) la dirección va siempre en el contacto
        # principal; si viene sin el flag (paridad histórica Omni) se conserva el chequeo previo.
        if (
            primera is None
            and vacio(draft.get("direccion_entrega"))
            and not draft.get("entrega_igual_fiscal")
        ):
            raise CapturaInvalida(
                "Indica la dirección de entrega o que es la misma fiscal.", ["direccion_entrega"]
            )
        normalizar_rif(str(draft["rif"]))
        validar_gps(draft["ubicacion"])
    _validar_lineas_comerciales(draft["lineas"])
    return []


def _validar_pago(kind: str, draft: dict[str, Any]) -> list[str]:
    if not vacio(draft.get("monto")) and a_decimal(draft["monto"], "Monto") <= 0:
        raise CapturaInvalida("El monto debe ser mayor que cero.", ["monto"])
    if not vacio(draft.get("rif")):
        normalizar_rif(str(draft["rif"]))
    if kind == PAGO:
        req = (
            ("monto", "moneda")
            if es_efectivo(draft.get("metodo_pago"))
            else ("monto", "moneda", "referencia")
        )
        faltan = [c for c in req if vacio(draft.get(c))]
        if all(vacio(draft.get(c)) for c in ("cliente", "rif", "pedido_ref")):
            faltan.append("cliente (nombre, RIF o pedido)")
        return faltan
    return [c for c in ("proveedor", "monto", "moneda") if vacio(draft.get(c))]


# ── monedas (regla de CxC: «USD» exacto; cualquier bolívar = VES) ────────────

_FAMILIA_VES = {"VES", "VEF", "VED", "BS", "BSF", "BSD", "BS."}


def clase_moneda(codigo: str) -> str:
    c = (codigo or "").strip().upper()
    if c in ("USD", "$"):
        return "USD"
    if c in _FAMILIA_VES:
        return "VES"
    return c
