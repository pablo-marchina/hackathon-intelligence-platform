#!/usr/bin/env python3
"""Test the actual API readiness route against migrated, disposable CI PostgreSQL."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient


def allowed() -> None:
    if os.getenv("APP_ENV") != "test" or os.getenv("HIP_ALLOW_SOURCE_DB_DRILL") != "1":
        raise RuntimeError("readiness integration requires explicit CI opt-in")
    db_url = os.getenv("DATABASE_URL", "")
    parsed = urlsplit(db_url)
    if (
        parsed.scheme != "postgresql+psycopg"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.path not in {"/hackathon", "/hip_test"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("readiness check requires a local disposable PostgreSQL database")


def main() -> None:
    allowed()
    from hackathon_api.main import app

    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")
        assert health.status_code == 200, health.text
        assert ready.status_code == 200, ready.text
        assert ready.json()["status"] == "ready"
    print("W6 migrated PostgreSQL API liveness + readiness: PASS")


if __name__ == "__main__":
    main()
