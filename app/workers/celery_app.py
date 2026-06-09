"""Celery application — RabbitMQ broker, Postgres result backend."""
from __future__ import annotations

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "fel_gateway",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Sane retry defaults — tasks override per-decorator if needed.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # Dead-letter queue: rejected tasks land here for manual inspection.
    task_default_queue="fel.default",
    task_queues=None,  # use default; DLX wired via broker config in production
    # Retries
    task_default_retry_delay=10,
    task_default_max_retries=10,
)
