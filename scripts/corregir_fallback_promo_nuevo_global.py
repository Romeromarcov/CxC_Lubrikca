#!/usr/bin/env python
"""Corrige el ``descuento_fallback`` de ``PROMO_NUEVO_GLOBAL``: 0,02 -> 0.

Hallazgo real (22-sep-2026, reportado por el usuario sobre la orden S01046):
la promoción "Recurrente" ``PROMO_NUEVO_GLOBAL`` (tipo "producto", el 12+1 de
GLOBAL OIL/CAJA) tenía ``descuento_fallback=0,02`` cargado. Ese campo es el
respaldo porcentual que ``_evaluar_promociones_producto`` usa SOLO cuando la
orden no alcanza la compra mínima del obsequio -- y, según el propio
docstring de esa función, las reglas reales de producción deben tener ese
campo en 0 ("si no alcanza la compra mínima, no hay nada"). Su hermana,
``PROMO_12_MAS_1`` (mismo marca/categoría/vigencia), sí lo tiene en 0.

Con el campo en 0,02, la regla le prestaba un 2 % a CUALQUIER orden de
GLOBAL OIL/CAJA que no llegara a la compra mínima del obsequio -- sin
importar si era la primera compra del cliente o la número 18 (S01046 lo
era). El código que arma la etiqueta también decía "Descuento primera
compra" sin importar el origen real -- eso se corrigió aparte en
``engine/discounts.py`` (``_evaluar_promociones_producto``); este script
corrige el dato.

No cambia ningún otro campo de la regla. Idempotente: correrlo dos veces con
el valor ya en 0 no hace nada (lo reporta y sale).

Uso:
    python scripts/corregir_fallback_promo_nuevo_global.py --env .env.qa --ver
    python scripts/corregir_fallback_promo_nuevo_global.py --env .env.qa --aplicar
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

REGLA_ID = "PROMO_NUEVO_GLOBAL"


def _cargar_env(ruta: str) -> None:
    p = Path(ruta)
    if not p.is_absolute():
        p = RAIZ / ruta
    if not p.exists():
        sys.exit(f"No existe {p}")
    for linea in p.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, _, valor = linea.partition("=")
            os.environ[clave.strip()] = valor.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default=".env")
    ap.add_argument("--aplicar", action="store_true", help="escribe la correccion")
    ap.add_argument("--ver", action="store_true", help="solo muestra que haria")
    args = ap.parse_args()
    if not (args.aplicar or args.ver):
        ap.error("elegi --ver o --aplicar")
    _cargar_env(args.env)

    from cxc.db.postgres_repository import PostgresRepository

    repo = PostgresRepository.from_url(os.environ["DATABASE_URL"])
    existentes = {p.regla_id: p for p in repo.promociones_primera_compra()}
    if REGLA_ID not in existentes:
        print(f"ERROR: {REGLA_ID} no existe en esta base.")
        return 1

    actual = existentes[REGLA_ID]
    print(f"{REGLA_ID} hoy: descuento_fallback={actual.descuento_fallback}")
    if actual.descuento_fallback == Decimal("0"):
        print("Ya está en 0 -- nada que hacer.")
        return 0

    corregida = replace(actual, descuento_fallback=Decimal("0"))
    print(f"Se va a escribir: descuento_fallback={corregida.descuento_fallback}")
    print("Ningún otro campo cambia.")

    if args.ver:
        print("\n(--ver: no se escribió nada)")
        return 0

    repo.append_promocion_primera_compra(corregida)
    despues = {p.regla_id: p for p in repo.promociones_primera_compra()}
    g = despues[REGLA_ID]
    if g.descuento_fallback != Decimal("0"):
        print("\nERROR: la corrección no quedó escrita.")
        return 1
    print(f"\nEscrita. Verificado en la base: descuento_fallback={g.descuento_fallback}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
