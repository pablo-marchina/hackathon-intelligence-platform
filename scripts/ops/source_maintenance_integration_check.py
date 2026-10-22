"""Destructive W6 scheduler check against an isolated, migrated CI PostgreSQL.

No network is contacted. Run only with HIP_ALLOW_SOURCE_DB_DRILL=1 and APP_ENV=test.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hackathon_operations.source_maintenance import (
    claim_due_source,
    complete_lease,
    maintain_sources,
    renew_lease,
)
from sqlalchemy import create_engine, text


def verify() -> None:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("HIP_ALLOW_SOURCE_DB_DRILL") != "1":
        raise RuntimeError("refusing to mutate non-isolated database")
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    sid: str | None = None
    now = datetime.now(UTC)
    domain = f"scheduler-ci-{uuid4().hex}.test"
    try:
        with engine.begin() as conn:
            sid = conn.execute(
                text("""
                INSERT INTO sources(name, domain, source_family, source_type, ingestion_mode,
                                    permission_status, detail_extraction_enabled)
                VALUES ('Scheduler CI', :domain, 'scheduler-ci', 'API', 'API', 'ALLOWED', true)
                RETURNING id::text
            """),
                {"domain": domain},
            ).scalar_one()
            conn.execute(
                text("""
                INSERT INTO crawl_runs(source_id,status,started_at,finished_at,
                                       fetched_count,parsed_count,valid_event_count)
                VALUES (CAST(:sid AS uuid),'SUCCEEDED',:start,:end,40,40,30)
            """),
                {"sid": sid, "start": now - timedelta(days=2), "end": now - timedelta(days=2)},
            )
            conn.execute(
                text("""
                INSERT INTO crawl_runs(source_id,status,started_at,finished_at,
                                       fetched_count,parsed_count,valid_event_count)
                VALUES (CAST(:sid AS uuid),'SUCCEEDED',:start,:end,40,0,0)
            """),
                {
                    "sid": sid,
                    "start": now - timedelta(minutes=12),
                    "end": now - timedelta(minutes=10),
                },
            )

        with engine.begin() as conn:
            first = maintain_sources(conn, now=now)
            assert first.runs_processed >= 1
            assert first.reviews_created == 2, first
        with engine.begin() as conn:
            repeated = maintain_sources(conn, now=now + timedelta(minutes=1))
            assert repeated.reviews_created == 0 and repeated.runs_processed == 0
            assert (
                conn.execute(
                    text("""
                SELECT health::text FROM sources WHERE id=CAST(:sid AS uuid)
            """),
                    {"sid": sid},
                ).scalar_one()
                == "BROKEN"
            )
            count = conn.execute(
                text("""
                SELECT count(*) FROM review_queue_items
                WHERE queue_type='SOURCE_ANOMALY' AND payload->>'source_id'=:sid
            """),
                {"sid": sid},
            ).scalar_one()
            assert count == 2
            conn.execute(
                text("""
                UPDATE source_operational_state SET next_due_at=:due
                 WHERE source_id=CAST(:sid AS uuid)
            """),
                {"sid": sid, "due": now - timedelta(seconds=1)},
            )
            conn.execute(
                text("UPDATE sources SET active=false WHERE id=CAST(:sid AS uuid)"), {"sid": sid}
            )
            assert (
                claim_due_source(conn, now=now, allowed_sources=[(domain, "scheduler-ci")]) is None
            )
            conn.execute(
                text(
                    "UPDATE sources SET active=true,permission_status='REVIEW_REQUIRED' WHERE id=CAST(:sid AS uuid)"
                ),
                {"sid": sid},
            )
            assert (
                claim_due_source(conn, now=now, allowed_sources=[(domain, "scheduler-ci")]) is None
            )
            conn.execute(
                text("UPDATE sources SET permission_status='ALLOWED' WHERE id=CAST(:sid AS uuid)"),
                {"sid": sid},
            )
            lease = claim_due_source(conn, now=now, allowed_sources=[(domain, "scheduler-ci")])
            assert lease is not None and lease["source_id"] == sid
            assert (
                claim_due_source(conn, now=now, allowed_sources=[(domain, "scheduler-ci")]) is None
            )
            assert not renew_lease(conn, source_id=sid, lease_token=str(uuid4()), now=now)
            assert renew_lease(conn, source_id=sid, lease_token=lease["lease_token"], now=now)
            conn.execute(
                text("UPDATE sources SET active=false WHERE id=CAST(:sid AS uuid)"), {"sid": sid}
            )
            assert not renew_lease(conn, source_id=sid, lease_token=lease["lease_token"], now=now)
            conn.execute(
                text("UPDATE sources SET active=true WHERE id=CAST(:sid AS uuid)"), {"sid": sid}
            )
            assert not renew_lease(
                conn,
                source_id=sid,
                lease_token=lease["lease_token"],
                now=now + timedelta(hours=1),
            ), "an expired owner must never renew its lease"
            assert not complete_lease(
                conn, source_id=sid, lease_token=str(uuid4()), success=False, now=now
            )
            assert complete_lease(
                conn, source_id=sid, lease_token=lease["lease_token"], success=True, now=now
            )
            # Expired owner with an unchanged token must not defer recrawl.
            conn.execute(
                text("""
                UPDATE source_operational_state SET next_due_at=:due
                 WHERE source_id=CAST(:sid AS uuid)
            """),
                {"sid": sid, "due": now - timedelta(minutes=1)},
            )
            expired = claim_due_source(
                conn,
                now=now + timedelta(minutes=1),
                allowed_sources=[(domain, "scheduler-ci")],
            )
            assert expired is not None
            assert not complete_lease(
                conn,
                source_id=sid,
                lease_token=expired["lease_token"],
                success=True,
                now=now + timedelta(hours=2),
            )
        print(
            "W6 scheduler DB idempotency, anomaly notices, pause, approval and fenced renewal: PASS"
        )
    finally:
        with engine.begin() as conn:
            if sid:
                conn.execute(
                    text("""
                    DELETE FROM review_queue_items WHERE queue_type='SOURCE_ANOMALY'
                    AND payload->>'source_id'=:sid
                """),
                    {"sid": sid},
                )
                conn.execute(text("DELETE FROM sources WHERE id=CAST(:sid AS uuid)"), {"sid": sid})
        engine.dispose()


if __name__ == "__main__":
    verify()
