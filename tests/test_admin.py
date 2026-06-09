"""Admin endpoints (tenant CRUD) — protected by master key."""
from __future__ import annotations


def _tenant_payload(name: str = "ovo") -> dict:
    return {
        "name": name,
        "nit": "12345678",
        "legal_name": "Sistema OVO S.A.",
        "commercial_name": "Sistema OVO",
        "address": "Av Reforma 1-23 Zona 9",
        "provider": "mock",
        "ambiente": "PRUEBAS",
    }


def test_create_tenant_requires_master_key(client) -> None:
    r = client.post("/admin/tenants/", json=_tenant_payload())
    assert r.status_code == 401


def test_create_tenant_rejects_wrong_master_key(client) -> None:
    r = client.post(
        "/admin/tenants/",
        json=_tenant_payload(),
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert r.status_code == 403


def test_create_tenant_returns_api_key_once(client) -> None:
    r = client.post(
        "/admin/tenants/",
        json=_tenant_payload(),
        headers={"Authorization": "Bearer test-master-key"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["api_key"].startswith("flg_")
    assert body["tenant"]["name"] == "ovo"
    assert body["tenant"]["provider"] == "mock"


def test_create_tenant_rejects_duplicate_name(client) -> None:
    r1 = client.post(
        "/admin/tenants/",
        json=_tenant_payload(),
        headers={"Authorization": "Bearer test-master-key"},
    )
    r2 = client.post(
        "/admin/tenants/",
        json=_tenant_payload(),
        headers={"Authorization": "Bearer test-master-key"},
    )
    assert r1.status_code == 201
    assert r2.status_code == 409


def test_rotate_key_invalidates_old_key(client) -> None:
    create = client.post(
        "/admin/tenants/",
        json=_tenant_payload(),
        headers={"Authorization": "Bearer test-master-key"},
    )
    tid = create.json()["tenant"]["id"]
    old_key = create.json()["api_key"]

    rotate = client.post(
        f"/admin/tenants/{tid}/rotate-key",
        headers={"Authorization": "Bearer test-master-key"},
    )
    new_key = rotate.json()["api_key"]
    assert new_key != old_key

    # Old key should now be rejected at the data plane.
    payload = {
        "tipo": "FACT",
        "fecha_emision": "2026-05-28T10:30:00",
        "iva_incluido": True,
        "tasa_iva": "0.12",
        "receptor": {"nit": "CF", "nombre": "X"},
        "items": [
            {
                "descripcion": "x",
                "cantidad": "1",
                "precio_unitario": "100",
                "bien_o_servicio": "S",
            }
        ],
        "totales": {"monto_iva": "10.71", "total": "100"},
    }
    r_old = client.post(
        "/v1/invoices?sync=true",
        json=payload,
        headers={"Authorization": f"Bearer {old_key}"},
    )
    assert r_old.status_code == 401
    r_new = client.post(
        "/v1/invoices?sync=true",
        json=payload,
        headers={"Authorization": f"Bearer {new_key}"},
    )
    assert r_new.status_code == 202


def test_deactivated_tenant_cannot_authenticate(client) -> None:
    create = client.post(
        "/admin/tenants/",
        json=_tenant_payload(),
        headers={"Authorization": "Bearer test-master-key"},
    )
    tid = create.json()["tenant"]["id"]
    key = create.json()["api_key"]
    client.post(
        f"/admin/tenants/{tid}/deactivate",
        headers={"Authorization": "Bearer test-master-key"},
    )
    r = client.get("/v1/invoices", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401
