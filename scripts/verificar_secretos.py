#!/usr/bin/env python
"""Impide que una credencial remota vuelva a entrar al repositorio.

Existe por un hecho, no por precaucion: el 1 de agosto de 2026 entro al historial
la contrasena de una base Postgres remota, hardcodeada en
``scripts/monitor_railway_logs.ps1``. Se saco el 2 de septiembre, pero **el valor
sigue en el historial** -- alcanzable en 269 commits, empujados a ``origin`` en
``main``, ``develop`` y una rama mas. Rotarla sin purgar el historial solo cambia
cual credencial esta expuesta.

Este chequeo no arregla eso (purgar es una reescritura de historial y una decision
del usuario: ver ``docs/blindaje/0-credencial.md``). Lo que hace es que **no vuelva
a pasar**, que es la unica mitad que se puede automatizar.

**Que se considera un secreto y que no.** Una URL con contrasena apuntando a
``localhost`` es la base de desarrollo y esta en media docena de archivos a
proposito. Lo que no puede entrar es una que apunte **afuera**. La diferencia es el
host, no la forma, y por eso el chequeo mira el host.

Uso:
    python scripts/verificar_secretos.py                # el arbol de trabajo
    python scripts/verificar_secretos.py --staged       # solo lo que se va a commitear
    python scripts/verificar_secretos.py --rango A..B   # un rango de commits

Sale 1 si encuentra algo, para que sirva de barrera. **Nunca imprime el valor
encontrado**: imprime donde esta y de que forma es, porque un chequeo de secretos
que los imprime en el log de CI es peor que no tenerlo.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Hosts que son la maquina de quien desarrolla. Una credencial contra estos no es
# un secreto: es la base de desarrollo, y esta en varios archivos a proposito.
HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1", "postgres", "db", "host.docker.internal"}

# Valores que son claramente un placeholder de plantilla.
PLACEHOLDERS = {
    "password",
    "pass",
    "changeme",
    "postgres",
    "tu_password",
    "your_password",
    "contrasena",
    "xxx",
    "secret",
    "cxc_ci_pw",
}

# Hosts que son un hueco para rellenar. Es la senal MAS fuerte de que la linea es
# una plantilla: nadie tiene una base en un host llamado "host". Se descarta por
# aca antes de mirar la contrasena, asi que una plantilla con una contrasena de
# aspecto real -- que las hay en los README -- tampoco genera ruido.
HOSTS_PLACEHOLDER = {
    "host",
    "hostname",
    "tu-host",
    "tu_host",
    "your-host",
    "servidor",
    "ejemplo.com",
    "example.com",
}

PATRONES: list[tuple[str, re.Pattern[str]]] = [
    (
        "URL de base de datos con contrasena",
        re.compile(
            r"(?P<esquema>postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://"
            r"(?P<usuario>[A-Za-z0-9_.\-]+):(?P<secreto>[^@\s\"'<>${}]{4,})@"
            r"(?P<host>[A-Za-z0-9_.\-]+)"
        ),
    ),
    (
        "clave de API de Odoo (40 hex)",
        # El formato de las API keys de Odoo. Se busca solo cuando esta asignada a
        # algo que se llame como una credencial, para no marcar cualquier hash.
        re.compile(
            r"(?i)(api[_-]?key|apikey|odoo[_-]?key)\s*[:=]\s*[\"']?(?P<secreto>[0-9a-f]{40})"
        ),
    ),
    (
        "token de Railway",
        re.compile(r"(?i)railway[_-]?token\s*[:=]\s*[\"']?(?P<secreto>[A-Za-z0-9_\-]{20,})"),
    ),
]

# Archivos que no se revisan: los que existen para documentar el formato.
EXCLUIDOS = {
    ".env.example",
    ".env.qa.example",
    "docs/blindaje/0-credencial.md",
    "scripts/verificar_secretos.py",
}


def _sh(*args: str) -> str:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=RAIZ
    ).stdout


def _es_placeholder(secreto: str) -> bool:
    limpio = secreto.strip().lower()
    if limpio in PLACEHOLDERS:
        return True
    # ``${VAR}``, ``$VAR``, ``<algo>``, ``...`` -- una interpolacion, no un valor.
    return bool(re.fullmatch(r"[.<>${}%*x\-_]+", limpio))


def revisar_texto(texto: str, origen: str) -> list[str]:
    """Los hallazgos de un texto, descritos sin citar el secreto."""
    hallazgos: list[str] = []
    for etiqueta, patron in PATRONES:
        for m in patron.finditer(texto):
            secreto = m.group("secreto")
            if _es_placeholder(secreto):
                continue
            host = m.groupdict().get("host")
            if host and host.lower() in HOSTS_LOCALES | HOSTS_PLACEHOLDER:
                continue
            linea = texto[: m.start()].count("\n") + 1
            donde = f"{origen}:{linea}"
            forma = f"{len(secreto)} caracteres"
            destino = f" hacia {host}" if host else ""
            hallazgos.append(f"{donde}  {etiqueta}{destino} (contrasena de {forma})")
    return hallazgos


def _archivos_del_arbol() -> list[str]:
    return [
        a
        for a in _sh("git", "ls-files").splitlines()
        if a and a not in EXCLUIDOS and not a.startswith("secrets/")
    ]


def _archivos_staged() -> list[str]:
    return [
        a
        for a in _sh("git", "diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines()
        if a and a not in EXCLUIDOS and not a.startswith("secrets/")
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--staged", action="store_true", help="solo lo que esta por commitearse")
    ap.add_argument("--rango", help="un rango de commits, p.ej. origin/main..HEAD")
    args = ap.parse_args()

    hallazgos: list[str] = []

    if args.rango:
        shas = _sh("git", "rev-list", args.rango).splitlines()
        print(f"Revisando {len(shas)} commit(s) de {args.rango}...")
        for sha in shas:
            diff = _sh("git", "show", "--format=", "--unified=0", sha)
            # Solo las lineas AGREGADAS: lo que un commit borra no es lo que
            # ese commit introduce.
            agregadas = "\n".join(
                linea[1:] for linea in diff.splitlines() if linea.startswith("+")
            )
            hallazgos += revisar_texto(agregadas, f"{sha[:8]}")
    else:
        archivos = _archivos_staged() if args.staged else _archivos_del_arbol()
        que = "por commitearse" if args.staged else "versionados"
        print(f"Revisando {len(archivos)} archivo(s) {que}...")
        for archivo in archivos:
            ruta = RAIZ / archivo
            try:
                texto = ruta.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue
            hallazgos += revisar_texto(texto, archivo)

    if not hallazgos:
        print("Sin credenciales remotas. (Las de localhost no cuentan: son de desarrollo.)")
        return 0

    print()
    print(f"{len(hallazgos)} credencial(es) que no deberian estar acá:")
    for h in dict.fromkeys(hallazgos):
        print(f"  {h}")
    print()
    print("El valor NO se imprime a propósito. Para verlo, abrí el archivo en esa línea.")
    print("Si es un falso positivo, agregá el archivo a EXCLUIDOS con el motivo.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
