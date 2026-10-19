#!/usr/bin/env python3
"""W6 PostgreSQL-only concurrency drill for source pause and lease fencing.

Creates and deletes an isolated source. Never dispatches network traffic.
Run only on a disposable migrated test database, with explicit opt-in flags.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import uuid4

from sqlalchemy import create_engine, text

from hackathon_sources.registry import SourceRegistryRepository


def verify() -> None:
    if os.getenv("APP_ENV") != "test" or os.getenv("HIP_ALLOW_SOURCE_DB_DRILL") != "1":
        raise RuntimeError("refusing to modify a database outside the isolated test drill")
    url = os.environ["DATABASE_URL"]
    if not (url.startswith("postgresql://") or url.startswith("postgresql+psycopg://")):
        raise RuntimeError("the concurrency drill requires PostgreSQL")

    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    sid: str | None = None
    done = Event()
    began = Event()
    failures: list[BaseException] = []
    worker: Thread | None = None
    try:
        with engine.begin() as conn:
            sid = conn.execute(text("""
                INSERT INTO sources(
                    name, domain, source_family, source_type, ingestion_mode,
                    permission_status, detail_extraction_enabled
                ) VALUES ('Fence CI', :domain, 'fence-ci', 'API', 'API', 'ALLOWED', true)
                RETURNING id::text
            """), {"domain": f"fence-{uuid4().hex}.test"}).scalar_one()

        def pause_in_other_transaction() -> None:
            began.set()
            try:
                with engine.begin() as other:
                    other.execute(text("""
                        UPDATE sources SET active=false
                         WHERE id=CAST(:sid AS uuid)
                    """), {"sid": sid})
            except BaseException as exc:
                failures.append(exc)
            finally:
                done.set()

        # The active page transaction holds FOR SHARE. The operator's FOR
        # UPDATE-equivalent must wait for the page to commit or roll back.
        with engine.begin() as conn:
            assert SourceRegistryRepository(conn).authorized_for_commit(sid)
            worker = Thread(target=pause_in_other_transaction, daemon=True)
            worker.start()
            assert began.wait(5), "admin update did not start"
            assert not done.wait(0.25), "operator pause bypassed page commit fence"
        assert done.wait(5), "operator pause remained blocked after page commit"
        worker.join(timeout=1)
        assert not failures, f"operator update failed: {[type(e).__name__ for e in failures]}"
        with engine.begin() as conn:
            assert not SourceRegistryRepository(conn).authorized_for_commit(sid)
            lease_token = str(uuid4())
            conn.execute(text("UPDATE sources SET active=true WHERE id=CAST(:sid AS uuid)"), {"sid": sid})
            conn.execute(text("""
                INSERT INTO source_operational_state(source_id,lease_token,lease_until)
                VALUES (CAST(:sid AS uuid),CAST(:token AS uuid),:until)
            """), {
                "sid": sid, "token": lease_token,
                "until": datetime.now(UTC) + timedelta(minutes=2),
            })

        def replace_lease_in_other_transaction() -> None:
            began.set()
            try:
                with engine.begin() as other:
                    other.execute(text("""
                        UPDATE source_operational_state
                           SET lease_token=CAST(:new_token AS uuid)
                         WHERE source_id=CAST(:sid AS uuid)
                    """), {"sid": sid, "new_token": str(uuid4())})
            except BaseException as exc:
                failures.append(exc)
            finally:
                done.set()

        began.clear()
        done.clear()
        with engine.begin() as conn:
            repo = SourceRegistryRepository(conn)
            assert repo.authorized_for_commit(sid, lease_token=lease_token)
            assert not repo.authorized_for_commit(sid, lease_token=str(uuid4()))
            worker = Thread(target=replace_lease_in_other_transaction, daemon=True)
            worker.start()
            assert began.wait(5), "lease replacement did not start"
            assert not done.wait(0.25), "new lease owner bypassed page commit fence"
        assert done.wait(5), "lease replacement remained blocked after page commit"
        worker.join(timeout=1)
        assert not failures, f"lease update failed: {[type(e).__name__ for e in failures]}"
        with engine.begin() as conn:
            assert not SourceRegistryRepository(conn).authorized_for_commit(
                sid, lease_token=lease_token,
            ), "stale owner must not commit after token replacement"

        # PostgreSQL now() is a transaction snapshot. The fencing read must
        # use clock_timestamp() so an expired token never passes a long TX.
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE source_operational_state SET lease_until=clock_timestamp()-interval '1 second'
                 WHERE source_id=CAST(:sid AS uuid)
            """), {"sid": sid})
            repo = SourceRegistryRepository(conn)
            assert not repo.has_current_lease(sid, lease_token)
            assert not repo.authorized_for_commit(sid, lease_token=lease_token)
        print("W6 source/lease commit fence with real PostgreSQL row locks: PASS")
    finally:
        if worker is not None and worker.is_alive():
            # Never delete while the concurrent update could still be blocked.
            worker.join(timeout=5)
        if sid and (worker is None or not worker.is_alive()):
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM sources WHERE id=CAST(:sid AS uuid)"), {"sid": sid})
        engine.dispose()


if __name__ == "__main__":
    verify()
