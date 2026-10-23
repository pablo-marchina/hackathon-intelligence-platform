"""Official YSWS API synthetic end-to-end PostgreSQL acceptance drill.

Uses a closed fake fetcher, a distinct per-test source family, and a disposable
local database. No public/third-party URL is ever requested by the test.
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
SEED = "https://events-api.hackclub.dev/api/programs"


class FakeOfficialFetcher:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def fetch(self, source, url: str, **kwargs) -> FetchResult:
        assert source.key == "hackclub_ysws_programs"
        assert url == SEED  # No event landing page follows from an API list.
        self.calls.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=json.dumps(self.payload).encode("utf-8"),
            fetched_at=datetime.now(UTC),
        )


def _disposable_url() -> str:
    if os.getenv("APP_ENV") != "test" or os.getenv("HIP_ALLOW_YSWS_DB_DRILL") != "1":
        raise RuntimeError("only explicitly opted-in tests are allowed")
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL required")
    url = _url(raw)
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in {"localhost", "127.0.0.1"}
        or (parsed.database != "hackathon" and "_test" not in (parsed.database or ""))
    ):
        raise RuntimeError("refusing non-disposable/nonlocal PostgreSQL")
    return url


def verify() -> None:
    source = next(
        item
        for item in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
        if item.key == "hackclub_ysws_programs"
    )
    assert [str(url) for url in source.seed_urls] == [SEED]
    tag = uuid4().hex[:12]
    source = source.model_copy(update={"source_family": f"ci-ysws-{tag}"})
    payload = {
        "success": True,
        "count": 4,
        "data": [
            {
                "id": f"rec-hack-{tag}",
                "Name": f"CI Global AI Hackathon {tag}",
                "Start Date": "2027-06-01",
                "End Date": "2027-06-03",
                "Website URL": f"https://ai-{tag}.invalid",
                "HCB": f"https://hcb.hackclub.com/ci-{tag}",
            },
            {
                "id": f"rec-data-{tag}",
                "Name": f"CI Open Climate Datathon {tag}",
                "Start Date": "2027-07-01",
                "End Date": "2027-07-02",
                "Website URL": None,
            },
            {
                "id": f"rec-workshop-{tag}",
                "Name": f"CI Electronics Workshop {tag}",
                "Start Date": "2027-07-12",
            },
            {
                "id": f"rec-ordinary-{tag}",
                "Name": f"CI Stardance Program {tag}",
                "Start Date": "2027-05-01",
            },
        ],
    }
    fetcher = FakeOfficialFetcher(payload)
    engine = create_engine(_disposable_url(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            SourceRegistryRepository(conn).upsert(source)

        first = asyncio.run(
            run_once(
                engine,
                source,
                max_pages=1,
                max_versions=10,
                fetcher_factory=lambda: fetcher,
            )
        )
        assert first["status"] == "SUCCEEDED", first
        assert first["pages_fetched"] == 1, first
        assert first["run_outcomes"]["CREATED"] == 2, first
        assert first["run_outcomes"]["REJECTED"] == 2, first
        assert first["run_outcomes"]["LINKED"] == 0, first
        assert first["catalog_delta_observed"] == 2, first
        assert fetcher.calls == [SEED]

        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text("""
                        SELECT r.source_event_id,r.hackathon_id::text AS event_id,
                               h.name,h.official_url::text AS official_url
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
            assert len(rows) == 4, rows
            accepted = [row for row in rows if row["event_id"] is not None]
            assert len(accepted) == 2, rows
            assert len({row["event_id"] for row in accepted}) == 2
            assert {row["source_event_id"] for row in accepted} == {
                f"rec-hack-{tag}",
                f"rec-data-{tag}",
            }
            assert all(row["official_url"] is None for row in accepted if "Datathon" in row["name"])
            selected = conn.execute(
                text("""
                    SELECT COUNT(*)
                      FROM canonical_field_selections cfs
                      JOIN source_observations so
                        ON so.id=cfs.selected_observation_id
                      JOIN sources s ON s.id=so.source_id
                     WHERE s.source_family=:family
                       AND cfs.field_path='name' AND cfs.active
                """),
                {"family": source.source_family},
            ).scalar_one()
            assert selected == 2

        repeat = asyncio.run(
            run_once(
                engine,
                source,
                max_pages=1,
                max_versions=10,
                fetcher_factory=lambda: fetcher,
            )
        )
        assert repeat["status"] == "SUCCEEDED", repeat
        assert repeat["run_outcomes"]["CREATED"] == 0, repeat
        assert repeat["catalog_delta_observed"] == 0, repeat
        print(
            "PASS: official YSWS synthetic API accepts two hackathons, rejects two programs, replays safely"
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
