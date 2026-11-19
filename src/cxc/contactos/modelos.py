"""Modelos de dominio y excepciones del Maestro Único de Contactos."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

ROLES_CONTACTO: tuple[str, ...] = ("prospecto", "cliente", "proveedor", "ambos")
ORIGENES_CONTACTO: tuple[str, ...] = ("odoo", "bot", "cxc")
ESTADOS_VALIDACION: tuple[str, ...] = (
    "PENDIENTE",
    "VALIDADO",
    "ENVIADO_ODOO",
    "DUPLICADO",
    "DESCARTADO",
)


class ContactoInvalido(ValueError):
    """Los datos del contacto no cumplen los requisitos mínimos."""


class ContactoNoEncontrado(LookupError):
    """El contacto solicitado no existe en el maestro."""


class ContactoDuplicadoError(RuntimeError):
    """Se detectó un contacto duplicado bloqueante (ej. RIF idéntico)."""

    def __init__(self, mensaje: str, duplicados: list[dict[str, Any]] | None = None) -> None:
        super().__init__(mensaje)
        self.duplicados: list[dict[str, Any]] = list(duplicados or [])


@dataclass
class ContactoMaestro:
    """Representación unificada de un contacto (Odoo + Bots + CxC)."""

    contacto_id: int | None
    odoo_partner_id: int | None = None
    origen: str = "cxc"
    rol: str = "prospecto"
    nombre: str = ""
    nombre_normalizado: str = ""
    rif: str = ""
    rif_normalizado: str = ""
    telefono: str = ""
    telefono_normalizado: str = ""
    email: str = ""
    direccion: str = ""
    ciudad: str = ""
    latitud: Decimal | float | None = None
    longitud: Decimal | float | None = None
    plus_code: str = ""
    vendedor_email: str = ""
    estado_validacion: str = "PENDIENTE"
    completitud_pct: int = 0
    campos_faltantes: list[str] = field(default_factory=list)
    duplicado_de_id: int | None = None
    posibles_duplicados: list[dict[str, Any]] = field(default_factory=list)
    es_borrador_telegram: bool = False
    captura_id: int | None = None
    creado_por: str = "sistema"
    validado_por: str = ""
    notas: str = ""
    activo: bool = True
    creado_en: datetime | None = None
    actualizado_en: datetime | None = None

    @property
    def listo_para_odoo(self) -> bool:
        """Indica si cumple el mínimo comercial según su rol para crearse/actualizarse en Odoo."""
        if not self.nombre.strip() or not self.telefono.strip():
            return False
        if self.rol in ("cliente", "proveedor", "ambos"):
            return bool(self.rif.strip())
        return True

    def ubicacion_dict(self) -> dict[str, Any] | None:
        """Devuelve la ubicación GPS estructurada con URL de Google Maps si existe."""
        if self.latitud is None or self.longitud is None:
            return None
        lat = float(self.latitud)
        lon = float(self.longitud)
        if lat == 0.0 and lon == 0.0:
            return None
        return {
            "latitud": round(lat, 7),
            "longitud": round(lon, 7),
            "plus_code": self.plus_code or "",
            "maps_url": f"https://maps.google.com/?q={lat:.7f},{lon:.7f}",
        }

    def a_dict(self) -> dict[str, Any]:
        """Serializa el contacto para respuestas JSON de la API web y bots."""
        ub = self.ubicacion_dict()
        return {
            "contacto_id": self.contacto_id,
            "odoo_partner_id": self.odoo_partner_id,
            "id": self.odoo_partner_id or self.contacto_id,
            "origen": self.origen,
            "rol": self.rol,
            "clasificacion": self.rol,
            "nombre": self.nombre,
            "rif": self.rif,
            "telefono": self.telefono,
            "email": self.email,
            "direccion": self.direccion,
            "ciudad": self.ciudad,
            "latitud": ub["latitud"] if ub else None,
            "longitud": ub["longitud"] if ub else None,
            "plus_code": self.plus_code,
            "ubicacion": ub,
            "vendedor_email": self.vendedor_email,
            "estado_validacion": self.estado_validacion,
            "completitud_pct": int(self.completitud_pct),
            "campos_faltantes": list(self.campos_faltantes),
            "listo_para_odoo": self.listo_para_odoo,
            "duplicado_de_id": self.duplicado_de_id,
            "posibles_duplicados": list(self.posibles_duplicados),
            "tiene_duplicados": bool(self.posibles_duplicados),
            "es_borrador_telegram": bool(self.es_borrador_telegram),
            "captura_id": self.captura_id,
            "creado_por": self.creado_por,
            "validado_por": self.validado_por,
            "notas": self.notas,
            "activo": bool(self.activo),
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
            "actualizado_en": self.actualizado_en.isoformat() if self.actualizado_en else None,
        }
