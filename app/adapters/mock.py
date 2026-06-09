"""Local mock certificador — no network, deterministic output.

Used for development & testing. Activated by setting ``provider="mock"``
on the tenant.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.adapters.base import (
    AnnulResult,
    CertificadorBase,
    CertifyResult,
    NITLookupResult,
)


class MockCertificador(CertificadorBase):
    nombre = "MOCK"

    async def certificar(self, *, xml: str, payload: dict, tenant) -> CertifyResult:
        return CertifyResult(
            ok=True,
            uuid_sat=str(uuid.uuid4()).upper(),
            serie="A",
            numero_autorizacion=str(uuid.uuid4()).upper(),
            fecha_certificacion=datetime.now(UTC),
            raw_response={"mock": True, "type": payload.get("type")},
        )

    async def anular(self, *, uuid_sat: str, motivo: str, tenant) -> AnnulResult:
        return AnnulResult(ok=True, uuid_sat=uuid_sat, raw_response={"mock": True, "reason": motivo})

    async def consultar_nit(self, nit: str) -> NITLookupResult:
        return NITLookupResult(
            ok=True,
            nombre=f"Contribuyente Mock {nit}",
            direccion="Ciudad",
            raw_response={"mock": True},
        )
