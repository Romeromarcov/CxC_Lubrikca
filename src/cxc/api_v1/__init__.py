"""Núcleo de Backend API v1 (/api/v1/*) para App Móvil Offline y Portal Web/Mobile."""

from .jwt_tokens import (
    AUD_PORTAL,
    AUD_VENDEDOR,
    RateLimiterV1,
    crear_access_token,
    crear_upload_token,
    generar_refresh_token,
    hash_payload,
    hash_token,
    secret_v1_desde_entorno,
    verificar_access_token,
    verificar_upload_token,
)
from .repositorio import (
    ApiV1Conflicto,
    ApiV1Error,
    ApiV1NoAutorizado,
    ApiV1NoEncontrado,
    RepoApiV1SQL,
)
from .storage import AdjuntoError, AdjuntosStorage

__all__ = [
    "AUD_PORTAL",
    "AUD_VENDEDOR",
    "AdjuntoError",
    "AdjuntosStorage",
    "ApiV1Conflicto",
    "ApiV1Error",
    "ApiV1NoAutorizado",
    "ApiV1NoEncontrado",
    "RateLimiterV1",
    "RepoApiV1SQL",
    "crear_access_token",
    "crear_upload_token",
    "generar_refresh_token",
    "hash_payload",
    "hash_token",
    "secret_v1_desde_entorno",
    "verificar_access_token",
    "verificar_upload_token",
]
