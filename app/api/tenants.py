"""Admin endpoints: tenant CRUD. Protected by MASTER_API_KEY."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_master_key
from app.audit import log_event
from app.db.models import Tenant
from app.db.session import get_session
from app.domain.schemas import TenantCreate, TenantCreateResponse, TenantRead
from app.security import generate_api_key, hash_api_key

router = APIRouter(prefix="/admin/tenants", tags=["admin"], dependencies=[Depends(require_master_key)])


def _serialize(t: Tenant) -> TenantRead:
    return TenantRead(
        id=str(t.id),
        name=t.name,
        nit=t.nit,
        legal_name=t.legal_name,
        commercial_name=t.commercial_name,
        provider=t.provider,
        ambiente=t.ambiente,
        is_active=t.is_active,
        created_at=t.created_at,
    )


@router.get("/", response_model=list[TenantRead])
def list_tenants(db: Session = Depends(get_session)) -> list[TenantRead]:
    rows = db.execute(select(Tenant).order_by(Tenant.created_at.desc())).scalars().all()
    return [_serialize(t) for t in rows]


@router.post("/", response_model=TenantCreateResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(payload: TenantCreate, db: Session = Depends(get_session)) -> TenantCreateResponse:
    existing = db.execute(select(Tenant).where(Tenant.name == payload.name)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Tenant name already exists.")

    plain_key = generate_api_key()
    tenant = Tenant(
        name=payload.name,
        api_key_hash=hash_api_key(plain_key),
        nit=payload.nit,
        legal_name=payload.legal_name,
        commercial_name=payload.commercial_name,
        address=payload.address,
        postal_code=payload.postal_code,
        municipio=payload.municipio,
        departamento=payload.departamento,
        country=payload.country,
        affiliation_iva=payload.affiliation_iva,
        establishment_code=payload.establishment_code,
        serie_fel=payload.serie_fel,
        contact_email=payload.contact_email,
        provider=payload.provider,
        provider_config=payload.provider_config,
        ambiente=payload.ambiente,
    )
    db.add(tenant)
    db.flush()
    log_event(db, tenant_id=tenant.id, event="tenant.created", payload={"name": tenant.name})
    db.commit()
    db.refresh(tenant)
    return TenantCreateResponse(tenant=_serialize(tenant), api_key=plain_key)


@router.post("/{tenant_id}/rotate-key", response_model=TenantCreateResponse)
def rotate_api_key(tenant_id: UUID, db: Session = Depends(get_session)) -> TenantCreateResponse:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found.")
    plain_key = generate_api_key()
    tenant.api_key_hash = hash_api_key(plain_key)
    log_event(db, tenant_id=tenant.id, event="tenant.key_rotated", payload=None)
    db.commit()
    db.refresh(tenant)
    return TenantCreateResponse(tenant=_serialize(tenant), api_key=plain_key)


@router.post("/{tenant_id}/deactivate", response_model=TenantRead)
def deactivate_tenant(tenant_id: UUID, db: Session = Depends(get_session)) -> TenantRead:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found.")
    tenant.is_active = False
    log_event(db, tenant_id=tenant.id, event="tenant.deactivated", payload=None)
    db.commit()
    db.refresh(tenant)
    return _serialize(tenant)
