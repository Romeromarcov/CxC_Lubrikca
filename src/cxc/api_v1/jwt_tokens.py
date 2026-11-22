"""Tokens JWT (HS256 stdlib), tokens opacos, upload tokens y rate limiting para API v1."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict
from typing import Any

AUD_VENDEDOR = "lubrikca-seller-app"
AUD_PORTAL = "lubrikca-client-portal"
AUDIENCIAS_V1 = frozenset({AUD_VENDEDOR, AUD_PORTAL})

DEFAULT_ACCESS_TTL_SECONDS = 900  # 15 minutos
DEFAULT_REFRESH_TTL_SECONDS = 86400 * 30  # 30 días
DEFAULT_UPLOAD_TTL_SECONDS = 300  # 5 minutos


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode((txt + pad).encode("ascii"))


def secret_v1_desde_entorno(session_secret: str) -> str:
    """Deriva la clave JWT de API v1 de forma aislada respecto a cookies de sesión."""
    propio = os.environ.get("API_V1_JWT_SECRET", "").strip()
    if propio:
        return propio
    return hmac.new(
        session_secret.encode("utf-8"),
        b"cxc-api-v1-jwt",
        hashlib.sha256,
    ).hexdigest()


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


def hash_payload(data: Any) -> str:
    serial = json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def generar_refresh_token() -> str:
    return "rt1_" + secrets.token_urlsafe(40)


def crear_access_token(
    *,
    subject: str,
    audience: str,
    secret_key: str,
    claims: dict[str, Any] | None = None,
    ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
) -> str:
    if audience not in AUDIENCIAS_V1:
        raise ValueError(f"Audiencia JWT inválida: {audience!r}")
    ahora = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        **(claims or {}),
        "sub": subject,
        "aud": audience,
        "iss": "cxc-lubrikca-v1",
        "iat": ahora,
        "exp": ahora + max(60, int(ttl_seconds)),
        "jti": secrets.token_hex(12),
    }
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{h_b64}.{p_b64}.{_b64url_encode(sig)}"


def verificar_access_token(
    token: str,
    secret_key: str,
    *,
    expected_audience: str | None = None,
) -> dict[str, Any] | None:
    if not token or token.count(".") != 2:
        return None
    try:
        h_b64, p_b64, s_b64 = token.split(".", 2)
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        expected_sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        got_sig = _b64url_decode(s_b64)
        if not hmac.compare_digest(expected_sig, got_sig):
            return None
        header = json.loads(_b64url_decode(h_b64).decode("utf-8"))
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            return None
        payload = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        exp = int(payload.get("exp") or 0)
        if exp < int(time.time()):
            return None
        aud = str(payload.get("aud") or "")
        if aud not in AUDIENCIAS_V1:
            return None
        if expected_audience is not None and aud != expected_audience:
            return None
        if not str(payload.get("sub") or "").strip():
            return None
        return payload
    except Exception:
        return None


def crear_upload_token(
    *,
    storage_key: str,
    mime: str,
    max_bytes: int,
    sha256: str,
    actor_tipo: str,
    actor_id: str,
    secret_key: str,
    ttl_seconds: int = DEFAULT_UPLOAD_TTL_SECONDS,
) -> str:
    ahora = int(time.time())
    payload = {
        "k": storage_key,
        "m": mime,
        "b": int(max_bytes),
        "s": sha256,
        "at": actor_tipo,
        "ai": actor_id,
        "exp": ahora + max(60, int(ttl_seconds)),
    }
    p_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(
        (secret_key + "|upload").encode("utf-8"), p_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{p_b64}.{_b64url_encode(sig)}"


def verificar_upload_token(token: str, secret_key: str) -> dict[str, Any] | None:
    if not token or token.count(".") != 1:
        return None
    try:
        p_b64, s_b64 = token.split(".", 1)
        expected = hmac.new(
            (secret_key + "|upload").encode("utf-8"), p_b64.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(s_b64)):
            return None
        payload = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        if not isinstance(payload, dict) or int(payload.get("exp") or 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


class RateLimiterV1:
    """Rate limiter en memoria por ventana deslizante para endpoints de autenticación v1."""

    def __init__(self, *, window_seconds: int = 300, max_attempts: int = 10) -> None:
        self._window = window_seconds
        self._max = max_attempts
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)

    def registrar_o_bloquear(self, clave_scope: str, identidad: str) -> bool:
        """Devuelve True si puede intentar, False si superó el límite vigente."""
        clave = (clave_scope, identidad.strip().lower())
        ahora = time.monotonic()
        corte = ahora - self._window
        with self._lock:
            hist = [t for t in self._hits[clave] if t >= corte]
            if len(hist) >= self._max:
                self._hits[clave] = hist
                return False
            hist.append(ahora)
            self._hits[clave] = hist
            return True

    def limpiar(self, clave_scope: str, identidad: str) -> None:
        clave = (clave_scope, identidad.strip().lower())
        with self._lock:
            self._hits.pop(clave, None)

    def clear_all(self) -> None:
        with self._lock:
            self._hits.clear()
