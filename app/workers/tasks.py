"""Celery tasks: certify + annul with exponential backoff retries."""
from __future__ import annotations

import logging
from uuid import UUID

from celery import Task

from app.adapters import ProviderTransientError
from app.audit import log_event
from app.db.models import Invoice
from app.db.session import session_scope
from app.domain.schemas import AnnulRequest
from app.services import annul_now, certify_now
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


class _BaseInvoiceTask(Task):
    """Common Celery task that adds DLQ logging on final failure."""

    autoretry_for = (ProviderTransientError,)
    retry_backoff = True
    retry_backoff_max = 600  # cap at 10 min
    retry_jitter = True
    max_retries = 10

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # Final failure (after retries). Persist as REJECTED + audit so the
        # operator can investigate without digging into Celery logs.
        invoice_id = args[0] if args else None
        if invoice_id is None:
            return
        try:
            with session_scope() as db:
                inv = db.get(Invoice, UUID(str(invoice_id)))
                if inv is None:
                    return
                inv.status = "REJECTED"
                inv.error_message = (
                    f"Falló tras {self.request.retries} reintentos: {exc}"
                )
                log_event(
                    db,
                    tenant_id=inv.tenant_id,
                    invoice_id=inv.id,
                    event="invoice.dlq",
                    payload={"error": str(exc), "task": self.name},
                )
                db.commit()
        except Exception:  # pragma: no cover - defensive
            log.exception("Failed to record DLQ for task %s", task_id)


@celery_app.task(bind=True, base=_BaseInvoiceTask, name="invoices.certify")
def certify_invoice_task(self, invoice_id: str) -> str:
    log.info("certify_invoice_task invoice=%s attempt=%s", invoice_id, self.request.retries)
    with session_scope() as db:
        inv = db.get(Invoice, UUID(invoice_id))
        if inv is None:
            log.warning("certify_invoice_task: invoice %s not found", invoice_id)
            return "NOT_FOUND"
        tenant = inv.tenant
        certify_now(db, tenant, inv)
        return inv.status


@celery_app.task(bind=True, base=_BaseInvoiceTask, name="invoices.annul")
def annul_invoice_task(self, invoice_id: str, motivo: str) -> str:
    log.info("annul_invoice_task invoice=%s attempt=%s", invoice_id, self.request.retries)
    with session_scope() as db:
        inv = db.get(Invoice, UUID(invoice_id))
        if inv is None:
            return "NOT_FOUND"
        tenant = inv.tenant
        annul_now(db, tenant, inv, AnnulRequest(motivo=motivo))
        return inv.status
