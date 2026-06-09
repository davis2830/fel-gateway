"""Settings via pydantic-settings (env-driven)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://felgateway:felgateway@localhost:5433/felgateway"
    )
    celery_broker_url: str = Field(
        default="amqp://felgateway:felgateway@localhost:5672//"
    )
    celery_result_backend: str = Field(
        default="db+postgresql+psycopg://felgateway:felgateway@localhost:5433/felgateway"
    )

    # Master key required to call the admin API (tenant CRUD).
    # NEVER use the default in production. Set MASTER_API_KEY in env/secrets.
    master_api_key: str = Field(default="dev-master-key-change-in-production")

    # External provider request timeout (seconds).
    provider_timeout: float = 30.0

    # Logging
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
