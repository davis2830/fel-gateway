"""Certificador adapter registry — pluggable FEL providers."""
from app.adapters.base import (
    AnnulResult,
    CertificadorBase,
    CertifyResult,
    NITLookupResult,
    ProviderConfigError,
    ProviderTransientError,
)
from app.adapters.felplex import FELplexCertificador
from app.adapters.mock import MockCertificador

_REGISTRY: dict[str, type[CertificadorBase]] = {
    "mock": MockCertificador,
    "felplex": FELplexCertificador,
}


def get_certificador(provider: str, config: dict, ambiente: str = "PRUEBAS") -> CertificadorBase:
    """Factory: returns an instantiated certificador for the given provider."""
    cls = _REGISTRY.get((provider or "mock").lower(), MockCertificador)
    return cls(config=config, ambiente=ambiente)


__all__ = [
    "AnnulResult",
    "CertificadorBase",
    "CertifyResult",
    "NITLookupResult",
    "ProviderConfigError",
    "ProviderTransientError",
    "get_certificador",
]
