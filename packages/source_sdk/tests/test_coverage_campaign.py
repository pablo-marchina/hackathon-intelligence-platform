"""Coverage campaign safety, provenance and observational delta contracts."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.ops import coverage_campaign as campaign


def fixture(value: int = 3, *, source: str = "approved") -> dict[str, Any]:
    catalog = {key: 0 for key in campaign.COUNTS}
    catalog["unique_active"] = value
    catalog["upcoming_by_date"] = value // 2
    counts = {"known_format": max(value - 1, 0), "with_prizes": 0}
    fun = {key: 0 for key in campaign.FUNNEL_COUNTS}
    fun["unique_searchable_hackathons"] = value
    attrs = {key: 0 for key in campaign.SOURCE_COUNTS}
    attrs["attributed_unique_active"] = value
    attrs["exclusive_canonicals"] = value - 1
    return {
        "as_of": "2026-10-23T00:00:00+00:00",
        "catalog": catalog,
        "field_counts": counts,
        "pipeline": {"staged_payloads": 3, "awaiting_normalization": 0},
        "global_funnel": fun,
        "source_cohorts": {
            source: {
                "registered": True,
                "source_id": "deadbeef-0000-4000-8000-000000000001",
                "attribution": attrs,
                "funnel": dict(fun),
                "backlog": {"backlog_pending": 0, "backlog_exhausted": 0},
            }
        },
    }


def test_campaign_is_dry_run_without_connection_or_crawl(monkeypatch, capsys) -> None:
    from hackathon_sources.models import (
        IngestionMode,
        PermissionStatus,
        SourceDefinition,
    )

    source = SourceDefinition(
        key="approved",
        name="Approved",
        domain="approved.example.org",
        source_family="approved",
        source_type="api",
        ingestion_mode=IngestionMode.API,
        permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        seed_urls=["https://approved.example.org/api/events"],
    )
    monkeypatch.setattr(campaign, "load_source_definitions", lambda path: [source])
    monkeypatch.setattr(
        campaign,
        "create_engine",
        lambda *args, **kwargs: pytest.fail("plan invoked PostgreSQL"),
    )
    monkeypatch.setattr(
        campaign,
        "run_batch",
        lambda *args, **kwargs: pytest.fail("plan invoked crawling"),
    )
    assert campaign.main(["--source", "approved"], env={}) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "PLAN_ONLY"
    assert report["network"] is False and report["writes"] is False
    assert report["campaign"]["min_net_unique"] == 0


def test_campaign_requires_extra_opt_in_and_nonproduction_staging() -> None:
    correct = {
        "APP_ENV": "staging",
        "DATABASE_URL": "postgresql://example",
        "HIP_ALLOW_LIVE_INGESTION": "1",
        "HIP_ALLOW_BATCH_INGESTION": "1",
        "HIP_ALLOW_COVERAGE_CAMPAIGN": "1",
    }
    campaign.require_campaign_apply(correct)
    for key in correct:
        with pytest.raises(ValueError):
            campaign.require_campaign_apply({k: v for k, v in correct.items() if k != key})
    with pytest.raises(ValueError):
        campaign.require_campaign_apply({**correct, "APP_ENV": "production"})
    with pytest.raises(ValueError):
        campaign.require_campaign_apply({**correct, "APP_ENV": "development"})


def test_deltas_are_not_mislabeled_as_global_exclusive_yield() -> None:
    before, after = fixture(3), fixture(5)
    after["source_cohorts"]["approved"]["attribution"]["exclusive_canonicals"] = 1
    report = campaign.compare(before, after, {"status": "SUCCEEDED", "sources": []})
    assert report["net_deltas"]["catalog"]["unique_active"] == 2
    assert report["net_deltas"]["field_counts"]["known_format"] == 2
    assert report["per_source"]["approved"]["net_deltas"]["attribution"][
        "exclusive_canonicals"
    ] == -1
    assert report["net_unique_target_met"] is True
    assert report["status"] == "MEASURED"
    assert report["evidence_kind"] == "OBSERVED_DATABASE_BEFORE_AFTER_NOT_CAUSAL"
    assert "concurrent" in report["limitations"]
    assert "never sum" in report["limitations"]


@pytest.mark.parametrize(
    ("status", "delta", "target", "backlog", "expected"),
    [
        ("SUCCEEDED", 0, 0, False, "MEASURED"),
        ("SUCCEEDED", 0, 1, False, "BELOW_TARGET"),
        ("SUCCEEDED", 3, 1, True, "NEEDS_BACKLOG_FOLLOWUP"),
        ("FAILED", 3, 1, False, "FAILED_OR_BLOCKED"),
    ],
)
def test_campaign_status_is_not_just_runner_success(
    status: str, delta: int, target: int, backlog: bool, expected: str
) -> None:
    report = campaign.compare(
        fixture(4),
        fixture(4 + delta),
        {
            "status": status,
            "sources": [{
                "source": "approved",
                "budget_hit_with_pending_urls": backlog,
                "backlog_by_state": {"EXHAUSTED": 0},
            }],
        },
        min_net_unique=target,
    )
    assert report["status"] == expected
    assert report["has_incomplete_or_exhausted_backlog"] is backlog


def test_exhausted_pages_are_not_conflated_with_drained_backlog() -> None:
    report = campaign.compare(
        fixture(),
        fixture(),
        {
            "status": "SUCCEEDED",
            "sources": [{
                "budget_hit_with_pending_urls": False,
                "backlog_by_state": {"EXHAUSTED": 2},
            }],
        },
    )
    assert report["has_incomplete_or_exhausted_backlog"] is True


def test_missing_cohort_produces_null_delta_not_fake_zero() -> None:
    before, after = fixture(), fixture()
    before["source_cohorts"]["approved"]["attribution"] = None
    result = campaign.compare(before, after, {"status": "SUCCEEDED", "sources": []})
    assert result["per_source"]["approved"]["net_deltas"]["attribution"] is None


def test_changed_source_identity_and_bad_snapshot_are_rejected() -> None:
    before, after = fixture(), fixture()
    after["source_cohorts"]["approved"]["source_id"] = "different"
    with pytest.raises(ValueError, match="identity"):
        campaign.compare(before, after, {"status": "SUCCEEDED"})
    with pytest.raises(ValueError, match="incompatible"):
        campaign._delta({"a": 1}, {"b": 2})
    with pytest.raises(ValueError, match="nonnegative"):
        campaign.compare(fixture(), fixture(), {}, min_net_unique=-1)


def test_exception_before_batch_prevents_any_network_calls(monkeypatch) -> None:
    def abort(*args, **kwargs):
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(campaign, "capture", abort)

    async def network(*args, **kwargs):
        pytest.fail("network should not start after missing baseline")

    with pytest.raises(RuntimeError, match="snapshot failed"):
        asyncio.run(
            campaign.run_campaign(
                SimpleNamespace(),
                [SimpleNamespace(key="approved", priority=50, seed_urls=["https://approved.example"])],
                pages=1,
                versions=1,
                runner=network,
            )
        )
