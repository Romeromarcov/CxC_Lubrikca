"""Migración idempotente de adjuntos locales v1 hacia Cloudflare R2 / S3 compatible.

Uso:
    python scripts/migrar_adjuntos_r2.py           # --dry-run por defecto
    python scripts/migrar_adjuntos_r2.py --apply   # sube archivos y actualiza backend a 's3_r2'
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select

from cxc.api_v1.jwt_tokens import secret_v1_desde_entorno
from cxc.api_v1.repositorio import RepoApiV1SQL
from cxc.api_v1.storage import AdjuntosStorage
from cxc.config import DatabaseConfig, _maybe_load_dotenv
from cxc.db.engine import make_engine
from cxc.db.schema import adjuntos_objeto


def migrar_adjuntos_locales_a_r2(
    engine: Engine,
    storage: AdjuntosStorage,
    *,
    dry_run: bool = True,
    http_put: Callable[[str, bytes, str], int] | None = None,
) -> dict[str, Any]:
    """Migra registros ``storage_backend == 'local'`` hacia R2/S3 de manera idempotente."""
    repo = RepoApiV1SQL(engine)
    with engine.connect() as conn:
        filas = conn.execute(
            select(adjuntos_objeto)
            .where(adjuntos_objeto.c.storage_backend == "local")
            .order_by(adjuntos_objeto.c.adjunto_id.asc())
        ).all()

    est = storage.estado_storage()
    if not dry_run and not est["s3_configurado"]:
        raise RuntimeError(
            "No se puede aplicar --apply sin configurar S3_ENDPOINT_URL / S3_BUCKET."
        )

    evaluados = len(filas)
    migrados = 0
    faltantes = 0
    for f in filas:
        aid = int(f.adjunto_id)
        key = str(f.storage_key)
        path = (Path(est["local_dir"]) / key.lstrip("/")).resolve()
        if not path.is_file():
            faltantes += 1
            continue
        if dry_run:
            migrados += 1
            continue
        storage.subir_bytes_r2(
            key=key,
            contenido=path.read_bytes(),
            mime=str(f.mime or "application/octet-stream"),
            http_put=http_put,
        )
        repo.actualizar_backend_adjunto(aid, "s3_r2")
        migrados += 1

    return {
        "dry_run": dry_run,
        "s3_configurado": est["s3_configurado"],
        "evaluados": evaluados,
        "migrados": migrados,
        "faltantes_en_disco": faltantes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrar adjuntos locales v1 hacia Cloudflare R2.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Ejecuta la subida real y actualiza storage_backend='s3_r2'.",
    )
    args = parser.parse_args()
    _maybe_load_dotenv()
    db_url = DatabaseConfig.from_env().url
    if not db_url:
        raise SystemExit("Falta DATABASE_URL en entorno.")
    engine = make_engine(db_url)
    repo = RepoApiV1SQL(engine)
    storage = AdjuntosStorage(repo, secret_v1_desde_entorno("migrate-key"))
    res = migrar_adjuntos_locales_a_r2(engine, storage, dry_run=not args.apply)
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
