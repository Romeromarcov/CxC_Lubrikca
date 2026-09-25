#!/usr/bin/env python
"""Corrige Vinculaciones congeladas con la tasa BCV-Euro en vez de BCV-USD.

Hallazgo real (25-sep-2026, reportado por el usuario sobre la Bandeja de
Auditoría "Tasa Implícita Fuera de Rango"): ``VINC_1735_S00054`` y
``VINC_1372_S00054`` quedaron congeladas con ``tasa_bcv_aplicada`` =
``tasa_bcv_euro`` de su fecha, no la BCV-USD real -- ambas SON tasas BCV
reales publicadas esos días, por eso al revisar en Odoo "la tasa
correspondía al BCV de ese día" (el usuario tenía razón en lo que vio,
solo que era la del Euro). El bug de fondo ya se corrigió el 12-sep-2026
(``resolver_tasa_bcv_vinculacion`` siempre devuelve la variante USD desde
esa fecha -- decisión del usuario en el quiz de esa fecha: "el euro es
solo para auditoría, nunca debe congelar un monto real"), pero estas dos
Vinculaciones se congelaron ANTES de esa fecha (9-sep y 12-ago) y, como
los equivalentes congelados nunca se recalculan solos, quedaron mal para
siempre hasta corregirlas a mano.

Medido contra el espejo completo (684 Vinculaciones en bolívares): SOLO
estas 2 tienen el problema, las dos del mismo cliente/orden (S00054). El
cliente tiene $11,17 de menos acreditados de lo que pagó realmente
(dividir entre una tasa más alta -- la del Euro -- da menos dólares).

Solo toca ``tasa_bcv_aplicada`` y ``equiv_usd_bcv`` -- ``equiv_ves_bcv``
no depende de la tasa (es el monto en bolívares tal cual, ver
``equivalentes_bcv``) y ``tasa_binance_aplicada``/``equiv_usd_binance``
ya estaban correctos (confirmado contra ``tasas_historicas_auditoria``).

Idempotente: correrlo dos veces con la tasa ya corregida no hace nada
(lo reporta y sale).

Uso:
    python scripts/corregir_vinculaciones_tasa_euro.py --env .env.qa --ver
    python scripts/corregir_vinculaciones_tasa_euro.py --env .env.qa --aplicar
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

VINC_IDS = ["VINC_1735_S00054", "VINC_1372_S00054"]


def _cargar_env(ruta: str) -> None:
    """Carga ``ruta`` en el entorno -- sin pisar una variable YA presente

    (ej. ``DATABASE_URL`` que ``railway run`` inyecta para apuntar a
    producción). El archivo rellena lo que falte, nunca reemplaza lo que
    el proceso llamador ya decidió.
    """
    p = Path(ruta)
    if not p.is_absolute():
        p = RAIZ / ruta
    if not p.exists():
        sys.exit(f"No existe {p}")
    for linea in p.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, _, valor = linea.partition("=")
            os.environ.setdefault(clave.strip(), valor.strip())


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
    from cxc.web.app import _all_serie_tasas_rows, get_rate_for_datetime

    repo = PostgresRepository.from_url(os.environ["DATABASE_URL"])
    tasas_rows = _all_serie_tasas_rows(repo)
    existentes = {v.vinc_id: v for v in repo.all_vinculaciones()}

    a_escribir = []
    for vinc_id in VINC_IDS:
        v = existentes.get(vinc_id)
        if v is None:
            print(f"ERROR: {vinc_id} no existe en esta base.")
            return 1

        tasa_usd_real, _tasa_binance = get_rate_for_datetime(v.hora_pago_confirmada, tasas_rows)
        print(
            f"{vinc_id}: fecha={v.hora_pago_confirmada.date()} "
            f"tasa_bcv_aplicada hoy={v.tasa_bcv_aplicada} tasa_bcv_usd_real={tasa_usd_real}"
        )
        diff_pct = abs(v.tasa_bcv_aplicada - tasa_usd_real) / tasa_usd_real
        if diff_pct <= Decimal("0.01"):
            print("  Ya coincide con la BCV-USD real -- nada que hacer.")
            continue

        equiv_usd_bcv_correcto = (v.monto_aplicado / tasa_usd_real).quantize(Decimal("0.000001"))
        print(
            f"  Se va a escribir: tasa_bcv_aplicada={tasa_usd_real} "
            f"equiv_usd_bcv={equiv_usd_bcv_correcto} (antes {v.equiv_usd_bcv})"
        )
        print("  equiv_ves_bcv, tasa_binance_aplicada y equiv_usd_binance no cambian.")
        a_escribir.append(
            replace(v, tasa_bcv_aplicada=tasa_usd_real, equiv_usd_bcv=equiv_usd_bcv_correcto)
        )

    if not a_escribir:
        print("\nNada que corregir.")
        return 0

    if args.ver:
        print("\n(--ver: no se escribió nada)")
        return 0

    for v in a_escribir:
        repo.update_vinculacion(v)

    despues = {v.vinc_id: v for v in repo.all_vinculaciones()}
    ok = True
    for v in a_escribir:
        actual = despues[v.vinc_id]
        tasa_ok = actual.tasa_bcv_aplicada == v.tasa_bcv_aplicada
        equiv_ok = actual.equiv_usd_bcv == v.equiv_usd_bcv
        if not (tasa_ok and equiv_ok):
            print(f"\nERROR: {v.vinc_id} no quedó escrita correctamente.")
            ok = False
    if not ok:
        return 1

    print(f"\nEscritas {len(a_escribir)} Vinculaciones. Verificado en la base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
