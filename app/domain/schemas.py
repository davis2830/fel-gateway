"""Neutral payload schemas — invoice issuance & annulment.

These types are deliberately decoupled from any consumer's domain
(``orden``, ``cita``, ``producto``, ...). They describe what SAT needs:
issuer + receiver + items + totals + metadata.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TipoDTE = Literal["FACT", "FCAM", "FPEQ", "NCRE", "NDEB"]
BienOServicio = Literal["B", "S"]


class InvoiceItem(BaseModel):
    """One line on the DTE."""

    model_config = ConfigDict(extra="forbid")

    descripcion: str = Field(min_length=1, max_length=200)
    cantidad: Decimal = Field(gt=Decimal("0"))
    precio_unitario: Decimal = Field(ge=Decimal("0"))
    descuento: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    bien_o_servicio: BienOServicio = "S"
    unidad_medida: str = Field(default="UND", max_length=10)


class InvoiceReceiver(BaseModel):
    """Receptor on the DTE — the customer being billed."""

    model_config = ConfigDict(extra="forbid")

    # ``CF`` (Consumidor Final) is allowed and signals an anonymous receiver.
    nit: str = Field(default="CF", max_length=20)
    nombre: str = Field(min_length=1, max_length=200)
    direccion: str = Field(default="Ciudad", max_length=300)
    email: str | None = None
    postal_code: str = Field(default="01001", max_length=10)
    municipio: str = Field(default="GUATEMALA", max_length=100)
    departamento: str = Field(default="GUATEMALA", max_length=100)
    country: str = Field(default="GT", max_length=2)

    @field_validator("nit", mode="before")
    @classmethod
    def normalize_nit(cls, v: str) -> str:
        if v is None:
            return "CF"
        cleaned = str(v).replace("-", "").replace(" ", "").upper()
        return cleaned or "CF"


class InvoiceTotals(BaseModel):
    """Totals on the DTE — caller-computed; gateway does NOT recalculate.

    The gateway trusts the caller's totals. The XML builder uses
    ``monto_iva`` as the global tax line and ``total`` as ``GranTotal``.
    """

    model_config = ConfigDict(extra="forbid")

    monto_iva: Decimal = Field(ge=Decimal("0"))
    total: Decimal = Field(gt=Decimal("0"))


class NCREReference(BaseModel):
    """Reference to the original DTE for a Nota de Crédito."""

    model_config = ConfigDict(extra="forbid")

    uuid_sat: str
    serie: str
    numero: str
    fecha_emision_original: datetime
    motivo: str = Field(min_length=1, max_length=200)


class InvoiceCreate(BaseModel):
    """Payload posted to ``POST /v1/invoices``."""

    model_config = ConfigDict(extra="forbid")

    tipo: TipoDTE = "FACT"
    fecha_emision: datetime
    moneda: str = Field(default="GTQ", max_length=3)
    iva_incluido: bool = True
    tasa_iva: Decimal = Field(default=Decimal("0.12"), ge=Decimal("0"), le=Decimal("1"))

    receptor: InvoiceReceiver
    items: list[InvoiceItem] = Field(min_length=1)
    totales: InvoiceTotals

    # FCAM (factura cambiaria) — pagos programados
    dias_credito: int | None = Field(default=None, ge=0)

    # NCRE — referencia al documento original
    ncre_reference: NCREReference | None = None

    # Idempotency / cross-system traceability.
    external_ref: str | None = Field(default=None, max_length=64)
    metadata: dict | None = None


class InvoiceRead(BaseModel):
    """Response for invoice endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    external_ref: str | None
    status: str
    tipo_dte: str
    fecha_emision: datetime
    uuid_sat: str | None
    serie: str | None
    numero_autorizacion: str | None
    fecha_certificacion: datetime | None
    total: Decimal
    monto_iva: Decimal
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class AnnulRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(min_length=1, max_length=200)


class TenantCreate(BaseModel):
    """Admin payload to register a new consumer of the gateway."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    nit: str = Field(min_length=1, max_length=20)
    legal_name: str = Field(min_length=1, max_length=200)
    commercial_name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    postal_code: str = Field(default="01001", max_length=10)
    municipio: str = Field(default="GUATEMALA", max_length=100)
    departamento: str = Field(default="GUATEMALA", max_length=100)
    country: str = Field(default="GT", max_length=2)
    affiliation_iva: Literal["GEN", "PEQ", "EXENTO"] = "GEN"
    establishment_code: str = Field(default="1", max_length=10)
    serie_fel: str = Field(default="A", max_length=20)
    contact_email: str = ""

    provider: Literal["mock", "felplex", "infile", "digifact"] = "mock"
    provider_config: dict = Field(default_factory=dict)
    ambiente: Literal["PRUEBAS", "PRODUCCION"] = "PRUEBAS"

    @field_validator("nit", mode="before")
    @classmethod
    def normalize_nit(cls, v: str) -> str:
        return str(v).replace("-", "").replace(" ", "").upper()


class TenantRead(BaseModel):
    """Tenant data without the API key hash."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    nit: str
    legal_name: str
    commercial_name: str
    provider: str
    ambiente: str
    is_active: bool
    created_at: datetime


class TenantCreateResponse(BaseModel):
    """Returned exactly once when a tenant is created.

    The API key is **not** persisted plaintext — store it now or rotate.
    """

    tenant: TenantRead
    api_key: str
