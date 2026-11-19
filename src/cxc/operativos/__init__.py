"""Documentos operativos de Tesorería, Compras y Gastos en CxC.

Cubre el flujo homologado Bot <-> CxC (Validación + Preview + Aprobación) <-> Odoo para:
- ``pago``: Pagos recibidos de clientes (``PAG-XXXXX``)
- ``factura_proveedor``: Facturas / recibos de proveedores (``FPR-XXXXX``)
- ``pago_proveedor``: Pagos a proveedores (``PPR-XXXXX``)
- ``gasto``: Gastos sin factura / compras menores (``GAS-XXXXX``)
"""

from .modelos import (
    BORRADOR,
    CATEGORIAS_GASTO,
    ENVIADO_ODOO,
    ERROR_ODOO,
    ESTADOS_OPERATIVOS,
    ETIQUETA_POR_TIPO,
    INCOMPLETO,
    PENDIENTE_APROBACION,
    PREFIJO_POR_TIPO,
    RECHAZADO,
    TIPO_FACTURA_PROVEEDOR,
    TIPO_GASTO,
    TIPO_PAGO,
    TIPO_PAGO_PROVEEDOR,
    TIPOS_COMPRAS,
    TIPOS_OPERATIVOS,
    DocumentoOperativo,
    OperativoEstadoInvalido,
    OperativoInvalido,
    OperativoNoEncontrado,
    OperativoYaEnviado,
    validar_campos_operativo,
)
from .servicio import ServicioOperativos

__all__ = [
    "BORRADOR",
    "CATEGORIAS_GASTO",
    "DocumentoOperativo",
    "ENVIADO_ODOO",
    "ERROR_ODOO",
    "ESTADOS_OPERATIVOS",
    "ETIQUETA_POR_TIPO",
    "INCOMPLETO",
    "OperativoEstadoInvalido",
    "OperativoInvalido",
    "OperativoNoEncontrado",
    "OperativoYaEnviado",
    "PENDIENTE_APROBACION",
    "PREFIJO_POR_TIPO",
    "RECHAZADO",
    "ServicioOperativos",
    "TIPOS_COMPRAS",
    "TIPOS_OPERATIVOS",
    "TIPO_FACTURA_PROVEEDOR",
    "TIPO_GASTO",
    "TIPO_PAGO",
    "TIPO_PAGO_PROVEEDOR",
    "validar_campos_operativo",
]
