from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import hackathon_sources.persistent_runner as module
import pytest
from hackathon_sources.adapter import SourceAdapter
from hackathon_sources.models import (
    DiscoveredUrl,
    FetchResult,
    IngestionMode,
    PermissionStatus,
    SourceDefinition,
    SourceEventRecord,
)


class Connection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class Adapter(SourceAdapter):
    async def discover(self):
        yield DiscoveredUrl(
            url="https://api.example.com/events",
            source_key=self.definition.key,
            discovery_method="documented_api",
        )

    async def parse_events(self, result: FetchResult):
        payload = json.loads(result.body)
        return [
            SourceEventRecord(
                source_key=self.definition.key,
                source_event_id=row["id"],
                name=row["name"],
                source_url=result.final_url,
                official_url=row["url"],
            )
            for row in payload["data"]
        ]


class Fetcher:
    async def fetch(self, source, url, *, etag=None, last_modified=None):
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=json.dumps(
                {"data": [{"id": "e1", "name": "Live Hack", "url": "https://hack.example/"}]}
            ).encode(),
            fetched_at=datetime.now(UTC),
        )


class Registry:
    def __init__(self, connection):
        pass

    def upsert(self, definition):
        pass

    def get_source_id(self, definition):
        return "00000000-0000-4000-8000-000000000001"

    def is_active(self, source_id):
        return True

    def is_fetch_authorized(self, source_id):
        return True

    def has_current_lease(self, source_id, lease_token):
        return True

    def authorized_for_commit(self, source_id, *, lease_token=None):
        return True


class Crawl:
    finished = None

    def __init__(self, connection):
        pass

    def start_run(self, source_id):
        return "00000000-0000-4000-8000-000000000002"

    def finish_run(self, run_id, *, status, stats):
        Crawl.finished = (status, stats)


class Frontier:
    marked = []

    def __init__(self, connection):
        pass

    def enqueue(self, **kwargs):
        return 7

    def mark_result(self, frontier_id, *, state, terminal_reason=None):
        Frontier.marked.append((frontier_id, state, terminal_reason))


class Raw:
    attempts = 0
    docs = 0

    def __init__(self, connection):
        pass

    def record_fetch_attempt(self, **kwargs):
        Raw.attempts += 1
        return 11

    def record_fetch_error(self, **kwargs):
        raise AssertionError("unexpected error")

    def has_content(self, canonical_url, content_hash):
        return False

    def persist_document(self, **kwargs):
        Raw.docs += 1
        assert kwargs["structured_data"]["data"][0]["id"] == "e1"
        return "00000000-0000-4000-8000-000000000003"


class Refs:
    count = 0

    def __init__(self, connection):
        pass

    def upsert(self, **kwargs):
        Refs.count += 1
        assert kwargs["source_event_id"] == "e1"
        return "00000000-0000-4000-8000-000000000004"


@pytest.fixture(autouse=True)
def stage_records_in_memory(monkeypatch):
    """The production handoff writes SQL; fake Connections cannot execute SQL."""

    class Payloads:
        staged: list[dict] = []

        def __init__(self, connection):
            pass

        def stage(self, **kwargs):
            self.staged.append(kwargs)

    monkeypatch.setattr(module, "SourcePayloadRepository", Payloads)
    yield Payloads


def definition() -> SourceDefinition:
    return SourceDefinition(
        key="example",
        name="Example",
        domain="api.example.com",
        source_family="example",
        source_type="api",
        ingestion_mode=IngestionMode.API,
        permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        seed_urls=["https://api.example.com/events"],
    )


def test_persistent_runner_closes_crawl_raw_and_refs(monkeypatch, stage_records_in_memory) -> None:
    Raw.attempts = Raw.docs = Refs.count = 0
    Frontier.marked = []
    Crawl.finished = None
    monkeypatch.setattr(module, "SourceRegistryRepository", Registry)
    monkeypatch.setattr(module, "CrawlRepository", Crawl)
    monkeypatch.setattr(module, "FrontierRepository", Frontier)
    monkeypatch.setattr(module, "RawDocumentRepository", Raw)
    monkeypatch.setattr(module, "SourceEventRefRepository", Refs)

    connection = Connection()
    result = asyncio.run(
        module.run_source_persisted(Adapter(definition()), Fetcher(), connection, max_pages=2)
    )

    assert result.status == "SUCCEEDED"
    assert result.fetched_pages == 1
    assert result.persisted_documents == 1
    assert result.persisted_event_refs == 1
    assert Raw.attempts == 1 and Raw.docs == 1 and Refs.count == 1
    staged = stage_records_in_memory.staged
    assert len(staged) == 1
    assert staged[0]["source_key"] == "example"
    assert staged[0]["payload"]["source_event_id"] == "e1"
    assert staged[0]["raw_document_id"] == "00000000-0000-4000-8000-000000000003"
    assert Frontier.marked == [(7, "FETCHED", None)]
    assert Crawl.finished is not None
    status, stats = Crawl.finished
    assert status == "SUCCEEDED"
    assert stats["fetched_count"] == 1
    assert stats["valid_event_count"] == 1
    assert stats["persisted_documents"] == 1
    assert stats["persisted_event_refs"] == 1
    assert connection.commits >= 4


def test_paused_source_does_not_create_run_or_issue_http(monkeypatch) -> None:
    from hackathon_sources.persistent_runner import SourcePausedError

    class PausedRegistry(Registry):
        def is_active(self, source_id):
            return False

    class FailIfFetched:
        async def fetch(self, *args, **kwargs):
            raise AssertionError("paused source should never issue a network call")

    class FailIfStarted(Crawl):
        def start_run(self, source_id):
            raise AssertionError("paused source should never create a crawl run")

    monkeypatch.setattr(module, "SourceRegistryRepository", PausedRegistry)
    monkeypatch.setattr(module, "CrawlRepository", FailIfStarted)
    try:
        asyncio.run(
            module.run_source_persisted(Adapter(definition()), FailIfFetched(), Connection())
        )
    except SourcePausedError:
        pass
    else:
        raise AssertionError("expected SourcePausedError")


def test_operator_pause_is_checked_again_between_pages(monkeypatch) -> None:
    class PausingRegistry(Registry):
        checks = 0

        def is_active(self, source_id):
            PausingRegistry.checks += 1
            return PausingRegistry.checks <= 4

    class TwoPageAdapter(Adapter):
        async def discover(self):
            yield DiscoveredUrl(
                url="https://api.example.com/events",
                source_key=self.definition.key,
                discovery_method="api",
            )
            yield DiscoveredUrl(
                url="https://api.example.com/events?page=2",
                source_key=self.definition.key,
                discovery_method="api",
            )

    class CountFetcher(Fetcher):
        calls = 0

        async def fetch(self, *args, **kwargs):
            self.calls += 1
            return await super().fetch(*args, **kwargs)

    PausingRegistry.checks = 0
    Crawl.finished = None
    monkeypatch.setattr(module, "SourceRegistryRepository", PausingRegistry)
    monkeypatch.setattr(module, "CrawlRepository", Crawl)
    monkeypatch.setattr(module, "FrontierRepository", Frontier)
    monkeypatch.setattr(module, "RawDocumentRepository", Raw)
    monkeypatch.setattr(module, "SourceEventRefRepository", Refs)
    fetcher = CountFetcher()
    result = asyncio.run(
        module.run_source_persisted(
            TwoPageAdapter(definition()), fetcher, Connection(), max_pages=2
        )
    )
    assert fetcher.calls == 1
    assert result.status == "CANCELLED"
    assert result.fetched_pages == 1
    assert Crawl.finished[0] == "CANCELLED"


def test_not_authorized_prevents_starting_crawl(monkeypatch) -> None:
    class RestrictedRegistry(Registry):
        def is_fetch_authorized(self, source_id):
            return False

    class NoCrawl(Crawl):
        def start_run(self, source_id):
            raise AssertionError("unapproved source must not create a crawl")

    monkeypatch.setattr(module, "SourceRegistryRepository", RestrictedRegistry)
    monkeypatch.setattr(module, "CrawlRepository", NoCrawl)
    with __import__("pytest").raises(module.SourceNotAuthorizedError):
        asyncio.run(module.run_source_persisted(Adapter(definition()), Fetcher(), Connection()))


def test_registry_upsert_does_not_overwrite_operator_policy():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "src/hackathon_sources/registry.py").read_text()
    update = text.split("ON CONFLICT (domain, source_family) DO UPDATE SET", 1)[1].split('"""', 1)[
        0
    ]
    assert "permission_status = EXCLUDED" not in update
    assert "ingestion_mode = EXCLUDED" not in update
    assert "discovery_enabled = EXCLUDED" not in update


def test_expired_scheduler_lease_prevents_discovery(monkeypatch):
    class LeaseExpired(Registry):
        def has_current_lease(self, source_id, lease_token):
            return False

    class NoCrawl(Crawl):
        def start_run(self, source_id):
            raise AssertionError("no crawl may start with expired lease")

    monkeypatch.setattr(module, "SourceRegistryRepository", LeaseExpired)
    monkeypatch.setattr(module, "CrawlRepository", NoCrawl)
    with __import__("pytest").raises(module.SourcePausedError, match="lease"):
        asyncio.run(
            module.run_source_persisted(
                Adapter(definition()),
                Fetcher(),
                Connection(),
                lease_token="expired",
            )
        )


def test_discovery_failure_finishes_failed_crawl(monkeypatch):
    class BrokenDiscovery(Adapter):
        async def discover(self):
            raise ValueError("source listing unavailable")
            yield  # pragma: no cover - preserves async-generator interface

    monkeypatch.setattr(module, "SourceRegistryRepository", Registry)
    monkeypatch.setattr(module, "CrawlRepository", Crawl)
    Crawl.finished = None
    output = asyncio.run(
        module.run_source_persisted(
            BrokenDiscovery(definition()),
            Fetcher(),
            Connection(),
        )
    )
    assert output.status == "FAILED"
    assert Crawl.finished is not None and Crawl.finished[0] == "FAILED"


def test_pause_during_http_response_prevents_document_persistence(monkeypatch) -> None:
    """A source disabled while HTTP is in flight cannot write a stale document."""

    class PausesDuringFetch(Registry):
        checks = 0

        def is_active(self, source_id):
            PausesDuringFetch.checks += 1
            return PausesDuringFetch.checks <= 2

    class MustNotPersist(Raw):
        def record_fetch_attempt(self, **kwargs):
            raise AssertionError("response from paused source must not be persisted")

    PausesDuringFetch.checks = 0
    Crawl.finished = None
    Frontier.marked = []
    monkeypatch.setattr(module, "SourceRegistryRepository", PausesDuringFetch)
    monkeypatch.setattr(module, "CrawlRepository", Crawl)
    monkeypatch.setattr(module, "FrontierRepository", Frontier)
    monkeypatch.setattr(module, "RawDocumentRepository", MustNotPersist)
    monkeypatch.setattr(module, "SourceEventRefRepository", Refs)
    result = asyncio.run(
        module.run_source_persisted(Adapter(definition()), Fetcher(), Connection())
    )
    assert result.status == "CANCELLED"
    assert result.fetched_pages == 0 and result.persisted_documents == 0
    assert Crawl.finished is not None and Crawl.finished[0] == "CANCELLED"
    assert Frontier.marked[-1][1] == "FAILED"


def test_lease_lost_before_fetch_does_not_start_a_run(monkeypatch) -> None:
    class FailIfStarted(Crawl):
        def start_run(self, source_id):
            raise AssertionError("lost lease must not start a crawl")

    monkeypatch.setattr(module, "SourceRegistryRepository", Registry)
    monkeypatch.setattr(module, "CrawlRepository", FailIfStarted)
    try:
        asyncio.run(
            module.run_source_persisted(
                Adapter(definition()),
                Fetcher(),
                Connection(),
                should_continue=lambda: False,
            )
        )
    except module.SourcePausedError:
        pass
    else:
        raise AssertionError("expected a stale-lease rejection")


def test_pause_during_async_parser_rolls_back_stale_document(monkeypatch) -> None:
    class ParsingPauseRegistry(Registry):
        checks = 0

        def is_active(self, source_id):
            ParsingPauseRegistry.checks += 1
            # initial + pre-page + after-network succeed; after-parse is paused
            return ParsingPauseRegistry.checks <= 3

    class ParsingAdapter(Adapter):
        async def parse_listing(self, result):
            return []

    class ForbidRefs(Refs):
        def upsert(self, **kwargs):
            raise AssertionError("a stale page must not create event references")

    ParsingPauseRegistry.checks = 0
    Crawl.finished = None
    Frontier.marked = []
    monkeypatch.setattr(module, "SourceRegistryRepository", ParsingPauseRegistry)
    monkeypatch.setattr(module, "CrawlRepository", Crawl)
    monkeypatch.setattr(module, "FrontierRepository", Frontier)
    monkeypatch.setattr(module, "RawDocumentRepository", Raw)
    monkeypatch.setattr(module, "SourceEventRefRepository", ForbidRefs)
    conn = Connection()
    result = asyncio.run(module.run_source_persisted(ParsingAdapter(definition()), Fetcher(), conn))
    assert conn.rollbacks == 1
    assert result.status == "CANCELLED"
    assert result.persisted_documents == 0 and result.persisted_event_refs == 0
    assert result.records == [] and result.fetched_pages == 0
    assert Crawl.finished is not None and Crawl.finished[0] == "CANCELLED"


def test_commit_fence_rejects_page_when_pause_races_final_commit(monkeypatch) -> None:
    """The source may switch AFTER parsing's ordinary reads but BEFORE its commit."""

    class RevokedAtCommit(Registry):
        calls = 0

        def authorized_for_commit(self, source_id, *, lease_token=None):
            RevokedAtCommit.calls += 1
            return False

    RevokedAtCommit.calls = 0
    Crawl.finished = None
    Frontier.marked = []
    Refs.count = 0
    monkeypatch.setattr(module, "SourceRegistryRepository", RevokedAtCommit)
    monkeypatch.setattr(module, "CrawlRepository", Crawl)
    monkeypatch.setattr(module, "FrontierRepository", Frontier)
    monkeypatch.setattr(module, "RawDocumentRepository", Raw)
    monkeypatch.setattr(module, "SourceEventRefRepository", Refs)
    connection = Connection()
    output = asyncio.run(
        module.run_source_persisted(
            Adapter(definition()),
            Fetcher(),
            connection,
            lease_token="00000000-0000-4000-8000-000000000099",
        )
    )
    assert RevokedAtCommit.calls == 1
    assert output.status == "CANCELLED"
    assert output.persisted_documents == 0 and output.persisted_event_refs == 0
    assert Refs.count == 0
    assert connection.rollbacks == 1
    assert Crawl.finished is not None and Crawl.finished[0] == "CANCELLED"
    assert Frontier.marked[-1][1] == "FAILED"


def test_commit_fence_rejects_http_failure_attempt_after_pause(monkeypatch) -> None:
    class RevokedAtCommit(Registry):
        def authorized_for_commit(self, source_id, *, lease_token=None):
            return False

    class HttpFailure(Fetcher):
        async def fetch(self, source, url, *, etag=None, last_modified=None):
            success = await super().fetch(source, url)
            return success.model_copy(update={"status_code": 503})

    monkeypatch.setattr(module, "SourceRegistryRepository", RevokedAtCommit)
    monkeypatch.setattr(module, "CrawlRepository", Crawl)
    monkeypatch.setattr(module, "FrontierRepository", Frontier)
    monkeypatch.setattr(module, "RawDocumentRepository", Raw)
    monkeypatch.setattr(module, "SourceEventRefRepository", Refs)
    conn = Connection()
    output = asyncio.run(module.run_source_persisted(Adapter(definition()), HttpFailure(), conn))
    assert output.status == "CANCELLED" and output.fetched_pages == 0
    assert conn.rollbacks == 1
