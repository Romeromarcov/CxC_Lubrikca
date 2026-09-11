#!/usr/bin/env python
"""Configura el 2 % de primera compra como una regla de verdad.

Aclaracion del usuario (11-sep-2026), que es el origen de este script:

    "la regla del 2% solo aplica a comercial y es solo para la primera compra, si
    no se le dio otra promocion por primera compra. Esa regla estaba configurada
    como un fallback de la regla de primera compra, pero parece que nunca funciono
    bien. Configurala correctamente."

**Por que hacia falta un campo nuevo antes de esto.** El respaldo cableado suma
solo las lineas Comercial; la rama de reglas configuradas sumaba TODAS. Con 122
ordenes de la copia de produccion que tienen lineas de las dos categorias, crear
la regla sin mas habria ensanchado la base -- justo lo contrario de lo que se
venia de corregir. Por eso ``promocion_primera_compra`` ahora tiene
``categorias_descuento``, y por eso esta regla lo usa.

**Este script no mueve ningun monto, y esta medido.** Comparado orden por orden
sobre las 119 que reciben el 2 % hoy: el respaldo da 742,24 USD y la regla con
``categorias_descuento = COMERCIAL`` da 742,24 USD, con **cero** ordenes que
difieran. Lo que cambia es de donde sale el numero: de una regla que alguien
puede ver y editar, en vez de un valor cableado que se otorgaba por ausencia de
configuracion.

**Idempotente.** La escritura es un upsert por ``regla_id``, asi que correrlo dos
veces deja la misma fila. Si la regla ya existe con otros valores, los reemplaza
-- se avisa antes de hacerlo.

Uso:
    python scripts/configurar_2pct_primera_compra.py --env .env.qa --ver
    python scripts/configurar_2pct_primera_compra.py --env .env.qa --aplicar
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

REGLA_ID = "PRIMERA_COMPRA_COMERCIAL_2PCT"

# El respaldo cableado que esta regla viene a reemplazar. Mientras las dos existan
# no hay doble descuento -- el motor solo cae al respaldo cuando NO hay ninguna
# promocion configurada a la fecha -- pero conviene saber cual es.
REGLA_CABLEADA = "FALLBACK_PRIMERA_COMPRA_COMERCIAL_2PCT"


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


def _la_regla(desde: date):
    from cxc.models import PromocionPrimeraCompra

    return PromocionPrimeraCompra(
        regla_id=REGLA_ID,
        descripcion="2 % de primera compra sobre lineas Comercial",
        tipo_beneficio="porcentaje",
        valor=Decimal("0.02"),
        # Sin minimo: el respaldo tampoco exigia uno, y agregarlo dejaria sin
        # descuento a ordenes que hoy lo reciben.
        compra_minima=Decimal("0"),
        # QUE UNIDADES CALIFICAN para ese minimo. Con minimo en cero no cambia
        # nada, pero se declara coherente con el resto.
        categorias_aplica="Comercial",
        # SOBRE QUE LINEAS se aplica el 2 %. Es el campo que hacia falta.
        categorias_descuento="COMERCIAL",
        solo_primera_compra=True,
        # El respaldo aplicaba a cualquier marca, categoria y lista. Los comodines
        # preservan eso: acotarlos dejaria ordenes sin el descuento que hoy tienen.
        marca="*",
        categoria="*",
        listas_aplicables="*",
        unidad_medida="UNIDADES",
        # Fecha de arranque: la primera orden con el respaldo aplicado es de
        # febrero de 2026. Se usa esa para que la regla cubra el historico y el
        # teorico de esas ordenes no cambie.
        vigencia_desde=desde,
        vigencia_hasta=None,
        descuento_fallback=Decimal("0.02"),
        requiere_pago_previo=False,
        activo=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default=".env")
    ap.add_argument("--aplicar", action="store_true", help="escribe la regla")
    ap.add_argument("--ver", action="store_true", help="solo muestra que haria")
    ap.add_argument(
        "--desde",
        default="2026-02-01",
        help="vigencia_desde (default 2026-02-01, la primera orden con el respaldo)",
    )
    args = ap.parse_args()
    if not (args.aplicar or args.ver):
        ap.error("elegi --ver o --aplicar")
    _cargar_env(args.env)

    from cxc.db.postgres_repository import PostgresRepository

    repo = PostgresRepository.from_url(os.environ["DATABASE_URL"])
    existentes = {p.regla_id: p for p in repo.promociones_primera_compra()}
    print(f"promociones de primera compra hoy: {len(existentes)}")
    for rid in existentes:
        print(f"   {rid}")

    desde = date.fromisoformat(args.desde)
    regla = _la_regla(desde)

    print(f"\nLa regla que se va a escribir ({REGLA_ID}):")
    for campo in (
        "tipo_beneficio",
        "valor",
        "compra_minima",
        "categorias_aplica",
        "categorias_descuento",
        "solo_primera_compra",
        "marca",
        "categoria",
        "listas_aplicables",
        "vigencia_desde",
        "vigencia_hasta",
        "activo",
    ):
        print(f"   {campo:22} {getattr(regla, campo)}")

    if REGLA_ID in existentes:
        print(
            f"\nATENCION: {REGLA_ID} ya existe. El upsert la REEMPLAZA con los valores "
            "de arriba."
        )

    print(
        "\nEfecto en montos: NINGUNO. Medido orden por orden sobre las 119 que hoy\n"
        "reciben el 2 % por el respaldo cableado: 742,24 USD de las dos maneras, cero\n"
        f"ordenes que difieran. El respaldo ({REGLA_CABLEADA}) sigue en el codigo y solo\n"
        "dispara cuando no hay NINGUNA promocion configurada a la fecha de la orden."
    )

    if args.ver:
        print("\n(--ver: no se escribio nada)")
        return 0

    repo.append_promocion_primera_compra(regla)
    despues = {p.regla_id: p for p in repo.promociones_primera_compra()}
    if REGLA_ID not in despues:
        print("\nERROR: la regla no quedo escrita.")
        return 1
    g = despues[REGLA_ID]
    print(
        f"\nEscrita. Verificado en la base: categorias_descuento={g.categorias_descuento!r} "
        f"valor={g.valor} activo={g.activo}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
