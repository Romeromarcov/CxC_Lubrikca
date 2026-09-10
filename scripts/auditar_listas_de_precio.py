#!/usr/bin/env python
"""Contra que lista de precios se valora el teorico, y cuanto cambia la respuesta.

El teorico es el punto de comparacion de toda la CxC: es lo que dice cuanto
DEBIO facturarse. Si se calcula contra la lista equivocada, no falla nada -- el
numero simplemente sale mal, y sale mal de forma sistematica.

Este script mide tres cosas, cada una porque ya dio un hallazgo:

1. **Que listas ofrece la configuracion, cuales estan activas en Odoo, y
   cuantas de sus reglas de precio siguen vigentes hoy.** Una lista puede
   estar activa y tener TODAS sus reglas vencidas: ``_primer_id_activo``
   (``web/app.py``) filtra por ``active``, no por vigencia, asi que ese caso
   pasa entero.

2. **Que lista elige cada camino de la aplicacion.** De los cuatro sitios que
   arman un ``OdooPriceResolver``, tres pasan por ``_primer_id_activo`` y uno
   no: ``_get_reporte_saldos_sync`` toma ``ves_ids[0]``/``usd_ids[0]`` crudo.
   Si el primer id de la config esta archivado, esa pagina valora con una
   lista distinta a las otras tres.

3. **Cuanta plata hay entre las dos elecciones.** Linea por linea, la misma
   orden valorada con una lista y con la otra.

Sobre el punto 3, dos advertencias que hay que leer antes de citar el numero:

* compara **precios de linea**, no el teorico completo del motor (que aplica
  descuentos, volumen, el fallback de ficha y la logica de moneda). Acota el
  orden de magnitud; no es el monto exacto que muestra la pantalla;
* replica a proposito el paso de ``_precio_fijo_en_lista`` que devuelve
  ``rules[0]`` cuando ninguna regla calza por fecha (``odoo/price.py``, ~174).
  Ese paso es justamente el que hace invisible que una lista este vencida:
  nunca devuelve None, asi que nunca marca ``usa_fallback``.

Uso:
    python scripts/auditar_listas_de_precio.py --env .env.qa
    python scripts/auditar_listas_de_precio.py --env .env.qa --sin-pruebas
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

PREFIJO_DE_PRUEBA = "ZZ BLINDAJE"


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


def _fecha(valor: object) -> dt.date | None:
    if not valor:
        return None
    return dt.datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()


def _precio_como_lo_hace_el_motor(
    reglas: list[tuple[dt.date | None, dt.date | None, Decimal]],
    fecha: dt.date,
) -> Decimal | None:
    """Replica ``_precio_fijo_en_lista``, incluido el paso ``rules[0]``.

    Devuelve None SOLO si no hay ninguna regla -- que es exactamente la unica
    condicion bajo la que el motor marca ``usa_fallback``. Con una regla
    vencida presente, devuelve su precio y no marca nada.
    """
    if not reglas:
        return None
    calzan = [
        (inicio or dt.date.min, precio)
        for inicio, fin, precio in reglas
        if not (inicio and fecha < inicio) and not (fin and fecha > fin)
    ]
    if calzan:
        calzan.sort(key=lambda par: par[0], reverse=True)
        return calzan[0][1]
    return reglas[0][2]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default=".env", help="archivo de entorno a cargar")
    ap.add_argument(
        "--sin-pruebas",
        action="store_true",
        help=f"excluye los clientes '{PREFIJO_DE_PRUEBA}%' que crea el banco de escenarios",
    )
    args = ap.parse_args()
    _cargar_env(args.env)

    import sqlalchemy as sa

    from cxc.config import AppConfig
    from cxc.db.postgres_repository import PostgresRepository
    from cxc.odoo.client import _connect
    from cxc.web.app import _primer_id_activo, get_valid_pricelists_usd_and_ves

    ejecutar = _connect(AppConfig.from_env().odoo)
    if not ejecutar:
        sys.exit("Sin conexion a Odoo.")
    repo = PostgresRepository.from_url(os.environ["DATABASE_URL"])
    hoy = dt.date.today()

    usd, ves = get_valid_pricelists_usd_and_ves(repo)
    ids_usd = [int(x) for x in usd if str(x).isdigit()]
    ids_ves = [int(x) for x in ves if str(x).isdigit()]

    # --- 1. que ofrece la config, que esta activo, que sigue vigente --------
    print("=" * 78)
    print("1. LAS LISTAS QUE OFRECE LA CONFIGURACION")
    print("=" * 78)
    print(f"  valid_pricelists_ves = {ves}")
    print(f"  valid_pricelists_usd = {usd}")
    cabeceras = ejecutar(
        "product.pricelist",
        "search_read",
        [[["id", "in", ids_ves + ids_usd]]],
        {"fields": ["id", "name", "active"], "context": {"active_test": False}},
    )
    por_id = {c["id"]: c for c in cabeceras}
    print()
    print(f"  {'id':>3} {'activa':>6} {'reglas':>6} {'vigentes':>8}  nombre")
    for pid in ids_ves + ids_usd:
        reglas = ejecutar(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "=", pid], ["compute_price", "=", "fixed"]]],
            {"fields": ["date_start", "date_end"]},
        )
        vivas = sum(
            1
            for r in reglas
            if not (_fecha(r["date_start"]) and hoy < _fecha(r["date_start"]))  # type: ignore[operator]
            and not (_fecha(r["date_end"]) and hoy > _fecha(r["date_end"]))  # type: ignore[operator]
        )
        cab = por_id.get(pid, {})
        marca = "  <-- activa y SIN reglas vigentes" if cab.get("active") and not vivas else ""
        print(
            f"  {pid:>3} {str(cab.get('active')):>6} {len(reglas):>6} {vivas:>8}  "
            f"{cab.get('name')}{marca}"
        )

    # --- 2. que elige cada camino ------------------------------------------
    print()
    print("=" * 78)
    print("2. QUE LISTA ELIGE CADA CAMINO DE LA APLICACION")
    print("=" * 78)
    con_guarda_ves = _primer_id_activo(ejecutar, ids_ves) or 5
    con_guarda_usd = _primer_id_activo(ejecutar, ids_usd) or 4
    sin_guarda_ves = ids_ves[0] if ids_ves else 5
    sin_guarda_usd = ids_usd[0] if ids_usd else 4
    print(
        f"  con _primer_id_activo (app.py 2623 / 3573 / 3709):  BCV -> {con_guarda_ves}"
        f"   USD -> {con_guarda_usd}"
    )
    print(
        f"  sin la guarda (_get_reporte_saldos_sync, app.py 4434): BCV -> {sin_guarda_ves}"
        f"   USD -> {sin_guarda_usd}"
    )
    if (sin_guarda_ves, sin_guarda_usd) == (con_guarda_ves, con_guarda_usd):
        print("  COINCIDEN: en esta base la guarda que falta no cambia la eleccion.")
        return 0
    print("  DIFIEREN: el reporte de saldos valora con otra lista que las otras tres paginas.")

    # --- 3. cuanta plata hay entre las dos elecciones -----------------------
    print()
    print("=" * 78)
    print("3. CUANTA PLATA HAY ENTRE LAS DOS ELECCIONES")
    print("=" * 78)
    motor = sa.create_engine(
        os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://")
    )
    filtro = f"and cl.nombre not like '{PREFIJO_DE_PRUEBA}%'" if args.sin_pruebas else ""
    with motor.connect() as con:
        lineas = (
            con.execute(
                sa.text(
                    f"""
                SELECT l.so_id, l.producto, l.cantidad, o.fecha
                FROM lineas_orden l
                JOIN ordenes_venta o ON o.so_id = l.so_id
                JOIN clientes cl ON cl.cliente_id = o.cliente_id
                WHERE l.producto ~ '^[0-9]+$' AND l.cantidad IS NOT NULL
                  AND o.estado_orden NOT IN ('draft','sent','cancel','cancelled')
                  {filtro}
                """
                )
            )
            .mappings()
            .all()
        )
    productos = sorted({int(f["producto"]) for f in lineas})
    print(f"  {len(lineas)} lineas, {len(productos)} productos distintos")

    crudas = ejecutar(
        "product.pricelist.item",
        "search_read",
        [[["product_tmpl_id", "in", productos], ["compute_price", "=", "fixed"]]],
        {"fields": ["pricelist_id", "product_tmpl_id", "fixed_price", "date_start", "date_end"]},
    )
    por_par: dict[tuple[int, int], list[tuple[dt.date | None, dt.date | None, Decimal]]] = {}
    for r in crudas:
        clave = (r["pricelist_id"][0], r["product_tmpl_id"][0])
        por_par.setdefault(clave, []).append(
            (_fecha(r["date_start"]), _fecha(r["date_end"]), Decimal(str(r["fixed_price"] or 0)))
        )

    salida = 0
    for moneda, sin_g, con_g in (
        ("VES", sin_guarda_ves, con_guarda_ves),
        ("USD", sin_guarda_usd, con_guarda_usd),
    ):
        total_sin = total_con = Decimal(0)
        comparadas = incomparables = 0
        por_orden: dict[str, Decimal] = {}
        for f in lineas:
            prod = int(f["producto"])
            cantidad = Decimal(str(f["cantidad"]))
            fecha = f["fecha"]
            fecha = fecha.date() if hasattr(fecha, "date") else fecha
            p_sin = _precio_como_lo_hace_el_motor(por_par.get((sin_g, prod), []), fecha)
            p_con = _precio_como_lo_hace_el_motor(por_par.get((con_g, prod), []), fecha)
            if p_sin is None or p_con is None:
                incomparables += 1
                continue
            comparadas += 1
            total_sin += cantidad * p_sin
            total_con += cantidad * p_con
            if p_sin != p_con:
                por_orden[f["so_id"]] = por_orden.get(f["so_id"], Decimal(0)) + cantidad * (
                    p_sin - p_con
                )
        print()
        print(f"  --- {moneda}: lista {sin_g} (reporte de saldos) vs {con_g} (las otras tres) ---")
        if not comparadas:
            print("  NO SE COMPARO NINGUNA LINEA: sin reglas en una de las dos listas.")
            continue
        print(f"  comparadas: {comparadas}   sin regla en alguna de las dos: {incomparables}")
        print(f"  reporte de saldos: {total_sin:>16,.2f}")
        print(f"  las otras paginas: {total_con:>16,.2f}")
        pct = (total_sin / total_con - 1) * 100 if total_con else Decimal(0)
        print(f"  DIFERENCIA NETA:   {total_sin - total_con:>16,.2f}   ({pct:+.1f} %)")
        arriba = sum(v for v in por_orden.values() if v > 0)
        abajo = sum(v for v in por_orden.values() if v < 0)
        print(f"  ordenes que difieren: {len(por_orden)}   bruto: {arriba - abajo:,.2f}")
        for so, v in sorted(por_orden.items(), key=lambda kv: abs(kv[1]), reverse=True)[:6]:
            print(f"     {so}: {v:>14,.2f}")
        if por_orden:
            salida = 1
    return salida


if __name__ == "__main__":
    raise SystemExit(main())
