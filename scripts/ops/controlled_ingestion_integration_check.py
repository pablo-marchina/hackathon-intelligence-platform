"""Opt-in PostgreSQL E2E for the controlled ingestion command; all HTTP is fake.

Only run in the GitHub Actions disposable PostgreSQL service. Creates a uniquely
named fake source in that database and never contacts external domains.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.registry import SourceRegistryRepository
from scripts.ops.controlled_catalog_ingestion import run_once, _url
from scripts.ops.canonical_evidence import controlled_receipt, write_ci_receipt


class FakeFetcher:
    def __init__(self, responses: dict[str, FetchResult]):
        self.responses = responses
        self.requests: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def fetch(self, definition, url: str, **kwargs):
        self.requests.append(url)
        return self.responses[url]  # raises rather than opening real network


def _response(url: str, body: str, content_type: str) -> FetchResult:
    return FetchResult(requested_url=url, final_url=url, status_code=200,
                       content_type=content_type, body=body.encode("utf-8"),
                       fetched_at=datetime.now(UTC))


def verify() -> None:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("HIP_ALLOW_CONTROLLED_INGESTION_DRILL") != "1":
        raise RuntimeError("isolated test and HIP_ALLOW_CONTROLLED_INGESTION_DRILL=1 required")
    db_url = _url(os.environ["DATABASE_URL"])
    parsed = make_url(db_url)
    if parsed.get_backend_name() != "postgresql" or parsed.host not in ("127.0.0.1", "localhost") or (
        parsed.database != "hackathon" and "_test" not in (parsed.database or "")
    ):
        raise RuntimeError("disposable local PostgreSQL only")
    tag = uuid4().hex[:12]
    hostname = f"ci-{tag}.invalid"
    root = f"https://{hostname}/events.xml"
    event = f"https://{hostname}/events/hackathon-2027"
    source = SourceDefinition(
        key=f"ci_controlled_{tag}", name=f"CI Controlled {tag}", domain=hostname,
        source_family=f"ci-controlled-{tag}", source_type="official_calendar",
        ingestion_mode=IngestionMode.RSS, permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True, seed_urls=[root],
    )
    rss = f"<rss><channel><item><link>{event}</link></item></channel></rss>"
    html = '<script type="application/ld+json">' + json.dumps({
        "@context": "https://schema.org", "@type": "Event",
        "name": f"CI Campus Hackathon {tag}",
        "startDate": "2027-05-01T10:00:00Z", "endDate": "2027-05-02T10:00:00Z",
        "url": event,
    }) + '</script>'
    fetcher = FakeFetcher({
        root: _response(root, rss, "application/rss+xml"),
        event: _response(event, html, "text/html"),
    })
    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            SourceRegistryRepository(conn).upsert(source)
        result = asyncio.run(run_once(engine, source, max_pages=3, max_versions=10,
                                      fetcher_factory=lambda: fetcher))
        assert result["status"] == "SUCCEEDED", result
        assert result["references_persisted"] == 1, result
        assert result["processed_this_invocation"]["CREATED"] == 1, result
        assert result["run_outcomes"]["CREATED"] == 1, result
        assert result["catalog_delta_observed"] == 1, result
        assert fetcher.requests == [root, event], fetcher.requests
        repeat = asyncio.run(run_once(engine, source, max_pages=3, max_versions=10,
                                      fetcher_factory=lambda: fetcher))
        assert repeat["pages_fetched"] == 0, repeat
        assert repeat["catalog_delta_observed"] == 0, repeat
        assert fetcher.requests == [root, event]
        receipt = controlled_receipt(source=source.key, first=result, repeat=repeat)
        # Operator pause must preempt even construction/use of a network client.
        with engine.begin() as conn:
            conn.execute(text("UPDATE sources SET active=false WHERE domain=:domain"), {"domain": hostname})
        try:
            asyncio.run(run_once(engine, source, max_pages=1, max_versions=1,
                                 fetcher_factory=lambda: RuntimeError("network forbidden")))
        except PermissionError:
            pass
        else:
            raise AssertionError("paused source was allowed to crawl")
        write_ci_receipt(receipt, name="controlled_ingestion")
        print("CONTROLLED E2E PASS: fake RSS -> PostgreSQL -> canonical -> measured yield; pause fail-closed")
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
