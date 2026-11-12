"""Ingesta de un archivo: bytes crudos -> parseo -> validación -> clasificación.

Lógica pura, sin base: devuelve un ``ArchivoPreparado`` que ``cxc.bancos.repositorio``
persiste en una sola transacción. Un archivo que no parsea no se guarda (no hay nada
que deduplicar ni conciliar); uno que parsea pero NO cuadra sí se guarda, marcado
``cuadra=False``, porque el descuadre en sí es información (falta una fila, corte
parcial) y esconderlo sería peor.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .clasificacion import clasificar
from .modelos import EstadoCuenta, MovimientoBancario
from .parsers import parsear
from .validacion import Resultado, validar_estado

TAMANO_MAXIMO = 10 * 1024 * 1024  # 10 MB: un estado mensual pesa decenas de KB


@dataclass(frozen=True)
class MovimientoClasificado:
    mov: MovimientoBancario
    categoria: str
    regla: str


@dataclass
class ArchivoPreparado:
    sha256: str
    nombre: str
    contenido: bytes
    estado: EstadoCuenta
    validacion: Resultado
    movimientos: list[MovimientoClasificado]

    @property
    def observaciones(self) -> str:
        partes = list(self.validacion.saltos)
        if self.validacion.duplicados:
            partes.append(f"{len(self.validacion.duplicados)} huella(s) repetida(s) en el archivo")
        if self.estado.saldo_inicial_derivado:
            partes.append("saldo inicial derivado del 1er movimiento")
        partes.extend(self.estado.avisos)
        return "; ".join(partes)


def sha256_de(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def preparar(contenido: bytes, nombre: str, banco: str) -> ArchivoPreparado:
    if not contenido:
        raise ValueError("El archivo está vacío")
    if len(contenido) > TAMANO_MAXIMO:
        raise ValueError(f"El archivo pesa más de {TAMANO_MAXIMO // (1024 * 1024)} MB")
    estado = parsear(contenido, nombre, banco)
    if not estado.movimientos:
        raise ValueError(f"{nombre}: no se encontró ningún movimiento")
    movs = [MovimientoClasificado(m, *clasificar(m)) for m in estado.movimientos]
    return ArchivoPreparado(
        sha256=sha256_de(contenido),
        nombre=nombre,
        contenido=contenido,
        estado=estado,
        validacion=validar_estado(estado),
        movimientos=movs,
    )
