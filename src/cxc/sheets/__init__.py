"""Persistencia Google Sheets — binding de producción del ``Repository``.

La lógica de negocio nunca importa esto directamente: usa ``Repository``. Aquí
vive el adaptador concreto (``SheetsRepository``) sobre un ``SheetGateway``
inyectable: ``GspreadGateway`` en producción, ``InMemorySheetGateway`` en tests.
"""

from .gateway import GspreadGateway, InMemorySheetGateway, SheetGateway
from .repository import SheetsRepository

__all__ = [
    "SheetGateway",
    "InMemorySheetGateway",
    "GspreadGateway",
    "SheetsRepository",
]
