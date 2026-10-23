"""Two public Dribdat feeds -> canonical PostgreSQL, entirely synthetic HTTP.

Checks independent source ownership, historic editions, source-selected
description evidence, refusal of unrelated workshops, and idempotent replay.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from hackathon_sources.models import FetchResult
from hackathon_sources.registry import SourceRegistryRepository, load_source_definitions
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from scripts.ops.controlled_catalog_ingestion import _url, run_once

ROOT = Path(__file__).resolve().parents[2]
SOURCE_KEYS = {"dribdat_glamhack", "dribdat_imprs_astro"}


def _test_db() -> str:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("HIP_ALLOW_DRIBDAT_DB_DRILL") != "1":
        raise RuntimeError("explicit local test opt-in required")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL required")
    parsed = make_url(_url(dsn))
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in {"localhost", "127.0.0.1"}
        or (parsed.database != "hackathon" and "_test" not in (parsed.database or ""))
    ):
        raise RuntimeError("requires disposable local PostgreSQL")
    return _url(dsn)


class ClosedFetcher:
    def __init__(self, pages: dict[str, dict]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def fetch(self, source, url: str, **kwargs) -> FetchResult:
        assert source.key in SOURCE_KEYS
        assert url == f"https://{source.domain}/api/events.json"
        assert url in self.pages, "no unknown or external URLs can be fetched"
        self.calls.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=json.dumps(self.pages[url]).encode(),
            fetched_at=datetime.now(UTC),
        )


def verify() -> None:
    tag = uuid4().hex[:10]
    selected = [
        src.model_copy(update={"source_family": f"ci-{src.key}-{tag}"})
        for src in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
        if src.key in SOURCE_KEYS
    ]
    assert len(selected) == 2
    glamhack = next(s for s in selected if s.key == "dribdat_glamhack")
    astro = next(s for s in selected if s.key == "dribdat_imprs_astro")
    pages = {
        str(glamhack.seed_urls[0]): {
            "events": [
                {
                    "id": 13,
                    "name": f"CI GLAMhack 2026 {tag}",
                    "summary": "Open Cultural Data Hackathon in Switzerland",
                    "starts_at": "Thu, 12 Nov 2026 09:00:00 GMT",
                    "ends_at": "Sat, 14 Nov 2026 16:00:00 GMT",
                    "location": "Winterthur",
                    "webpage_url": f"https://organizer-{tag}.invalid/2026",
                },
                {
                    "id": 9,
                    "name": f"CI Open Cultural Data Hackathon 2015 {tag}",
                    "summary": "Historical cultural data hackathon",
                    "starts_at": "Fri, 27 Feb 2015 09:00:00 GMT",
                    "ends_at": "Sat, 28 Feb 2015 16:00:00 GMT",
                },
                {"id": 18, "name": f"CI Generic Workshop {tag}"},
            ]
        },
        str(astro.seed_urls[0]): {
            "events": [
                {
                    "id": 1,
                    "name": f"CI IMPRS Astro Hackathon 2024 {tag}",
                    "summary": "Science and astronomy hackathon",
                    "starts_at": "Tue, 21 May 2024 10:00:00 GMT",
                    "ends_at": "Fri, 24 May 2024 17:00:00 GMT",
                    "location": "Heidelberg, Germany",
                    "webpage_url": f"https://astronomy-{tag}.invalid/2024",
                }
            ]
        },
    }
    fetcher = ClosedFetcher(pages)
    engine = create_engine(_test_db(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            for source in selected:
                SourceRegistryRepository(conn).upsert(source)

        outcome = {}
        for source in selected:
            outcome[source.key] = asyncio.run(
                run_once(
                    engine,
                    source,
                    max_pages=1,
                    max_versions=10,
                    fetcher_factory=lambda: fetcher,
                )
            )

        assert outcome["dribdat_glamhack"]["run_outcomes"]["CREATED"] == 2, outcome
        assert outcome["dribdat_glamhack"]["run_outcomes"]["REJECTED"] == 1, outcome
        assert outcome["dribdat_imprs_astro"]["run_outcomes"]["CREATED"] == 1, outcome
        assert outcome["dribdat_glamhack"]["catalog_delta_observed"] == 2, outcome
        assert outcome["dribdat_imprs_astro"]["catalog_delta_observed"] == 1, outcome
        assert fetcher.calls == [str(glamhack.seed_urls[0]), str(astro.seed_urls[0])]
        assert all("/api/events.json" in url for url in fetcher.calls)

        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text("""
                        SELECT s.source_family,r.source_event_id,
                               h.name,h.start_at,h.official_url::text AS official_url,
                               h.id::text AS canonical_id
                          FROM sources s
                          JOIN source_event_refs r ON r.source_id=s.id
                          LEFT JOIN hackathons h ON h.id=r.hackathon_id
                         WHERE s.source_family IN (:family_a,:family_b)
                    """),
                    {"family_a": glamhack.source_family, "family_b": astro.source_family},
                )
                .mappings()
                .all()
            )
            assert len(rows) == 4, rows
            accepted = [row for row in rows if row["canonical_id"]]
            assert len(accepted) == 3
            assert len({row["canonical_id"] for row in accepted}) == 3
            older = next(row for row in accepted if "2015" in row["name"])
            assert older["start_at"].year == 2015
            assert sum(row["canonical_id"] is None for row in rows) == 1
            count_selected = conn.execute(
                text("""
                    SELECT COUNT(*)
                      FROM canonical_field_selections cfs
                      JOIN source_observations so ON so.id=cfs.selected_observation_id
                      JOIN sources s ON s.id=so.source_id
                     WHERE s.source_family IN (:family_a,:family_b)
                       AND cfs.field_path='short_description'
                       AND cfs.active
                """),
                {"family_a": glamhack.source_family, "family_b": astro.source_family},
            ).scalar_one()
            assert count_selected == 3
            glamhack_event = next(row for row in accepted if "GLAMhack 2026" in row["name"])
            assert glamhack_event["official_url"] == f"https://organizer-{tag}.invalid/2026"

        for source in selected:
            repeat = asyncio.run(
                run_once(
                    engine,
                    source,
                    max_pages=1,
                    max_versions=10,
                    fetcher_factory=lambda: fetcher,
                )
            )
            assert repeat["run_outcomes"]["CREATED"] == 0, repeat
            assert repeat["catalog_delta_observed"] == 0, repeat
        print(
            "PASS: two self-published Dribdat APIs, three canonical editions, one rejected workshop"
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
