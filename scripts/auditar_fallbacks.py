#!/usr/bin/env python3
"""Inventario de fallbacks silenciosos (Fase 1.1 del plan de blindaje).

Recorre el AST del paquete y clasifica en tres categorias cada ``except``
amplio y cada valor por defecto numerico:

* ``legitimo``  - el dato es opcional de verdad y el default no viaja a dinero.
* ``ruidoso``   - deberia dejar rastro (log/alerta) y no lo deja.
* ``mina``      - devuelve un numero donde no hay dato, en un camino de dinero.

La diferencia entre ruidoso y mina no es de estilo: es si el valor inventado
puede terminar sumado en un saldo. Los bugs de "sin datos no es cero" de
septiembre 2026 salieron todos de la tercera categoria.

Uso:  python scripts/auditar_fallbacks.py [--json salida.json] [--categoria mina]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PAQUETE = RAIZ / "src" / "cxc"

# Nombres que delatan un camino de dinero. Si la funcion o la variable que
# recibe el default cae aca, un numero inventado se convierte en un saldo.
CAMINO_DINERO = re.compile(
    r"saldo|monto|teorico|teórico|pago|abono|equiv|total|importe|precio|"
    r"descuento|balance|concilia|factur|cobr|deuda|residual|tasa|rate|"
    r"credito|crédito|debito|débito|neto|bruto|iva|subtotal",
    re.IGNORECASE,
)

# Contextos donde un default numerico es inofensivo: contadores de UI,
# paginacion, ordenamiento, medidas de tiempo.
CONTEXTO_INOCUO = re.compile(
    r"^(page|pagina|offset|limit|index|idx|orden|order|sort|count|n|i|j|k|"
    r"intentos|reintentos|timeout|segundos|ms|elapsed|duracion|len|size|"
    r"ancho|alto|width|height|zoom|nivel|depth|prioridad)$",
    re.IGNORECASE,
)

LLAMADAS_QUE_DEJAN_RASTRO = re.compile(
    r"^(log|logger|logging|_log|LOGGER|alerta|alertar|alert|warn|warning|"
    r"capture|notificar|registrar)",
    re.IGNORECASE,
)


@dataclass
class Hallazgo:
    archivo: str
    linea: int
    clase: str  # "except" | "default"
    categoria: str  # "legitimo" | "ruidoso" | "mina"
    funcion: str
    fragmento: str
    motivo: str


def _nombre_llamada(nodo: ast.AST) -> str:
    if isinstance(nodo, ast.Call):
        return _nombre_llamada(nodo.func)
    if isinstance(nodo, ast.Attribute):
        return f"{_nombre_llamada(nodo.value)}.{nodo.attr}"
    if isinstance(nodo, ast.Name):
        return nodo.id
    return ""


def _deja_rastro(cuerpo: list[ast.stmt]) -> bool:
    """El manejador registra algo, alerta o re-lanza."""
    for nodo in ast.walk(ast.Module(body=cuerpo, type_ignores=[])):
        if isinstance(nodo, ast.Raise):
            return True
        if isinstance(nodo, ast.Call):
            nombre = _nombre_llamada(nodo)
            cabeza = nombre.split(".")[0]
            if LLAMADAS_QUE_DEJAN_RASTRO.match(cabeza) or LLAMADAS_QUE_DEJAN_RASTRO.match(nombre):
                return True
            if ".error" in nombre or ".warning" in nombre or ".exception" in nombre:
                return True
    return False


def _es_numero(nodo: ast.AST) -> bool:
    """0, 0.0, -1, Decimal("0"), Decimal(0)."""
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, int | float):
        return not isinstance(nodo.value, bool)
    if isinstance(nodo, ast.Call):
        nombre = _nombre_llamada(nodo)
        if nombre.endswith("Decimal") and nodo.args:
            return isinstance(nodo.args[0], ast.Constant)
    if isinstance(nodo, ast.UnaryOp) and isinstance(nodo.op, ast.USub):
        return _es_numero(nodo.operand)
    return False


def _devuelve_numero(cuerpo: list[ast.stmt]) -> bool:
    for nodo in ast.walk(ast.Module(body=cuerpo, type_ignores=[])):
        if isinstance(nodo, ast.Return) and nodo.value is not None:
            if _es_numero(nodo.value):
                return True
            if isinstance(nodo.value, ast.Dict):
                for clave, valor in zip(nodo.value.keys, nodo.value.values, strict=False):
                    if (
                        isinstance(clave, ast.Constant)
                        and isinstance(clave.value, str)
                        and CAMINO_DINERO.search(clave.value)
                        and _es_numero(valor)
                    ):
                        return True
    return False


def _solo_traga(cuerpo: list[ast.stmt]) -> bool:
    """El manejador no hace nada: ``pass``, ``continue``, ``...``.

    Por si solo es inofensivo. Se vuelve mina cuando la funcion que lo
    contiene termina devolviendo un numero: entonces el fallo de Odoo sale
    por la misma puerta que un cero legitimo. Es el caso de
    ``FastPriceResolver.precio`` -- con Odoo caido, todo producto vale 0 y
    el teorico de la orden queda saldado.
    """
    for stmt in cuerpo:
        if isinstance(stmt, ast.Pass | ast.Continue):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        return False
    return bool(cuerpo)


def _lineas_con_centinela(arbol: ast.AST) -> set[int]:
    """Lineas donde el default numerico es un centinela, no una mina.

    El patron correcto -- el que quedo en ``rates.py`` despues de la
    reescritura de septiembre -- es asignar el default y descartarlo acto
    seguido::

        v = fila.get(clave, Decimal("0"))
        if v <= 0:
            return None

    Ahi el cero nunca sale de la funcion: sirve para no ramificar sobre
    ``None``. Marcarlo como mina seria acusar justamente al codigo que ya
    esta blindado.
    """
    guardadas: set[int] = set()
    for nodo in ast.walk(arbol):
        cuerpo = getattr(nodo, "body", None)
        if not isinstance(cuerpo, list):
            continue
        for i, stmt in enumerate(cuerpo):
            if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                continue
            destino = stmt.targets[0]
            if not isinstance(destino, ast.Name):
                continue
            for siguiente in cuerpo[i + 1 : i + 3]:
                if not isinstance(siguiente, ast.If):
                    continue
                for sub in ast.walk(siguiente.test):
                    if (
                        isinstance(sub, ast.Compare)
                        and isinstance(sub.left, ast.Name)
                        and sub.left.id == destino.id
                        and any(_es_numero(c) for c in sub.comparators)
                    ):
                        guardadas.add(stmt.lineno)
                        if stmt.value is not None:
                            guardadas.add(stmt.value.lineno)
    return guardadas


class Visitante(ast.NodeVisitor):
    def __init__(self, archivo: Path, fuente: str) -> None:
        self.archivo = archivo
        self.lineas = fuente.splitlines()
        self.pila: list[str] = []
        self.nodos: list[ast.AST] = []
        self.hallazgos: list[Hallazgo] = []
        self.centinelas: set[int] = set()

    def _funcion(self) -> str:
        return ".".join(self.pila) or "<modulo>"

    def _funcion_devuelve_numero(self) -> bool:
        """La funcion mas interna que contiene el nodo actual devuelve un numero."""
        for nodo in reversed(self.nodos):
            if isinstance(nodo, ast.FunctionDef | ast.AsyncFunctionDef):
                return _devuelve_numero(nodo.body)
        return False

    def visit_FunctionDef(self, nodo: ast.FunctionDef) -> None:
        self.pila.append(nodo.name)
        self.nodos.append(nodo)
        self.generic_visit(nodo)
        self.nodos.pop()
        self.pila.pop()

    def visit_AsyncFunctionDef(self, nodo: ast.AsyncFunctionDef) -> None:
        self.pila.append(nodo.name)
        self.nodos.append(nodo)
        self.generic_visit(nodo)
        self.nodos.pop()
        self.pila.pop()

    def visit_ClassDef(self, nodo: ast.ClassDef) -> None:
        self.pila.append(nodo.name)
        self.generic_visit(nodo)
        self.pila.pop()

    def _fragmento(self, linea: int) -> str:
        try:
            return self.lineas[linea - 1].strip()[:120]
        except IndexError:
            return ""

    def _rel(self) -> str:
        return self.archivo.relative_to(RAIZ).as_posix()

    def visit_Try(self, nodo: ast.Try) -> None:
        for manejador in nodo.handlers:
            tipo = manejador.type
            amplio = (
                tipo is None
                or (isinstance(tipo, ast.Name) and tipo.id in {"Exception", "BaseException"})
                or (
                    isinstance(tipo, ast.Tuple)
                    and any(
                        isinstance(e, ast.Name) and e.id in {"Exception", "BaseException"}
                        for e in tipo.elts
                    )
                )
            )
            if not amplio:
                continue
            rastro = _deja_rastro(manejador.body)
            numero = _devuelve_numero(manejador.body)
            funcion = self._funcion()
            en_dinero = bool(CAMINO_DINERO.search(funcion))
            traga_mudo = _solo_traga(manejador.body)
            if numero and en_dinero:
                categoria = "mina"
                motivo = (
                    "el manejador devuelve un numero en una funcion de dinero: "
                    "un fallo se vuelve indistinguible de un saldo cero"
                )
            elif traga_mudo and en_dinero and self._funcion_devuelve_numero():
                categoria = "mina"
                motivo = (
                    "el manejador no hace nada y la funcion termina devolviendo un "
                    "numero: el fallo de Odoo sale por la misma puerta que un cero real"
                )
            elif numero:
                categoria = "ruidoso"
                motivo = "devuelve un numero ante el fallo, fuera de un camino de dinero"
            elif not rastro:
                categoria = "ruidoso"
                motivo = "traga la excepcion sin dejar rastro"
            else:
                categoria = "legitimo"
                motivo = "registra o re-lanza"
            self.hallazgos.append(
                Hallazgo(
                    self._rel(),
                    manejador.lineno,
                    "except",
                    categoria,
                    funcion,
                    self._fragmento(manejador.lineno),
                    motivo,
                )
            )
        self.generic_visit(nodo)

    def _registrar_default(self, linea: int, destino: str, nodo_default: ast.AST) -> None:
        if not _es_numero(nodo_default):
            return
        funcion = self._funcion()
        etiqueta = destino or ""
        cabeza = etiqueta.split(".")[-1].split("[")[0]
        if linea in self.centinelas:
            categoria = "legitimo"
            motivo = "centinela: el default se descarta con una guarda inmediata"
        elif CONTEXTO_INOCUO.match(cabeza):
            categoria = "legitimo"
            motivo = "contador o indice, no viaja a dinero"
        elif CAMINO_DINERO.search(etiqueta) or CAMINO_DINERO.search(funcion):
            categoria = "mina"
            motivo = (
                "default numerico sobre un nombre de dinero: la ausencia del dato "
                "se vuelve un cero sumable"
            )
        else:
            categoria = "ruidoso"
            motivo = "default numerico sin senal de que el dato faltaba"
        self.hallazgos.append(
            Hallazgo(
                self._rel(), linea, "default", categoria, funcion, self._fragmento(linea), motivo
            )
        )

    def visit_BoolOp(self, nodo: ast.BoolOp) -> None:
        if isinstance(nodo.op, ast.Or) and len(nodo.values) >= 2:
            ultimo = nodo.values[-1]
            if _es_numero(ultimo):
                izq = nodo.values[0]
                destino = ""
                if isinstance(izq, ast.Name | ast.Attribute | ast.Call):
                    destino = _nombre_llamada(izq)
                elif isinstance(izq, ast.Subscript) and isinstance(izq.slice, ast.Constant):
                    destino = str(izq.slice.value)
                self._registrar_default(nodo.lineno, destino, ultimo)
        self.generic_visit(nodo)

    def visit_Call(self, nodo: ast.Call) -> None:
        nombre = _nombre_llamada(nodo)
        if nombre.endswith(".get") and len(nodo.args) == 2:
            clave = nodo.args[0]
            etiqueta = clave.value if isinstance(clave, ast.Constant) else ""
            self._registrar_default(nodo.lineno, str(etiqueta), nodo.args[1])
        self.generic_visit(nodo)


def analizar(rutas: list[Path]) -> list[Hallazgo]:
    todos: list[Hallazgo] = []
    for ruta in rutas:
        fuente = ruta.read_text(encoding="utf-8")
        arbol = ast.parse(fuente)
        visitante = Visitante(ruta, fuente)
        visitante.centinelas = _lineas_con_centinela(arbol)
        visitante.visit(arbol)
        todos.extend(visitante.hallazgos)
    return todos


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--categoria", default=None, help="filtra: mina | ruidoso | legitimo")
    args = parser.parse_args()

    rutas = sorted(PAQUETE.rglob("*.py"))
    hallazgos = analizar(rutas)
    if args.categoria:
        hallazgos = [h for h in hallazgos if h.categoria == args.categoria]

    por_archivo: dict[str, dict[str, int]] = {}
    for h in hallazgos:
        fila = por_archivo.setdefault(h.archivo, {"mina": 0, "ruidoso": 0, "legitimo": 0})
        fila[h.categoria] += 1

    print(f"{'archivo':<42} {'mina':>6} {'ruidoso':>8} {'legitimo':>9}")
    print("-" * 68)
    for archivo in sorted(por_archivo, key=lambda a: -por_archivo[a]["mina"]):
        f = por_archivo[archivo]
        print(f"{archivo:<42} {f['mina']:>6} {f['ruidoso']:>8} {f['legitimo']:>9}")
    print("-" * 68)
    tot = {c: sum(f[c] for f in por_archivo.values()) for c in ("mina", "ruidoso", "legitimo")}
    print(f"{'TOTAL':<42} {tot['mina']:>6} {tot['ruidoso']:>8} {tot['legitimo']:>9}")

    if args.json:
        args.json.write_text(
            json.dumps([asdict(h) for h in hallazgos], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nDetalle en {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
