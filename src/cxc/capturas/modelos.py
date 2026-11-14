"""Modelo y validación de las capturas que llegan de los bots de Telegram.

La validación es pura (sin red ni base). Hay dos niveles:

- **Inválido de fondo** (``CapturaInvalida``): no se puede registrar (monto negativo, RIF mal
  formado, pedido sin productos...). El bot muestra el mensaje y conserva el borrador.
- **Incompleto tolerable** (lista de faltantes): solo en pagos. Se registra con estado
  ``INCOMPLETO`` y se completa después, desde el grupo o desde la interfaz de CxC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

CONTACTO = "contacto"
PEDIDO = "pedido"
PAGO = "pago"
PAGO_PROVEEDOR = "pago_proveedor"
KINDS: tuple[str, ...] = (CONTACTO, PEDIDO, PAGO, PAGO_PROVEEDOR)
KINDS_COMPLETABLES = frozenset({PAGO, PAGO_PROVEEDOR})

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
        _exigir(draft, ["nombre", "telefono"])
        if not 7 <= len(digitos(str(draft["telefono"]))) <= 15:
            raise CapturaInvalida("Teléfono inválido (7 a 15 dígitos).", ["telefono"])
        return []
    if kind == PEDIDO:
        return _validar_pedido(draft)
    return _validar_pago(kind, draft)


def _validar_pedido(draft: dict[str, Any]) -> list[str]:
    _exigir(draft, ["cliente", "lineas"])
    lineas = draft["lineas"]
    if not isinstance(lineas, list) or not all(isinstance(ln, dict) for ln in lineas):
        raise CapturaInvalida("Las líneas del pedido son inválidas.", ["lineas"])
    for ln in lineas:
        if vacio(ln.get("producto")):
            raise CapturaInvalida("Hay una línea sin producto.", ["lineas"])
        if a_decimal(ln.get("cantidad"), "Cantidad") <= 0:
            raise CapturaInvalida(f"Cantidad inválida para «{ln['producto']}».", ["lineas"])
        if not vacio(ln.get("precio_unitario")) and a_decimal(ln["precio_unitario"], "Precio") < 0:
            raise CapturaInvalida(f"Precio inválido para «{ln['producto']}».", ["lineas"])
    if not vacio(draft.get("rif")):
        normalizar_rif(str(draft["rif"]))
    return []


def _validar_pago(kind: str, draft: dict[str, Any]) -> list[str]:
    if not vacio(draft.get("monto")) and a_decimal(draft["monto"], "Monto") <= 0:
        raise CapturaInvalida("El monto debe ser mayor que cero.", ["monto"])
    if not vacio(draft.get("rif")):
        normalizar_rif(str(draft["rif"]))
    if kind == PAGO:
        faltan = [c for c in ("monto", "moneda", "referencia") if vacio(draft.get(c))]
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
