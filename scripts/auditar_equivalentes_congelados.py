#!/usr/bin/env python
"""Que vinculaciones congelaron una tasa que despues cambio.

Cierra un item de la Fase 6 del plan de blindaje, citado textual:

    "Si se corrige una tasa vieja, las vinculaciones ya aplicadas conservan el
    equivalente que congelaron. Es correcto como diseno contable, pero **hoy no
    hay nada que liste cuales quedaron con una tasa que despues se corrigio**."

Esto es esa lista. No corrige nada -- congelar el equivalente es la decision
contable correcta y descongelarlo es una decision del usuario. Lo que hace es
convertir "no hay nada que lo liste" en "esta listado, con su monto".

**La regla que este script se aplica a si mismo.** Dos partidas del balance
reportaban "cero divergencias" sin haber comparado un solo documento, porque
salteaban en silencio los que no tenian tasa nuestra para esa fecha (ver
``docs/blindaje/1.2-1.5-auditoria.md``). Aca cada seccion dice **cuantas
comparo** antes de decir cuantas difieren, y si no comparo ninguna lo dice con
todas las letras en vez de mostrar un cero tranquilizador.

Tres cosas se reportan, y son distintas:

1. **Congeladas con el default de 2019** (36,50 / 38,00). No es una correccion
   posterior: es que nunca hubo tasa. Es el caso mas grave porque el numero
   quedo escrito y por diseno no se revisa.
2. **Congeladas con una tasa que hoy es otra.** La correccion posterior
   propiamente dicha: habia tasa, se congelo, y despues cambio.
3. **Sin tasa para comparar.** Ni serie ni auditoria tienen dato para esa
   fecha, asi que no se puede afirmar nada. Se cuentan aparte y NO entran a
   "cuadran".

Uso:
    python scripts/auditar_equivalentes_congelados.py --env .env.qa
    python scripts/auditar_equivalentes_congelados.py --env .env.qa --detalle
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

# El ultimo recurso de ``get_rate_for_datetime``: las tasas de 2019.
DEFAULT_2019_BCV = Decimal("36.50")
DEFAULT_2019_BINANCE = Decimal("38.00")

# Cuanto puede diferir una tasa congelada de la vigente y seguir contando como
# la misma. Las tasas se guardan con 2 decimales en varios lugares, asi que un
# centavo es redondeo y no una correccion.
TOLERANCIA_TASA = Decimal("0.01")


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
    ap.add_argument("--detalle", action="store_true", help="lista cada vinculacion")
    ap.add_argument(
        "--tope", type=int, default=15, help="cuantas filas listar por seccion (con --detalle)"
    )
    args = ap.parse_args()
    _cargar_env(args.env)

    import sqlalchemy as sa

    motor = sa.create_engine(
        os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://")
    )

    with motor.connect() as con:
        # Las tasas que hoy conocemos, por fecha. La serie manda; la auditoria
        # historica completa lo que la serie no tiene -- mismo orden de
        # prioridad que ``get_rate_for_datetime``.
        tasas: dict[str, tuple[Decimal | None, Decimal | None, str]] = {}
        for fila in con.execute(
            sa.text(
                "SELECT fecha::text AS f, tasa_bcv_usd, tasa_binance_promedio_diario "
                "FROM tasas_historicas_auditoria"
            )
        ).mappings():
            tasas[fila["f"]] = (
                fila["tasa_bcv_usd"],
                fila["tasa_binance_promedio_diario"],
                "auditoria",
            )
        for fila in con.execute(
            sa.text(
                "SELECT (timestamp AT TIME ZONE 'UTC')::date::text AS f, "
                "       tasa_bcv, tasa_binance "
                "FROM serie_tasas ORDER BY timestamp"
            )
        ).mappings():
            tasas[fila["f"]] = (fila["tasa_bcv"], fila["tasa_binance"], "serie")

        vincs = con.execute(
            sa.text(
                """
                SELECT v.vinc_id, v.pago_id, v.so_id, v.monto_aplicado,
                       v.hora_pago_confirmada::date::text AS fecha,
                       v.tasa_bcv_aplicada, v.tasa_binance_aplicada,
                       v.es_tasa_heredada, v.equiv_usd_bcv, v.equiv_usd_binance,
                       v.moneda_abono, v.estado
                FROM vinculaciones v
                ORDER BY v.hora_pago_confirmada
                """
            )
        ).mappings().all()

    con_default: list[dict] = []
    corregidas: list[dict] = []
    sin_tasa: list[dict] = []
    coinciden = 0

    for v in vincs:
        bcv_congelada = v["tasa_bcv_aplicada"]
        es_default = (
            bcv_congelada is not None
            and abs(Decimal(bcv_congelada) - DEFAULT_2019_BCV) <= TOLERANCIA_TASA
            and v["tasa_binance_aplicada"] is not None
            and abs(Decimal(v["tasa_binance_aplicada"]) - DEFAULT_2019_BINANCE) <= TOLERANCIA_TASA
        )
        vigente = tasas.get(v["fecha"] or "")
        if es_default:
            # Se reporta aparte incluso si hoy hay tasa: el problema no es la
            # divergencia, es que el numero escrito nunca salio de un dato.
            fila = dict(v)
            fila["bcv_hoy"] = vigente[0] if vigente else None
            con_default.append(fila)
            continue
        if vigente is None or vigente[0] is None:
            sin_tasa.append(dict(v))
            continue
        dif = Decimal(bcv_congelada or 0) - Decimal(vigente[0])
        if abs(dif) <= TOLERANCIA_TASA:
            coinciden += 1
            continue
        fila = dict(v)
        fila["bcv_hoy"] = vigente[0]
        fila["dif_tasa"] = dif
        fila["fuente_hoy"] = vigente[2]
        # Que pasaria con el equivalente si se recongelara a la tasa de hoy.
        if v["moneda_abono"] == "VES" and Decimal(vigente[0]) > 0:
            nominal_ves = Decimal(v["monto_aplicado"]) * Decimal(bcv_congelada)
            fila["equiv_usd_hoy"] = nominal_ves / Decimal(vigente[0])
            fila["dif_usd"] = fila["equiv_usd_hoy"] - Decimal(v["equiv_usd_bcv"] or 0)
        else:
            fila["equiv_usd_hoy"] = None
            fila["dif_usd"] = Decimal(0)
        corregidas.append(fila)

    total = len(vincs)
    print("=" * 78)
    print("EQUIVALENTES CONGELADOS: QUE TASA LOS CONGELO, Y SI ESA TASA SIGUE SIENDO ESA")
    print("=" * 78)
    print(f"  {total} vinculaciones en el espejo")
    print(f"  {len(tasas)} fecha(s) con tasa conocida (serie + auditoria historica)")
    print()

    comparables = coinciden + len(corregidas)
    print(f"[1] Congeladas con el DEFAULT DE 2019 (36,50 / 38,00): {len(con_default)}")
    if con_default:
        suma = sum(Decimal(f["equiv_usd_bcv"] or 0) for f in con_default)
        ves = [f for f in con_default if f["moneda_abono"] == "VES"]
        suma_ves = sum(Decimal(f["monto_aplicado"]) for f in ves)
        print("    No es una correccion posterior: es que nunca hubo tasa. El numero")
        print("    quedo escrito en un campo que por diseno no se revisa.")
        print(f"    equivalente USD acreditado con ese default: {suma:,.2f}")
        if ves:
            print(f"    de los cuales {len(ves)} son abonos en VES por {suma_ves:,.2f} Bs")
            recuperables = [f for f in ves if f["bcv_hoy"]]
            if recuperables:
                a_hoy = sum(
                    Decimal(f["monto_aplicado"]) * Decimal(f["tasa_bcv_aplicada"])
                    / Decimal(f["bcv_hoy"])
                    for f in recuperables
                )
                congelado = sum(Decimal(f["equiv_usd_bcv"] or 0) for f in recuperables)
                print(
                    f"    de esos, {len(recuperables)} tienen tasa conocida hoy: "
                    f"{congelado:,.2f} USD congelados contra {a_hoy:,.2f} a la tasa de hoy"
                )
            else:
                print(
                    "    NO SE PUDO ESTIMAR EL MONTO CORRECTO DE NINGUNO: no hay tasa "
                    "conocida para ninguna de esas fechas."
                )

    print()
    print(f"[2] Congeladas con una tasa que HOY ES OTRA: {len(corregidas)}")
    if comparables == 0:
        print("    NO SE COMPARO NINGUNA. Sin tasa conocida para ninguna fecha, este")
        print("    cero no significa 'todas bien' -- significa 'no se pudo mirar'.")
    else:
        print(f"    Comparadas {comparables} de {total}.")
        if corregidas:
            suma = sum(f["dif_usd"] for f in corregidas)
            print(f"    diferencia en el equivalente si se recongelaran: {suma:,.2f} USD")

    print()
    print(f"[3] SIN TASA PARA COMPARAR: {len(sin_tasa)}")
    if sin_tasa:
        print("    Ni la serie ni la auditoria historica tienen dato para su fecha.")
        print("    No entran a 'coinciden': no se puede afirmar nada de ellas.")

    print()
    print(f"[4] Coinciden con la tasa de hoy: {coinciden}")

    if args.detalle:
        for titulo, filas in (
            ("CONGELADAS CON EL DEFAULT DE 2019", con_default),
            ("CONGELADAS CON UNA TASA QUE HOY ES OTRA", corregidas),
            ("SIN TASA PARA COMPARAR", sin_tasa),
        ):
            if not filas:
                continue
            print()
            print(f"--- {titulo} ({len(filas)}, se listan {min(args.tope, len(filas))}) ---")
            for f in filas[: args.tope]:
                extra = ""
                if f.get("dif_usd"):
                    extra = f"  dif {f['dif_usd']:+,.2f} USD (hoy {f['bcv_hoy']})"
                print(
                    f"    {f['vinc_id'][:18]:<19} {f['fecha']} pago {f['pago_id']:<6} "
                    f"{f['so_id']:<8} {f['moneda_abono']:<4} "
                    f"{Decimal(f['monto_aplicado']):>12,.2f} @ {f['tasa_bcv_aplicada']}{extra}"
                )

    # Sale distinto de cero si hay algo que mirar, para que sirva como chequeo.
    return 1 if (con_default or corregidas) else 0


if __name__ == "__main__":
    raise SystemExit(main())
