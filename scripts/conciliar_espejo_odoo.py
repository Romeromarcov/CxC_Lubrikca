#!/usr/bin/env python3
"""Conciliacion total del espejo contra Odoo (Fase 1.4 del plan de blindaje).

Las nueve partidas que hoy comparan contra Odoo lo hacen sobre el universo
que los reportes listan. Eso detecta un monto mal calculado pero NO detecta
una fila que Odoo borro y el espejo sigue contando: si la fila no esta en el
reporte, nadie la mira de ningun lado.

Aca la comparacion es por tabla completa: cuantos registros y que suma hay
de cada lado, y la lista nominal de los que estan en uno y no en el otro.

    python scripts/conciliar_espejo_odoo.py --env .env.qa
    python scripts/conciliar_espejo_odoo.py --env .env.qa --detalle
    python scripts/conciliar_espejo_odoo.py --env .env.qa --json out.json

Salida distinta de cero si alguna tabla tiene faltantes o sobrantes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

# Se importa, no se copia: si el sync cambia que estados considera confirmados,
# esta conciliacion tiene que moverse con el, no quedar comparando otro
# universo y reportar un descuadre inventado.
from cxc.odoo.client import PAGO_ESTADOS_CONFIRMADOS  # noqa: E402


@dataclass(frozen=True)
class Partida:
    """Una tabla del espejo y su modelo equivalente en Odoo.

    ``dominio`` tiene que describir EXACTAMENTE el mismo universo que el
    ``changed_*`` del sync que llena la tabla. Si difieren, la conciliacion
    reporta un descuadre que no existe -- o peor, tapa uno que si.
    """

    nombre: str
    tabla: str
    clave_espejo: str
    modelo: str
    dominio: list[Any]
    campo_monto_odoo: str | None = None
    campo_monto_espejo: str | None = None
    campos_extra: list[str] = field(default_factory=list)
    contexto: dict[str, Any] = field(default_factory=dict)
    # ``stock.move.line`` no tiene ``name`` en Odoo 18 -- pedirlo es un
    # ValueError del servidor, no una lectura vacia.
    tiene_name: bool = True
    clave_odoo: Callable[[dict[str, Any]], str] = lambda r: str(r["id"])


def _nombre(rec: dict[str, Any]) -> str:
    return str(rec.get("name") or rec["id"])


PARTIDAS: list[Partida] = [
    Partida(
        nombre="ordenes",
        tabla="ordenes_venta",
        clave_espejo="so_id",
        modelo="sale.order",
        # El sync trae TODA sale.order (sin filtro de estado): el espejo
        # necesita ver tambien las canceladas para poder decir que una
        # orden entregada se cancelo.
        dominio=[],
        campo_monto_odoo="amount_total",
        campo_monto_espejo="monto_total",
        campos_extra=["state"],
        clave_odoo=_nombre,
    ),
    Partida(
        nombre="lineas_orden",
        tabla="lineas_orden",
        clave_espejo="linea_id",
        modelo="sale.order.line",
        # ``display_type = False``: las lineas de seccion y de nota no son
        # producto y el sync no las trae.
        dominio=[["display_type", "=", False]],
    ),
    Partida(
        nombre="clientes",
        tabla="clientes",
        clave_espejo="cliente_id",
        modelo="res.partner",
        dominio=[],
        # ``active_test=False``: el sync trae tambien los contactos
        # archivados, porque una orden historica los sigue referenciando.
        contexto={"active_test": False},
    ),
    Partida(
        nombre="pagos",
        tabla="pagos",
        clave_espejo="pago_id",
        modelo="account.payment",
        # Mismo dominio que ``changed_pagos``: solo cobros (inbound) en
        # estado confirmado. Sin filtro de ``is_reconciled`` -- ver el
        # comentario de ese metodo.
        dominio=[
            ["payment_type", "=", "inbound"],
            ["state", "in", PAGO_ESTADOS_CONFIRMADOS],
        ],
        campos_extra=["state"],
    ),
    Partida(
        nombre="facturas",
        tabla="facturas",
        clave_espejo="factura_id",
        modelo="account.move",
        dominio=[["move_type", "in", ["out_invoice", "out_refund", "out_debit"]]],
        campo_monto_odoo="amount_total",
        campo_monto_espejo="monto_total",
        campos_extra=["state", "move_type"],
    ),
    Partida(
        nombre="entregas",
        tabla="entregas",
        clave_espejo="entrega_id",
        modelo="stock.picking",
        # ``incoming`` incluido a proposito: las devoluciones entran por ahi
        # y el espejo las necesita para marcar ``tiene_devolucion``.
        dominio=[["picking_type_code", "in", ["outgoing", "incoming"]]],
        campos_extra=["state"],
    ),
    Partida(
        nombre="catalogo",
        tabla="catalogo",
        clave_espejo="producto_id",
        # ``product.product`` (la variante), no ``product.template``.
        modelo="product.product",
        dominio=[["active", "in", [True, False]], ["sale_ok", "=", True]],
    ),
    Partida(
        nombre="lineas_entrega",
        tabla="lineas_entrega",
        clave_espejo="linea_id",
        modelo="stock.move.line",
        dominio=[["picking_id.picking_type_id.code", "=", "outgoing"]],
        tiene_name=False,
    ),
    Partida(
        nombre="lineas_factura",
        tabla="lineas_factura",
        clave_espejo="linea_id",
        modelo="account.move.line",
        dominio=[["display_type", "in", ["product", False]]],
    ),
]


def cargar_env(ruta: Path | None) -> None:
    if ruta is None:
        ruta = RAIZ / ".env"
    if not ruta.exists():
        sys.exit(f"No existe {ruta}")
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, _, valor = linea.partition("=")
            os.environ[clave.strip()] = valor.strip()


def conectar_odoo():
    from cxc.config import AppConfig
    from cxc.odoo.client import _connect

    ejecutar = _connect(AppConfig.from_env().odoo)
    if not ejecutar:
        sys.exit("No se pudo conectar a Odoo con las credenciales del entorno.")
    return ejecutar


def _motor():
    import sqlalchemy as sa

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        sys.exit("Falta DATABASE_URL.")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return sa.create_engine(url)


def conciliar(partida: Partida, ejecutar, con, max_nominal: int) -> dict[str, Any]:
    import sqlalchemy as sa

    campos = ["id", *(["name"] if partida.tiene_name else []), *partida.campos_extra]
    if partida.campo_monto_odoo:
        campos.append(partida.campo_monto_odoo)
    campos = list(dict.fromkeys(campos))

    opciones: dict[str, Any] = {"fields": campos}
    if partida.contexto:
        opciones["context"] = partida.contexto
    try:
        registros = ejecutar(partida.modelo, "search_read", [partida.dominio], opciones)
    except Exception as exc:  # noqa: BLE001 -- se reporta, no se traga
        return {"partida": partida.nombre, "estado": "ERROR_ODOO", "error": str(exc)[:300]}

    odoo: dict[str, Decimal | None] = {}
    for rec in registros:
        clave = partida.clave_odoo(rec)
        monto = None
        if partida.campo_monto_odoo:
            monto = Decimal(str(rec.get(partida.campo_monto_odoo) or "0"))
        odoo[clave] = monto

    cols = f'"{partida.clave_espejo}"'
    if partida.campo_monto_espejo:
        cols += f', "{partida.campo_monto_espejo}"'
    filas = con.execute(sa.text(f'SELECT {cols} FROM "{partida.tabla}"')).all()
    espejo: dict[str, Decimal | None] = {
        str(f[0]): (Decimal(str(f[1])) if partida.campo_monto_espejo else None) for f in filas
    }

    solo_odoo = sorted(set(odoo) - set(espejo))
    solo_espejo = sorted(set(espejo) - set(odoo))
    comunes = set(odoo) & set(espejo)

    resultado: dict[str, Any] = {
        "partida": partida.nombre,
        "tabla": partida.tabla,
        "modelo": partida.modelo,
        "odoo": len(odoo),
        "espejo": len(espejo),
        "comunes": len(comunes),
        "faltan_en_el_espejo": len(solo_odoo),
        "sobran_en_el_espejo": len(solo_espejo),
        "nominal_faltan": solo_odoo[:max_nominal],
        "nominal_sobran": solo_espejo[:max_nominal],
    }

    if partida.campo_monto_odoo:
        suma_odoo = sum((odoo[k] or Decimal(0) for k in odoo), Decimal(0))
        suma_espejo = sum((espejo[k] or Decimal(0) for k in espejo), Decimal(0))
        difieren = [
            {
                "clave": k,
                "odoo": str(odoo[k]),
                "espejo": str(espejo[k]),
                "dif": str((odoo[k] or Decimal(0)) - (espejo[k] or Decimal(0))),
            }
            for k in sorted(comunes)
            if abs((odoo[k] or Decimal(0)) - (espejo[k] or Decimal(0))) > Decimal("0.01")
        ]
        resultado |= {
            "suma_odoo": str(suma_odoo),
            "suma_espejo": str(suma_espejo),
            "dif_suma": str(suma_odoo - suma_espejo),
            "montos_que_difieren": len(difieren),
            "nominal_montos": difieren[:max_nominal],
        }

    sano = not solo_odoo and not solo_espejo and not resultado.get("montos_que_difieren")
    resultado["estado"] = "CUADRA" if sano else "DESCUADRE"
    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=None)
    parser.add_argument("--detalle", action="store_true")
    parser.add_argument("--max-nominal", type=int, default=15)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--partida", default=None)
    args = parser.parse_args()

    cargar_env(args.env)
    ejecutar = conectar_odoo()
    motor = _motor()

    partidas = [p for p in PARTIDAS if not args.partida or p.nombre == args.partida]
    resultados = []
    with motor.connect() as con:
        for partida in partidas:
            print(f"  conciliando {partida.nombre}...", file=sys.stderr)
            resultados.append(conciliar(partida, ejecutar, con, args.max_nominal))

    print(
        f"\n{'partida':<16} {'odoo':>7} {'espejo':>7} {'faltan':>7} "
        f"{'sobran':>7} {'dif $':>14}  estado"
    )
    print("-" * 78)
    for r in resultados:
        if r.get("estado") == "ERROR_ODOO":
            print(f"{r['partida']:<16} {'':>7} {'':>7} {'':>7} {'':>7} {'':>14}  ERROR")
            continue
        dif = r.get("dif_suma", "")
        print(
            f"{r['partida']:<16} {r['odoo']:>7} {r['espejo']:>7} "
            f"{r['faltan_en_el_espejo']:>7} {r['sobran_en_el_espejo']:>7} "
            f"{dif:>14}  {r['estado']}"
        )

    if args.detalle:
        for r in resultados:
            if r.get("estado") != "DESCUADRE":
                continue
            print(f"\n### {r['partida']} ({r['tabla']} vs {r['modelo']})")
            if r["nominal_faltan"]:
                print(f"  en Odoo y NO en el espejo ({r['faltan_en_el_espejo']}):")
                print("    " + ", ".join(r["nominal_faltan"]))
            if r["nominal_sobran"]:
                print(f"  en el espejo y NO en Odoo ({r['sobran_en_el_espejo']}):")
                print("    " + ", ".join(r["nominal_sobran"]))
            for m in r.get("nominal_montos", []):
                print(f"    {m['clave']}: odoo={m['odoo']} espejo={m['espejo']} dif={m['dif']}")

    if args.json:
        args.json.write_text(
            json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nDetalle en {args.json}")

    return 1 if any(r.get("estado") != "CUADRA" for r in resultados) else 0


if __name__ == "__main__":
    sys.exit(main())
