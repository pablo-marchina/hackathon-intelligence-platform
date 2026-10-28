"""OpenData.ch JSON hackdays -> PostgreSQL, with synthetic closed HTTP only.

This tests explicit hashtag-derived event kind, historic editions, rejected
ordinary festivals, strict source URL boundaries and replay idempotency.
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
KEY = "dribdat_opendata_ch"
SEED = "https://new-hack.opendata.ch/api/events.json"


class ClosedFetcher:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def fetch(self, source, url: str, **kwargs) -> FetchResult:
        assert source.key == KEY
        assert url == SEED
        self.calls.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=json.dumps(self.data).encode(),
            fetched_at=datetime.now(UTC),
        )


def disposable_db() -> str:
    if os.getenv("APP_ENV") != "test" or os.getenv("HIP_ALLOW_OPENDATA_DB_DRILL") != "1":
        raise RuntimeError("disposable test opt-in required")
    dsn = os.environ["DATABASE_URL"]
    url = make_url(_url(dsn))
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"localhost", "127.0.0.1"}
        or url.database not in {"hackathon", "hackathon_test"}
    ):
        raise RuntimeError("refusing nonlocal or non-test PostgreSQL")
    return _url(dsn)


def verify() -> None:
    source = next(
        s for s in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json") if s.key == KEY
    )
    assert [str(u) for u in source.seed_urls] == [SEED]
    tag = uuid4().hex[:12]
    source = source.model_copy(update={"source_family": f"ci-opendata-ch-{tag}"})
    pages = {
        "events": [
            {
                "id": 76,
                "name": f"CI Energy Data Hackdays 2025 {tag}",
                "starts_at": "Thu, 11 Sep 2025 07:00:00 GMT",
                "ends_at": "Fri, 12 Sep 2025 15:00:00 GMT",
                "summary": "Open public energy data challenges",
                "webpage_url": f"https://energy-{tag}.invalid/2025",
            },
            {
                "id": 72,
                "name": f"CI Smart Regio Lab {tag}",
                "starts_at": "Fri, 21 Mar 2025 09:00:00 GMT",
                "ends_at": "Sat, 22 Mar 2025 16:00:00 GMT",
                "hashtags": "#lenzburg #hackathon #SmartRegioLab",
                "summary": "Hackathon for civic tech regional challenges",
                "webpage_url": f"https://regional-{tag}.invalid",
            },
            {
                "id": 73,
                "name": f"CI Tourism Mapping Festival {tag}",
                "hashtags": "#innovationfestival",
                "starts_at": "Fri, 14 Mar 2025 08:00:00 GMT",
            },
        ]
    }
    fetcher = ClosedFetcher(pages)
    engine = create_engine(disposable_db(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            SourceRegistryRepository(conn).upsert(source)

        first = asyncio.run(
            run_once(engine, source, max_pages=1, max_versions=10, fetcher_factory=lambda: fetcher)
        )
        assert first["status"] == "SUCCEEDED", first
        assert first["run_outcomes"]["CREATED"] == 2, first
        assert first["run_outcomes"]["REJECTED"] == 1, first
        assert first["catalog_delta_observed"] == 2, first
        assert fetcher.calls == [SEED]

        with engine.connect() as conn:
            refs = (
                conn.execute(
                    text("""
                        SELECT r.source_event_id, h.name, h.start_at, h.id::text AS canonical_id
                        FROM source_event_refs r
                        JOIN sources s ON s.id=r.source_id
                        LEFT JOIN hackathons h ON h.id=r.hackathon_id
                        WHERE s.source_family=:family
                    """),
                    {"family": source.source_family},
                )
                .mappings()
                .all()
            )
            assert len(refs) == 3, refs
            accepted = [r for r in refs if r["canonical_id"]]
            assert {r["source_event_id"] for r in accepted} == {"76", "72"}
            assert len({r["canonical_id"] for r in accepted}) == 2
            assert all(r["start_at"].year == 2025 for r in accepted)
            assert sum(r["canonical_id"] is None for r in refs) == 1
            descriptions = conn.execute(
                text("""
                    SELECT COUNT(*) FROM canonical_field_selections cfs
                    JOIN source_observations so ON so.id=cfs.selected_observation_id
                    JOIN sources s ON s.id=so.source_id
                    WHERE s.source_family=:family
                      AND cfs.field_path='short_description' AND cfs.active
                """),
                {"family": source.source_family},
            ).scalar_one()
            assert descriptions == 2

        repeat = asyncio.run(
            run_once(engine, source, max_pages=1, max_versions=10, fetcher_factory=lambda: fetcher)
        )
        assert repeat["run_outcomes"]["CREATED"] == 0, repeat
        assert repeat["catalog_delta_observed"] == 0, repeat
        print("PASS: OpenData.ch two hackdays, explicit hashtag, festival rejected, replay idempotent")
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
