from __future__ import annotations

from celery import Celery
from hackathon_shared.logging import configure_logging
from hackathon_shared.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery(
    "hackathon_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="alerts.deliver_email")
def deliver_alert_email(delivery_id: str, dispatch_token: str) -> dict[str, object]:
    from hackathon_worker.alerts import deliver_pending_email
    return deliver_pending_email(delivery_id, dispatch_token)


@celery_app.task(name="alerts.dispatch_pending")
def dispatch_pending_email() -> dict[str, int]:
    from hackathon_worker.alerts import dispatch_due_emails

    return dispatch_due_emails(publish=lambda delivery_id, token: celery_app.send_task(
        "alerts.deliver_email", args=[delivery_id, token],
    ))


@celery_app.task(name="alerts.generate_matches")
def generate_saved_search_matches() -> dict[str, int]:
    from hackathon_worker.matches import process_new_matches
    return process_new_matches()


@celery_app.task(name="sources.maintenance_and_dispatch")
def run_source_scheduler_task() -> dict[str, int]:
    """Opt-in periodic operations; no scheduler fetch when disabled."""
    import asyncio
    from dataclasses import asdict
    from pathlib import Path

    from sqlalchemy import create_engine

    from hackathon_operations.dispatcher import run_scheduler_cycle

    config = get_settings()
    if not config.source_scheduler_enabled:
        return {"disabled": 1}
    root = Path(__file__).resolve().parents[4]
    engine = create_engine(config.database_url, pool_pre_ping=True)
    try:
        report = asyncio.run(run_scheduler_cycle(
            engine, root=root, dispatch=True,
            limit=config.source_scheduler_batch_size,
            max_pages=config.source_scheduler_max_pages,
        ))
        return asdict(report)
    finally:
        engine.dispose()


# Requires an independently running Celery Beat process. Both periodic jobs
# are opt-in, independent, and disabled by default.
beat_schedule: dict[str, dict[str, object]] = {}
if settings.source_scheduler_enabled:
    beat_schedule.update({
        "adaptive-source-maintenance": {
            "task": "sources.maintenance_and_dispatch",
            "schedule": 900.0,  # 15 minutes; database leases prevent concurrent claims.
        }
    })

if settings.alert_match_enabled:
    beat_schedule["saved-search-new-matches"] = {
        "task": "alerts.generate_matches",
        "schedule": 60.0,
    }

if settings.alert_dispatch_enabled:
    beat_schedule["alert-outbox-dispatch"] = {
        "task": "alerts.dispatch_pending",
        "schedule": 60.0,
    }
if beat_schedule:
    celery_app.conf.beat_schedule = beat_schedule
