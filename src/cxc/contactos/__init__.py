"""Paquete de dominio del Maestro Único de Contactos y Motor Anti-Duplicados (Fase 6)."""

from cxc.contactos.modelos import (
    ESTADOS_VALIDACION,
    ORIGENES_CONTACTO,
    ROLES_CONTACTO,
    ContactoDuplicadoError,
    ContactoInvalido,
    ContactoMaestro,
    ContactoNoEncontrado,
)
from cxc.contactos.motor import (
    buscar_posibles_duplicados,
    detectar_grupos_duplicados,
    evaluar_completitud,
    normalizar_email,
    normalizar_nombre,
    normalizar_rif_canonico,
    normalizar_telefono,
)
from cxc.contactos.repositorio import RepoContactos, RepoContactosMemoria, RepoContactosSQL
from cxc.contactos.servicio import ServicioContactos

__all__ = [
    "ESTADOS_VALIDACION",
    "ORIGENES_CONTACTO",
    "ROLES_CONTACTO",
    "ContactoDuplicadoError",
    "ContactoInvalido",
    "ContactoMaestro",
    "ContactoNoEncontrado",
    "RepoContactos",
    "RepoContactosMemoria",
    "RepoContactosSQL",
    "ServicioContactos",
    "buscar_posibles_duplicados",
    "detectar_grupos_duplicados",
    "evaluar_completitud",
    "normalizar_email",
    "normalizar_nombre",
    "normalizar_rif_canonico",
    "normalizar_telefono",
]
