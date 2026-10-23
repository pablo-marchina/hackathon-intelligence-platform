"""Offline controls for opt-in, source-scoped, multi-source catalog collection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from hackathon_sources.models import IngestionMode, PermissionStatus, SourceDefinition

from scripts.ops import controlled_batch_ingestion as batch


def fixture_source(key: str, priority: int = 50, **changes: object) -> SourceDefinition:
    values = {
        "key": key,
        "name": key,
        "domain": f"{key}.example.org",
        "source_family": key,
        "source_type": "official_api",
        "ingestion_mode": IngestionMode.API,
        "permission_status": PermissionStatus.ALLOWED,
        "detail_extraction_enabled": True,
        "priority": priority,
        "seed_urls": [f"https://{key}.example.org/api/events"],
    }
    values.update(changes)
    return SourceDefinition(**values)


def test_plan_without_explicit_sources_orders_approved_and_has_zero_side_effects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    allowed = fixture_source("official", priority=80)
    other = fixture_source("community", priority=45)
    denied = fixture_source("devpost", permission_status=PermissionStatus.RESTRICTED, priority=100)
    monkeypatch.setattr(batch, "load_source_definitions", lambda path: [other, denied, allowed])
    monkeypatch.setattr(batch, "create_engine", lambda *a, **kw: pytest.fail("dry run reached DB"))
    monkeypatch.setattr(batch, "run_once", lambda *a, **kw: pytest.fail("dry run reached fetch"))
    assert batch.main(["--max-pages", "2"], env={}) == 0
    report = __import__("json").loads(capsys.readouterr().out)
    assert report["status"] == "PLAN_ONLY"
    assert [source["key"] for source in report["sources"]] == ["official", "community"]
    assert report["max_total_pages"] == 4
    assert report["network"] is False and report["writes"] is False


@pytest.mark.parametrize(
    "keys",
    [
        ["devpost"],
        ["unknown"],
        ["allowed", "allowed"],
        [],
        ["allowed"] * 9,
    ],
)
def test_source_selection_fails_closed_before_network(keys: list[str]) -> None:
    allowed = fixture_source("allowed")
    denied = fixture_source("devpost", permission_status=PermissionStatus.RESTRICTED)
    with pytest.raises(ValueError):
        batch.select_sources([allowed, denied], keys)


def test_invalid_source_in_batch_causes_whole_selection_failure() -> None:
    allowed = fixture_source("allowed")
    partner = fixture_source("partner", permission_status=PermissionStatus.PARTNER_REQUIRED)
    with pytest.raises(ValueError):
        batch.select_sources([allowed, partner], ["allowed", "partner"])


@pytest.mark.parametrize(
    ("pages", "versions", "number"),
    [(0, 5, 2), (21, 5, 1), (1, 1001, 1), (20, 1000, 5)],
)
def test_bounded_budget_rejects_invalid_requests(pages: int, versions: int, number: int) -> None:
    with pytest.raises(ValueError, match="budget|source"):
        batch.validate_budget(
            [fixture_source(f"approved{i}") for i in range(number)], pages, versions
        )


def test_apply_requires_both_opt_in_flags_and_safe_environment() -> None:
    valid = {
        "APP_ENV": "staging",
        "DATABASE_URL": "postgresql://example",
        "HIP_ALLOW_LIVE_INGESTION": "1",
        "HIP_ALLOW_BATCH_INGESTION": "1",
    }
    batch.require_batch_apply(valid)
    for key in valid:
        with pytest.raises(ValueError):
            batch.require_batch_apply({k: v for k, v in valid.items() if k != key})
    with pytest.raises(ValueError):
        batch.require_batch_apply({**valid, "APP_ENV": "production"})


def test_run_batch_isolates_denied_source_and_secret_containing_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one, blocked, failed = (
        fixture_source("one"),
        fixture_source("blocked"),
        fixture_source("failed"),
    )
    counts = iter([10, 11])
    monkeypatch.setattr(batch, "_catalog_count", lambda engine: next(counts))
    monkeypatch.setattr(
        batch, "check_postgres_active", lambda engine, source: source.key != "blocked"
    )
    called = []

    async def runner(engine, source, *, max_pages, max_versions):
        called.append(source.key)
        if source.key == "failed":
            raise RuntimeError("secret-DSN-password=xyz987")
        return {
            "source": source.key,
            "status": "SUCCEEDED",
            "crawl_run_id": "synthetic-run",
            "pages_fetched": 2,
            "references_persisted": 2,
            "versions_processed": 2,
            "run_outcomes": {"CREATED": 1, "LINKED": 1, "UPDATED": 0, "REJECTED": 0},
            "processed_this_invocation": {"CREATED": 0, "LINKED": 2, "UPDATED": 0, "REJECTED": 0},
            "catalog_delta_observed": 1,
        }

    report = asyncio.run(
        batch.run_batch(
            SimpleNamespace(),
            [one, blocked, failed],
            pages=2,
            versions=10,
            runner=runner,
        )
    )
    assert report["status"] == "PARTIAL"
    assert report["sources_succeeded"] == 1
    assert report["sources_blocked"] == 1
    assert report["sources_failed"] == 1
    assert report["run_attributed_outcomes"]["CREATED"] == 1
    assert report["catalog_delta_observed"] == 1
    assert called == ["one", "failed"]
    assert report["sources"][1]["reason"] == "DB_NOT_AUTHORIZED"
    assert report["sources"][2]["error_type"] == "RuntimeError"
    assert "secret-DSN" not in str(report)
    assert "processed_this_invocation" in report["sources"][0]


def test_run_batch_refuses_authorization_change_mid_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = [fixture_source("first"), fixture_source("second")]
    counts = iter([0, 1])
    monkeypatch.setattr(batch, "_catalog_count", lambda engine: next(counts))
    approved = iter([True, False])
    monkeypatch.setattr(batch, "check_postgres_active", lambda engine, source: next(approved))
    observed = []

    async def runner(engine, source, **kwargs):
        observed.append(source.key)
        return {"status": "SUCCEEDED", "run_outcomes": {}}

    result = asyncio.run(
        batch.run_batch(SimpleNamespace(), sources, pages=1, versions=1, runner=runner)
    )
    assert observed == ["first"]
    assert result["sources_blocked"] == 1
    assert result["run_attributed_outcomes"]["CREATED"] == 0


def test_missing_outcomes_are_zero_not_falsely_reported_as_created() -> None:
    assert batch._outcome_counts(None) == {key: 0 for key in batch._OUTCOMES}
    assert batch._outcome_counts({"CREATED": True})["CREATED"] == 0
