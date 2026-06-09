"""Service layer: orchestrates DB + adapter for invoice operations.

These functions are reused by both the synchronous API path (when
the gateway is run without a worker) and the Celery task path.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.adapters import (
    ProviderConfigError,
    ProviderTransientError,
    get_certificador,
)
from app.audit import log_event
from app.db.models import Invoice, Tenant
from app.domain.schemas import AnnulRequest, InvoiceCreate
from app.domain.xml_builder import build_xml_dte

log = logging.getLogger(__name__)


def queue_invoice(db: Session, tenant: Tenant, payload: InvoiceCreate) -> Invoice:
    """Persist a PENDING invoice + audit. Returns the row.

    Idempotency: if the same ``(tenant, external_ref)`` already has an
    active row (PENDING or CERTIFIED), return it instead of creating a new one.
    """
    if payload.external_ref:
        existing = (
            db.query(Invoice)
            .filter(
                Invoice.tenant_id == tenant.id,
                Invoice.external_ref == payload.external_ref,
                Invoice.status.in_(("PENDING", "CERTIFIED")),
            )
            .first()
        )
        if existing is not None:
            return existing

    request_payload = payload.model_dump(mode="json")

    invoice = Invoice(
        tenant_id=tenant.id,
        external_ref=payload.external_ref,
        status="PENDING",
        tipo_dte=payload.tipo,
        fecha_emision=payload.fecha_emision,
        moneda=payload.moneda,
        iva_incluido=payload.iva_incluido,
        tasa_iva=Decimal(payload.tasa_iva),
        request_payload=request_payload,
        total=Decimal(payload.totales.total),
        monto_iva=Decimal(payload.totales.monto_iva),
    )
    db.add(invoice)
    db.flush()
    log_event(
        db,
        tenant_id=tenant.id,
        invoice_id=invoice.id,
        event="invoice.queued",
        payload={"tipo": payload.tipo, "external_ref": payload.external_ref},
    )
    db.commit()
    db.refresh(invoice)
    return invoice


def certify_now(db: Session, tenant: Tenant, invoice: Invoice) -> Invoice:
    """Synchronously certify an invoice. Used by the Celery task and by tests.

    Mutates the invoice row in place. Re-raises ``ProviderTransientError``
    for the worker's retry decorator; converts ``ProviderConfigError`` into
    a REJECTED status (no retry).
    """
    payload_dict = dict(invoice.request_payload)
    payload_obj = InvoiceCreate.model_validate(payload_dict)
    xml = build_xml_dte(payload_obj, tenant)
    invoice.xml_local = xml
    db.flush()

    cert = get_certificador(tenant.provider, tenant.provider_config or {}, tenant.ambiente)

    # FELplex expects its own JSON shape; building it here keeps the
    # adapter ignorant of our schema.
    if tenant.provider == "felplex":
        from app.adapters.felplex import build_felplex_payload

        provider_payload = build_felplex_payload(payload_dict)
    else:
        provider_payload = payload_dict

    try:
        result = asyncio.run(
            cert.certificar(xml=xml, payload=provider_payload, tenant=tenant)
        )
    except ProviderConfigError as exc:
        log.warning("Config error tenant=%s invoice=%s: %s", tenant.id, invoice.id, exc)
        invoice.status = "REJECTED"
        invoice.error_message = str(exc)
        log_event(
            db,
            tenant_id=tenant.id,
            invoice_id=invoice.id,
            event="invoice.config_error",
            payload={"error": str(exc)},
        )
        db.commit()
        return invoice
    except ProviderTransientError:
        log.exception("Transient error tenant=%s invoice=%s", tenant.id, invoice.id)
        log_event(
            db,
            tenant_id=tenant.id,
            invoice_id=invoice.id,
            event="invoice.transient_error",
            payload=None,
        )
        db.commit()
        raise

    invoice.provider_response = result.raw_response
    if result.ok:
        invoice.status = "CERTIFIED"
        invoice.uuid_sat = result.uuid_sat
        invoice.serie = result.serie
        invoice.numero_autorizacion = result.numero_autorizacion
        invoice.fecha_certificacion = result.fecha_certificacion
        invoice.error_message = None
        log_event(
            db,
            tenant_id=tenant.id,
            invoice_id=invoice.id,
            event="invoice.certified",
            payload={"uuid": result.uuid_sat},
        )
    else:
        invoice.status = "REJECTED"
        invoice.error_message = result.error
        log_event(
            db,
            tenant_id=tenant.id,
            invoice_id=invoice.id,
            event="invoice.rejected",
            payload={"error": result.error},
        )

    db.commit()
    db.refresh(invoice)
    return invoice


def annul_now(
    db: Session,
    tenant: Tenant,
    invoice: Invoice,
    req: AnnulRequest,
) -> Invoice:
    if invoice.status != "CERTIFIED":
        invoice.error_message = (
            f"No se puede anular en estado {invoice.status} (debe estar CERTIFIED)."
        )
        return invoice
    if not invoice.uuid_sat:
        invoice.error_message = "Falta UUID SAT para anular."
        return invoice

    cert = get_certificador(tenant.provider, tenant.provider_config or {}, tenant.ambiente)
    try:
        result = asyncio.run(
            cert.anular(uuid_sat=invoice.uuid_sat, motivo=req.motivo, tenant=tenant)
        )
    except ProviderConfigError as exc:
        invoice.error_message = str(exc)
        db.commit()
        return invoice
    except ProviderTransientError:
        log_event(
            db,
            tenant_id=tenant.id,
            invoice_id=invoice.id,
            event="invoice.annul_transient_error",
            payload=None,
        )
        db.commit()
        raise

    if result.ok:
        invoice.status = "ANNULLED"
        invoice.annul_reason = req.motivo
        invoice.annulled_at = datetime.now(UTC)
        log_event(
            db,
            tenant_id=tenant.id,
            invoice_id=invoice.id,
            event="invoice.annulled",
            payload={"reason": req.motivo},
        )
    else:
        invoice.error_message = result.error
        log_event(
            db,
            tenant_id=tenant.id,
            invoice_id=invoice.id,
            event="invoice.annul_rejected",
            payload={"error": result.error},
        )

    db.commit()
    db.refresh(invoice)
    return invoice


def get_invoice_for_tenant(db: Session, tenant: Tenant, invoice_id: UUID) -> Invoice | None:
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.tenant_id != tenant.id:
        return None
    return inv
