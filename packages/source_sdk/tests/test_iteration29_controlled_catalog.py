"""Local guardrail checks; real PostgreSQL test is separately gated in CI."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from hackathon_sources.models import IngestionMode, PermissionStatus, SourceDefinition
from scripts.ops import controlled_catalog_ingestion as cli


def source(**overrides):
    defaults = dict(
        key="authorized", name="Approved", domain="example.org",
        source_family="demo", source_type="official_calendar",
        ingestion_mode=IngestionMode.RSS, permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True, seed_urls=["https://example.org/feed.xml"],
    )
    defaults.update(overrides)
    return SourceDefinition(**defaults)


def test_source_must_be_unique_registered_and_preapproved():
    valid = source()
    assert cli.validate_selection([valid], "authorized") == valid
    with pytest.raises(ValueError, match="exactly one"):
        cli.validate_selection([], "missing")
    with pytest.raises(ValueError, match="exactly one"):
        cli.validate_selection([valid, valid], "authorized")


@pytest.mark.parametrize("change", [
    {"permission_status": PermissionStatus.REVIEW_REQUIRED},
    {"permission_status": PermissionStatus.RESTRICTED},
    {"permission_status": PermissionStatus.PARTNER_REQUIRED},
    {"discovery_enabled": False},
    {"detail_extraction_enabled": False},
    {"ingestion_mode": IngestionMode.DISCOVERY_ONLY},
    {"ingestion_mode": IngestionMode.MANUAL},
    {"ingestion_mode": IngestionMode.BLOCKED},
    {"seed_urls": []},
])
def test_preflight_rejects_unapproved_or_non_collecting_source(change):
    with pytest.raises(ValueError, match="not approved"):
        cli.validate_selection([source(**change)], "authorized")


@pytest.mark.parametrize("env", [
    {},
    {"APP_ENV": "staging", "DATABASE_URL": "x"},
    {"HIP_ALLOW_LIVE_INGESTION": "1", "APP_ENV": "production", "DATABASE_URL": "x"},
    {"HIP_ALLOW_LIVE_INGESTION": "1", "APP_ENV": "staging"},
    {"HIP_ALLOW_LIVE_INGESTION": "1", "APP_ENV": "unknown", "DATABASE_URL": "x"},
    {"HIP_ALLOW_LIVE_INGESTION": "1", "DATABASE_URL": "x"},
])
def test_apply_requires_all_explicit_guards(env):
    with pytest.raises(ValueError):
        cli.require_explicit_apply(env)
    cli.require_explicit_apply({"HIP_ALLOW_LIVE_INGESTION": "1", "APP_ENV": "staging", "DATABASE_URL": "x"})


def test_plan_only_never_initializes_database_or_fetcher(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_source_definitions", lambda path: [source()])
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kwargs: pytest.fail("database invoked"))
    monkeypatch.setattr(cli, "FetchEngine", lambda: pytest.fail("network invoked"))
    assert cli.main(["--source", "authorized"], env={}) == 0
    assert '"status": "PLAN_ONLY"' in capsys.readouterr().out


def test_invalid_page_budget_rejected_before_network(monkeypatch):
    monkeypatch.setattr(cli, "load_source_definitions", lambda path: [source()])
    with pytest.raises(SystemExit):
        cli.main(["--source", "authorized", "--max-pages", "1000"])


class FakeEngine:
    def __init__(self):
        self.connection = SimpleNamespace(commit=lambda: None)

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        yield self.connection


def test_database_pause_prevents_fetcher_construction(monkeypatch):
    monkeypatch.setattr(cli, "check_postgres_active", lambda engine, source: False)

    class ForbiddenFetcher:
        def __init__(self):
            pytest.fail("network client must not be instantiated")

    with pytest.raises(PermissionError):
        asyncio.run(cli.run_once(FakeEngine(), source(), max_pages=1,
                                 max_versions=1, fetcher_factory=ForbiddenFetcher))


def test_processing_scoped_to_single_source_with_separate_run_yield(monkeypatch):
    engine = FakeEngine()
    snapshots = iter([12, 13])
    monkeypatch.setattr(cli, "_unique_active", lambda conn: next(snapshots))
    monkeypatch.setattr(cli, "check_postgres_active", lambda engine, source: True)
    monkeypatch.setattr(cli, "_run_outcomes", lambda conn, crawl_run_id: {
        "CREATED": 1, "LINKED": 0, "UPDATED": 0, "REJECTED": 0,
    })
    states = iter(["CREATED", "UPDATED", None])
    real_outcomes = iter(["CREATED", "LINKED", None])
    seen_sources = []

    class FakeProcessor:
        def __init__(self, connection, *, source_key=None):
            seen_sources.append(source_key)

        def process_one(self):
            result = next(states)
            self.last_outcome = next(real_outcomes)
            return result

    monkeypatch.setattr(cli, "CanonicalProcessor", FakeProcessor)
    monkeypatch.setattr(cli, "build_adapter", lambda definition: definition)

    class FakeFetcher:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_runner(adapter, fetcher, conn, *, max_pages):
        assert adapter.key == "authorized" and max_pages == 2
        return SimpleNamespace(status="SUCCEEDED", crawl_run_id="run-1",
                               fetched_pages=2, persisted_event_refs=3)

    monkeypatch.setattr(cli, "run_source_persisted", fake_runner)
    result = asyncio.run(cli.run_once(engine, source(), max_pages=2, max_versions=7,
                                      fetcher_factory=FakeFetcher))
    assert result["run_outcomes"]["CREATED"] == 1
    assert result["processed_this_invocation"] == {
        "CREATED": 1, "LINKED": 1, "UPDATED": 0, "REJECTED": 0,
    }
    assert result["catalog_delta_observed"] == 1
    assert result["versions_processed"] == 2
    assert seen_sources == ["authorized"] * 3


def test_failed_or_cancelled_crawl_never_normalizes(monkeypatch):
    monkeypatch.setattr(cli, "check_postgres_active", lambda engine, source: True)
    monkeypatch.setattr(cli, "_unique_active", lambda conn: 7)
    monkeypatch.setattr(cli, "_run_outcomes", lambda conn, run: {key: 0 for key in cli._OUTCOMES})
    monkeypatch.setattr(cli, "build_adapter", lambda definition: definition)
    monkeypatch.setattr(cli, "CanonicalProcessor", lambda conn, **kwargs: pytest.fail("normalization forbidden"))

    class Fetcher:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(status="CANCELLED", crawl_run_id="fake-run",
                               fetched_pages=0, persisted_event_refs=0)

    monkeypatch.setattr(cli, "run_source_persisted", fake_runner)
    outcome = asyncio.run(cli.run_once(FakeEngine(), source(), max_pages=1,
                                       max_versions=1, fetcher_factory=Fetcher))
    assert outcome["status"] == "CANCELLED"
    assert outcome["versions_processed"] == 0


def test_postgres_source_filter_casts_null_bind_to_text():
    from pathlib import Path
    statement = (Path(cli.ROOT) / "packages/intelligence/src/hackathon_intelligence/incremental.py").read_text()
    assert "CAST(:source_key AS text)" in statement
