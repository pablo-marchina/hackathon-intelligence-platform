#!/usr/bin/env python3
"""Run a two-worker PostgreSQL lease contention drill on a disposable test DB.

This creates one isolated source and performs no network ingestion. Run only
with APP_ENV=test and HIP_ALLOW_SOURCE_DB_DRILL=1 after Alembic migrations.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from hackathon_operations.source_maintenance import (
    LEASE_DURATION,
    claim_due_source,
    complete_lease,
    renew_lease,
)
from sqlalchemy import create_engine, text


def _allowed() -> str:
    if os.getenv("APP_ENV") != "test" or os.getenv("HIP_ALLOW_SOURCE_DB_DRILL") != "1":
        raise RuntimeError("refusing the multiworker test on a non-isolated database")
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgresql+psycopg://", "postgresql://")):
        raise RuntimeError("multiworker drill requires an explicit PostgreSQL URL")
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.query or parts.fragment:
        raise RuntimeError("multiworker drill refuses PostgreSQL URL options and fragments")
    for variable in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        if os.getenv(variable):
            raise RuntimeError(f"multiworker drill refuses inherited {variable} override")
    hostname = parts.hostname
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("multiworker drill requires loopback PostgreSQL")
    if parts.path not in {"/hackathon", "/hip_test"}:
        raise RuntimeError("multiworker drill requires a disposable test database name")
    return url


def verify() -> None:
    url = _allowed()
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    engine = create_engine(
        url, pool_pre_ping=True, pool_size=3, connect_args={"connect_timeout": 5}
    )
    sid: str | None = None
    domain = f"w6-multiworker-{uuid4().hex}.test"
    try:
        with engine.begin() as conn:
            sid = conn.execute(
                text("""
                INSERT INTO sources(name, domain, source_family, source_type, ingestion_mode,
                                    permission_status, detail_extraction_enabled, discovery_enabled)
                VALUES ('Multiworker CI', :domain, 'multiworker-ci', 'API', 'API',
                        'ALLOWED', true, true)
                RETURNING id::text
            """),
                {"domain": domain},
            ).scalar_one()
            conn.execute(
                text("""
                INSERT INTO source_operational_state(
                    source_id, next_due_at, interval_seconds, recrawl_priority
                ) VALUES (CAST(:sid AS uuid), clock_timestamp()-interval '1 minute', 3600, 1)
            """),
                {"sid": sid},
            )

        barrier = Barrier(2)

        def attempt_claim() -> dict[str, str] | None:
            barrier.wait(timeout=10)
            with engine.begin() as conn:
                return claim_due_source(conn, allowed_sources=[(domain, "multiworker-ci")])

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(attempt_claim)
            second = pool.submit(attempt_claim)
            attempts = [first.result(timeout=20), second.result(timeout=20)]
        claims = [item for item in attempts if item is not None]
        assert len(claims) == 1, "two schedulers claimed the same due slot"
        owner = claims[0]
        assert owner["source_id"] == sid
        token = owner["lease_token"]
        with engine.begin() as conn:
            remaining_seconds = conn.execute(
                text("""
                SELECT EXTRACT(EPOCH FROM lease_until-clock_timestamp())
                  FROM source_operational_state WHERE source_id=CAST(:sid AS uuid)
            """),
                {"sid": sid},
            ).scalar_one()
            assert (
                LEASE_DURATION.total_seconds() - 30
                < remaining_seconds
                <= LEASE_DURATION.total_seconds()
            ), "lease TTL was not set from PostgreSQL time"
            assert not renew_lease(conn, source_id=sid, lease_token=str(uuid4()))
            assert not complete_lease(conn, source_id=sid, lease_token=str(uuid4()), success=True)
            assert renew_lease(conn, source_id=sid, lease_token=token)
            # Simulate expiry at the database, never by trusting worker clocks.
            conn.execute(
                text("""
                UPDATE source_operational_state
                   SET lease_until=clock_timestamp()-interval '1 second'
                 WHERE source_id=CAST(:sid AS uuid)
            """),
                {"sid": sid},
            )
            assert not renew_lease(conn, source_id=sid, lease_token=token)
            assert not complete_lease(conn, source_id=sid, lease_token=token, success=True)

        with engine.begin() as conn:
            replacement = claim_due_source(conn, allowed_sources=[(domain, "multiworker-ci")])
            assert replacement is not None and replacement["lease_token"] != token
            assert not complete_lease(conn, source_id=sid, lease_token=token, success=True)
            conn.execute(
                text("UPDATE sources SET active=false WHERE id=CAST(:sid AS uuid)"), {"sid": sid}
            )
            assert not renew_lease(conn, source_id=sid, lease_token=replacement["lease_token"])
            assert claim_due_source(conn, allowed_sources=[(domain, "multiworker-ci")]) is None
        print("W6 PostgreSQL concurrent claims, DB-clock TTL, expiry, fencing and pause: PASS")
    finally:
        if sid is not None:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM sources WHERE id=CAST(:sid AS uuid)"), {"sid": sid})
        engine.dispose()


if __name__ == "__main__":
    verify()
