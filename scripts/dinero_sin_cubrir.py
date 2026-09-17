#!/usr/bin/env python
"""Funciones de dinero cuyas lineas NO estan cubiertas por ninguna prueba.

Instrumento de la Fase 2.4: sirve para elegir la proxima pieza a extraer con una
medicion en vez de con intuicion. Las piezas 14 a 19 salieron de su version
anterior, y encontraron defectos reales -- una conjuncion que invertia un bloqueo,
un docstring que prometia lo contrario de lo que el codigo hacia, tres endpoints que
recongelaban equivalentes con otra precision.

**Por que esta version y no la anterior.** La primera buscaba el NOMBRE de la
funcion en ``tests/`` y ``escenarios/``. Eso da falsos positivos: el 11-sep-2026
marco ``post_editar_tasa_binance_pago_pendiente`` como "sin pruebas" y si tenia una
--la ejercita por HTTP, sin nombrarla-- lo cual se descubrio porque al tocarla el
test fallo.

Lo que mide esta version es la cobertura de verdad: el porcentaje de lineas de cada
funcion que ninguna prueba ejecuta. Un nombre que no aparece puede estar cubierto; un
tramo de lineas que nadie ejecuta, no.

**Y separa las que no tienen llamador**, que es el segundo arreglo, del 11-sep-2026.
`_leer_descuentos_lineas_odoo` aparecia arriba en la lista --95 lineas de lectura en
vivo que NADIE invoca-- y de ahi saque la pieza 21, que quedo cableada en esa misma
funcion, o sea en ningun lado. Una funcion muerta esta sin cubrir por la razon
correcta: no corre. Probarla es trabajo tirado; lo que corresponde es borrarla o
declararla. Van en su propia seccion para que no compitan por el primer puesto.

Uso:
    python -m pytest --cov=cxc --cov-report=json --cov-report=
    python scripts/dinero_sin_cubrir.py
    python scripts/dinero_sin_cubrir.py --archivo src/cxc/engine/discounts.py --tope 20
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Lo que hace que una funcion sea "de dinero". Es un filtro por nombre y por lo
# tanto grueso: una funcion que mueve plata con un nombre neutro se escapa. Se
# prefiere asi antes que revisar 600 funciones -- el objetivo es ELEGIR la proxima,
# no inventariar todo.
PLATA = re.compile(
    r"monto|saldo|precio|tasa|equiv|descuento|pago|cobr|factur|teorico|iva|igtf|deud|"
    r"credito|debito|abono|vincul|conciliac",
    re.I,
)


def _cargar_cobertura(ruta: Path) -> dict[str, set[int]]:
    """``archivo`` -> lineas sin cubrir. Las claves se normalizan con ``/``."""
    if not ruta.exists():
        sys.exit(
            f"No existe {ruta}. Genera el reporte primero:\n"
            "    python -m pytest --cov=cxc --cov-report=json --cov-report="
        )
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return {
        k.replace("\\", "/"): set(v.get("missing_lines") or [])
        for k, v in datos.get("files", {}).items()
    }


def _tiene_decorador_que_la_registra(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True si un decorador la registra en algun lado, o sea que ALGUIEN la llama.

    **Esto es la mitad del asunto.** La primera version de esta cuenta marco 27
    funciones sin llamador y 25 eran endpoints: un ``@app.get("/api/...")`` registra
    la funcion en FastAPI, que la llama por cada request, y el nombre no vuelve a
    aparecer en el archivo nunca. Una seccion con 25 falsos positivos de 27 es peor
    que no tener seccion, que es justo el defecto que este plan viene corrigiendo en
    otros instrumentos.
    """
    return bool(fn.decorator_list)


def _sin_llamador(ruta: Path) -> set[str]:
    """Funciones de nivel de modulo del archivo que nadie nombra ni registra.

    Es la misma cuenta que hace ``tests/test_piezas_cableadas.py``, repetida aca en
    vez de importada: un script no deberia depender de la suite para correr.

    Sigue siendo grosera --solo mira DENTRO del archivo-- asi que una funcion
    exportada y llamada desde otro modulo aparece como huerfana. Por eso el
    resultado se presenta como "revisar", no como "borrar".
    """
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    definidas = [
        n.name
        for n in arbol.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and not _tiene_decorador_que_la_registra(n)
    ]
    todas = [n.name for n in arbol.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    usados: set[str] = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            usados.add(n.id)
        elif isinstance(n, ast.Attribute):
            usados.add(n.attr)
    # El desempate se cuenta sobre TODAS: un nombre definido dos veces (un
    # ``@overload``, una redefinicion condicional) no es el caso que se persigue.
    return {fn for fn in definidas if todas.count(fn) == 1 and fn not in usados}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cobertura", default="coverage.json")
    ap.add_argument("--archivo", default="src/cxc/web/app.py")
    ap.add_argument("--tope", type=int, default=15, help="cuantas listar")
    ap.add_argument(
        "--minimo-lineas", type=int, default=8, help="ignorar funciones mas cortas que esto"
    )
    args = ap.parse_args()

    faltantes = _cargar_cobertura(RAIZ / args.cobertura)
    clave = args.archivo.replace("\\", "/")
    sin_cubrir = faltantes.get(clave)
    if sin_cubrir is None:
        disponibles = [k for k in faltantes if "web" in k or "engine" in k][:8]
        sys.exit(f"{clave} no esta en el reporte. Hay, por ejemplo: {disponibles}")

    fuente = (RAIZ / args.archivo).read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    filas = []
    for n in ast.walk(arbol):
        if not isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not PLATA.search(n.name):
            continue
        total = (n.end_lineno or n.lineno) - n.lineno + 1
        if total < args.minimo_lineas:
            continue
        descubiertas = sum(
            1 for ln in range(n.lineno, (n.end_lineno or n.lineno) + 1) if ln in sin_cubrir
        )
        if not descubiertas:
            continue
        filas.append((descubiertas, descubiertas / total, total, n.name, n.lineno))

    huerfanas = _sin_llamador(RAIZ / args.archivo)
    vivas = [f for f in filas if f[3] not in huerfanas]
    muertas = [f for f in filas if f[3] in huerfanas]

    print(f"{args.archivo}: {len(sin_cubrir)} lineas sin cubrir en total")
    print(f"funciones de dinero con alguna linea sin cubrir: {len(filas)}")
    print(f"   de esas, sin llamador en el archivo: {len(muertas)}\n")
    print(f"{'sin cubrir':>11} {'de':>6} {'%':>5}  {'linea':>7}  funcion")
    for descubiertas, frac, total, nombre, ln in sorted(vivas, reverse=True)[: args.tope]:
        print(f"{descubiertas:>11} {total:>6} {frac * 100:>4.0f}%  {ln:>7}  {nombre}")

    if muertas:
        print(
            "\nSIN LLAMADOR en el archivo -- probarlas es trabajo tirado; corresponde\n"
            "borrarlas o declararlas (ver SIN_LLAMADOR_EN_APP en\n"
            "tests/test_piezas_cableadas.py). La cuenta solo mira dentro del archivo,\n"
            "asi que verifica antes de borrar:"
        )
        for descubiertas, _f, total, nombre, ln in sorted(muertas, key=lambda x: -x[2]):
            print(
                f"   {descubiertas:>4} de {total:<5} sin cubrir  "
                f"{args.archivo}:{ln:<6} {nombre}"
            )

    enteras = [f for f in vivas if f[1] >= 0.99]
    print(f"\nfunciones de dinero VIVAS y SIN NINGUNA linea cubierta: {len(enteras)}")
    for _d, _f, total, nombre, ln in sorted(enteras, key=lambda x: -x[2])[: args.tope]:
        print(f"   {total:>4} lineas  {args.archivo}:{ln:<6} {nombre}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
