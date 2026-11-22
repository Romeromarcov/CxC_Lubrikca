"""Gestión de adjuntos (comprobantes/fotos) con dedupe SHA-256 y soporte S3/R2 o local."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .jwt_tokens import crear_upload_token, verificar_upload_token
from .repositorio import RepoApiV1SQL

MAX_BYTES_ADJUNTO = 10 * 1024 * 1024  # 10 MB
MIMES_PERMITIDOS = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "application/pdf",
    }
)
_EXT_POR_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
_RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AdjuntoError(ValueError):
    """Error de validación de archivo adjunto."""


def _s3_config() -> dict[str, str] | None:
    endpoint = os.environ.get("S3_ENDPOINT_URL", "").strip()
    bucket = os.environ.get("S3_BUCKET", "").strip()
    ak = os.environ.get("S3_ACCESS_KEY_ID", "").strip()
    sk = os.environ.get("S3_SECRET_ACCESS_KEY", "").strip()
    if endpoint and bucket and ak and sk:
        return {
            "endpoint": endpoint.rstrip("/"),
            "bucket": bucket,
            "access_key": ak,
            "secret_key": sk,
            "region": os.environ.get("S3_REGION", "auto").strip() or "auto",
        }
    return None


def _sigv4_presigned_put(
    cfg: dict[str, str],
    *,
    key: str,
    expires: int = 300,
) -> str:
    """Firma URL PUT prefirmada AWS SigV4 compatible con Cloudflare R2 / S3 usando stdlib."""
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    endpoint = cfg["endpoint"]
    host = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    canonical_uri = f"/{cfg['bucket']}/{quote(key.lstrip('/'), safe='/')}"
    scope = f"{date_stamp}/{cfg['region']}/s3/aws4_request"
    query_params = [
        ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
        ("X-Amz-Credential", f"{cfg['access_key']}/{scope}"),
        ("X-Amz-Date", amz_date),
        ("X-Amz-Expires", str(expires)),
        ("X-Amz-SignedHeaders", "host"),
    ]
    canonical_qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in sorted(query_params))
    canonical_req = "\n".join(
        [
            "PUT",
            canonical_uri,
            canonical_qs,
            f"host:{host}\n",
            "host",
            "UNSIGNED-PAYLOAD",
        ]
    )
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_req.encode("utf-8")).hexdigest(),
        ]
    )

    def _hmac(k: bytes, msg: str) -> bytes:
        return hmac.new(k, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(("AWS4" + cfg["secret_key"]).encode("utf-8"), date_stamp)
    k_region = _hmac(k_date, cfg["region"])
    k_service = _hmac(k_region, "s3")
    k_signing = _hmac(k_service, "aws4_request")
    sig = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{endpoint}{canonical_uri}?{canonical_qs}&X-Amz-Signature={sig}"


class AdjuntosStorage:
    """Servicio de carga prefirmada y almacenamiento de comprobantes v1."""

    def __init__(
        self,
        repo: RepoApiV1SQL,
        secret_key: str,
        *,
        local_dir: Path | str | None = None,
    ) -> None:
        self._repo = repo
        self._secret = secret_key
        env_dir = os.environ.get("CXC_ADJUNTOS_DIR", "").strip()
        self._local_dir = Path(local_dir or env_dir or "data/adjuntos_v1")

    def preparar_presign(
        self,
        *,
        mime: str,
        bytes_len: int,
        sha256_hex: str,
        nombre_archivo: str = "",
        actor_tipo: str,
        actor_id: str,
    ) -> dict[str, Any]:
        mime_norm = mime.strip().lower()
        if mime_norm not in MIMES_PERMITIDOS:
            raise AdjuntoError(f"Tipo MIME no permitido: {mime!r}.")
        if bytes_len <= 0 or bytes_len > MAX_BYTES_ADJUNTO:
            raise AdjuntoError(f"Tamaño inválido ({bytes_len} bytes; máximo {MAX_BYTES_ADJUNTO}).")
        sha_norm = sha256_hex.strip().lower()
        if not _RE_SHA256.match(sha_norm):
            raise AdjuntoError("sha256 inválido (debe ser hex de 64 caracteres).")

        existente = self._repo.buscar_adjunto_por_sha256(sha_norm)
        if existente is not None:
            return {
                "ya_existe": True,
                "adjunto_id": int(existente["adjunto_id"]),
                "storage_key": existente["storage_key"],
                "storage_backend": existente["storage_backend"],
                "upload_method": None,
                "upload_url": None,
                "sha256": sha_norm,
            }

        ext = _EXT_POR_MIME.get(mime_norm, "bin")
        fecha_dir = datetime.now().strftime("%Y/%m")
        key = f"adjuntos/{fecha_dir}/{sha_norm[:16]}-{uuid.uuid4().hex[:8]}.{ext}"
        s3 = _s3_config()
        if s3 is not None:
            put_url = _sigv4_presigned_put(s3, key=key, expires=300)
            reg = self._repo.registrar_adjunto(
                sha256_hex=sha_norm,
                storage_key=key,
                storage_backend="s3_r2",
                mime=mime_norm,
                bytes_len=bytes_len,
                nombre_archivo=nombre_archivo,
                subido_por_tipo=actor_tipo,
                subido_por_id=actor_id,
            )
            return {
                "ya_existe": False,
                "adjunto_id": int(reg["adjunto_id"]),
                "storage_key": key,
                "storage_backend": "s3_r2",
                "upload_method": "PUT",
                "upload_url": put_url,
                "sha256": sha_norm,
            }

        upload_tok = crear_upload_token(
            storage_key=key,
            mime=mime_norm,
            max_bytes=bytes_len,
            sha256=sha_norm,
            actor_tipo=actor_tipo,
            actor_id=actor_id,
            secret_key=self._secret,
        )
        return {
            "ya_existe": False,
            "adjunto_id": None,
            "storage_key": key,
            "storage_backend": "local",
            "upload_method": "PUT",
            "upload_url": f"/api/v1/adjuntos/upload/{upload_tok}",
            "sha256": sha_norm,
        }

    def recibir_upload_local(
        self,
        *,
        upload_token: str,
        contenido: bytes,
        content_type: str = "",
        entidad_tipo: str = "",
        entidad_id: str = "",
    ) -> dict[str, Any]:
        meta = verificar_upload_token(upload_token, self._secret)
        if meta is None:
            raise AdjuntoError("Token de carga inválido o expirado.")
        if not contenido or len(contenido) > MAX_BYTES_ADJUNTO:
            raise AdjuntoError("Contenido vacío o excede el máximo permitido.")
        if int(meta.get("b") or 0) != len(contenido):
            raise AdjuntoError("El tamaño recibido no coincide con el tamaño declarado.")
        sha_real = hashlib.sha256(contenido).hexdigest().lower()
        if sha_real != str(meta.get("s") or "").lower():
            raise AdjuntoError("El hash SHA-256 del archivo no coincide con el declarado.")
        mime_decl = str(meta.get("m") or "")
        ct_clean = content_type.split(";")[0].strip().lower()
        if (
            ct_clean
            and ct_clean != mime_decl
            and not (ct_clean == "image/jpg" and mime_decl == "image/jpeg")
        ):
            raise AdjuntoError("Content-Type no coincide con el MIME autorizado.")

        existente = self._repo.buscar_adjunto_por_sha256(sha_real)
        if existente is not None:
            return existente

        key = str(meta["k"]).lstrip("/")
        destino = (self._local_dir / key).resolve()
        base_resolved = self._local_dir.resolve()
        if base_resolved not in destino.parents:
            raise AdjuntoError("Ruta de almacenamiento inválida.")
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)

        return self._repo.registrar_adjunto(
            sha256_hex=sha_real,
            storage_key=key,
            storage_backend="local",
            mime=mime_decl,
            bytes_len=len(contenido),
            nombre_archivo=Path(key).name,
            subido_por_tipo=str(meta.get("at") or ""),
            subido_por_id=str(meta.get("ai") or ""),
            entidad_tipo=entidad_tipo,
            entidad_id=entidad_id,
        )
