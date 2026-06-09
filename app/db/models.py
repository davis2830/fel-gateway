"""ORM models — tenants, invoices (mirror of certified docs), audit events."""
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Tenant(Base):
    """A consumer of the gateway (e.g. SistemaOVO, Taller_mecanica_HG)."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)

    # API key authentication. Stored as argon2 hash; the plain key is shown
    # exactly once when the tenant is created.
    api_key_hash: Mapped[str] = mapped_column(String(255))

    # Issuer (emisor SAT) — the tenant's own fiscal identity.
    nit: Mapped[str] = mapped_column(String(20))
    legal_name: Mapped[str] = mapped_column(String(200))
    commercial_name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500))
    postal_code: Mapped[str] = mapped_column(String(10), default="01001")
    municipio: Mapped[str] = mapped_column(String(100), default="GUATEMALA")
    departamento: Mapped[str] = mapped_column(String(100), default="GUATEMALA")
    country: Mapped[str] = mapped_column(String(2), default="GT")
    affiliation_iva: Mapped[str] = mapped_column(String(10), default="GEN")
    establishment_code: Mapped[str] = mapped_column(String(10), default="1")
    serie_fel: Mapped[str] = mapped_column(String(20), default="A")
    contact_email: Mapped[str] = mapped_column(String(254), default="")

    # Provider config (which certificador + its credentials).
    provider: Mapped[str] = mapped_column(String(30))  # mock / felplex / infile / digifact
    provider_config: Mapped[dict] = mapped_column(JSON, default=dict)
    ambiente: Mapped[str] = mapped_column(String(15), default="PRUEBAS")  # PRUEBAS / PRODUCCION

    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="tenant")


class Invoice(Base):
    """Mirror of a certified DTE — one row per certify call (success or failure).

    The gateway is the source of truth for what was sent to SAT and what was
    returned. Consumer apps reference invoices by ``id`` (gateway UUID) or by
    ``uuid_sat`` once certified.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        # One ACTIVE document per (tenant, external_ref) — prevents the
        # consumer from accidentally certifying the same source order twice.
        UniqueConstraint("tenant_id", "external_ref", "status", name="uq_invoice_active_external_ref"),
        Index("ix_invoice_uuid_sat", "uuid_sat"),
        Index("ix_invoice_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    tenant: Mapped[Tenant] = relationship(back_populates="invoices")

    # Caller-supplied opaque reference (e.g. SistemaOVO's invoice UUID).
    # Used for idempotency.
    external_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # PENDING (queued) | CERTIFIED (SAT OK) | REJECTED (SAT NO) | ANNULLED
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)

    tipo_dte: Mapped[str] = mapped_column(String(10))  # FACT / FCAM / FPEQ / NCRE / NDEB
    fecha_emision: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    moneda: Mapped[str] = mapped_column(String(3), default="GTQ")
    iva_incluido: Mapped[bool] = mapped_column(default=True)
    tasa_iva: Mapped[float] = mapped_column(Numeric(6, 4), default=0.12)

    # Snapshot of the request payload (receiver + items + totals + metadata).
    request_payload: Mapped[dict] = mapped_column(JSON)
    # XML built locally before sending (always present once the row is touched
    # by the worker). Kept for compliance + reproducibility.
    xml_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provider raw response (for audit + debugging).
    provider_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    uuid_sat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serie: Mapped[str | None] = mapped_column(String(20), nullable=True)
    numero_autorizacion: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fecha_certificacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    total: Mapped[float] = mapped_column(Numeric(14, 2))
    monto_iva: Mapped[float] = mapped_column(Numeric(14, 2))

    annulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    annul_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditEvent(Base):
    """Append-only event log for every state change on a tenant or invoice."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_tenant_at", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True, native_uuid=True), nullable=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True, native_uuid=True), nullable=True)
    event: Mapped[str] = mapped_column(String(40))  # tenant.created / invoice.queued / ...
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
