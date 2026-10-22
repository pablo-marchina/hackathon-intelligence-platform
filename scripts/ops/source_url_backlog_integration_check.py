"""Rollback-safe-ish (cleanup via cascade) SQL integration for source-scoped feed queues.

Requires an explicitly designated test DB. No HTTP, crawls or emails.
Creates test sources committed so two independent PostgreSQL connections can
exercise real fencing; deletes them in finally, cascading the queue rows.
"""

from __future__ import annotations

import os
from uuid import uuid4

from hackathon_sources.backlog import BacklogClaim, SourceUrlBacklog
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def verify() -> None:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("HIP_ALLOW_BACKLOG_DB_DRILL") != "1":
        raise RuntimeError(
            "refusing to run source URL backlog exercise outside an explicit test environment"
        )
    database_url = os.environ["DATABASE_URL"]
    parsed_url = make_url(database_url)
    if parsed_url.host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("backlog drill only accepts a localhost PostgreSQL test server")
    if parsed_url.database != "hackathon" and "_test" not in (parsed_url.database or ""):
        raise RuntimeError("backlog drill requires an isolated test database")
    engine = create_engine(database_url, pool_pre_ping=True)
    tag = uuid4().hex
    sources = []
    try:
        with engine.begin() as conn:
            for suffix in ("a", "b"):
                sid = conn.execute(
                    text("""
                    INSERT INTO sources(name, domain, source_family, source_type,
                                        ingestion_mode, permission_status, active)
                    VALUES (:name, :domain, :family, 'CI', 'RSS', 'ALLOWED', true)
                    RETURNING id::text
                """),
                    {
                        "name": f"Backlog CI {tag}-{suffix}",
                        "domain": f"{tag}-{suffix}.invalid",
                        "family": f"backlog-test-{tag}",
                    },
                ).scalar_one()
                sources.append(sid)
        url = f"https://{tag}.example/feed.xml"
        with engine.begin() as conn:
            one = SourceUrlBacklog(conn)
            one.add(sources[0], url, kind="SEED", priority=10)
            one.add(sources[0], url, kind="SEED", priority=10)
            one.add(sources[1], url, kind="SEED", priority=10)
            assert (
                conn.execute(
                    text("""
                SELECT count(*) FROM source_url_backlog
                WHERE source_id IN (CAST(:a AS uuid), CAST(:b AS uuid))
            """),
                    {"a": sources[0], "b": sources[1]},
                ).scalar_one()
                == 2
            )
        with engine.begin() as conn:
            first = SourceUrlBacklog(conn).claim(sources[0])
            assert first is not None and first.kind == "SEED"
        with engine.begin() as concurrent:
            assert SourceUrlBacklog(concurrent).claim(sources[0]) is None
            second = SourceUrlBacklog(concurrent).claim(sources[1])
            assert second is not None and second.id != first.id
            assert SourceUrlBacklog(concurrent).succeed(second)
        with engine.begin() as conn:
            backlog = SourceUrlBacklog(conn)
            assert not backlog.succeed(BacklogClaim(first.id, first.url, str(uuid4()), first.kind))
            assert backlog.succeed(first)
            assert not backlog.succeed(first)
            # A successful seed remains DONE until the configured revisit window.
            # Advance the test record's clock to simulate that window expiring.
            conn.execute(
                text("""
                UPDATE source_url_backlog SET next_fetch_at=clock_timestamp()-INTERVAL '1 second'
                WHERE id=:id
            """),
                {"id": first.id},
            )
            backlog.add(sources[0], url, kind="SEED")
            retry = backlog.claim(sources[0])
            assert retry is not None and retry.token != first.token
            assert backlog.fail(retry, reason="temporary upstream failure")
            assert backlog.claim(sources[0]) is None  # retry delay is enforced
            conn.execute(
                text("""
                UPDATE source_url_backlog SET next_fetch_at = clock_timestamp() - INTERVAL '1 second'
                 WHERE id=:id
            """),
                {"id": retry.id},
            )
            recovered = backlog.claim(sources[0])
            assert recovered is not None
            assert backlog.succeed(recovered)
            # Same URL from another source must not share a claim or status.
            assert backlog.stats(sources[0]).get("DONE") == 1
            assert backlog.stats(sources[1]).get("DONE") == 1
        with engine.begin() as conn:
            SourceUrlBacklog(conn).add(sources[0], f"https://{tag}.example/a", kind="DISCOVERED")
            crashed = SourceUrlBacklog(conn).claim(sources[0])
            assert crashed is not None
            conn.execute(
                text("""
                UPDATE source_url_backlog SET claim_until=clock_timestamp()-INTERVAL '1 second'
                WHERE id=:id
            """),
                {"id": crashed.id},
            )
        with engine.begin() as conn:
            resumed = SourceUrlBacklog(conn).claim(sources[0])
            assert resumed is not None and resumed.token != crashed.token
            assert not SourceUrlBacklog(conn).succeed(crashed)
            assert SourceUrlBacklog(conn).succeed(resumed)
        # RSS page depth must survive a worker restart and a SQL roundtrip;
        # it is used to bound cross-document pagination chains.
        with engine.begin() as conn:
            backlog = SourceUrlBacklog(conn)
            backlog.add(
                sources[0],
                f"https://{tag}.example/feed.xml?page=14",
                kind="DISCOVERED",
                metadata={"pagination_depth": 14},
            )
            paginated = backlog.claim(sources[0])
            assert paginated is not None and paginated.pagination_depth == 14
            assert backlog.succeed(paginated)
            # A sitemap-index child retains its traversal depth after the
            # reservation transaction is committed and another worker claims.
            backlog.add(
                sources[0],
                f"https://{tag}.example/nested.xml",
                kind="DISCOVERED",
                metadata={"sitemap_depth": 7},
            )
            # Rediscovery via a shorter path takes precedence over a deep
            # back-edge. A duplicate lacking depth must not erase that depth.
            backlog.add(
                sources[0],
                f"https://{tag}.example/nested.xml",
                kind="DISCOVERED",
                metadata={"sitemap_depth": 3},
            )
            backlog.add(
                sources[0],
                f"https://{tag}.example/nested.xml",
                kind="DISCOVERED",
                metadata={"parent_url": url},
            )
        with engine.begin() as conn:
            backlog = SourceUrlBacklog(conn)
            sitemap = backlog.claim(sources[0])
            assert sitemap is not None and sitemap.sitemap_depth == 3
            assert sitemap.pagination_depth == 0
            assert backlog.succeed(sitemap)
        print(
            "Source URL backlog SQL: isolation, fencing, retries, recovery, RSS/sitemap depth PASS"
        )
    finally:
        try:
            with engine.begin() as conn:
                for sid in sources:
                    conn.execute(
                        text("DELETE FROM sources WHERE id=CAST(:id AS uuid)"), {"id": sid}
                    )
        finally:
            engine.dispose()


if __name__ == "__main__":
    verify()
