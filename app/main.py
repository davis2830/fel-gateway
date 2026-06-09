"""FastAPI application entry point."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import invoices, nit, tenants
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = FastAPI(
        title="FEL Gateway",
        version="0.1.0",
        description=(
            "Multi-tenant FEL (Facturación Electrónica) gateway for SAT Guatemala. "
            "Plugs in FELplex / INFILE / Digifact / mock certificadores."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    app.include_router(invoices.router)
    app.include_router(nit.router)
    app.include_router(tenants.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    return app


app = create_app()
