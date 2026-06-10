"""Contract tests for the FELplex adapter using respx (httpx mocks)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import httpx
import pytest
import respx

from app.adapters.base import ProviderConfigError, ProviderTransientError
from app.adapters.felplex import FELplexCertificador, build_felplex_payload
from app.domain.schemas import (
    InvoiceCreate,
    InvoiceItem,
    InvoiceReceiver,
    InvoiceTotals,
    NCREReference,
)

SANDBOX = "https://felplex-gt.stage.plex.lat"


def _payload() -> dict:
    inv = InvoiceCreate(
        tipo="FACT",
        fecha_emision=datetime(2026, 5, 28, 10, 30, 0),
        moneda="GTQ",
        iva_incluido=True,
        tasa_iva=Decimal("0.12"),
        receptor=InvoiceReceiver(
            nit="98765432",
            nombre="Cliente Demo",
            direccion="Calle 1",
            email="cliente@x.com",
        ),
        items=[
            InvoiceItem(
                descripcion="Cartón Huevos",
                cantidad=Decimal("2"),
                precio_unitario=Decimal("100"),
                bien_o_servicio="B",
            )
        ],
        totales=InvoiceTotals(monto_iva=Decimal("21.43"), total=Decimal("200")),
    )
    return inv.model_dump(mode="json")


def _adapter() -> FELplexCertificador:
    return FELplexCertificador(
        config={"entity_id": "ent1", "api_key": "secret-test-key"},
        ambiente="PRUEBAS",
    )


def _tenant_stub() -> object:
    class _T:
        id = "tenant-1"
        nit = "12345678"
        legal_name = "Empresa Demo"
        commercial_name = "Demo"
        address = "Av Reforma"
        postal_code = "01009"
        municipio = "GUATEMALA"
        departamento = "GUATEMALA"
        country = "GT"
        affiliation_iva = "GEN"
        establishment_code = "1"

    return _T()


def test_build_payload_includes_to_when_not_cf() -> None:
    pld = build_felplex_payload(_payload())
    assert pld["type"] == "FACT"
    assert pld["to_cf"] == 0
    assert pld["to"]["tax_code"] == "98765432"
    assert pld["to"]["address"]["country"] == "GT"
    assert len(pld["items"]) == 1
    assert pld["items"][0]["price"] == 100.0


def test_build_payload_taxes_must_be_object_not_array() -> None:
    """Regression: FELplex rejects items where ``taxes`` is ``[]``.

    The API expects an object with null fields when no explicit tax is
    declared; sending an empty array surfaces as a misleading
    "problemas de comunicación con SAT" error.
    """
    pld = build_felplex_payload(_payload())
    taxes = pld["items"][0]["taxes"]
    assert isinstance(taxes, dict), (
        f"taxes must be an object, got {type(taxes).__name__}: {taxes!r}"
    )
    assert taxes == {
        "quantity": None,
        "tax_code": None,
        "full_name": None,
        "short_name": None,
        "tax_amount": None,
        "taxable_amount": None,
    }


def test_build_payload_omits_to_when_cf() -> None:
    inv = _payload()
    inv["receptor"]["nit"] = "CF"
    inv["receptor"]["nombre"] = "Anónimo"
    pld = build_felplex_payload(inv)
    assert pld["to_cf"] == 1
    assert "to" not in pld


def test_build_payload_fcam_includes_payments() -> None:
    inv = _payload()
    inv["tipo"] = "FCAM"
    inv["dias_credito"] = 30
    pld = build_felplex_payload(inv)
    assert pld["use_payments"] == 1
    assert pld["payments"][0]["amount"] == 200.0


def test_build_payload_ncre_includes_parent() -> None:
    inv = _payload()
    inv["tipo"] = "NCRE"
    inv["ncre_reference"] = NCREReference(
        uuid_sat="ABC-UUID",
        serie="A",
        numero="1234",
        fecha_emision_original=datetime(2026, 4, 1),
        motivo="Devolución",
    ).model_dump(mode="json")
    pld = build_felplex_payload(inv)
    assert pld["parent_invoice_id"] == "ABC-UUID"


@pytest.mark.asyncio
async def test_certificar_success() -> None:
    adapter = _adapter()
    payload = build_felplex_payload(_payload())
    url = f"{SANDBOX}/api/entity/ent1/invoices/await"

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url).respond(
            200,
            json={
                "valid": True,
                "uuid": "UUID-1234",
                "sat": {
                    "serie": "A",
                    "no": "5678",
                    "authorization": "AUTH-9876",
                    "certification_date": "2026-05-28 10:31:00",
                },
            },
        )
        result = await adapter.certificar(xml="<x/>", payload=payload, tenant=_tenant_stub())

    assert result.ok is True
    assert result.uuid_sat == "UUID-1234"
    assert result.serie == "A"
    assert result.numero_autorizacion == "AUTH-9876"


@pytest.mark.asyncio
async def test_certificar_rejects_invalid_response() -> None:
    adapter = _adapter()
    payload = build_felplex_payload(_payload())
    url = f"{SANDBOX}/api/entity/ent1/invoices/await"

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url).respond(
            200,
            json={"valid": False, "errors": {"nit": "NIT inválido"}},
        )
        result = await adapter.certificar(xml="<x/>", payload=payload, tenant=_tenant_stub())

    assert result.ok is False
    assert "NIT" in (result.error or "")


@pytest.mark.asyncio
async def test_certificar_5xx_raises_transient() -> None:
    adapter = _adapter()
    payload = build_felplex_payload(_payload())
    url = f"{SANDBOX}/api/entity/ent1/invoices/await"

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url).respond(503, json={"error": "service unavailable"})
        with pytest.raises(ProviderTransientError):
            await adapter.certificar(
                xml="<x/>", payload=payload, tenant=_tenant_stub()
            )


@pytest.mark.asyncio
async def test_certificar_network_error_raises_transient() -> None:
    adapter = _adapter()
    payload = build_felplex_payload(_payload())
    url = f"{SANDBOX}/api/entity/ent1/invoices/await"

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url).mock(side_effect=httpx.ConnectError("dns"))
        with pytest.raises(ProviderTransientError):
            await adapter.certificar(
                xml="<x/>", payload=payload, tenant=_tenant_stub()
            )


@pytest.mark.asyncio
async def test_missing_config_raises_config_error() -> None:
    bad = FELplexCertificador(config={}, ambiente="PRUEBAS")
    with pytest.raises(ProviderConfigError):
        await bad.certificar(xml="<x/>", payload={}, tenant=_tenant_stub())


@pytest.mark.asyncio
async def test_anular_success() -> None:
    adapter = _adapter()
    url = f"{SANDBOX}/api/entity/ent1/invoices/UUID-1"
    with respx.mock(assert_all_called=False) as mock:
        mock.delete(url).respond(200, json={"success": True, "uuid": "UUID-1"})
        result = await adapter.anular(uuid_sat="UUID-1", motivo="x", tenant=_tenant_stub())
    assert result.ok is True
    assert result.uuid_sat == "UUID-1"


@pytest.mark.asyncio
async def test_consultar_nit_returns_name() -> None:
    adapter = _adapter()
    url = f"{SANDBOX}/api/entity/ent1/find/NIT/12345678"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).respond(200, json={"name": "Empresa Demo S.A."})
        r = await adapter.consultar_nit("12345678")
    assert r.ok is True
    assert r.nombre == "Empresa Demo S.A."
