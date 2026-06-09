"""End-to-end API tests for the invoice flow.

Uses the mock provider (no network) and runs everything inline via
``?sync=true`` on the certify endpoint, so Celery is not required.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal


def _payload() -> dict:
    return {
        "tipo": "FACT",
        "fecha_emision": datetime(2026, 5, 28, 10, 30, 0).isoformat(),
        "moneda": "GTQ",
        "iva_incluido": True,
        "tasa_iva": "0.12",
        "receptor": {
            "nit": "98765432",
            "nombre": "Cliente Demo",
            "direccion": "Calle 1",
        },
        "items": [
            {
                "descripcion": "Cartón Huevos Blancos",
                "cantidad": "2",
                "precio_unitario": "100",
                "bien_o_servicio": "B",
            }
        ],
        "totales": {"monto_iva": "21.43", "total": "200"},
    }


def test_create_invoice_requires_auth(client) -> None:
    r = client.post("/v1/invoices", json=_payload())
    assert r.status_code == 401


def test_create_invoice_rejects_bad_key(client) -> None:
    r = client.post(
        "/v1/invoices",
        json=_payload(),
        headers={"Authorization": "Bearer flg_invalid"},
    )
    assert r.status_code == 401


def test_create_and_get_invoice_sync_mock(client, tenant_factory) -> None:
    tenant, plain_key = tenant_factory(provider="mock")
    headers = {"Authorization": f"Bearer {plain_key}"}

    r = client.post("/v1/invoices?sync=true", json=_payload(), headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "CERTIFIED"
    assert body["uuid_sat"]
    assert Decimal(body["total"]) == Decimal("200.00")

    invoice_id = body["id"]
    r2 = client.get(f"/v1/invoices/{invoice_id}", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["uuid_sat"] == body["uuid_sat"]


def test_idempotency_via_external_ref(client, tenant_factory) -> None:
    tenant, plain_key = tenant_factory(provider="mock")
    headers = {"Authorization": f"Bearer {plain_key}"}

    payload = _payload() | {"external_ref": "ovo-order-1"}
    r1 = client.post("/v1/invoices?sync=true", json=payload, headers=headers)
    r2 = client.post("/v1/invoices?sync=true", json=payload, headers=headers)
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["uuid_sat"] == r2.json()["uuid_sat"]


def test_tenant_can_only_see_own_invoices(client, tenant_factory) -> None:
    a, key_a = tenant_factory(provider="mock")
    b, key_b = tenant_factory(provider="mock")

    r = client.post(
        "/v1/invoices?sync=true",
        json=_payload(),
        headers={"Authorization": f"Bearer {key_a}"},
    )
    invoice_id = r.json()["id"]

    r2 = client.get(
        f"/v1/invoices/{invoice_id}", headers={"Authorization": f"Bearer {key_b}"}
    )
    assert r2.status_code == 404


def test_list_invoices_sorted_desc(client, tenant_factory) -> None:
    tenant, key = tenant_factory(provider="mock")
    headers = {"Authorization": f"Bearer {key}"}
    for i in range(3):
        p = _payload() | {"external_ref": f"ref-{i}"}
        client.post("/v1/invoices?sync=true", json=p, headers=headers)
    r = client.get("/v1/invoices", headers=headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 3
    # newest first
    assert rows[0]["created_at"] >= rows[-1]["created_at"]


def test_annul_certified_invoice_sync(client, tenant_factory) -> None:
    tenant, key = tenant_factory(provider="mock")
    headers = {"Authorization": f"Bearer {key}"}
    r = client.post("/v1/invoices?sync=true", json=_payload(), headers=headers)
    invoice_id = r.json()["id"]
    r2 = client.post(
        f"/v1/invoices/{invoice_id}/annul?sync=true",
        json={"motivo": "Cliente canceló compra"},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "ANNULLED"


def test_payload_validation_rejects_zero_quantity(client, tenant_factory) -> None:
    tenant, key = tenant_factory(provider="mock")
    bad = _payload()
    bad["items"][0]["cantidad"] = "0"
    r = client.post(
        "/v1/invoices?sync=true",
        json=bad,
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 422
