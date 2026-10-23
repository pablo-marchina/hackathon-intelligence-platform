"""Opt-in PostgreSQL E2E of two approved source APIs and canonical source fusion.

All HTTP payloads are synthetic and supplied from a closed in-memory fixture
dictionary. Never connects to an external domain or a production database.
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

from scripts.ops.controlled_batch_ingestion import run_batch
from scripts.ops.controlled_catalog_ingestion import _url, run_once

ROOT = Path(__file__).resolve().parents[2]


class ClosedFetcher:
    def __init__(self, pages: dict[str, dict]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def fetch(self, source, url: str, **kwargs) -> FetchResult:
        # A newly discovered third-party event URL raises, not an HTTP call.
        self.requests.append(url)
        payload = self.pages[url]
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=json.dumps(payload).encode("utf-8"),
            fetched_at=datetime.now(UTC),
        )


def disposable_database_url() -> str:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("HIP_ALLOW_BATCH_DB_DRILL") != "1":
        raise RuntimeError("explicit disposable-test opt-in required")
    value = os.environ["DATABASE_URL"]
    url = _url(value)
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in ("127.0.0.1", "localhost")
        or (parsed.database != "hackathon" and "_test" not in (parsed.database or ""))
    ):
        raise RuntimeError("refusing non-disposable PostgreSQL")
    return url


def verify() -> None:
    engine = create_engine(disposable_database_url(), pool_pre_ping=True)
    tag = uuid4().hex[:12]
    name = f"CI Multi-Source Hackathon {tag}"
    external_url = f"https://event-{tag}.invalid/hackathon"
    sources = {
        item.key: item for item in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    }
    directory = sources["hackclub_hackathons"]
    programs = sources["hackclub_programs"]
    pages: dict[str, dict] = {
        str(directory.seed_urls[0]): {
            "data": [
                {
                    "id": f"directory-{tag}",
                    "name": name,
                    "website": external_url,
                    "starts_at": "2027-04-01T10:00:00Z",
                    "ends_at": "2027-04-02T10:00:00Z",
                    "modality": "online",
                    "location": {},
                }
            ],
            "links": {"next": None},
        },
    }
    for seed in programs.seed_urls:
        status = str(seed).split("status=", 1)[1].split("&", 1)[0]
        pages[str(seed)] = {
            "data": [
                {
                    "id": f"program-{tag}",
                    "name": name,
                    "url": external_url,
                    "startDate": "2027-04-01",
                    "endDate": "2027-04-02",
                    "format": "both",
                    "status": "upcoming",
                    "description": "Teams build projects at this hackathon.",
                }
            ]
            if status == "upcoming"
            else [],
        }
    fetcher = ClosedFetcher(pages)

    async def runner(engine, source, *, max_pages, max_versions):
        return await run_once(
            engine,
            source,
            max_pages=max_pages,
            max_versions=max_versions,
            fetcher_factory=lambda: fetcher,
        )

    try:
        with engine.begin() as conn:
            for source in (directory, programs):
                SourceRegistryRepository(conn).upsert(source)

        first = asyncio.run(
            run_batch(engine, [directory, programs], pages=3, versions=10, runner=runner)
        )
        assert first["status"] == "SUCCEEDED", first
        assert first["run_attributed_outcomes"]["CREATED"] == 1, first
        assert first["run_attributed_outcomes"]["LINKED"] == 1, first
        assert first["catalog_delta_observed"] == 1, first
        assert first["sources_succeeded"] == 2, first
        assert len(fetcher.requests) == 4
        assert set(fetcher.requests) == set(pages)
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text("""
                    SELECT r.hackathon_id::text
                      FROM source_event_refs r
                      JOIN sources s ON s.id=r.source_id
                     WHERE r.source_event_id IN (:dir_ref,:program_ref)
                       AND s.source_family IN ('hackclub_hackathons','hackclub_programs')
                """),
                    {"dir_ref": f"directory-{tag}", "program_ref": f"program-{tag}"},
                )
                .scalars()
                .all()
            )
            assert len(rows) == 2 and rows[0] == rows[1], rows
            canonical = (
                conn.execute(
                    text("""
                    SELECT source_count,name
                      FROM hackathons
                     WHERE id=CAST(:id AS uuid)
                """),
                    {"id": rows[0]},
                )
                .mappings()
                .one()
            )
            assert canonical["name"] == name
            assert canonical["source_count"] >= 2

        repeat = asyncio.run(
            run_batch(engine, [directory, programs], pages=3, versions=10, runner=runner)
        )
        assert repeat["status"] == "SUCCEEDED", repeat
        assert repeat["run_attributed_outcomes"]["CREATED"] == 0, repeat
        assert repeat["run_attributed_outcomes"]["LINKED"] == 0, repeat
        assert repeat["catalog_delta_observed"] == 0, repeat
        print("PASS: synthetic two-API PostgreSQL batch creates one unique event, links, replays")
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
