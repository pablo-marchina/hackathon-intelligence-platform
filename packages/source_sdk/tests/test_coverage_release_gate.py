"""Deterministic no-network launch-review evidence gates.

This test suite demonstrates that a successful CI/dry-run or unverified
sample is never treated as real production or global coverage evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from scripts.ops import coverage_release_gate as gate
from scripts.ops.staging_campaign_dispatch import PROFILES, RECEIPT_SCHEMA


def verified_receipt() -> dict:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    return {
        "schema_version": RECEIPT_SCHEMA,
        "mode": "apply",
        "profile": "all",
        "source_keys": list(PROFILES["all"]),
        "status": "MEASURED",
        "preflight": {
            "database_identity_verified": True,
            "schema_current": True,
            "source_permissions_checked": 8,
        },
        "results": {
            "status": "MEASURED",
            "batch_status": "SUCCEEDED",
            "evidence_kind": gate.EXPECTED_EVIDENCE,
            "net_unique_target_met": True,
            "observed_at": (now - timedelta(hours=2)).isoformat(),
            "post_catalog": {
                "unique_active": 300,
                "upcoming_by_date": 60,
                "ongoing_by_date": 2,
                "confirmed_registration_window": 13,
                "unknown_registration_close": 190,
                "with_official_url": 275,
                "with_start_end": 254,
            },
            "post_global_funnel": {
                "unique_searchable_hackathons": 255,
            },
            "has_unverified_backlog_sources": False,
            "has_incomplete_or_exhausted_backlog": False,
            "sources": [
                {
                    "key": key,
                    "status": "SUCCEEDED",
                    "pages_fetched": 1,
                    "versions_processed": 3,
                    "run_outcomes": {
                        "CREATED": 1,
                        "LINKED": 0,
                        "UPDATED": 0,
                        "REJECTED": 0,
                    },
                }
                for key in PROFILES["all"]
            ],
        },
    }


CLOCK = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def test_valid_full_staging_evidence_only_requests_independent_review() -> None:
    report = gate.evaluate(verified_receipt(), now=CLOCK)
    assert report["status"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert report["checks_passed"] == report["checks_total"]
    assert report["checks_total"] >= 12
    assert "NOT launch authorization" in report["limitations"]
    assert "global" in report["limitations"]


@pytest.mark.parametrize(
    ("mutator", "failed"),
    [
        (lambda r: r.update(mode="plan"), "actual_staging_execution"),
        (lambda r: r.update(status="PLAN_ONLY"), "measured_successful_batch"),
        (lambda r: r.update(profile="dribdat"), "complete_required_portfolio"),
        (lambda r: r["source_keys"].pop(), "complete_required_portfolio"),
        (
            lambda r: r["preflight"].update(source_permissions_checked=6),
            "authorized_current_schema",
        ),
        (
            lambda r: r["results"].update(evidence_kind="synthetic"),
            "measured_successful_batch",
        ),
        (
            lambda r: r["results"].update(batch_status="FAILED"),
            "measured_successful_batch",
        ),
        (
            lambda r: r["results"].update(observed_at="2020-01-01T00:00:00Z"),
            "recent_database_evidence",
        ),
        (
            lambda r: r["results"].update(observed_at="unparseable"),
            "recent_database_evidence",
        ),
        (
            lambda r: r["results"].update(observed_at="2026-10-24T11:00:00"),
            "recent_database_evidence",
        ),
        (
            lambda r: r["results"]["post_catalog"].update(unique_active=0),
            "minimum_active_unique",
        ),
        (
            lambda r: r["results"]["post_catalog"].update(upcoming_by_date=3),
            "minimum_upcoming_unique",
        ),
        (
            lambda r: r["results"]["post_catalog"].update(with_official_url=5),
            "official_url_completeness",
        ),
        (
            lambda r: r["results"]["post_catalog"].update(with_start_end=5),
            "start_end_completeness",
        ),
        (
            lambda r: r["results"]["post_catalog"].update(unknown_registration_close=300),
            "known_registration_close_completeness",
        ),
        (
            lambda r: r["results"]["post_catalog"].update(unknown_registration_close=350),
            "nonnegative_coherent_catalog",
        ),
        (
            lambda r: r["results"]["post_global_funnel"].update(unique_searchable_hackathons=1),
            "minimum_searchable_unique",
        ),
        (
            lambda r: r["results"]["sources"][0].update(status="BLOCKED"),
            "all_sources_succeeded",
        ),
        (
            lambda r: r["results"].update(has_unverified_backlog_sources=True),
            "all_backlog_evidence_verified",
        ),
        (
            lambda r: r["results"].update(has_incomplete_or_exhausted_backlog=True),
            "all_backlog_evidence_verified",
        ),
        (
            lambda r: r["results"]["post_catalog"].pop("with_official_url"),
            "official_url_completeness",
        ),
    ],
)
def test_missing_inconsistent_or_weak_evidence_is_never_ready(mutator, failed: str) -> None:
    candidate = verified_receipt()
    mutator(candidate)
    report = gate.evaluate(candidate, now=CLOCK)
    assert report["status"] == "NOT_READY"
    assert any(row["id"] == failed and not row["passed"] for row in report["checks"])


def test_empty_and_false_are_not_treated_as_unknown_but_null_is() -> None:
    receipt = verified_receipt()
    receipt["results"]["post_catalog"]["unknown_registration_close"] = None
    report = gate.evaluate(receipt, now=CLOCK)
    assert report["status"] == "NOT_READY"
    assert not next(
        x["passed"] for x in report["checks"] if x["id"] == "known_registration_close_completeness"
    )


def test_gates_are_versioned_and_no_external_sources_are_contacted(monkeypatch) -> None:
    report = gate.evaluate(verified_receipt(), now=CLOCK)
    assert report["policy_version"] == "coverage-release-policy-v1"
    assert report["status"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert not hasattr(gate, "create_engine")
    assert not hasattr(gate, "run_campaign")


def test_read_file_and_write_privacy_safe_review(tmp_path, monkeypatch, capsys) -> None:
    original_evaluate = gate.evaluate
    monkeypatch.setattr(gate, "evaluate", lambda data: original_evaluate(data, now=CLOCK))
    receipt_file = tmp_path / "source.json"
    output = tmp_path / "review.json"
    data = verified_receipt()
    data["raw_secret"] = "NEVER_INCLUDE_THIS"
    receipt_file.write_text(json.dumps(data), encoding="utf-8")
    code = gate.main(["--receipt", str(receipt_file), "--output", str(output)])
    assert code == 0
    emitted = json.loads(output.read_text())
    assert emitted["status"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert "NEVER_INCLUDE_THIS" not in output.read_text()
    assert "NEVER_INCLUDE_THIS" not in capsys.readouterr().out
    assert output.stat().st_mode & 0o077 == 0


def test_no_receipt_fails_closed_and_advisory_does_not_claim_readiness(tmp_path, capsys) -> None:
    output = tmp_path / "review.json"
    args = ["--receipt", str(tmp_path / "missing.json"), "--output", str(output)]
    assert gate.main(args) == 1
    assert json.loads(output.read_text())["status"] == "UNVERIFIED"
    assert gate.main([*args, "--advisory"]) == 0
    assert json.loads(output.read_text())["status"] == "UNVERIFIED"
    assert "UNVERIFIED" in capsys.readouterr().out
