"""Ajustes temporales por regla y negociaciones especiales sobre órdenes existentes de Odoo."""

from .servicio import (
    ACCION_ESPECIAL,
    ACCION_EXCLUIR_MOTOR,
    SETTING_LIMPIEZA_ACTIVA,
    AjusteDescuentoDeshabilitado,
    AjusteDescuentoError,
    ServicioAjustesDescuentos,
    aplicar_ajustes_a_descuentos_orden,
)

__all__ = [
    "ACCION_ESPECIAL",
    "ACCION_EXCLUIR_MOTOR",
    "AjusteDescuentoDeshabilitado",
    "AjusteDescuentoError",
    "SETTING_LIMPIEZA_ACTIVA",
    "ServicioAjustesDescuentos",
    "aplicar_ajustes_a_descuentos_orden",
]
