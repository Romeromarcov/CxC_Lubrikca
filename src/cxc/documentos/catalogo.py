"""Catálogo real de productos de Odoo y resolución de la lista comercial por defecto.

Sirve tanto a la interfaz web de documentos comerciales (Cotizaciones y Pedidos dentro de Ventas)
como a los bots de Telegram (``GET /api/bots/catalogo``) y al validador del motor de descuentos.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Engine, select

from cxc.db.schema import catalogo

logger = logging.getLogger("cxc.documentos.catalogo")

ExecuteFn = Callable[[str, str, list[Any], dict[str, Any]], Any]

_RE_PRESENTACION = re.compile(r"\(([^)]*)\)\s*$")
_RE_PREFIJO_CODIGO = re.compile(r"^\[([^\]]+)\]\s*")


def _sin_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in normalizado if unicodedata.category(c) != "Mn")


def _normalizar(texto: str) -> str:
    limpio = re.sub(r"[^a-z0-9.\-]+", " ", _sin_acentos(texto))
    return re.sub(r"\s+", " ", limpio).strip()


def extraer_presentacion(nombre: str) -> str:
    m = _RE_PRESENTACION.search((nombre or "").strip())
    return m.group(1).strip().upper() if m else ""


def tipo_envase_de_presentacion(presentacion: str, nombre: str = "") -> str:
    """Clasifica una presentación de Odoo en CAJA, PAILA, TAMBOR o UNIDAD para las reglas."""
    p = _sin_acentos(f"{presentacion} {nombre}").upper()
    if "TAMBOR" in p:
        return "TAMBOR"
    if "PAILA" in p or "CUÑETE" in p or "CUNETE" in p or "BALDE" in p:
        return "PAILA"
    if re.search(r"\b1\s*X\s*\d+\b", p) or "CAJA" in p:
        return "CAJA"
    return "UNIDAD"


@dataclass(frozen=True)
class ListaComercialActiva:
    lista_id: str
    nombre: str
    moneda: str = "VES"

    def a_dict(self) -> dict[str, Any]:
        return {
            "id": self.lista_id,
            "nombre": self.nombre,
            "moneda": self.moneda,
        }


LISTA_POR_DEFECTO_FALLBACK = ListaComercialActiva(
    lista_id="21",
    nombre="Pago VES Nov 2026",
    moneda="VES",
)


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
    stock_disponible: Decimal | None = None

    def estado_stock(self) -> str | None:
        if self.stock_disponible is None:
            return None
        if self.stock_disponible <= Decimal("0"):
            return "agotado"
        if self.stock_disponible <= Decimal("5"):
            return "bajo"
        return "disponible"

    def a_dict(self) -> dict[str, Any]:
        pres = self.presentacion or extraer_presentacion(self.nombre)
        return {
            "producto_id": self.producto_id,
            "codigo": self.codigo,
            "nombre": self.nombre,
            "marca": self.marca,
            "presentacion": pres,
            "tipo_envase": tipo_envase_de_presentacion(pres, self.nombre),
            "litros": float(self.litros),
            "precio_usd": float(self.precio_usd),
            "precio_ves": float(self.precio_ves),
            "stock_disponible": (
                float(self.stock_disponible) if self.stock_disponible is not None else None
            ),
            "estado_stock": self.estado_stock(),
            "etiqueta": f"[{self.codigo}] {self.nombre}" if self.codigo else self.nombre,
        }


class ProveedorCatalogo(Protocol):
    def listar(self, *, refresh: bool = False) -> list[ItemCatalogo]: ...

    def lista_comercial_defecto(self, *, refresh: bool = False) -> ListaComercialActiva: ...


def elegir_lista_comercial_ves_activa(
    pricelists_odoo: Sequence[dict[str, Any]],
    *,
    preferida_id: str | None = None,
) -> ListaComercialActiva:
    """Selecciona la lista comercial VES vigente en Odoo (p. ej. «Pago VES Nov 2026»)."""
    activas = [p for p in pricelists_odoo if p.get("active", True)]
    if not activas:
        return LISTA_POR_DEFECTO_FALLBACK

    # 1. Listas activas comerciales principales tipo «Pago VES ...» (sin foráneas/industrial)
    pago_ves: list[dict[str, Any]] = []
    for p in activas:
        nom = _sin_acentos(str(p.get("name") or ""))
        if (
            "ves" in nom
            and "pago" in nom
            and not any(x in nom for x in ("industrial", "foranea", "maturin"))
        ):
            pago_ves.append(p)
    if pago_ves:
        # El ID mayor corresponde a la lista mensual más reciente creada en Odoo (ej. Nov > Sept)
        pago_ves.sort(key=lambda x: int(x.get("id") or 0), reverse=True)
        elegida = pago_ves[0]
        return ListaComercialActiva(
            lista_id=str(elegida["id"]),
            nombre=str(elegida["name"]).strip(),
            moneda="VES",
        )

    # 2. Si existe una preferida configurada en pricelist_referencia_por_grupo y está activa
    if preferida_id:
        for p in activas:
            if str(p.get("id")) == str(preferida_id):
                return ListaComercialActiva(
                    lista_id=str(p["id"]),
                    nombre=str(p.get("name") or f"Lista #{p['id']}").strip(),
                    moneda="VES",
                )

    # 3. Cualquier lista activa VES no industrial
    ves_otras = [
        p
        for p in activas
        if "ves" in _sin_acentos(str(p.get("name") or ""))
        and "industrial" not in _sin_acentos(str(p.get("name") or ""))
    ]
    if ves_otras:
        ves_otras.sort(key=lambda x: int(x.get("id") or 0), reverse=True)
        e = ves_otras[0]
        return ListaComercialActiva(
            lista_id=str(e["id"]),
            nombre=str(e["name"]).strip(),
            moneda="VES",
        )

    e0 = activas[0]
    return ListaComercialActiva(
        lista_id=str(e0["id"]),
        nombre=str(e0.get("name") or "Comercial VES").strip(),
        moneda="VES",
    )


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
    """Intenta resolver inequívocamente un producto del catálogo real."""
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


@dataclass(frozen=True)
class DatosPreciosOdoo:
    lista_defecto: ListaComercialActiva
    precios: dict[str, tuple[Decimal, Decimal]]
    stock: dict[str, Decimal] = field(default_factory=dict)


class CatalogoSQLOdoo:
    """Lee el catálogo del espejo Postgres y enriquece precios USD/VES + lista comercial activa."""

    CACHE_TTL_S = 300.0
    _lock = threading.Lock()
    _cache_datos: tuple[float, DatosPreciosOdoo] | None = None

    def __init__(
        self,
        engine: Engine,
        lector_fn: Callable[[], DatosPreciosOdoo | dict[str, tuple[Decimal, Decimal]]]
        | None = None,
    ) -> None:
        self._engine = engine
        self._lector = lector_fn

    @classmethod
    def limpiar_cache(cls) -> None:
        with cls._lock:
            cls._cache_datos = None

    def _cargar_datos(self, refresh: bool = False) -> DatosPreciosOdoo:
        if self._lector is None:
            return DatosPreciosOdoo(LISTA_POR_DEFECTO_FALLBACK, {}, {})
        ahora = time.monotonic()
        with self._lock:
            if not refresh and self._cache_datos is not None:
                ts, datos = self._cache_datos
                if ahora - ts < self.CACHE_TTL_S:
                    return datos
        try:
            crudo = self._lector()
            nuevos = (
                crudo
                if isinstance(crudo, DatosPreciosOdoo)
                else DatosPreciosOdoo(LISTA_POR_DEFECTO_FALLBACK, crudo, {})
            )
        except Exception:
            logger.warning("No pude enriquecer precios en vivo desde Odoo; uso espejo local.")
            return DatosPreciosOdoo(LISTA_POR_DEFECTO_FALLBACK, {}, {})
        with self._lock:
            self._cache_datos = (ahora, nuevos)
        return nuevos

    def lista_comercial_defecto(self, *, refresh: bool = False) -> ListaComercialActiva:
        return self._cargar_datos(refresh=refresh).lista_defecto

    def listar(self, *, refresh: bool = False) -> list[ItemCatalogo]:
        with self._engine.connect() as conn:
            filas = conn.execute(select(catalogo)).mappings().all()
        datos = self._cargar_datos(refresh=refresh)
        precios = datos.precios
        stock_map = datos.stock
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
                    stock_disponible=stock_map.get(pid),
                )
            )
        items.sort(key=lambda x: (x.nombre.lower(), x.codigo.lower()))
        return items


def cargar_datos_precios_odoo(
    execute: ExecuteFn,
    usd_pricelist_ids: Sequence[int],
    ves_pricelist_ids: Sequence[int],
    *,
    preferida_ves_id: str | None = None,
) -> DatosPreciosOdoo:
    """Resuelve la lista comercial VES por defecto (ej. Pago VES Nov 2026) y sus precios."""
    pricelists = execute(
        "product.pricelist",
        "search_read",
        [[["active", "=", True]]],
        {"fields": ["id", "name", "active"], "limit": 100},
    )
    lista_defecto = elegir_lista_comercial_ves_activa(
        pricelists or [], preferida_id=preferida_ves_id
    )
    activas_ids = {int(p["id"]) for p in (pricelists or []) if p.get("id")}

    # Asegura que la lista comercial VES activa por defecto tenga máxima prioridad para precio_ves
    defecto_id = int(lista_defecto.lista_id) if lista_defecto.lista_id.isdigit() else None
    ves_activas = [int(x) for x in ves_pricelist_ids if int(x) in activas_ids]
    usd_activas = [int(x) for x in usd_pricelist_ids if int(x) in activas_ids]

    # También detecta par activo «Pago USD ...» si aún no estaba en valid_pricelists_usd
    for p in sorted(pricelists or [], key=lambda x: int(x.get("id") or 0), reverse=True):
        nom = _sin_acentos(str(p.get("name") or ""))
        pid_int = int(p["id"])
        if (
            "pago" in nom
            and "usd" in nom
            and "industrial" not in nom
            and pid_int not in usd_activas
        ):
            usd_activas.append(pid_int)

    try:
        prods = execute(
            "product.product",
            "search_read",
            [[["sale_ok", "=", True], ["active", "=", True]]],
            {"fields": ["id", "product_tmpl_id", "qty_available"], "limit": 5000},
        )
    except Exception:
        logger.warning("No pude leer qty_available en product.product; reintento sin stock.")
        prods = execute(
            "product.product",
            "search_read",
            [[["sale_ok", "=", True], ["active", "=", True]]],
            {"fields": ["id", "product_tmpl_id"], "limit": 5000},
        )
    tmpl_por_prod: dict[str, int] = {}
    stock_por_prod: dict[str, Decimal] = {}
    tmpl_ids: set[int] = set()
    for p in prods or []:
        pt = p.get("product_tmpl_id")
        tid = int(pt[0]) if isinstance(pt, list | tuple) and pt else None
        if tid is not None:
            pid_str = str(p["id"])
            tmpl_por_prod[pid_str] = tid
            tmpl_ids.add(tid)
            if "qty_available" in p and p.get("qty_available") not in (None, False, ""):
                with suppress(Exception):
                    stock_por_prod[pid_str] = Decimal(str(p["qty_available"]))

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

    listas_consulta = list(
        dict.fromkeys(
            [*(usd_activas or usd_pricelist_ids), *(ves_activas or ves_pricelist_ids)]
            + ([defecto_id] if defecto_id is not None else [])
        )
    )
    prices_usd: dict[int, Decimal] = {}
    prices_ves: dict[int, Decimal] = {}
    prices_ves_defecto: dict[int, Decimal] = {}

    if listas_consulta:
        rules = execute(
            "product.pricelist.item",
            "search_read",
            [
                [
                    ["pricelist_id", "in", [int(x) for x in listas_consulta]],
                    ["compute_price", "=", "fixed"],
                ]
            ],
            {"fields": ["pricelist_id", "product_tmpl_id", "fixed_price"], "limit": 25000},
        )
        usd_set = {int(x) for x in (usd_activas or usd_pricelist_ids)}
        ves_set = {int(x) for x in (ves_activas or ves_pricelist_ids)}
        for r in rules or []:
            pl = r.get("pricelist_id")
            pl_id = int(pl[0]) if isinstance(pl, list | tuple) and pl else int(pl or 0)
            pt = r.get("product_tmpl_id")
            pt_id = int(pt[0]) if isinstance(pt, list | tuple) and pt else None
            if pt_id is None:
                continue
            val = Decimal(str(r.get("fixed_price") or "0"))
            if defecto_id is not None and pl_id == defecto_id:
                prices_ves_defecto[pt_id] = val
            if pl_id in ves_set and pt_id not in prices_ves:
                prices_ves[pt_id] = val
            if pl_id in usd_set:
                prices_usd[pt_id] = val

    salida: dict[str, tuple[Decimal, Decimal]] = {}
    for pid, tid in tmpl_por_prod.items():
        fb = fallback_por_tmpl.get(tid, Decimal("0"))
        p_ves = prices_ves_defecto.get(tid, prices_ves.get(tid, fb))
        p_usd = prices_usd.get(tid, fb * Decimal("0.65") if fb > 0 else Decimal("0"))
        salida[pid] = (p_usd, p_ves)
    return DatosPreciosOdoo(lista_defecto=lista_defecto, precios=salida, stock=stock_por_prod)


def cargar_precios_odoo_por_producto(
    execute: ExecuteFn,
    usd_pricelist_ids: Sequence[int],
    ves_pricelist_ids: Sequence[int],
) -> dict[str, tuple[Decimal, Decimal]]:
    """Compatibilidad directa: devuelve solo el mapa ``producto_id -> (usd, ves)``."""
    return cargar_datos_precios_odoo(execute, usd_pricelist_ids, ves_pricelist_ids).precios
