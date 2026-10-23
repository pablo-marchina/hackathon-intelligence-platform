"""End-to-end authorized API coverage test with disposable PostgreSQL.

No third-party network. Only APP_ENV=test, explicit opt-in, and local disposable
PostgreSQL are accepted. The test databases are destroyed by GitHub Actions.
"""

from __future__ import annotations

import asyncio
import base64
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
MAX_EVENTS = 10


class FakeFetcher:
    """Only exact, preinstalled fixture URLs may be fetched."""

    def __init__(self, pages: dict[str, object]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    async def __aenter__(self) -> FakeFetcher:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def fetch(self, source: object, url: str, **kwargs: object) -> FetchResult:
        self.requests.append(url)
        body = self.pages[url]
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=json.dumps(body, ensure_ascii=False).encode(),
            fetched_at=datetime.now(UTC),
        )


def _disposable_url() -> str:
    if os.getenv("APP_ENV") != "test" or os.getenv("HIP_ALLOW_COVERAGE_API_DB_DRILL") != "1":
        raise RuntimeError("disposable test env and explicit opt-in are required")
    target = os.getenv("DATABASE_URL")
    if not target:
        raise RuntimeError("DATABASE_URL is required")
    url = _url(target)
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in ("127.0.0.1", "localhost")
        or (parsed.database != "hackathon" and "_test" not in (parsed.database or ""))
    ):
        raise RuntimeError("refusing to modify non-disposable database")
    return url


def _verify_one(
    engine: object,
    source_key: str,
    pages: dict[str, object],
    *,
    expected_created: int,
    expected_rejected: int,
    max_pages: int,
    expected_names: set[str],
    tag: str,
) -> None:
    source = next(
        item
        for item in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
        if item.key == source_key
    )
    # These E2E fixtures have dedicated DB families so durable seed cooldowns
    # cannot mask the next test's API pages or bias per-run yield receipts.
    source = source.model_copy(update={"source_family": f"ci-coverage-{source_key}-{tag}"})
    # Original API URLs stay on the approved source host; no event-website
    # fetches are allowed. The fixture must cover all expected seeds.
    assert set(str(item) for item in source.seed_urls) == set(pages)
    assert len(pages) == max_pages
    with engine.begin() as conn:
        SourceRegistryRepository(conn).upsert(source)
    fetcher = FakeFetcher(pages)
    first = asyncio.run(
        run_once(
            engine,
            source,
            max_pages=max_pages,
            max_versions=MAX_EVENTS,
            fetcher_factory=lambda: fetcher,
        )
    )
    assert first["status"] == "SUCCEEDED", first
    assert first["pages_fetched"] == max_pages, first
    assert first["processed_this_invocation"]["CREATED"] == expected_created, first
    assert first["processed_this_invocation"]["REJECTED"] == expected_rejected, first
    assert first["run_outcomes"]["CREATED"] == expected_created, first
    assert first["run_outcomes"]["REJECTED"] == expected_rejected, first
    assert fetcher.requests == [str(u) for u in source.seed_urls]

    with engine.connect() as conn:
        refs = (
            conn.execute(
                text("""
                SELECT r.source_event_id,r.hackathon_id,h.name,h.official_url
                  FROM source_event_refs r
                  JOIN sources s ON s.id=r.source_id
                  LEFT JOIN hackathons h ON h.id=r.hackathon_id
                 WHERE s.domain=:domain AND s.source_family=:family
                   AND r.source_event_id IS NOT NULL
                 ORDER BY r.source_event_id
            """),
                {"domain": source.domain, "family": source.source_family},
            )
            .mappings()
            .all()
        )
        names = {row["name"] for row in refs if row["name"] in expected_names}
        assert names == expected_names, (names, first)
        assert (
            len({row["hackathon_id"] for row in refs if row["name"] in names}) == expected_created
        )
        assert len(refs) == expected_created + expected_rejected
        if source_key == "hackclub_programs":
            # The official status is source evidence, never invented
            # registration availability or a permission to fetch the event site.
            statuses = conn.execute(
                text("""
                    SELECT COUNT(DISTINCT so.hackathon_id)
                      FROM source_observations so
                      JOIN sources s ON s.id=so.source_id
                     WHERE s.source_family=:family
                       AND so.field_path='source_event_status'
                """),
                {"family": source.source_family},
            ).scalar_one()
            assert statuses == expected_created
        if source_key == "agenda_tech_brasil":
            assert any(row["official_url"] is None for row in refs if row["name"] in names)

    second = asyncio.run(
        run_once(
            engine,
            source,
            max_pages=max_pages,
            max_versions=MAX_EVENTS,
            fetcher_factory=lambda: fetcher,
        )
    )
    assert second["processed_this_invocation"]["CREATED"] == 0, second
    assert second["catalog_delta_observed"] == 0, second
    assert second["run_outcomes"]["CREATED"] == 0, second
    print(f"PASS {source_key} ({tag}): created={expected_created}, rejected={expected_rejected}")


def verify() -> None:
    url = _disposable_url()
    tag = uuid4().hex[:10]
    sources = {
        item.key: item for item in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    }
    programs = sources["hackclub_programs"]
    pages: dict[str, object] = {}
    for i, status in enumerate(("upcoming", "ongoing", "ended")):
        name = (
            f"CI AI Hackathon {tag}"
            if status == "upcoming"
            else f"CI Climate Datathon {tag}"
            if status == "ongoing"
            else f"CI Regular Workshop {tag}"
        )
        pages[str(programs.seed_urls[i])] = {
            "data": [
                {
                    "id": f"ci-{tag}-{status}",
                    "slug": f"ci-{tag}-{status}",
                    "name": name,
                    "description": f"CI test event {tag}",
                    "url": f"https://event-{tag}-{status}.invalid",
                    "startDate": "2026-10-21",
                    "endDate": "2026-10-22",
                    "format": "both",
                    "status": status,
                    "projectTypes": ["Software"],
                    "requirements": None,
                }
            ]
        }
    agenda = sources["agenda_tech_brasil"]
    historical = f"CI Historic Hackathon {tag}"
    no_website = f"CI Regional Datathon {tag}"
    calendar = {
        "eventos": [
            {
                "ano": 2025,
                "arquivado": True,
                "meses": [
                    {
                        "mes": "maio",
                        "arquivado": True,
                        "eventos": [
                            {
                                "nome": historical,
                                "url": f"https://historic-{tag}.invalid",
                                "data": ["11", "12"],
                            },
                            {
                                "nome": f"CI Ordinary Workshop {tag}",
                                "url": f"https://workshop-{tag}.invalid",
                                "data": ["13"],
                            },
                        ],
                    }
                ],
            },
            {
                "ano": 2026,
                "meses": [
                    {
                        "mes": "novembro",
                        "eventos": [{"nome": no_website, "url": None, "data": ["11"]}],
                    }
                ],
            },
        ]
    }
    agenda_url = str(agenda.seed_urls[0])
    agenda_pages: dict[str, object] = {
        agenda_url: {
            "encoding": "base64",
            "content": base64.b64encode(json.dumps(calendar).encode()).decode(),
        }
    }

    engine = create_engine(url, pool_pre_ping=True)
    try:
        _verify_one(
            engine,
            "hackclub_programs",
            pages,
            expected_created=2,
            expected_rejected=1,
            max_pages=3,
            expected_names={f"CI AI Hackathon {tag}", f"CI Climate Datathon {tag}"},
            tag=tag,
        )
        _verify_one(
            engine,
            "agenda_tech_brasil",
            agenda_pages,
            expected_created=2,
            expected_rejected=1,
            max_pages=1,
            expected_names={historical, no_website},
            tag=tag,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
