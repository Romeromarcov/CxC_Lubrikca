"""Catálogo real de productos de Odoo (espejo local + precios vigentes USD/VES).

Sirve tanto a la interfaz web de documentos comerciales (Cotizaciones y Pedidos dentro de Ventas)
como a los bots de Telegram (``GET /api/bots/catalogo``) y a la validación al enviar a Odoo.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Engine, select

from cxc.db.schema import catalogo

logger = logging.getLogger("cxc.documentos.catalogo")

ExecuteFn = Callable[[str, str, list[Any], dict[str, Any]], Any]

_RE_PRESENTACION = re.compile(r"\(([^)]*)\)\s*$")
_RE_PREFIJO_CODIGO = re.compile(r"^\[([^\]]+)\]\s*")


def _sin_acentos(texto: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def _normalizar(texto: str) -> str:
    limpio = re.sub(r"[^a-z0-9.\-]+", " ", _sin_acentos(texto))
    return re.sub(r"\s+", " ", limpio).strip()


def extraer_presentacion(nombre: str) -> str:
    m = _RE_PRESENTACION.search((nombre or "").strip())
    return m.group(1).strip().upper() if m else ""


@dataclass(frozen=True)
class ItemCatalogo:
    producto_id: str
    codigo: str
    nombre: str
    marca: str = ""
    presentacion: str = ""
    litros: Decimal = Decimal("0")
    precio_usd: Decimal = Decimal("0")
    precio_ves: Decimal = Decimal("0")

    def a_dict(self) -> dict[str, Any]:
        return {
            "producto_id": self.producto_id,
            "codigo": self.codigo,
            "nombre": self.nombre,
            "marca": self.marca,
            "presentacion": self.presentacion or extraer_presentacion(self.nombre),
            "litros": float(self.litros),
            "precio_usd": float(self.precio_usd),
            "precio_ves": float(self.precio_ves),
            "etiqueta": f"[{self.codigo}] {self.nombre}" if self.codigo else self.nombre,
        }


class ProveedorCatalogo(Protocol):
    def listar(self, *, refresh: bool = False) -> list[ItemCatalogo]: ...


def buscar_productos(
    items: Sequence[ItemCatalogo], consulta: str | None = None, *, limite: int = 20
) -> list[ItemCatalogo]:
    """Busca productos por código, ID, nombre, marca o presentación ordenados por relevancia."""
    lim = max(1, min(limite, 500))
    if not consulta or not consulta.strip():
        ordenados = sorted(items, key=lambda x: (x.nombre.lower(), x.codigo.lower()))
        return list(ordenados[:lim])

    crudo = consulta.strip()
    m_pref = _RE_PREFIJO_CODIGO.match(crudo)
    codigo_prefijo = m_pref.group(1).strip().lower() if m_pref else ""
    q_norm = _normalizar(crudo)
    tokens = [t for t in q_norm.split() if t]
    if not tokens:
        return []

    puntuados: list[tuple[int, str, ItemCatalogo]] = []
    for item in items:
        pid_l = item.producto_id.strip().lower()
        cod_l = item.codigo.strip().lower()
        nom_norm = _normalizar(item.nombre)
        pres_norm = _normalizar(item.presentacion or extraer_presentacion(item.nombre))
        marca_norm = _normalizar(item.marca)
        bolsa = f"{cod_l} {nom_norm} {marca_norm} {pres_norm}".strip()

        if crudo.lower() == pid_l or (cod_l and crudo.lower() == cod_l):
            puntuados.append((1000, item.nombre.lower(), item))
            continue
        if codigo_prefijo and cod_l == codigo_prefijo:
            puntuados.append((980, item.nombre.lower(), item))
            continue
        if q_norm == nom_norm:
            puntuados.append((950, item.nombre.lower(), item))
            continue

        coincidencias = [t for t in tokens if t in bolsa]
        if len(coincidencias) == len(tokens):
            pts = 200 + len(tokens) * 30
            if cod_l and any(cod_l.startswith(t) for t in tokens):
                pts += 80
            if nom_norm.startswith(tokens[0]):
                pts += 40
            puntuados.append((pts, item.nombre.lower(), item))
        elif len(tokens) >= 2 and len(coincidencias) >= len(tokens) - 1:
            # Coincidencia parcial útil cuando el vendedor agrega una palabra genérica extra
            pts = 60 + len(coincidencias) * 20
            puntuados.append((pts, item.nombre.lower(), item))

    puntuados.sort(key=lambda par: (-par[0], par[1]))
    return [it for _, _, it in puntuados[:lim]]


def resolver_producto_unico(
    items: Sequence[ItemCatalogo],
    *,
    producto_id: str | None = None,
    texto: str | None = None,
) -> tuple[ItemCatalogo | None, list[ItemCatalogo]]:
    """Intenta resolver inequívocamente un producto del catálogo real.

    Devuelve ``(elegido, candidatos)``:
    - Si ``producto_id`` coincide o el código/nombre es único, ``elegido`` trae el ``ItemCatalogo``.
    - Si hay varias presentaciones posibles (p. ej. «liga para frenos» → 6 SKUs), ``elegido`` es
      ``None`` y ``candidatos`` trae las opciones reales para elegir.
    """
    if producto_id and str(producto_id).strip():
        buscado_id = str(producto_id).strip()
        for it in items:
            if it.producto_id == buscado_id:
                return it, [it]

    txt = (texto or "").strip()
    if not txt:
        return None, []

    m_pref = _RE_PREFIJO_CODIGO.match(txt)
    cod_explicit = m_pref.group(1).strip().lower() if m_pref else txt.lower()
    por_codigo = [it for it in items if it.codigo and it.codigo.strip().lower() == cod_explicit]
    if len(por_codigo) == 1:
        return por_codigo[0], por_codigo

    q_norm = _normalizar(txt)
    por_nombre_exacto = [it for it in items if _normalizar(it.nombre) == q_norm]
    if len(por_nombre_exacto) == 1:
        return por_nombre_exacto[0], por_nombre_exacto

    candidatos = buscar_productos(items, txt, limite=8)
    if len(candidatos) == 1:
        tokens = [t for t in q_norm.split() if t]
        it = candidatos[0]
        bolsa = _normalizar(f"{it.codigo} {it.nombre} {it.marca} {it.presentacion}")
        if tokens and all(t in bolsa for t in tokens):
            return it, candidatos
    return None, candidatos


class CatalogoSQLOdoo:
    """Lee el catálogo del espejo Postgres y enriquece precios USD/VES desde Odoo (con caché)."""

    CACHE_TTL_S = 300.0
    _lock = threading.Lock()
    _cache_precios: tuple[float, dict[str, tuple[Decimal, Decimal]]] | None = None

    def __init__(
        self,
        engine: Engine,
        lector_precios_fn: Callable[[], dict[str, tuple[Decimal, Decimal]]] | None = None,
    ) -> None:
        self._engine = engine
        self._lector_precios = lector_precios_fn

    @classmethod
    def limpiar_cache(cls) -> None:
        with cls._lock:
            cls._cache_precios = None

    def _precios_por_producto(self, refresh: bool = False) -> dict[str, tuple[Decimal, Decimal]]:
        if self._lector_precios is None:
            return {}
        ahora = time.monotonic()
        with self._lock:
            if not refresh and self._cache_precios is not None:
                ts, datos = self._cache_precios
                if ahora - ts < self.CACHE_TTL_S:
                    return datos
        try:
            nuevos = self._lector_precios()
        except Exception:
            logger.warning("No pude enriquecer precios en vivo desde Odoo; uso espejo local.")
            return {}
        with self._lock:
            self._cache_precios = (ahora, nuevos)
        return nuevos

    def listar(self, *, refresh: bool = False) -> list[ItemCatalogo]:
        with self._engine.connect() as conn:
            filas = conn.execute(select(catalogo)).mappings().all()
        precios = self._precios_por_producto(refresh=refresh)
        items: list[ItemCatalogo] = []
        for f in filas:
            pid = str(f["producto_id"])
            nombre = str(f["nombre"] or "").strip()
            p_usd, p_ves = precios.get(pid, (Decimal("0"), Decimal("0")))
            items.append(
                ItemCatalogo(
                    producto_id=pid,
                    codigo=str(f["codigo"] or "").strip(),
                    nombre=nombre,
                    marca=str(f["marca"] or "").strip(),
                    presentacion=extraer_presentacion(nombre),
                    litros=Decimal(str(f["volumen"] or "0")),
                    precio_usd=p_usd,
                    precio_ves=p_ves,
                )
            )
        items.sort(key=lambda x: (x.nombre.lower(), x.codigo.lower()))
        return items


def cargar_precios_odoo_por_producto(
    execute: ExecuteFn,
    usd_pricelist_ids: Sequence[int],
    ves_pricelist_ids: Sequence[int],
) -> dict[str, tuple[Decimal, Decimal]]:
    """Mapa ``product.product.id -> (precio_usd, precio_ves)`` desde Odoo."""
    prods = execute(
        "product.product",
        "search_read",
        [[["sale_ok", "=", True], ["active", "=", True]]],
        {"fields": ["id", "product_tmpl_id"], "limit": 5000},
    )
    tmpl_por_prod: dict[str, int] = {}
    tmpl_ids: set[int] = set()
    for p in prods or []:
        pt = p.get("product_tmpl_id")
        tid = int(pt[0]) if isinstance(pt, list | tuple) and pt else None
        if tid is not None:
            tmpl_por_prod[str(p["id"])] = tid
            tmpl_ids.add(tid)

    fallback_por_tmpl: dict[int, Decimal] = {}
    if tmpl_ids:
        tmpls = execute(
            "product.template",
            "read",
            [sorted(tmpl_ids)],
            {"fields": ["id", "list_price_usd"]},
        )
        for t in tmpls or []:
            fallback_por_tmpl[int(t["id"])] = Decimal(str(t.get("list_price_usd") or "0"))

    all_lists = [int(x) for x in (*usd_pricelist_ids, *ves_pricelist_ids)]
    prices_usd: dict[int, Decimal] = {}
    prices_ves: dict[int, Decimal] = {}
    if all_lists:
        rules = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "in", all_lists], ["compute_price", "=", "fixed"]]],
            {"fields": ["pricelist_id", "product_tmpl_id", "fixed_price"], "limit": 20000},
        )
        usd_set = {int(x) for x in usd_pricelist_ids}
        ves_set = {int(x) for x in ves_pricelist_ids}
        for r in rules or []:
            pl = r.get("pricelist_id")
            pl_id = int(pl[0]) if isinstance(pl, list | tuple) and pl else int(pl or 0)
            pt = r.get("product_tmpl_id")
            pt_id = int(pt[0]) if isinstance(pt, list | tuple) and pt else None
            if pt_id is None:
                continue
            val = Decimal(str(r.get("fixed_price") or "0"))
            if pl_id in usd_set:
                prices_usd[pt_id] = val
            elif pl_id in ves_set:
                prices_ves[pt_id] = val

    salida: dict[str, tuple[Decimal, Decimal]] = {}
    for pid, tid in tmpl_por_prod.items():
        fb = fallback_por_tmpl.get(tid, Decimal("0"))
        salida[pid] = (prices_usd.get(tid, fb), prices_ves.get(tid, fb))
    return salida
