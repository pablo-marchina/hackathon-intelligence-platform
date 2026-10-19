"""W6: prevent all diagnostic runners from silently bypassing source pause."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from hackathon_sources.adapter import SourceAdapter
from hackathon_sources.models import (
    DiscoveredUrl,
    FetchResult,
    IngestionMode,
    PermissionStatus,
    SourceDefinition,
)
from hackathon_sources.persistent_runner import SourcePausedError
from hackathon_sources.rest_gate import run_source_rest_gate
from hackathon_sources.runner import run_source_once
from hackathon_sources.source_switch import check_postgres_active


def source_definition() -> SourceDefinition:
    return SourceDefinition(
        key="api_controlled",
        name="Source controlled by operator",
        domain="api.example.com",
        source_family="controlled_api",
        source_type="api",
        ingestion_mode=IngestionMode.API,
        permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        seed_urls=["https://api.example.com/events"],
    )


class Adapter(SourceAdapter):
    def __init__(self):
        super().__init__(source_definition())
        self.discover_calls = 0

    async def discover(self):
        self.discover_calls += 1
        for page in (1, 2):
            yield DiscoveredUrl(
                url=f"https://api.example.com/events?page={page}",
                source_key=self.definition.key,
                discovery_method="api",
            )

    async def parse_listing(self, result):
        return []

    async def parse_events(self, result):
        return []


class Fetcher:
    def __init__(self):
        self.urls = []

    async def fetch(self, source, url, *, etag=None, last_modified=None):
        self.urls.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=b"{}",
            fetched_at=datetime.now(UTC),
        )


class Sink:
    def __init__(self):
        self.inserts = []
        self.patches = []

    async def insert(self, table, payload):
        self.inserts.append((table, payload))
        return payload

    async def patch(self, table, row_id, payload):
        self.patches.append((table, row_id, payload))


def test_no_persist_pause_happens_before_discovery_or_fetch():
    adapter, fetcher = Adapter(), Fetcher()
    with pytest.raises(SourcePausedError):
        asyncio.run(run_source_once(adapter, fetcher, is_active=lambda _: False))
    assert adapter.discover_calls == 0
    assert fetcher.urls == []


def test_no_persist_pause_between_pages_cancels():
    adapter, fetcher = Adapter(), Fetcher()
    checks = iter([True, True, False])
    result = asyncio.run(run_source_once(adapter, fetcher, is_active=lambda _: next(checks)))
    assert result.status == "CANCELLED"
    assert result.fetched_pages == 1
    assert len(fetcher.urls) == 1


def test_rest_gate_pause_before_first_insert():
    adapter, fetcher, sink = Adapter(), Fetcher(), Sink()
    with pytest.raises(SourcePausedError):
        asyncio.run(run_source_rest_gate(adapter, fetcher, sink, is_active=lambda _: False))
    assert adapter.discover_calls == 0
    assert not sink.inserts and not fetcher.urls


def test_rest_gate_pause_between_pages_finishes_cancelled():
    adapter, fetcher, sink = Adapter(), Fetcher(), Sink()
    checks = iter([True, True, False])
    result = asyncio.run(run_source_rest_gate(adapter, fetcher, sink, is_active=lambda _: next(checks)))
    assert result.status == "CANCELLED"
    assert result.fetched_pages == 1
    assert len(fetcher.urls) == 1
    assert sink.patches[-1][2]["status"] == "CANCELLED"
    assert sum(1 for t, _ in sink.inserts if t == "crawl_runs") == 1


class FakeEngine:
    def __init__(self, active):
        self.active = active
        self.params = None

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, statement, params):
        self.params = params
        assert "domain = :domain AND source_family = :family" in str(statement)
        assert "permission_status = 'ALLOWED'" in str(statement)
        assert "detail_extraction_enabled" in str(statement)
        return self

    def scalar_one_or_none(self):
        if isinstance(self.active, Exception):
            raise self.active
        return self.active


@pytest.mark.parametrize("active,expected", [(True, True), (False, False), (None, False)])
def test_postgres_switch_fails_closed_on_disabled_or_missing(active, expected):
    engine = FakeEngine(active)
    assert check_postgres_active(engine, source_definition()) is expected
    assert engine.params == {"domain": "api.example.com", "family": "controlled_api"}


def test_postgres_switch_database_error_is_not_interpreted_as_active():
    with pytest.raises(OSError):
        check_postgres_active(FakeEngine(OSError("database connection lost")), source_definition())
