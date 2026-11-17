"""Consultas al espejo de Odoo (Postgres) para identificar clientes y productos de una captura.

Solo lectura y solo del espejo: no abre ninguna conexión a Odoo.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, func, select

from cxc.db.schema import catalogo, clientes, ordenes_venta
from cxc.documentos.catalogo import ItemCatalogo, extraer_presentacion, resolver_producto_unico


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
        """``producto_id`` por código/nombre exacto o coincidencia única en catálogo."""
        t = texto.strip().lower()
        with self._engine.connect() as conn:
            for columna in (catalogo.c.codigo, catalogo.c.nombre):
                filas = conn.execute(
                    select(catalogo.c.producto_id).where(func.lower(columna) == t).limit(3)
                ).all()
                if filas:
                    return [str(f[0]) for f in filas]
            todas = conn.execute(
                select(
                    catalogo.c.producto_id,
                    catalogo.c.codigo,
                    catalogo.c.nombre,
                    catalogo.c.marca,
                )
            ).all()
        items = [
            ItemCatalogo(
                producto_id=str(r[0]),
                codigo=str(r[1] or ""),
                nombre=str(r[2] or ""),
                marca=str(r[3] or ""),
                presentacion=extraer_presentacion(str(r[2] or "")),
            )
            for r in todas
        ]
        elegido, candidatos = resolver_producto_unico(items, texto=texto)
        if elegido is not None:
            return [elegido.producto_id]
        return [c.producto_id for c in candidatos[:3]]
