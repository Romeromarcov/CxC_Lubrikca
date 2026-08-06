"""Resuelve producto_id_odoo en listas_precios_historicas.

Cruza el "código" crudo del CSV origen de la Lista Histórica de
Auditoría (numeración interna de Lubrikca) contra los productos reales
de Odoo, en dos pasadas:

  1. Código == default_code de Odoo, normalizando ceros a la izquierda
     (ej. "880" == "0880").
  2. Para lo que no matcheó en (1): nombre de producto normalizado
     (mayúsculas, espacios colapsados) contra product.product.name.

Reporta cuántas filas quedaron sin match en ninguna pasada (no se puede
inventar un match sin más información -- se listan para revisión manual).

Uso:
    railway run --service CxC_Lubrikca --environment production \\
        python scripts/cruzar_codigos_lista_historica.py [--dry-run]
"""

from __future__ import annotations

import re
import sys

sys.path.insert(0, "src")

from cxc.config import AppConfig  # noqa: E402
from cxc.db.postgres_repository import PostgresRepository  # noqa: E402
from cxc.odoo.client import _connect  # noqa: E402


def _normalizar_codigo(code: str) -> str:
    code = (code or "").strip()
    if code.isdigit():
        return str(int(code))
    return code


def _normalizar_nombre(nombre: str) -> str:
    nombre = (nombre or "").upper()
    nombre = re.sub(r"[^A-Z0-9]+", " ", nombre)
    return re.sub(r"\s+", " ", nombre).strip()


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    config = AppConfig.from_env()
    execute = _connect(config.odoo)
    if not execute:
        print("ERROR: sin conexión a Odoo.")
        sys.exit(1)
    repo = PostgresRepository.from_url(config.database.url)

    rows = repo.all_listas_precios_historicas()
    print(f"Filas en listas_precios_historicas: {len(rows)}")

    productos = execute(
        "product.product", "search_read", [[]], {"fields": ["default_code", "name"]}
    )
    codigo_a_id: dict[str, int] = {}
    nombre_a_id: dict[str, int] = {}
    for p in productos:
        dc = _normalizar_codigo(str(p.get("default_code") or ""))
        if dc:
            codigo_a_id.setdefault(dc, p["id"])
        nombre_norm = _normalizar_nombre(str(p.get("name") or ""))
        if nombre_norm:
            nombre_a_id.setdefault(nombre_norm, p["id"])

    match_por_codigo = 0
    match_por_nombre = 0
    sin_match: list[dict[str, str]] = []
    actualizadas: list[dict[str, str]] = []

    for r in rows:
        codigo_norm = _normalizar_codigo(r.get("codigo", ""))
        nombre_norm = _normalizar_nombre(r.get("producto_nombre", ""))

        pid = codigo_a_id.get(codigo_norm)
        origen = "codigo"
        if pid is None:
            pid = nombre_a_id.get(nombre_norm)
            origen = "nombre"

        nueva_fila = dict(r)
        if pid is not None:
            nueva_fila["producto_id_odoo"] = str(pid)
            if origen == "codigo":
                match_por_codigo += 1
            else:
                match_por_nombre += 1
        else:
            nueva_fila["producto_id_odoo"] = ""
            sin_match.append(r)
        actualizadas.append(nueva_fila)

    total = len(rows)
    print(f"\nMatch por código: {match_por_codigo}")
    print(f"Match por nombre (fallback): {match_por_nombre}")
    print(f"Sin match: {len(sin_match)} / {total}")
    if sin_match:
        print("\nFilas sin match (revisar manualmente):")
        for r in sin_match:
            try:
                print(f"  codigo={r.get('codigo')!r} nombre={r.get('producto_nombre')!r}")
            except UnicodeEncodeError:
                print(f"  codigo={r.get('codigo')!r} nombre=<no imprimible>")

    if dry_run:
        print("\n--dry-run: no se escribió nada en la base de datos.")
        return

    repo.replace_listas_precios_historicas(actualizadas)
    print(f"\nOK: {total} filas actualizadas en listas_precios_historicas.")


if __name__ == "__main__":
    main()
