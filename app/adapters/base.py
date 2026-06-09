"""Adapter contract: every certificador implements ``certificar`` / ``anular``.

All result objects are simple dataclasses (not Pydantic) — adapters are
internal and call sites need fast attribute access.

Two specialized exceptions let workers decide whether to retry:

- ``ProviderConfigError``: caller error (bad config, missing API key,
  malformed payload). Do NOT retry.
- ``ProviderTransientError``: network blip, 5xx, timeout. Retry with backoff.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class ProviderConfigError(Exception):
    """Non-retryable: caller's config or payload is wrong."""


class ProviderTransientError(Exception):
    """Retryable: provider is temporarily unavailable."""


@dataclass
class CertifyResult:
    ok: bool
    uuid_sat: str | None = None
    serie: str | None = None
    numero_autorizacion: str | None = None
    fecha_certificacion: datetime | None = None
    raw_response: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class AnnulResult:
    ok: bool
    uuid_sat: str | None = None
    raw_response: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class NITLookupResult:
    ok: bool
    nombre: str | None = None
    direccion: str | None = None
    raw_response: dict[str, Any] | None = None
    error: str | None = None


class CertificadorBase:
    """All adapters must subclass this and implement the three operations."""

    nombre: str = "BASE"

    def __init__(self, config: dict[str, Any], ambiente: str = "PRUEBAS") -> None:
        self.config = config or {}
        self.ambiente = ambiente

    async def certificar(self, *, xml: str, payload: dict, tenant) -> CertifyResult:
        raise NotImplementedError

    async def anular(self, *, uuid_sat: str, motivo: str, tenant) -> AnnulResult:
        raise NotImplementedError

    async def consultar_nit(self, nit: str) -> NITLookupResult:
        raise NotImplementedError
