"""Modelos y cálculos puros de Documentos Comerciales (Cotizaciones y Pedidos)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from cxc.capturas.modelos import normalizar_rif, vacio

COTIZACION = "cotizacion"
PEDIDO = "pedido"
TIPOS_DOCUMENTO = (COTIZACION, PEDIDO)

BORRADOR = "BORRADOR"
ENVIADO_ODOO = "ENVIADO_ODOO"
ERROR_ODOO = "ERROR"
ESTADOS_DOCUMENTO = (BORRADOR, ENVIADO_ODOO, ERROR_ODOO)

MONEDAS_LISTA = ("USD", "VES")


class DocumentoInvalido(ValueError):
    """Error de validación al crear, editar o enviar un documento comercial."""

    def __init__(self, mensaje: str, faltan: list[str] | None = None) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.faltan = faltan or []


def decimal_limpio(valor: Any, campo: str, *, minimo: Decimal | None = None) -> Decimal:
    if valor in (None, ""):
        return Decimal("0")
    try:
        d = Decimal(str(valor).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as e:
        raise DocumentoInvalido(f"Valor numérico inválido en {campo}: {valor!r}.", [campo]) from e
    if not d.is_finite():
        raise DocumentoInvalido(f"Valor numérico inválido en {campo}.", [campo])
    if minimo is not None and d < minimo:
        raise DocumentoInvalido(f"{campo} no puede ser menor que {minimo}.", [campo])
    return d


def calcular_subtotal_linea(
    cantidad: Decimal, precio_unitario: Decimal, descuento_pct: Decimal = Decimal("0")
) -> Decimal:
    factor = Decimal("1") - (descuento_pct / Decimal("100"))
    if factor < Decimal("0"):
        factor = Decimal("0")
    return (cantidad * precio_unitario * factor).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class LineaDocumento:
    linea_id: int
    orden: int
    producto_id: str
    codigo: str
    descripcion: str
    presentacion: str
    litros_unidad: Decimal
    cantidad: Decimal
    precio_unitario: Decimal
    descuento_pct: Decimal
    subtotal: Decimal

    def a_dict(self) -> dict[str, Any]:
        return {
            "linea_id": self.linea_id,
            "orden": self.orden,
            "producto_id": self.producto_id,
            "codigo": self.codigo,
            "descripcion": self.descripcion,
            "presentacion": self.presentacion,
            "litros_unidad": float(self.litros_unidad),
            "litros_linea": float((self.litros_unidad * self.cantidad).quantize(Decimal("0.01"))),
            "cantidad": float(self.cantidad),
            "precio_unitario": float(self.precio_unitario),
            "descuento_pct": float(self.descuento_pct),
            "subtotal": float(self.subtotal),
            "vinculada_catalogo": bool(self.producto_id and self.producto_id.isdigit()),
        }


@dataclass(frozen=True)
class DocumentoComercial:
    documento_id: int
    numero: str
    tipo: str
    estado: str
    origen: str
    captura_id: int | None
    partner_odoo_id: int | None
    tipo_contacto: str
    cliente_nombre: str
    rif: str
    email: str
    telefono: str
    direccion: str
    plus_code: str
    latitud: float | None
    longitud: float | None
    es_primera_compra: bool
    moneda_lista: str
    vendedor_email: str
    fecha_entrega: date | None
    condiciones_negociacion: str
    observaciones: str
    subtotal: Decimal
    total_litros: Decimal
    odoo_order_id: int | None
    odoo_order_name: str
    error_odoo: str
    creado_por: str
    creado_en: datetime
    actualizado_en: datetime
    lineas: tuple[LineaDocumento, ...] = ()

    def a_dict(self, *, incluir_lineas: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.documento_id,
            "numero": self.numero,
            "tipo": self.tipo,
            "estado": self.estado,
            "origen": self.origen,
            "captura_id": self.captura_id,
            "partner_odoo_id": self.partner_odoo_id,
            "tipo_contacto": self.tipo_contacto,
            "cliente_nombre": self.cliente_nombre,
            "rif": self.rif,
            "email": self.email,
            "telefono": self.telefono,
            "direccion": self.direccion,
            "plus_code": self.plus_code,
            "latitud": self.latitud,
            "longitud": self.longitud,
            "es_primera_compra": self.es_primera_compra,
            "moneda_lista": self.moneda_lista,
            "vendedor_email": self.vendedor_email,
            "fecha_entrega": self.fecha_entrega.isoformat() if self.fecha_entrega else None,
            "condiciones_negociacion": self.condiciones_negociacion,
            "observaciones": self.observaciones,
            "subtotal": float(self.subtotal),
            "total_litros": float(self.total_litros),
            "n_lineas": len(self.lineas),
            "lineas_sin_catalogo": sum(
                1 for ln in self.lineas if not (ln.producto_id and ln.producto_id.isdigit())
            ),
            "odoo_order_id": self.odoo_order_id,
            "odoo_order_name": self.odoo_order_name,
            "error_odoo": self.error_odoo,
            "creado_por": self.creado_por,
            "creado_en": self.creado_en.isoformat(timespec="seconds"),
            "actualizado_en": self.actualizado_en.isoformat(timespec="seconds"),
        }
        if incluir_lineas:
            out["lineas"] = [ln.a_dict() for ln in self.lineas]
        return out


def normalizar_rif_opcional(rif: Any) -> str:
    if vacio(rif):
        return ""
    try:
        return normalizar_rif(str(rif))
    except Exception as e:
        raise DocumentoInvalido(
            "RIF inválido: usa letra (V/J/E/G/B/P) y números, p. ej. J-123456789.",
            ["rif"],
        ) from e


def parsear_fecha_opcional(valor: Any) -> date | None:
    if vacio(valor):
        return None
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor).strip()[:10])
    except ValueError as e:
        raise DocumentoInvalido(
            "Fecha de entrega inválida (usa AAAA-MM-DD).", ["fecha_entrega"]
        ) from e
