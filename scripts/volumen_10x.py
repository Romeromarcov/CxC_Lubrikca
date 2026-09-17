#!/usr/bin/env python
"""Multiplica el espejo local por N y cronometra los reportes contra el.

Cierra el unico item de la Fase 4 que en la primera pasada se extrapolo en vez de
ejecutarse. El plan preguntaba:

    "Diez veces las ordenes y los pagos actuales. Hoy el reporte tarda unos diez
    minutos con los caches frios; con diez veces los datos hay que ver si
    termina."

**La clave, que la primera pasada no vio:** los reportes leen del ESPEJO LOCAL.
Multiplicar el espejo no requiere tocar Odoo. Escribir 9.000 ordenes en Odoo por
XML-RPC son mas de una hora contra un servidor compartido y deja el entorno
inservible; clonar la base y multiplicar filas tarda dos segundos.

Odoo va desconectado a proposito, por dos motivos que se refuerzan: asi se mide el
computo LOCAL, que es lo que crece con el volumen, y de paso se ejercita el modo
degradado. Los ~10 minutos con caches frios del enunciado son ida y vuelta a Odoo,
no computo -- eso quedo medido y es el hallazgo principal.

Los clientes NO se multiplican: en el negocio crecen las ordenes y los pagos, no la
cartera, y mantener los mismos deja N veces mas filas por cliente, que es el caso
pesado para la agrupacion del reporte por cliente.

**No toca la base de origen.** Clona a una base aparte (``--destino``) y trabaja
ahi. La de origen queda intacta.

Uso:
    python scripts/volumen_10x.py --origen cxc_qa --factor 10
    python scripts/volumen_10x.py --origen cxc_qa --factor 10 --dejar-la-base
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

# (tabla, columnas de id a sufijar). El orden respeta las claves foraneas.
PLAN_DE_COPIA: list[tuple[str, list[str]]] = [
    ("ordenes_venta", ["so_id"]),
    ("lineas_orden", ["linea_id", "so_id"]),
    ("pagos", ["pago_id"]),
    ("vinculaciones", ["vinc_id", "pago_id", "so_id"]),
    ("facturas", ["factura_id", "so_id"]),
    ("entregas", ["entrega_id", "so_id"]),
    ("ventas_teoricos", ["so_id"]),
    ("bandeja_facturacion", ["so_id"]),
]

TABLAS_A_CONTAR = [
    "clientes",
    "ordenes_venta",
    "lineas_orden",
    "pagos",
    "vinculaciones",
    "facturas",
    "entregas",
    "ventas_teoricos",
    "bandeja_facturacion",
]

ENDPOINTS = [
    ("Ventas", "/api/ventas"),
    ("Reporte de saldos", "/api/reporte-saldos"),
    ("Reporte por cliente", "/api/reporte-cxc-cliente"),
    ("Bandeja", "/api/bandeja"),
    ("Balance de comprobacion", "/api/auditoria/balance-comprobacion"),
]

SUFIJO = "-V"


def _url(base: str) -> str:
    return f"postgresql+psycopg://cxc:cxc_ci_pw@localhost:5432/{base}"


def clonar(origen: str, destino: str) -> None:
    import sqlalchemy as sa

    motor = sa.create_engine(_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with motor.connect() as con:
            con.execute(sa.text(f'DROP DATABASE IF EXISTS "{destino}"'))
            con.execute(sa.text(f'CREATE DATABASE "{destino}" TEMPLATE "{origen}"'))
    finally:
        motor.dispose()
    print(f"  {destino} creada como copia de {origen}")


def multiplicar(destino: str, factor: int) -> None:
    import sqlalchemy as sa

    motor = sa.create_engine(_url(destino))
    with motor.connect() as con:  # noqa: SIM117
        columnas: dict[str, list[str]] = {}
        for tabla, _ in PLAN_DE_COPIA:
            columnas[tabla] = [
                r[0]
                for r in con.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :t ORDER BY ordinal_position"
                    ),
                    {"t": tabla},
                )
            ]
        for k in range(1, factor):
            t0 = time.time()
            for tabla, ids in PLAN_DE_COPIA:
                cols = columnas[tabla]
                faltan = [i for i in ids if i not in cols]
                if faltan:
                    print(f"    OJO: {tabla} no tiene {faltan}, se salta")
                    continue
                seleccion = ", ".join(
                    (f"({c} || '{SUFIJO}{k}')" if c in ids else c) for c in cols
                )
                # ``NOT LIKE '%-V%'`` acota el origen al bloque BASE, para que las
                # copias no se copien entre si y el factor sea exacto.
                con.execute(
                    sa.text(
                        f"INSERT INTO {tabla} ({', '.join(cols)}) SELECT {seleccion} "
                        f"FROM {tabla} WHERE {ids[0]} NOT LIKE '%{SUFIJO}%'"
                    )
                )
            con.commit()
            print(f"  copia {k} de {factor - 1} en {time.time() - t0:.1f}s")
    motor.dispose()


def contar(base: str) -> dict[str, int]:
    import sqlalchemy as sa

    motor = sa.create_engine(_url(base))
    try:
        with motor.connect() as con:
            return {
                t: int(con.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar() or 0)
                for t in TABLAS_A_CONTAR
            }
    finally:
        # Sin esto el pool queda abierto y Postgres se niega a clonar la base:
        # "source database is being accessed by other users".
        motor.dispose()


def cronometrar(base: str) -> dict[str, tuple[float, str]]:
    """Corre los cinco endpoints contra ``base`` con Odoo desconectado."""
    from unittest.mock import patch

    os.environ["DATABASE_URL"] = f"postgresql://cxc:cxc_ci_pw@localhost:5432/{base}"
    os.environ.setdefault("SESSION_SECRET", "x" * 40)
    for clave in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY"):
        os.environ.setdefault(clave, "sin-odoo-a-proposito")

    from fastapi.testclient import TestClient

    from cxc.web.app import app

    async def _nada() -> None:
        return None

    salida: dict[str, tuple[float, str]] = {}
    # Los dos demonios de arranque quedan apagados. El del scraper no solo mete
    # ruido en el cronometro: ESCRIBE en ``serie_tasas`` de la base que estamos
    # midiendo, y una herramienta de medicion no debe mutar lo que mide.
    with (
        patch("cxc.web.app.hay_sesion_valida", return_value=True),
        patch("cxc.web.app._connect", return_value=None),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app.run_sync_in_background", _nada),
        TestClient(app) as cliente,
    ):
        for nombre, ruta in ENDPOINTS:
            t0 = time.time()
            try:
                r = cliente.get(ruta)
                dt = time.time() - t0
                cuerpo = r.json() if r.status_code == 200 else {}
                nota = f"HTTP {r.status_code}"
                if isinstance(cuerpo, dict):
                    filas = (
                        cuerpo.get("items")
                        or cuerpo.get("clientes")
                        or cuerpo.get("partidas")
                        or cuerpo.get("ordenes_por_facturar")
                        or []
                    )
                    nota += f", {len(filas)} filas"
                    if cuerpo.get("descuadres"):
                        nota += f", {cuerpo['descuadres']} DESCUADRE(S)"
                    if cuerpo.get("calculando"):
                        nota += ", calculando"
                    if cuerpo.get("evaluable") is False:
                        nota += ", se abstuvo"
                elif isinstance(cuerpo, list):
                    nota += f", {len(cuerpo)} filas"
                salida[nombre] = (dt, nota)
            except Exception as exc:  # noqa: BLE001 -- se reporta, no se traga
                salida[nombre] = (
                    time.time() - t0,
                    f"EXCEPCION {type(exc).__name__}: {str(exc)[:100]}",
                )
            d, n = salida[nombre]
            print(f"  {nombre:<26} {d:>8.1f}s  {n}", flush=True)
    return salida


def cronometrar_aparte(base: str) -> None:
    """Cronometra ``base`` en un proceso nuevo.

    Dos razones, las dos aprendidas rompiendo esto:

    1. La app cachea el motor de base y los reportes **por proceso**, asi que
       medir las dos escalas en el mismo proceso daria el 1x cacheado.
    2. Postgres se niega a clonar una base que tenga sesiones abiertas
       (``source database is being accessed by other users``), y el pool de la
       app queda abierto. Un proceso que termina las cierra.
    """
    import subprocess

    # El hijo escribe directo y los print del padre quedan en buffer, asi que
    # sin este flush el informe sale desordenado -- los tiempos aparecen antes
    # que el encabezado que los explica.
    sys.stdout.flush()
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--solo-cronometrar", base],
        check=False,
    )
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--origen", default="cxc_qa", help="base espejo a clonar")
    ap.add_argument("--destino", default="cxc_vol", help="base de trabajo (se recrea)")
    ap.add_argument("--factor", type=int, default=10)
    ap.add_argument(
        "--dejar-la-base",
        action="store_true",
        help="no borra la base de trabajo al terminar (para inspeccionarla)",
    )
    ap.add_argument(
        "--solo-cronometrar",
        metavar="BASE",
        help="uso interno: cronometra los reportes contra BASE y sale. Existe "
        "porque la app cachea el motor de base y los reportes por proceso, asi "
        "que la medicion a Nx tiene que correr en un proceso nuevo.",
    )
    args = ap.parse_args()

    if args.solo_cronometrar:
        cronometrar(args.solo_cronometrar)
        return 0

    if args.factor < 2:
        sys.exit("El factor tiene que ser 2 o mas.")
    if args.destino == args.origen:
        sys.exit("El destino no puede ser el origen: este script multiplica filas.")

    print("=" * 78)
    print(f"VOLUMEN {args.factor}x  --  {args.origen} -> {args.destino}")
    print("=" * 78)

    antes = contar(args.origen)
    print()
    print(f"1. LOS REPORTES A 1x (sobre {args.origen}, Odoo desconectado)")
    cronometrar_aparte(args.origen)

    print()
    print(f"2. MULTIPLICANDO POR {args.factor}")
    clonar(args.origen, args.destino)
    multiplicar(args.destino, args.factor)
    despues = contar(args.destino)
    print()
    print(f"  {'tabla':<24} {'1x':>9} {args.factor}x")
    for t in TABLAS_A_CONTAR:
        print(f"  {t:<24} {antes[t]:>9,} {despues[t]:>9,}")

    print()
    print(f"3. LOS REPORTES A {args.factor}x")
    cronometrar_aparte(args.destino)

    if not args.dejar_la_base:
        import sqlalchemy as sa

        motor = sa.create_engine(_url("postgres"), isolation_level="AUTOCOMMIT")
        with motor.connect() as con:
            con.execute(sa.text(f'DROP DATABASE IF EXISTS "{args.destino}"'))
        print()
        print(f"  {args.destino} borrada. Con --dejar-la-base queda para inspeccionar.")

    print()
    print("Los tiempos de 1x estan arriba para comparar. Lo que importa no es el")
    print("numero absoluto sino el FACTOR: si crece lineal, el reporte escala.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
