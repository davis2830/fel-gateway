"""Invoice REST endpoints (per-tenant via API key)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant
from app.db.models import Invoice, Tenant
from app.db.session import get_session
from app.domain.schemas import (
    AnnulRequest,
    InvoiceCreate,
    InvoiceRead,
)
from app.services import certify_now, get_invoice_for_tenant, queue_invoice

router = APIRouter(prefix="/v1/invoices", tags=["invoices"])


def _to_read(inv: Invoice) -> InvoiceRead:
    return InvoiceRead(
        id=str(inv.id),
        tenant_id=str(inv.tenant_id),
        external_ref=inv.external_ref,
        status=inv.status,
        tipo_dte=inv.tipo_dte,
        fecha_emision=inv.fecha_emision,
        uuid_sat=inv.uuid_sat,
        serie=inv.serie,
        numero_autorizacion=inv.numero_autorizacion,
        fecha_certificacion=inv.fecha_certificacion,
        total=inv.total,
        monto_iva=inv.monto_iva,
        error_message=inv.error_message,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )


@router.post("", response_model=InvoiceRead, status_code=status.HTTP_202_ACCEPTED)
def create_invoice(
    payload: InvoiceCreate,
    sync: bool = Query(
        default=False,
        description="If true, certify synchronously instead of queueing on Celery.",
    ),
    tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_session),
) -> InvoiceRead:
    """Queue an invoice for certification.

    Default behaviour: persists ``PENDING`` row and dispatches a Celery
    task. The caller polls ``GET /v1/invoices/{id}`` for the final state.

    With ``?sync=true``: certifies inline (useful for tests, mock provider,
    or environments without a worker). The response will already reflect
    ``CERTIFIED`` / ``REJECTED``.
    """
    invoice = queue_invoice(db, tenant, payload)

    # Idempotency: if queue_invoice returned an already-finished row, skip
    # re-certifying. The caller gets the same UUID back.
    if invoice.status in ("CERTIFIED", "REJECTED", "ANNULLED"):
        return _to_read(invoice)

    if sync:
        invoice = certify_now(db, tenant, invoice)
    else:
        # Lazy import so the API service can run without celery installed.
        from app.workers.tasks import certify_invoice_task

        certify_invoice_task.delay(str(invoice.id))

    return _to_read(invoice)


@router.get("/{invoice_id}", response_model=InvoiceRead)
def get_invoice(
    invoice_id: UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_session),
) -> InvoiceRead:
    inv = get_invoice_for_tenant(db, tenant, invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    return _to_read(inv)


@router.get("", response_model=list[InvoiceRead])
def list_invoices(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_session),
) -> list[InvoiceRead]:
    stmt = (
        select(Invoice)
        .where(Invoice.tenant_id == tenant.id)
        .order_by(Invoice.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(Invoice.status == status_filter.upper())
    rows = db.execute(stmt).scalars().all()
    return [_to_read(r) for r in rows]


@router.post("/{invoice_id}/annul", response_model=InvoiceRead)
def annul_invoice(
    invoice_id: UUID,
    payload: AnnulRequest,
    sync: bool = Query(default=False),
    tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_session),
) -> InvoiceRead:
    inv = get_invoice_for_tenant(db, tenant, invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    if sync:
        from app.services import annul_now

        inv = annul_now(db, tenant, inv, payload)
    else:
        from app.workers.tasks import annul_invoice_task

        annul_invoice_task.delay(str(inv.id), payload.motivo)
    return _to_read(inv)
