"""Evidence-driven launch *review* gate; never deploys or authorizes sources.

Reads only the allowlisted aggregate GitHub Actions staging campaign receipt.
A green CI, a successful batch, a dry-run, or missing evidence cannot imply
launch readiness. Policy is an immutable, versioned project artifact.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.ops.staging_campaign_dispatch import PROFILES, RECEIPT_SCHEMA, write_receipt

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs/coverage_release_policy_v1.json"
MAX_INPUT_BYTES = 1024 * 1024
EXPECTED_EVIDENCE = "OBSERVED_DATABASE_BEFORE_AFTER_NOT_CAUSAL"
EXPECTED_METRICS = (
    "unique_active",
    "upcoming_by_date",
    "with_official_url",
    "with_start_end",
    "unknown_registration_close",
)


def load_policy() -> dict[str, Any]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if policy["schema_version"] != "coverage-release-policy-v1":
        raise ValueError("unsupported policy version")
    return policy


def _nonnegative(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def evaluate(
    receipt: dict[str, Any],
    *,
    now: datetime | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed on missing/malformed data; result never says production ready."""
    rules = policy or load_policy()
    clock = (now or datetime.now(UTC)).astimezone(UTC)
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, observed: Any = None) -> None:
        checks.append({"id": name, "passed": bool(passed), "observed": observed})

    if not isinstance(receipt, dict):
        raise ValueError("expected staging receipt object")
    check("supported_receipt", receipt.get("schema_version") == RECEIPT_SCHEMA)
    check("actual_staging_execution", receipt.get("mode") == "apply")
    check(
        "complete_required_portfolio",
        (
            receipt.get("profile") == rules["required_profile"]
            and isinstance(receipt.get("source_keys"), list)
            and len(receipt["source_keys"]) == rules["required_source_count"]
            and set(receipt["source_keys"]) == set(PROFILES[rules["required_profile"]])
        ),
    )
    preflight = receipt.get("preflight")
    check(
        "authorized_current_schema",
        (
            isinstance(preflight, dict)
            and preflight.get("database_identity_verified") is True
            and preflight.get("schema_current") is True
            and preflight.get("source_permissions_checked") == rules["required_source_count"]
        ),
    )
    result = receipt.get("results")
    if not isinstance(result, dict):
        result = {}
    check(
        "measured_successful_batch",
        (
            receipt.get("status") == "MEASURED"
            and result.get("status") == "MEASURED"
            and result.get("evidence_kind") == EXPECTED_EVIDENCE
            and result.get("batch_status") == "SUCCEEDED"
            and result.get("net_unique_target_met") is True
        ),
    )
    observed_at = result.get("observed_at")
    age_hours: float | None = None
    if isinstance(observed_at, str):
        try:
            instant = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if instant.utcoffset() is not None:
                age_hours = (clock - instant.astimezone(UTC)).total_seconds() / 3600
        except ValueError:
            pass
    check(
        "recent_database_evidence",
        (age_hours is not None and -5 / 60 <= age_hours <= rules["maximum_evidence_age_hours"]),
    )
    catalog = result.get("post_catalog")
    catalog = catalog if isinstance(catalog, dict) else {}
    values = {field: _nonnegative(catalog.get(field)) for field in EXPECTED_METRICS}
    active = values["unique_active"]
    consistent = (
        active is not None
        and all(value is not None and value <= active for value in values.values())
        and (values["upcoming_by_date"] or 0) <= active
    )
    check("nonnegative_coherent_catalog", consistent)
    check(
        "minimum_active_unique",
        (active is not None and active >= rules["minimum_active_unique"]),
        active,
    )
    check(
        "minimum_upcoming_unique",
        (
            values["upcoming_by_date"] is not None
            and values["upcoming_by_date"] >= rules["minimum_upcoming_unique"]
        ),
        values["upcoming_by_date"],
    )
    funnel = result.get("post_global_funnel")
    funnel = funnel if isinstance(funnel, dict) else {}
    searchable = _nonnegative(funnel.get("unique_searchable_hackathons"))
    check(
        "minimum_searchable_unique",
        (
            searchable is not None
            and active is not None
            and searchable <= active
            and searchable >= rules["minimum_searchable_unique"]
        ),
        searchable,
    )

    def percent(numerator: int | None) -> float | None:
        if numerator is None or active is None or active == 0 or not consistent:
            return None
        return round(100 * numerator / active, 2)

    official = percent(values["with_official_url"])
    dated = percent(values["with_start_end"])
    known_close = (
        percent(active - values["unknown_registration_close"])
        if active is not None and values["unknown_registration_close"] is not None
        else None
    )
    for key, actual, threshold in (
        ("official_url_completeness", official, rules["minimum_official_url_percent"]),
        ("start_end_completeness", dated, rules["minimum_start_and_end_percent"]),
        (
            "known_registration_close_completeness",
            known_close,
            rules["minimum_known_registration_close_percent"],
        ),
    ):
        check(key, actual is not None and actual >= threshold, actual)

    sources = result.get("sources")
    sources = sources if isinstance(sources, list) else []
    by_key = {
        x.get("key"): x for x in sources if isinstance(x, dict) and isinstance(x.get("key"), str)
    }
    check(
        "all_sources_succeeded",
        (
            len(sources) == rules["required_source_count"]
            and len(by_key) == rules["required_source_count"]
            and set(by_key) == set(PROFILES[rules["required_profile"]])
            and all(x.get("status") == "SUCCEEDED" for x in by_key.values())
        ),
        sum(x.get("status") == "SUCCEEDED" for x in by_key.values()),
    )
    # A status flag alone is not proof that each source was actually queried.
    # Strict positive-page evidence and consistent processed counts are needed
    # before an operator can independently review a candidate launch.
    allowed_outcomes = {"CREATED", "LINKED", "UPDATED", "REJECTED"}
    min_pages = rules["minimum_fetched_pages_per_source"]
    complete_fetch = len(by_key) == rules["required_source_count"] and all(
        _nonnegative(row.get("pages_fetched")) is not None
        and row["pages_fetched"] >= min_pages
        and _nonnegative(row.get("versions_processed")) is not None
        and isinstance(row.get("run_outcomes"), dict)
        and set(row["run_outcomes"]) == allowed_outcomes
        and all(_nonnegative(value) is not None for value in row["run_outcomes"].values())
        and sum(row["run_outcomes"].values()) <= row["versions_processed"]
        for row in by_key.values()
    )
    check("each_source_has_fetch_and_processing_evidence", complete_fetch)
    backlog_unknown = result.get("has_unverified_backlog_sources")
    backlog_open = result.get("has_incomplete_or_exhausted_backlog")
    check(
        "all_backlog_evidence_verified",
        (
            backlog_unknown is False and backlog_open is False
            if rules["require_drained_and_tracked_backlog"]
            else True
        ),
    )
    # Observational evidence qualifies for *human launch review* only.
    passed = all(row["passed"] for row in checks)
    return {
        "schema_version": "coverage-release-review-v1",
        "policy_version": rules["schema_version"],
        "status": "READY_FOR_INDEPENDENT_REVIEW" if passed else "NOT_READY",
        "checks_passed": sum(row["passed"] for row in checks),
        "checks_total": len(checks),
        "checks": checks,
        "limitations": (
            "This is a bounded staging catalog evidence review, NOT launch "
            "authorization, production validation, global recall, or proof "
            "that the campaign caused the observed increase. Independent "
            "security, data rights, operational approval and live user-path "
            "tests are still required."
        ),
    }


def run_file(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("missing or oversized receipt")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return evaluate(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="write NOT_READY evidence without failing the data-collection workflow",
    )
    args = parser.parse_args(argv)
    try:
        result = run_file(args.receipt)
    except (ValueError, OSError, json.JSONDecodeError, TypeError, KeyError) as exc:
        result = {
            "schema_version": "coverage-release-review-v1",
            "policy_version": "coverage-release-policy-v1",
            "status": "UNVERIFIED",
            "error_type": type(exc).__name__,
            "limitations": "No trustworthy staging evidence available; never approve a release.",
        }
    write_receipt(args.output, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "checks_passed": result.get("checks_passed", 0),
                "checks_total": result.get("checks_total", 0),
            }
        )
    )
    return 0 if result["status"] == "READY_FOR_INDEPENDENT_REVIEW" or args.advisory else 1


if __name__ == "__main__":
    raise SystemExit(main())
