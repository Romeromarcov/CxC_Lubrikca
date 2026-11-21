"""Fuente única de verdad para Rutas de Pago (BCV vs Binance) por lista de precios.

Reglas de negocio:
  - Por defecto, toda lista comercial que incluya ``VES`` en su nombre opera por
    ``Ruta BCV`` (``"bcv"``) y toda lista que incluya ``USD`` en su nombre opera
    por ``Ruta Binance`` (``"binance"``).
  - La configuración en ``pricelist_mapeo_unificado`` puede asignar/sobreescribir
    explícitamente el campo ``ruta`` (``"bcv"`` | ``"binance"``) de cada lista.
  - Enrutamiento bidireccional estricto: una orden nacida en Ruta Binance (USD)
    no puede liquidarse por Ruta BCV, y una orden nacida en Ruta BCV (VES) no
    puede liquidarse por Ruta Binance.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RUTA_BCV = "bcv"
RUTA_BINANCE = "binance"
RUTAS_VALIDAS = (RUTA_BCV, RUTA_BINANCE)


def normalizar_ruta(valor: Any) -> str:
    """Normaliza una etiqueta arbitraria a ``'bcv'``, ``'binance'`` o ``''``."""
    txt = str(valor or "").strip().lower()
    if txt in ("bcv", "ves", "ruta_bcv", "ruta bcv", "bs"):
        return RUTA_BCV
    if txt in ("binance", "usd", "ruta_binance", "ruta binance"):
        return RUTA_BINANCE
    return ""


def inferir_ruta_por_nombre(nombre: str = "", moneda: str = "") -> str:
    """Infiere ``'bcv'`` o ``'binance'`` según el nombre de la lista y, si no, su moneda."""
    nom_up = str(nombre or "").upper()
    has_ves = "VES" in nom_up
    has_usd = "USD" in nom_up
    if has_ves and not has_usd:
        return RUTA_BCV
    if has_usd and not has_ves:
        return RUTA_BINANCE
    mon_low = str(moneda or "").strip().lower()
    if mon_low == "ves":
        return RUTA_BCV
    if mon_low == "usd":
        return RUTA_BINANCE
    if has_ves:
        return RUTA_BCV
    if has_usd:
        return RUTA_BINANCE
    return ""


def _nombre_de_mapeo(entrada: Any) -> str:
    if isinstance(entrada, Mapping):
        return str(entrada.get("nombre") or entrada.get("name") or "").strip()
    return str(entrada or "").strip()


def ruta_de_lista(
    lista_id: Any,
    mapeo: Mapping[str, Any] | None = None,
    *,
    lista_nombre: str = "",
    moneda_lista: str = "",
    es_historica: bool = False,
    pricelist_names: Mapping[str, str] | None = None,
) -> str:
    """Resuelve la ruta de pago autorizada (``'bcv'`` | ``'binance'``) para una orden/lista."""
    if es_historica:
        return RUTA_BCV

    lid = str(lista_id or "").strip()
    info = (mapeo or {}).get(lid) if lid else None

    if isinstance(info, Mapping):
        ruta_cfg = normalizar_ruta(info.get("ruta"))
        if ruta_cfg in RUTAS_VALIDAS:
            return ruta_cfg

    nom_eff = (
        str(lista_nombre or "").strip()
        or _nombre_de_mapeo(info)
        or str((pricelist_names or {}).get(lid) or "").strip()
    )
    mon_mapeo = str(info.get("moneda") or "").strip() if isinstance(info, Mapping) else ""

    por_nombre = inferir_ruta_por_nombre(nom_eff, "")
    if por_nombre in RUTAS_VALIDAS:
        return por_nombre

    if mon_mapeo:
        r_mon = normalizar_ruta(mon_mapeo)
        if r_mon in RUTAS_VALIDAS:
            return r_mon

    if lid.upper() == "USD":
        return RUTA_BINANCE
    if lid.upper() == "VES":
        return RUTA_BCV

    if moneda_lista:
        r_arg = normalizar_ruta(moneda_lista)
        if r_arg in RUTAS_VALIDAS:
            return r_arg

    return RUTA_BCV


def etiqueta_ruta(ruta: str) -> str:
    """Etiqueta legible para UI y respuestas API."""
    return "Ruta Binance (USD)" if normalizar_ruta(ruta) == RUTA_BINANCE else "Ruta BCV (VES)"


def listas_por_ruta(
    mapeo: Mapping[str, Any] | None,
    pricelist_names: Mapping[str, str] | None = None,
) -> tuple[set[str], set[str]]:
    """Devuelve ``(ids_bcv, ids_binance)`` combinando mapeo configurado y nombres."""
    ids_bcv: set[str] = set()
    ids_binance: set[str] = set()

    claves: set[str] = set()
    if mapeo:
        claves.update(str(k).strip() for k in mapeo if str(k).strip())
    if pricelist_names:
        claves.update(str(k).strip() for k in pricelist_names if str(k).strip())

    for pid in claves:
        info = (mapeo or {}).get(pid)
        nom = _nombre_de_mapeo(info) or str((pricelist_names or {}).get(pid) or "").strip()
        mon = str(info.get("moneda") or "").strip() if isinstance(info, Mapping) else ""
        ruta_exp = normalizar_ruta(info.get("ruta")) if isinstance(info, Mapping) else ""
        # Solo clasifica entradas que tengan ruta/nombre/moneda identificables.
        if not (ruta_exp or inferir_ruta_por_nombre(nom, mon)):
            continue
        ruta = ruta_de_lista(
            pid,
            mapeo,
            lista_nombre=nom,
            moneda_lista=mon,
            pricelist_names=pricelist_names,
        )
        if ruta == RUTA_BINANCE:
            ids_binance.add(pid)
        else:
            ids_bcv.add(pid)

    return ids_bcv, ids_binance


def validar_compatibilidad_ruta_pago(
    ruta_orden: str,
    *,
    ruta_pago_declarada: str | None = None,
    metodo_pago_nombre: str = "",
    tipo_tasa_metodo: str | None = None,
) -> tuple[bool, str]:
    """Valida si un pago es compatible con la ruta de la orden.

    Un pago con ruta explícita o perteneciente a un diario exclusivo de Binance
    o BCV solo puede aplicarse a órdenes de esa misma ruta. Pagos genéricos en
    efectivo USD, Zelle o diarios bancarios VES se adaptan a la tasa de la
    orden destino y son compatibles.
    """
    del tipo_tasa_metodo
    r_ord = normalizar_ruta(ruta_orden) or RUTA_BCV

    r_pago = normalizar_ruta(ruta_pago_declarada)
    if not r_pago:
        met_low = str(metodo_pago_nombre or "").strip().lower()
        if "binance" in met_low:
            r_pago = RUTA_BINANCE
        elif "bcv" in met_low:
            r_pago = RUTA_BCV

    if not r_pago:
        return True, ""

    if r_pago != r_ord:
        if r_ord == RUTA_BINANCE:
            return (
                False,
                "Orden nacida en lista USD (Ruta Binance): no puede pagarse ni cerrarse "
                "por Ruta BCV.",
            )
        return (
            False,
            "Orden nacida en lista VES (Ruta BCV): no puede pagarse ni cerrarse "
            "por Ruta Binance.",
        )

    return True, ""
