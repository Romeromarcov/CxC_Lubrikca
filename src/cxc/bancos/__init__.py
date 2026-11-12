"""Ingesta bancaria (fase 1): estados de cuenta -> modelo canónico -> saldos consolidados.

Pipeline por capas, con Odoo como libro contable y no como motor de conciliación::

    archivo del banco ─► bancos_archivos (crudo, inmutable, sha256)
                     ─► parsers (uno por banco) ─► MovimientoBancario canónico
                     ─► validación de la cadena de saldos (cuadra al céntimo)
                     ─► clasificación por reglas ─► bancos_movimientos (dedupe por huella)
                     ─► saldos consolidados (USD/VES, tasa BCV) + Excel
                     ─► CSV de líneas de extracto para importar en Odoo

Este paquete NO escribe en Odoo (ver README, principio rector).
"""

from .ingesta import ArchivoPreparado, MovimientoClasificado, preparar
from .modelos import EstadoCuenta, MovimientoBancario
from .parsers import PARSERS, parsear
from .validacion import validar_estado

__all__ = [
    "PARSERS",
    "ArchivoPreparado",
    "EstadoCuenta",
    "MovimientoBancario",
    "MovimientoClasificado",
    "parsear",
    "preparar",
    "validar_estado",
]
