"""Append-only audit helpers."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def log_event(
    db: Session,
    *,
    tenant_id: UUID | None = None,
    invoice_id: UUID | None = None,
    event: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Insert an audit event row. Caller is responsible for committing."""
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            invoice_id=invoice_id,
            event=event,
            payload=payload,
        )
    )
