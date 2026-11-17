"""Consultas al espejo de Odoo (Postgres) para identificar clientes y productos de una captura.

Solo lectura y solo del espejo: no abre ninguna conexión a Odoo.
"""

from __future__ import annotations

import re
from typing import Protocol

from sqlalchemy import Engine, func, select

from cxc.db.schema import catalogo, clientes, ordenes_venta

_RE_PREFIJO_COD = re.compile(r"^\[([^\]]+)\]\s*")


class Espejo(Protocol):
    def clientes_por_nombre(self, nombre: str) -> list[str]: ...

    def cliente_de_orden(self, so_id: str) -> str | None: ...

    def productos(self, texto: str) -> list[str]: ...


class EspejoSQL:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def clientes_por_nombre(self, nombre: str) -> list[str]:
        """``cliente_id`` (res.partner) cuyo nombre es exactamente ``nombre``, sin mayúsculas."""
        with self._engine.connect() as conn:
            filas = conn.execute(
                select(clientes.c.cliente_id)
                .where(func.lower(clientes.c.nombre) == nombre.strip().lower())
                .limit(3)
            ).all()
        return [str(f[0]) for f in filas]

    def cliente_de_orden(self, so_id: str) -> str | None:
        with self._engine.connect() as conn:
            fila = conn.execute(
                select(ordenes_venta.c.cliente_id).where(
                    func.lower(ordenes_venta.c.so_id) == so_id.strip().lower()
                )
            ).first()
        return str(fila[0]) if fila is not None else None

    def productos(self, texto: str) -> list[str]:
        """``producto_id`` (product.product) por código exacto y, si no hay, por nombre exacto."""
        raw = texto.strip()
        m = _RE_PREFIJO_COD.match(raw)
        candidatos_txt = [m.group(1).strip().lower(), raw.lower()] if m else [raw.lower()]
        with self._engine.connect() as conn:
            for t in candidatos_txt:
                for columna in (catalogo.c.codigo, catalogo.c.nombre):
                    filas = conn.execute(
                        select(catalogo.c.producto_id).where(func.lower(columna) == t).limit(3)
                    ).all()
                    if filas:
                        return [str(f[0]) for f in filas]
        return []
