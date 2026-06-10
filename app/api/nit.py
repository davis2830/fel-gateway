"""NIT lookup endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.adapters import ProviderConfigError, get_certificador
from app.api.deps import get_current_tenant
from app.db.models import Tenant

router = APIRouter(prefix="/v1/nit", tags=["nit"])


@router.get("/{nit}")
async def lookup_nit(
    nit: str, tenant: Tenant = Depends(get_current_tenant)
) -> dict:
    """Lookup contributor name/address by NIT via the tenant's provider."""
    cert = get_certificador(tenant.provider, tenant.provider_config or {}, tenant.ambiente)
    try:
        result = await cert.consultar_nit(nit)
    except ProviderConfigError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "ok": result.ok,
        "nit": nit,
        "nombre": result.nombre,
        "direccion": result.direccion,
        "error": result.error,
    }
