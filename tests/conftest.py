"""Test fixtures: SQLite in-memory DB + FastAPI TestClient."""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Force the master key BEFORE importing any app code.
os.environ.setdefault("MASTER_API_KEY", "test-master-key")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

from app.config import get_settings
from app.db import models
from app.db.session import Base, get_session
from app.main import create_app
from app.security import generate_api_key, hash_api_key


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db_session(engine) -> Iterator[Session]:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with SessionLocal() as s:
        yield s


@pytest.fixture
def client(engine) -> Iterator[TestClient]:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _get_session_override() -> Iterator[Session]:
        with SessionLocal() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _get_session_override
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tenant_factory(db_session: Session):
    """Build an active tenant + return (tenant, plain_api_key)."""

    def _factory(provider: str = "mock", provider_config: dict | None = None) -> tuple:
        plain_key = generate_api_key()
        tenant = models.Tenant(
            id=uuid.uuid4(),
            name=f"tenant-{uuid.uuid4().hex[:8]}",
            api_key_hash=hash_api_key(plain_key),
            nit="12345678",
            legal_name="Empresa Demo S.A.",
            commercial_name="Empresa Demo",
            address="Avenida Reforma 1-23 Zona 9",
            postal_code="01009",
            municipio="GUATEMALA",
            departamento="GUATEMALA",
            country="GT",
            affiliation_iva="GEN",
            establishment_code="1",
            serie_fel="A",
            contact_email="ops@demo.gt",
            provider=provider,
            provider_config=provider_config or {},
            ambiente="PRUEBAS",
            is_active=True,
        )
        db_session.add(tenant)
        db_session.commit()
        db_session.refresh(tenant)
        return tenant, plain_key

    return _factory
