"""Staging workflow gate contracts; no network or DB in plan/negative cases."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.ops import staging_campaign_dispatch as dispatch

ROOT = Path(__file__).resolve().parents[3]


def staging_env() -> dict[str, str]:
    return {
        "APP_ENV": "staging",
        "DATABASE_URL": "postgresql+psycopg://fake:SECRET@localhost:5432/hip_staging_test",
        "HIP_STAGING_EXPECTED_DB_NAME": "hip_staging_test",
        "HIP_ALLOW_LIVE_INGESTION": "1",
        "HIP_ALLOW_BATCH_INGESTION": "1",
        "HIP_ALLOW_COVERAGE_CAMPAIGN": "1",
    }


def invoke(mode="plan", profile="dribdat", env=None, confirmation="") -> tuple[int, dict[str, Any]]:
    return dispatch.run_dispatch(
        mode=mode,
        profile=profile,
        confirmation=confirmation,
        pages=1,
        versions=25,
        min_net_unique=0,
        require_drained=False,
        env={} if env is None else env,
    )


@pytest.mark.parametrize(
    "profile,expected",
    [
        ("dribdat", 2),
        ("hackclub", 5),
        ("all", 7),
    ],
)
def test_plan_has_no_db_network_or_secrets(monkeypatch, profile: str, expected: int) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("plan attempted DB, migrations, network or secret access")

    monkeypatch.setattr(dispatch, "create_engine", forbidden)
    monkeypatch.setattr(dispatch, "preflight", forbidden)
    monkeypatch.setattr(dispatch, "run_campaign", forbidden)
    code, plan = invoke(profile=profile, env={"DATABASE_URL": "SECRET"})
    assert code == 0
    assert plan["status"] == "PLAN_ONLY"
    assert plan["network_attempted"] is False
    assert plan["database_accessed"] is False
    assert len(plan["source_keys"]) == expected
    assert "SECRET" not in json.dumps(plan)


@pytest.mark.parametrize(
    "mode,profile,pages,versions,target",
    [
        ("trigger", "dribdat", 1, 25, 0),
        ("apply", "unknown", 1, 25, 0),
        ("apply", "dribdat", 99, 25, 0),
        ("apply", "dribdat", 1, 999, 0),
        ("apply", "dribdat", 1, 25, 999),
    ],
)
def test_invalid_selection_is_rejected_before_db(
    monkeypatch, mode, profile, pages, versions, target
) -> None:
    monkeypatch.setattr(
        dispatch,
        "create_engine",
        lambda *args, **kwargs: pytest.fail("invalid selection opened DB"),
    )
    code, receipt = dispatch.run_dispatch(
        mode=mode,
        profile=profile,
        confirmation=dispatch.CONFIRMATION,
        pages=pages,
        versions=versions,
        min_net_unique=target,
        require_drained=False,
        env=staging_env(),
    )
    assert code == 2
    assert receipt["status"] == "BLOCKED"
    assert receipt["database_accessed"] is False


@pytest.mark.parametrize(
    "missing",
    [
        "DATABASE_URL",
        "HIP_ALLOW_LIVE_INGESTION",
        "HIP_ALLOW_BATCH_INGESTION",
        "HIP_ALLOW_COVERAGE_CAMPAIGN",
        "HIP_STAGING_EXPECTED_DB_NAME",
    ],
)
def test_apply_fails_before_opening_connection_when_opt_in_missing(
    monkeypatch, missing: str
) -> None:
    monkeypatch.setattr(
        dispatch,
        "create_engine",
        lambda *args, **kwargs: pytest.fail("missing secret/flag opened DB"),
    )
    env = staging_env()
    env.pop(missing)
    code, report = invoke("apply", env=env, confirmation=dispatch.CONFIRMATION)
    assert code == 2
    assert report["status"] == "BLOCKED"


def test_wrong_confirmation_and_production_db_refused_before_connect(monkeypatch) -> None:
    monkeypatch.setattr(
        dispatch,
        "create_engine",
        lambda *args, **kwargs: pytest.fail("unapproved run opened DB"),
    )
    code, receipt = invoke("apply", env=staging_env(), confirmation="YES")
    assert code == 2
    assert receipt["reason_code"] == "MANUAL_CONFIRMATION_REQUIRED"
    for changes in (
        {"APP_ENV": "production"},
        {"DATABASE_URL": "postgresql://user:secret@db.example/hackathon_prod"},
        {"HIP_STAGING_EXPECTED_DB_NAME": "hackathon_prod"},
        {"HIP_STAGING_EXPECTED_DB_NAME": "hip_staging_elsewhere"},
    ):
        code, report = invoke(
            "apply", env={**staging_env(), **changes}, confirmation=dispatch.CONFIRMATION
        )
        assert code == 2
        assert report["status"] == "BLOCKED"


def test_preflight_failure_never_starts_campaign_or_network(monkeypatch) -> None:
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(dispatch, "create_engine", lambda *args, **kwargs: engine)

    def reject(*args, **kwargs):
        raise dispatch.DispatchRefusal("SOURCE_NOT_AUTHORIZED_IN_STAGING")

    monkeypatch.setattr(dispatch, "preflight", reject)

    async def forbidden(*args, **kwargs):
        pytest.fail("preflight rejection dispatched live campaign")

    monkeypatch.setattr(dispatch, "run_campaign", forbidden)
    code, receipt = invoke("apply", env=staging_env(), confirmation=dispatch.CONFIRMATION)
    assert code == 2
    assert receipt["reason_code"] == "SOURCE_NOT_AUTHORIZED_IN_STAGING"
    assert receipt["network_attempted"] is False


def sample_report() -> dict[str, Any]:
    return {
        "status": "MEASURED",
        "evidence_kind": "OBSERVED_DATABASE_BEFORE_AFTER_NOT_CAUSAL",
        "batch_status": "SUCCEEDED",
        "net_unique_target_met": True,
        "minimum_net_unique_target": 0,
        "has_incomplete_or_exhausted_backlog": False,
        "has_unverified_backlog_sources": False,
        "net_deltas": {
            "catalog": {"unique_active": 2, "upcoming_by_date": 1},
            "field_counts": {"with_known_format": 1},
        },
        "per_source": {
            "dribdat_glamhack": {"net_deltas": {"attribution": {"attributed_unique_active": 2}}}
        },
        "snapshots": {
            "before": {"SECRET": "private"},
            "after": {
                "as_of": "2026-10-24T03:30:00+00:00",
                "catalog": {
                    "unique_active": 250,
                    "upcoming_by_date": 45,
                    "ongoing_by_date": 3,
                    "confirmed_registration_window": 10,
                    "unknown_registration_close": 100,
                    "with_official_url": 230,
                    "with_start_end": 210,
                },
                "global_funnel": {"unique_searchable_hackathons": 225},
            },
        },
        "batch": {
            "status": "SUCCEEDED",
            "sources": [
                {
                    "source": "dribdat_glamhack",
                    "status": "SUCCEEDED",
                    "pages_fetched": 1,
                    "versions_processed": 2,
                    "crawl_run_id": "private-crawl-id",
                    "run_outcomes": {"CREATED": 2},
                    "backlog_by_state": {"DONE": 1},
                    "unprocessed_backlog_urls": 0,
                    "private": "SECRET",
                },
                {"source": "dribdat_imprs_astro", "status": "SUCCEEDED"},
            ],
        },
    }


def test_success_report_only_emits_allowlisted_aggregate_metrics(monkeypatch) -> None:
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(dispatch, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(dispatch, "preflight", lambda *args: {"schema_current": True})

    async def synthetic(*args, **kwargs):
        return sample_report()

    monkeypatch.setattr(dispatch, "run_campaign", synthetic)
    code, receipt = invoke("apply", env=staging_env(), confirmation=dispatch.CONFIRMATION)
    assert code == 0
    assert receipt["status"] == "MEASURED"
    assert receipt["network_attempted"] is True
    assert receipt["results"]["catalog_net_deltas"]["unique_active"] == 2
    assert receipt["results"]["sources"][0]["attributed_active_delta"] == 2
    rendered = json.dumps(receipt)
    assert "SECRET" not in rendered and "private-crawl-id" not in rendered
    assert "fake:SECRET" not in rendered


def test_strict_drained_gate_blocks_partial_campaign_without_hiding_stats(monkeypatch) -> None:
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(dispatch, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(dispatch, "preflight", lambda *args: {"schema_current": True})

    async def unfinished(*args, **kwargs):
        report = sample_report()
        report["has_unverified_backlog_sources"] = True
        report["status"] = "NEEDS_BACKLOG_FOLLOWUP"
        return report

    monkeypatch.setattr(dispatch, "run_campaign", unfinished)
    kwargs = {
        "mode": "apply",
        "profile": "dribdat",
        "confirmation": dispatch.CONFIRMATION,
        "pages": 1,
        "versions": 25,
        "min_net_unique": 0,
        "env": staging_env(),
    }
    code, receipt = dispatch.run_dispatch(**kwargs, require_drained=True)
    assert code == 1 and receipt["status"] == "NEEDS_BACKLOG_FOLLOWUP"
    code, receipt = dispatch.run_dispatch(**kwargs, require_drained=False)
    assert code == 0 and receipt["status"] == "NEEDS_BACKLOG_FOLLOWUP"


def test_exception_redaction_does_not_leak_database_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        dispatch,
        "create_engine",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("failure includes SECRET and connection details")
        ),
    )
    code, receipt = invoke("apply", env=staging_env(), confirmation=dispatch.CONFIRMATION)
    assert code == 3
    assert receipt["status"] == "FAILED"
    assert receipt["error_type"] == "RuntimeError"
    assert "SECRET" not in json.dumps(receipt)


def test_receipt_written_as_private_json(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    dispatch.write_receipt(path, {"schema_version": dispatch.RECEIPT_SCHEMA})
    assert json.loads(path.read_text())["schema_version"] == dispatch.RECEIPT_SCHEMA
    assert path.stat().st_mode & 0o077 == 0


def test_workflow_only_runs_on_manual_dispatch_with_protected_apply() -> None:
    text = (ROOT / ".github/workflows/staging-coverage.yml").read_text()
    assert "workflow_dispatch:" in text
    assert "\n  push:" not in text and "\n  pull_request:" not in text
    assert "environment: staging-coverage" in text
    assert "permissions:\n  contents: read" in text
    assert "cancel-in-progress: false" in text
    assert "if: github.ref == 'refs/heads/main'" in text
    assert "retention-days: 14" in text
    plan = text.split("\n  plan:", 1)[1].split("\n  apply:", 1)[0]
    assert "secrets." not in plan and "DATABASE_URL:" not in plan
    assert "STAGING_DATABASE_URL" in text
    assert "RUN_STAGING_COVERAGE" in text
