"""FELplex adapter — certificador FEL autorizado por SAT Guatemala.

Ported from ``taller_mecanico/facturacion/services/felplex.py`` to a
neutral, async (httpx) interface. Builds the FELplex JSON payload from
the gateway's neutral ``InvoiceCreate`` schema (already validated upstream).

API docs: https://documenter.getpostman.com/view/1055317/TVCZYqHT

URLs:
  Sandbox    https://felplex.stage.plex.lat
  Production https://felplex.plex.lat

Auth:    header ``X-Authorization: <api_key>``
Config:  tenant.provider_config = {
            "entity_id": "<entidad>",
            "api_key": "<key>",
            "base_url": "<override optional>",
         }
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.adapters.base import (
    AnnulResult,
    CertificadorBase,
    CertifyResult,
    NITLookupResult,
    ProviderConfigError,
    ProviderTransientError,
)
from app.config import get_settings

log = logging.getLogger(__name__)

_TIPO_MAP = {"FACT": "FACT", "FCAM": "FCAM", "FPEQ": "FPEQ", "NCRE": "NCRE", "NDEB": "NDEB"}


def _base_url(config: dict, ambiente: str) -> str:
    if config.get("base_url"):
        return str(config["base_url"]).rstrip("/")
    return (
        "https://felplex.plex.lat"
        if ambiente == "PRODUCCION"
        else "https://felplex.stage.plex.lat"
    )


def _entity(config: dict) -> str:
    return str(config.get("entity_id", "")).strip()


def _api_key(config: dict) -> str:
    return str(config.get("api_key", "")).strip()


def _headers(config: dict) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Authorization": _api_key(config),
    }


def _validate(config: dict) -> None:
    if not _entity(config):
        raise ProviderConfigError(
            "FELplex: falta 'entity_id' en provider_config del tenant."
        )
    if not _api_key(config):
        raise ProviderConfigError(
            "FELplex: falta 'api_key' en provider_config del tenant."
        )


def _build_item(it: dict, iva_incluido: bool) -> dict:
    return {
        "qty": float(it["cantidad"]),
        "type": it.get("bien_o_servicio", "S"),
        "price": float(it["precio_unitario"]),
        "description": str(it["descripcion"])[:200],
        "without_iva": 0 if iva_incluido else 1,
        "discount": float(it.get("descuento", 0)),
        "is_discount_percentage": 0,
        "taxes": [],
    }


def build_felplex_payload(invoice_dict: dict) -> dict:
    """Translate a neutral invoice dict into FELplex's JSON shape.

    ``invoice_dict`` is the result of ``InvoiceCreate.model_dump(mode='json')``.
    """
    tipo = _TIPO_MAP.get(invoice_dict.get("tipo", "FACT"), "FACT")
    iva_incluido = bool(invoice_dict.get("iva_incluido", True))

    items = [_build_item(it, iva_incluido) for it in invoice_dict.get("items", [])]
    if not items:
        # Defensive: schema enforces ≥1 but keep behaviour aligned with taller code
        items.append(
            {
                "qty": 1,
                "type": "S",
                "price": float(invoice_dict["totales"]["total"]),
                "description": "Consumo",
                "without_iva": 0 if iva_incluido else 1,
                "discount": 0,
                "is_discount_percentage": 0,
                "taxes": [],
            }
        )

    receptor = invoice_dict["receptor"]
    nit_limpio = str(receptor.get("nit", "CF")).replace("-", "").upper().strip() or "CF"
    es_cf = nit_limpio == "CF"

    fecha_iso = invoice_dict["fecha_emision"]
    if isinstance(fecha_iso, str):
        # Pydantic dumps datetime → ISO string; keep up to seconds (FELplex format)
        # If it has timezone, drop it for FELplex's expected format
        try:
            fecha_iso = datetime.fromisoformat(fecha_iso).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            fecha_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    elif isinstance(fecha_iso, datetime):
        fecha_iso = fecha_iso.strftime("%Y-%m-%dT%H:%M:%S")

    emails: list[dict[str, str]] = []
    if receptor.get("email"):
        emails.append({"email": receptor["email"]})

    payload: dict = {
        "type": tipo,
        "datetime_issue": fecha_iso,
        "items": items,
        "total": float(invoice_dict["totales"]["total"]),
        "total_tax": f'{float(invoice_dict["totales"]["monto_iva"]):.2f}',
        "emails": emails,
        "to_cf": 1 if es_cf else 0,
    }

    if not es_cf:
        payload["to"] = {
            "tax_code_type": "NIT",
            "tax_code": nit_limpio,
            "tax_name": receptor.get("nombre") or "Consumidor Final",
            "address": {
                "street": receptor.get("direccion") or "Ciudad",
                "city": receptor.get("municipio") or "Guatemala",
                "state": receptor.get("departamento") or "Guatemala",
                "zip": receptor.get("postal_code") or "01001",
                "country": receptor.get("country") or "GT",
            },
        }

    if tipo == "FCAM" and invoice_dict.get("dias_credito"):
        from datetime import timedelta

        base = datetime.fromisoformat(invoice_dict["fecha_emision"]).date()
        venc = base + timedelta(days=int(invoice_dict["dias_credito"]))
        payload["use_payments"] = 1
        payload["payments"] = [
            {"date": venc.isoformat(), "amount": float(invoice_dict["totales"]["total"])}
        ]

    if tipo == "NCRE" and invoice_dict.get("ncre_reference"):
        payload["parent_invoice_id"] = invoice_dict["ncre_reference"].get("uuid_sat")

    return payload


class FELplexCertificador(CertificadorBase):
    nombre = "FELPLEX"

    async def _post(self, url: str, json: dict | None = None) -> tuple[int, dict]:
        timeout = get_settings().provider_timeout
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=json, headers=_headers(self.config))
        except httpx.TimeoutException as exc:
            raise ProviderTransientError(f"Timeout FELplex: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"Error de red FELplex: {exc}") from exc

        try:
            data = resp.json()
        except ValueError:
            raise ProviderTransientError(
                f"FELplex respondió contenido no-JSON (HTTP {resp.status_code})"
            ) from None

        if resp.status_code >= 500:
            raise ProviderTransientError(
                f"FELplex 5xx (HTTP {resp.status_code}): {data}"
            )
        return resp.status_code, data

    async def _delete(self, url: str, json: dict | None = None) -> tuple[int, dict]:
        timeout = get_settings().provider_timeout
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(
                    "DELETE", url, json=json, headers=_headers(self.config)
                )
        except httpx.TimeoutException as exc:
            raise ProviderTransientError(f"Timeout FELplex: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"Error de red FELplex: {exc}") from exc

        try:
            data = resp.json()
        except ValueError:
            raise ProviderTransientError(
                f"FELplex respondió contenido no-JSON (HTTP {resp.status_code})"
            ) from None

        if resp.status_code >= 500:
            raise ProviderTransientError(
                f"FELplex 5xx (HTTP {resp.status_code}): {data}"
            )
        return resp.status_code, data

    async def _get(self, url: str) -> tuple[int, dict]:
        timeout = get_settings().provider_timeout
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=_headers(self.config))
        except httpx.TimeoutException as exc:
            raise ProviderTransientError(f"Timeout FELplex: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"Error de red FELplex: {exc}") from exc

        try:
            data = resp.json()
        except ValueError:
            return resp.status_code, {}

        if resp.status_code >= 500:
            raise ProviderTransientError(
                f"FELplex 5xx (HTTP {resp.status_code}): {data}"
            )
        return resp.status_code, data

    async def certificar(self, *, xml: str, payload: dict, tenant) -> CertifyResult:
        _validate(self.config)
        entity = _entity(self.config)
        url = f"{_base_url(self.config, self.ambiente)}/api/entity/{entity}/invoices/await"

        log.info("FELplex certificar tipo=%s tenant=%s", payload.get("type"), tenant.id)

        status, data = await self._post(url, json=payload)

        if not (200 <= status < 300) or not data.get("valid", False):
            errors = data.get("errors", data.get("error", ""))
            if isinstance(errors, dict):
                errors = "; ".join(f"{k}: {v}" for k, v in errors.items())
            return CertifyResult(
                ok=False,
                error=f"FELplex rechazó el DTE (HTTP {status}): {errors}",
                raw_response=data,
            )

        sat = data.get("sat", {}) or {}
        uuid_sat = data.get("uuid", "") or sat.get("uuid", "")
        serie = sat.get("serie", "")
        numero = sat.get("no", "")
        autorizacion = sat.get("authorization", "") or numero
        fecha_str = sat.get("certification_date", "")

        fecha_cert: datetime | None = None
        if fecha_str:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    fecha_cert = datetime.strptime(fecha_str, fmt).replace(
                        tzinfo=UTC
                    )
                    break
                except ValueError:
                    continue
        if fecha_cert is None:
            fecha_cert = datetime.now(UTC)

        return CertifyResult(
            ok=True,
            uuid_sat=uuid_sat,
            serie=serie,
            numero_autorizacion=autorizacion,
            fecha_certificacion=fecha_cert,
            raw_response=data,
        )

    async def anular(self, *, uuid_sat: str, motivo: str, tenant) -> AnnulResult:
        _validate(self.config)
        entity = _entity(self.config)
        url = f"{_base_url(self.config, self.ambiente)}/api/entity/{entity}/invoices/{uuid_sat}"

        log.info("FELplex anular uuid=%s tenant=%s", uuid_sat, tenant.id)

        status, data = await self._delete(
            url, json={"reason": motivo or "Anulación solicitada por el emisor."}
        )

        if not (200 <= status < 300) or not data.get("success", False):
            errors = data.get("errors", data.get("error", ""))
            if isinstance(errors, dict):
                errors = "; ".join(f"{k}: {v}" for k, v in errors.items())
            return AnnulResult(
                ok=False,
                error=f"FELplex rechazó la anulación (HTTP {status}): {errors}",
                raw_response=data,
            )

        return AnnulResult(
            ok=True,
            uuid_sat=data.get("uuid", uuid_sat),
            raw_response=data,
        )

    async def consultar_nit(self, nit: str) -> NITLookupResult:
        _validate(self.config)
        entity = _entity(self.config)
        nit_limpio = nit.replace("-", "").replace(" ", "").upper().strip()
        if not nit_limpio:
            return NITLookupResult(ok=False, error="NIT vacío.")

        url = f"{_base_url(self.config, self.ambiente)}/api/entity/{entity}/find/NIT/{nit_limpio}"

        status, data = await self._get(url)
        if not (200 <= status < 300):
            return NITLookupResult(
                ok=False,
                error=f"NIT no encontrado (HTTP {status}).",
                raw_response=data,
            )

        nombre = data.get("name") or data.get("nombre") or ""
        return NITLookupResult(
            ok=bool(nombre),
            nombre=nombre or None,
            direccion=data.get("address") or data.get("direccion"),
            raw_response=data,
            error=None if nombre else "Respuesta sin nombre fiscal.",
        )
