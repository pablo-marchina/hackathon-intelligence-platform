"""Synthetic, disposable PostgreSQL E2E: approved API pagination survives runs.

No real HTTP occurs. All fetches must correspond to explicit in-memory fixture
URLs on the approved official API host, and organizer websites are never fetched.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from hackathon_sources.backlog import SourceUrlBacklog
from hackathon_sources.factory import build_adapter
from hackathon_sources.models import FetchResult
from hackathon_sources.persistent_runner import (
    MAX_API_PAGINATION_HOPS,
    SourcePausedError,
    run_source_persisted,
)
from hackathon_sources.registry import SourceRegistryRepository, load_source_definitions
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def local_test_database() -> str:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("HIP_ALLOW_API_PAGINATION_DRILL") != "1":
        raise RuntimeError("explicit disposable-test opt-in required")
    url = os.environ["DATABASE_URL"]
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in {"127.0.0.1", "localhost"}
        or (parsed.database != "hackathon" and "_test" not in (parsed.database or ""))
    ):
        raise RuntimeError("refusing non-local/non-test PostgreSQL")
    return url


class FixtureFetcher:
    def __init__(self, pages: dict[str, dict]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    async def fetch(self, definition, url, *, etag=None, last_modified=None):
        assert definition.key == "hackclub_hackathons"
        assert url in self.pages, "unexpected/unapproved page requested"
        self.requests.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=json.dumps(self.pages[url]).encode(),
            fetched_at=datetime.now(UTC),
        )


def verify() -> None:
    source = next(
        item
        for item in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
        if item.key == "hackclub_hackathons"
    )
    assert source.durable_api_pages is True
    assert source.permission_status.value == "ALLOWED"
    assert MAX_API_PAGINATION_HOPS == 50
    seed = str(source.seed_urls[0])
    second = seed + "?page=2"
    third = seed + "?page=3"
    deep = seed + "?page=50"
    overlimit = seed + "?page=51"
    tag = uuid4().hex[:12]

    def page(number: int, next_url: str | None) -> dict:
        return {
            "data": (
                [
                    {
                        "id": f"ci-api-{tag}-{number}",
                        "name": f"CI Official Hackathon {tag} Edition {number}",
                        "website": f"https://ci-organizer-{tag}.invalid/{number}",
                        "starts_at": "2027-03-20T08:00:00Z",
                        "ends_at": "2027-03-21T08:00:00Z",
                        "modality": "online",
                    }
                ]
                if number <= 3
                else []
            ),
            "links": {"next": next_url},
        }

    pages = {
        seed: page(1, second),
        second: page(2, third),
        third: page(3, None),
        deep: page(50, overlimit),
    }
    fetcher = FixtureFetcher(pages)
    engine = create_engine(local_test_database(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            source_id = SourceRegistryRepository(conn).upsert(source)
            # Database permissions remain operator-owned.
            source_id = SourceRegistryRepository(conn).get_source_id(source)

        def run_one():
            with engine.connect() as conn:
                return asyncio.run(
                    run_source_persisted(build_adapter(source), fetcher, conn, max_pages=1)
                )

        first = run_one()
        assert first.fetched_pages == 1
        assert first.backlog_counts.get("PENDING") == 1
        assert first.backlog_counts.get("DONE") == 1
        assert fetcher.requests == [seed]

        # Revocation in PostgreSQL persists across the registry upsert; it must
        # block *before* the network request, leaving the second page queued.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE sources SET active=false WHERE id=CAST(:id AS uuid)"),
                {"id": source_id},
            )
        try:
            run_one()
        except SourcePausedError:
            pass
        else:
            raise AssertionError("paused source was allowed to crawl")
        assert fetcher.requests == [seed]
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE sources SET active=true WHERE id=CAST(:id AS uuid)"),
                {"id": source_id},
            )

        second_run = run_one()
        assert second_run.fetched_pages == 1
        assert second_run.backlog_counts.get("PENDING") == 1
        assert second_run.backlog_counts.get("DONE") == 2
        assert fetcher.requests == [seed, second]

        third_run = run_one()
        assert third_run.fetched_pages == 1
        assert third_run.backlog_counts.get("PENDING", 0) == 0
        assert third_run.backlog_counts.get("DONE") == 3
        assert fetcher.requests == [seed, second, third]

        immediate_repeat = run_one()
        assert immediate_repeat.fetched_pages == 0
        assert fetcher.requests == [seed, second, third]
        assert immediate_repeat.backlog_counts.get("DONE") == 3

        with engine.connect() as conn:
            refs = conn.execute(
                text("""
                    SELECT COUNT(DISTINCT source_event_id)
                      FROM source_event_refs
                     WHERE source_id=CAST(:id AS uuid)
                       AND source_event_id LIKE :prefix
                """),
                {"id": source_id, "prefix": f"ci-api-{tag}-%"},
            ).scalar_one()
            assert refs == 3
            stages = conn.execute(
                text("""
                    SELECT COUNT(*) FROM source_event_payloads
                    WHERE source_id=CAST(:id AS uuid)
                      AND payload->>'name' LIKE :prefix
                """),
                {"id": source_id, "prefix": f"CI Official Hackathon {tag}%"},
            ).scalar_one()
            assert stages == 3
            hops = conn.execute(
                text("""
                    SELECT b.metadata->>'pagination_depth' AS depth
                      FROM source_url_backlog b
                     WHERE b.source_id=CAST(:id AS uuid)
                       AND b.kind='DISCOVERED'
                     ORDER BY b.url
                """),
                {"id": source_id},
            ).scalars().all()
            assert sorted(hops) == ["1", "2"]

        # A unique API pagination chain must not evade hop limits merely
        # because the collector restarts between pages.
        with engine.begin() as conn:
            SourceUrlBacklog(conn).add(
                source_id,
                deep,
                kind="DISCOVERED",
                metadata={"pagination_depth": MAX_API_PAGINATION_HOPS},
            )
        deep_result = run_one()
        assert deep_result.fetched_pages == 1
        assert fetcher.requests[-1] == deep
        with engine.connect() as conn:
            assert conn.execute(
                text("""
                    SELECT COUNT(*) FROM source_url_backlog
                     WHERE source_id=CAST(:id AS uuid)
                       AND url=:url
                """),
                {"id": source_id, "url": overlimit},
            ).scalar_one() == 0

        print(
            "PASS: approved API pagination resumes across runs, respects pause, "
            "avoids re-fetch, and limits durable traversal depth"
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
