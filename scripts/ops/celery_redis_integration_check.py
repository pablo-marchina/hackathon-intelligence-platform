#!/usr/bin/env python3
"""Opt-in Redis/Celery smoke drill that sends only the inert system.ping task.

Requires a separately started worker listening on the dedicated 'hip-ci-smoke'
queue. No source collection, email delivery, or external API calls are made.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit
from uuid import uuid4


def _allowed(queue: str) -> None:
    if os.getenv("APP_ENV") != "test" or os.getenv("HIP_ALLOW_CELERY_DRILL") != "1":
        raise RuntimeError("Celery smoke requires an explicitly isolated test environment")
    if os.getenv("SOURCE_SCHEDULER_ENABLED", "false").lower() not in {"false", "0", "no"}:
        raise RuntimeError("source scheduler must be disabled for the Celery smoke drill")
    if queue != "hip-ci-smoke":
        raise RuntimeError("Celery smoke requires the dedicated hip-ci-smoke queue")
    for variable, database in (("CELERY_BROKER_URL", "13"), ("CELERY_RESULT_BACKEND", "14")):
        url = os.getenv(variable, "")
        parts = urlsplit(url)
        if (parts.scheme != "redis" or parts.hostname not in {"localhost", "127.0.0.1"}
                or parts.port != 6379 or parts.path != f"/{database}"
                or parts.query or parts.fragment or parts.username or parts.password):
            raise RuntimeError(f"{variable} must point to isolated local Redis DB {database}")


def verify(queue: str = "hip-ci-smoke", *, app=None) -> str:
    _allowed(queue)
    if app is None:
        from hackathon_worker.app import celery_app

        app = celery_app
    task_id = f"hip-ci-ping-{uuid4().hex}"
    result = app.send_task("system.ping", task_id=task_id, queue=queue, expires=60)
    try:
        response = result.get(timeout=40, propagate=True)
        if response != "pong" or result.status != "SUCCESS":
            raise AssertionError("Celery worker did not return a successful ping")
    finally:
        result.forget()
    print("W6 Celery/Redis isolated queued task: PASS")
    return task_id


if __name__ == "__main__":
    verify()
