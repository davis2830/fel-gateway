# fel-gateway

Multi-tenant FEL (Facturación Electrónica) gateway for SAT Guatemala.

This is a single, language-agnostic HTTP service that any backend can call
to certify and annul DTEs (FACT, FCAM, FPEQ, NCRE, NDEB) without bundling
its own XML builder, signing logic, or certificador SDK.

## Why

Every Guatemalan business app eventually needs FEL. Each app shouldn't
have to:

- Maintain its own SAT XML schema implementation (the schema changes ~2×/year).
- Embed credentials for INFILE / FELplex / Digifact / Megaprint.
- Reimplement retries when the certificador or SAT is briefly down.
- Worry about race conditions when an order is double-clicked.

The gateway centralizes all of that. Consumer apps speak a clean, neutral
JSON contract; the gateway speaks the certificador's specific dialect.

## Architecture

```
        ┌─────────────────────────────────────────────────────────────┐
        │ Consumer apps  (SistemaOVO, Taller_mecanica_HG, others)     │
        └───────────────┬─────────────────────────────────────────────┘
                        │  POST /v1/invoices  (Bearer <api_key>)
                        ▼
                 ┌─────────────┐    queue    ┌──────────────┐
                 │  FastAPI    │────────────▶│  RabbitMQ    │
                 │  app        │             └──────┬───────┘
                 └─────┬───────┘                    │
                       │                            ▼
                       │                     ┌──────────────┐
                       │                     │ Celery       │
                       │                     │ worker       │
                       │                     └──────┬───────┘
                       ▼                            │
                ┌──────────────┐                    │
                │ Postgres     │◀───────────────────┘
                │ (tenants,    │   POST /api/entity/{id}/invoices/await
                │  invoices,   │              ▼
                │  audit)      │       ┌──────────────┐
                └──────────────┘       │ FELplex /    │
                                       │ INFILE /     │
                                       │ Digifact     │
                                       └──────────────┘
```

## Endpoints

### Public (per-tenant, `Authorization: Bearer <api_key>`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/invoices` | Queue an invoice. `?sync=true` certifies inline. |
| POST | `/v1/invoices?sync=true` | Synchronous variant (mock provider, tests). |
| GET  | `/v1/invoices/{id}` | Status + UUID/serie of an invoice. |
| GET  | `/v1/invoices` | List (newest first), filter by `?status=`. |
| POST | `/v1/invoices/{id}/annul` | Annul a CERTIFIED invoice. |
| GET  | `/v1/nit/{nit}` | Lookup a NIT against SAT. |

### Admin (master key, `Authorization: Bearer $MASTER_API_KEY`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/tenants/` | Create tenant. Returns API key **once**. |
| GET  | `/admin/tenants/` | List tenants. |
| POST | `/admin/tenants/{id}/rotate-key` | Issue new API key. |
| POST | `/admin/tenants/{id}/deactivate` | Disable a tenant. |

## Local development

```bash
# 1. Bring up Postgres + RabbitMQ + API + worker
docker compose up -d --build

# 2. Health check
curl http://localhost:8001/health

# 3. Create a tenant (use the master key from docker-compose env)
curl -s -X POST http://localhost:8001/admin/tenants/ \
  -H "Authorization: Bearer dev-master-key-change-in-production" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "sistema-ovo",
    "nit": "12345678",
    "legal_name": "Empresa Demo S.A.",
    "commercial_name": "Empresa Demo",
    "address": "Av Reforma 1-23 Zona 9",
    "provider": "mock"
  }'
# → returns { "tenant": {...}, "api_key": "flg_xxxxx" }

# 4. Issue an invoice (substitute the api_key from step 3)
curl -X POST http://localhost:8001/v1/invoices \
  -H "Authorization: Bearer flg_xxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "tipo": "FACT",
    "fecha_emision": "2026-05-28T10:30:00",
    "iva_incluido": true,
    "tasa_iva": "0.12",
    "receptor": {"nit": "98765432", "nombre": "Cliente Demo"},
    "items": [
      {"descripcion": "Cartón Huevos", "cantidad": "2",
       "precio_unitario": "100", "bien_o_servicio": "B"}
    ],
    "totales": {"monto_iva": "21.43", "total": "200"}
  }'
# → 202 PENDING → worker picks up → CERTIFIED with mock UUID
```

## Tests

```bash
. .venv/bin/activate
pytest
```

31 tests cover XML structure, FELplex contract (mocked with `respx`),
admin auth, tenant isolation, idempotency, and the worker happy-path.

## Configuration

See `.env.example`. Per-tenant provider config lives on the tenant row,
e.g. for FELplex:

```json
{
  "name": "sistema-ovo",
  "provider": "felplex",
  "provider_config": {
    "entity_id": "<entidad-felplex>",
    "api_key": "<your-felplex-api-key>"
  },
  "ambiente": "PRUEBAS"
}
```

FELplex base URLs (Guatemala):

| Ambiente | URL |
|---|---|
| Sandbox | `https://felplex-gt.stage.plex.lat` |
| Producción | `https://app.felplex.com` |

The `-gt` country suffix is mandatory in the sandbox host. The adapter
selects the right URL automatically from the tenant's `ambiente`; you
only need to set `base_url` in `provider_config` if you want to point
to a different country (e.g. `https://felplex-sv.stage.plex.lat`
for El Salvador) or override for a private deployment.

## Adding a new provider

1. Implement `app.adapters.base.CertificadorBase` in
   `app/adapters/<provider>.py`.
2. Register it in `app/adapters/__init__.py`'s `_REGISTRY`.
3. Add the literal to `TenantCreate.provider`.
4. Write contract tests with `respx`.

## Production notes

- Set a strong `MASTER_API_KEY` and store API keys in a secret manager.
- Run behind HTTPS (nginx / cloud LB).
- Scale Celery workers horizontally with `--concurrency=N`.
- Postgres + RabbitMQ should be managed services in production
  (Cloud SQL / CloudAMQP) — the local `docker-compose.yml` is for dev.
- Set up alerting on `audit_events.event = 'invoice.dlq'` (DLQ entries
  mean a doc failed even after 10 retries).
