#!/usr/bin/env python
"""Corrige Vinculaciones en VES creadas por el daemon con el equivalente en
USD escrito en el campo del monto nativo (bug real, 27-sep-2026, reportado
por el usuario a partir de una captura de pantalla del Panel de Cobranza:
"no entiendo por qué el sistema repartió el pago, si este cliente solo
tiene una orden de venta").

Causa real: ``_get_conciliaciones_sugerencias_sync`` calcula
``monto_sugerido`` en USD (el motor FIFO trabaja en ``restante``/
``saldo_usd``), y tanto el daemon (``_auto_vincular_fifo_pendientes``) como
el botón "✓ Vincular" del Panel de Cobranza y el modal "Vincular
manualmente" lo mandaban tal cual a ``/api/vincular``/``_vincular_masivo_
sync`` como ``monto_aplicado`` -- un campo que ``_congelar_equivalentes``
exige en la moneda PROPIA del pago. Para un pago en VES, eso metía el
equivalente en dólares donde iba el monto en bolívares: un pago real de
$343 quedaba "aplicado" como Bs. 342,83, y su equivalente USD -- al
dividirse por la tasa OTRA VEZ -- se hundía a $0,46. Arreglado en el código
(ver "monto_sugerido_nativo" en ``_get_conciliaciones_sugerencias_sync``);
este script corrige lo que ya quedó mal escrito.

Alcance medido en producción: 712 Vinculaciones en VES en total, 144 con
esta firma (``confirmado_por="Auto-FIFO (daemon)"``, estado PENDIENTE,
``pago.monto / monto_aplicado >= 100`` -- ninguna Vinculación legítima de
un solo pago a una sola orden debería tener un monto aplicado 100 veces
menor al pago completo). Se excluyen deliberadamente las de
``confirmado_por="Odoo (reconciliación)"``: esas SÍ pueden tener ratios
enormes quando Odoo reparte un pago grande entre muchas facturas, y su
monto es la fuente autoritativa -- nunca se tocan (ver el docstring de
``_sincronizar_aplicaciones_conciliadas`` en web/app.py). Todas las 144
siguen en PENDIENTE (nunca llegaron a confirmarse en Odoo), así que la
corrección no toca ningún dato ya conciliado.

Corrección: ``monto_aplicado`` (VES) = valor actual (que en realidad es el
equivalente USD) × ``tasa_bcv_aplicada`` -- deshace la división de más.
``equiv_usd_bcv``/``equiv_usd_binance``/``equiv_ves_bcv``/``equiv_ves_
binance`` se recalculan desde cero con ``_congelar_equivalentes`` usando la
tasa ya congelada (nunca se busca una tasa nueva).

Idempotente: correrlo dos veces con la Vinculación ya corregida no hace
nada (el ratio ya no cumple el umbral).

Uso:
    python scripts/corregir_vinculaciones_monto_usd_en_ves.py --env .env.qa --ver
    python scripts/corregir_vinculaciones_monto_usd_en_ves.py --env .env.qa --aplicar
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

RATIO_MINIMO_SOSPECHOSO = Decimal("100")
CONFIRMADO_POR_ELEGIBLE = "Auto-FIFO (daemon)"


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
    from cxc.models import Moneda
    from cxc.web.app import _congelar_equivalentes

    repo = PostgresRepository.from_url(os.environ["DATABASE_URL"])
    pagos_by_id = {str(p.get("pago_id", "")).strip(): p for p in repo.all_pagos_full()}

    candidatas = []
    for v in repo.all_vinculaciones():
        if v.moneda_abono != Moneda.VES or v.confirmado_por != CONFIRMADO_POR_ELEGIBLE:
            continue
        pago = pagos_by_id.get(v.pago_id)
        if not pago:
            print(f"AVISO: {v.vinc_id} -- pago {v.pago_id} no está en el espejo, se omite.")
            continue
        monto_pago_raw = Decimal(str(pago.get("monto", "0")))
        if monto_pago_raw <= 0 or v.monto_aplicado <= 0:
            continue
        ratio = monto_pago_raw / v.monto_aplicado
        if ratio >= RATIO_MINIMO_SOSPECHOSO:
            candidatas.append((v, ratio))

    print(f"Candidatas encontradas: {len(candidatas)}")
    if not candidatas:
        print("Nada que corregir.")
        return 0

    a_escribir = []
    for v, ratio in candidatas:
        monto_correcto = (v.monto_aplicado * v.tasa_bcv_aplicada).quantize(Decimal("0.0001"))
        try:
            equiv_usd_bcv, equiv_usd_binance, equiv_ves_bcv, equiv_ves_binance = (
                _congelar_equivalentes(
                    monto_correcto,
                    Moneda.VES,
                    v.tasa_bcv_aplicada,
                    v.tasa_binance_aplicada,
                )
            )
        except ValueError as e:
            print(f"ERROR: {v.vinc_id} -- no se pudo recalcular ({e}), se omite.")
            continue

        print(
            f"{v.vinc_id} (pago {v.pago_id}, {v.estado.value}, ratio={ratio:.1f}): "
            f"monto_aplicado {v.monto_aplicado} -> {monto_correcto}  "
            f"equiv_usd_bcv {v.equiv_usd_bcv} -> {equiv_usd_bcv}"
        )
        a_escribir.append(
            replace(
                v,
                monto_aplicado=monto_correcto,
                equiv_usd_bcv=equiv_usd_bcv,
                equiv_usd_binance=equiv_usd_binance,
                equiv_ves_bcv=equiv_ves_bcv,
                equiv_ves_binance=equiv_ves_binance,
            )
        )

    total_usd_antes = sum((v.equiv_usd_bcv for v, _ in candidatas), Decimal("0"))
    total_usd_despues = sum((nv.equiv_usd_bcv for nv in a_escribir), Decimal("0"))
    print(
        f"\nTotal equiv_usd_bcv sumado: antes=${total_usd_antes:.2f} -> "
        f"después=${total_usd_despues:.2f} (dinero que estas Vinculaciones "
        "pasan a reflejar correctamente, no dinero nuevo)"
    )

    if args.ver:
        print("\n(--ver: no se escribió nada)")
        return 0

    for v in a_escribir:
        repo.update_vinculacion(v)

    despues = {v.vinc_id: v for v in repo.all_vinculaciones()}
    ok = True
    for v in a_escribir:
        actual = despues[v.vinc_id]
        if actual.monto_aplicado != v.monto_aplicado or actual.equiv_usd_bcv != v.equiv_usd_bcv:
            print(f"\nERROR: {v.vinc_id} no quedó escrita correctamente.")
            ok = False
    if not ok:
        return 1

    print(f"\nCorregidas {len(a_escribir)} Vinculaciones. Verificado en la base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
