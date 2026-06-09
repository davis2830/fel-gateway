"""FastAPI dependencies: tenant auth via API key, master-key auth for admin."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Tenant
from app.db.session import get_session
from app.security import verify_api_key


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() not in {"bearer", "apikey"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1].strip()


def get_current_tenant(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
) -> Tenant:
    """Resolve a tenant from the ``Authorization: Bearer <api_key>`` header."""
    key = _extract_bearer(authorization)

    # We cannot index argon2 hashes, but the active-tenant cardinality is tiny
    # (≤ a few dozen). Fetching all active and verifying is fine and constant-
    # time relative to the threat model.
    rows = db.execute(select(Tenant).where(Tenant.is_active.is_(True))).scalars().all()
    for tenant in rows:
        if verify_api_key(key, tenant.api_key_hash):
            return tenant

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_master_key(authorization: str | None = Header(default=None)) -> None:
    """Used on admin endpoints. Compares against ``MASTER_API_KEY``."""
    key = _extract_bearer(authorization)
    expected = get_settings().master_api_key
    if not expected or key != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Master key required.",
        )
