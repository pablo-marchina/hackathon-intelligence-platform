#!/usr/bin/env python3
"""Run an explicitly authorized staging collection with before/after SQL evidence.

Without --apply: JSON plan only, no database connection and no HTTP.
With --apply: snapshots from one PostgreSQL database bracket the existing
source-scoped, permission-fenced controlled batch. Never auto-approves a source.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text

from scripts._local_packages import add_repo_packages

ROOT = Path(__file__).resolve().parents[2]
add_repo_packages(ROOT)

from hackathon_api.repositories.coverage import CoverageRepository  # noqa: E402
from hackathon_api.repositories.source_opportunities import (  # noqa: E402
    SourceOpportunityRepository,
)
from hackathon_sources.models import SourceDefinition  # noqa: E402
from hackathon_sources.registry import load_source_definitions  # noqa: E402

from scripts.ops.controlled_batch_ingestion import (  # noqa: E402
    _url,
    plan,
    require_batch_apply,
    run_batch,
    select_sources,
)

COUNTS = (
    "unique_active",
    "upcoming_by_date",
    "ongoing_by_date",
    "confirmed_registration_window",
    "unknown_registration_close",
    "with_official_url",
    "with_start_end",
)
SOURCE_COUNTS = (
    "attributed_unique_active",
    "exclusive_canonicals",
    "shared_canonicals",
    "missing_registration_deadline",
    "unknown_cost",
    "missing_prize_details",
)
FUNNEL_COUNTS = (
    "active_references",
    "staged_references",
    "pending_references",
    "accepted_references",
    "rejected_references",
    "linked_searchable_references",
    "unique_searchable_hackathons",
)


def require_campaign_apply(env: dict[str, str]) -> None:
    """Operator must separately authorize batch collection AND its report."""
    require_batch_apply(env)
    if env.get("HIP_ALLOW_COVERAGE_CAMPAIGN") != "1":
        raise ValueError("HIP_ALLOW_COVERAGE_CAMPAIGN=1 is required")
    if env.get("APP_ENV", "").lower() not in {"staging", "test"}:
        raise ValueError("coverage campaigns require APP_ENV=staging or test")


def capture(engine: Engine, sources: list[SourceDefinition]) -> dict[str, Any]:
    """Use a repeatable-read transaction to prevent inconsistent SQL joins.

    This only obtains metadata/counts. Neither this function nor the
    administrators' dashboard obtains any permission to fetch new URLs.
    """
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        coverage = CoverageRepository(conn).snapshot()
        opportunities = SourceOpportunityRepository(conn).snapshot(limit=100)
        by_source = {
            str(row["source_id"]): row for row in opportunities["sources"]
        }
        by_coverage = {str(row["source_id"]): row for row in coverage["sources"]}
        cohorts: dict[str, Any] = {}
        for source in sources:
            source_id = conn.execute(
                text("""
                    SELECT id::text FROM sources
                     WHERE domain=:domain AND source_family=:family
                """),
                {"domain": source.domain, "family": source.source_family},
            ).scalar_one_or_none()
            opportunity = by_source.get(str(source_id)) if source_id else None
            discovered = by_coverage.get(str(source_id)) if source_id else None
            cohorts[source.key] = {
                "registered": source_id is not None,
                "source_id": source_id,
                "attribution": (
                    {field: int(opportunity[field]) for field in SOURCE_COUNTS}
                    if opportunity is not None
                    else None
                ),
                "funnel": (
                    {field: int(discovered["funnel"][field]) for field in FUNNEL_COUNTS}
                    if discovered is not None
                    else None
                ),
                "backlog": (
                    {
                        field: int(discovered[field])
                        for field in (
                            "backlog_pending",
                            "backlog_retry",
                            "backlog_claimed",
                            "backlog_exhausted",
                            "backlog_ready_now",
                        )
                    }
                    if discovered is not None
                    else None
                ),
            }
        return {
            "as_of": coverage["as_of"],
            "catalog": {field: int(coverage["catalog"][field]) for field in COUNTS},
            "field_counts": {
                key: int(value)
                for key, value in coverage["field_completeness"]["counts"].items()
            },
            "pipeline": {
                key: int(value)
                for key, value in coverage["pipeline"].items()
                if key != "backlog" and type(value) is int
            },
            "global_funnel": {
                field: int(coverage["conversion_funnel"][field])
                for field in FUNNEL_COUNTS
            },
            "source_cohorts": cohorts,
        }


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    if before.keys() != after.keys():
        raise ValueError("incompatible before/after database snapshot fields")
    return {key: after[key] - before[key] for key in before}


def compare(
    before: dict[str, Any],
    after: dict[str, Any],
    batch: dict[str, Any],
    *,
    min_net_unique: int = 0,
) -> dict[str, Any]:
    """Net deltas are observed changes, not causal incremental unique yield."""
    if min_net_unique < 0:
        raise ValueError("minimum net unique must be nonnegative")
    for field in ("catalog", "field_counts", "pipeline", "global_funnel"):
        if field not in before or field not in after:
            raise ValueError("before/after snapshot section missing")
    if before["source_cohorts"].keys() != after["source_cohorts"].keys():
        raise ValueError("different source sets in before/after snapshots")
    cohort_changes: dict[str, Any] = {}
    for key, earlier in before["source_cohorts"].items():
        later = after["source_cohorts"][key]
        if earlier["source_id"] is not None and later["source_id"] != earlier["source_id"]:
            raise ValueError("source identity changed during campaign")
        changes = {
            field: (
                _delta(earlier[field], later[field])
                if earlier[field] is not None and later[field] is not None
                else None
            )
            for field in ("attribution", "funnel", "backlog")
        }
        cohort_changes[key] = {
            "source_id": later["source_id"],
            "registered_before": earlier["registered"],
            "registered_after": later["registered"],
            "net_deltas": changes,
            "after": later,
        }
    count_change = _delta(before["catalog"], after["catalog"])
    meets_threshold = count_change["unique_active"] >= min_net_unique
    batch_success = batch.get("status") == "SUCCEEDED"
    backlog_incomplete = any(
        row.get("budget_hit_with_pending_urls") is True
        or int((row.get("backlog_by_state") or {}).get("EXHAUSTED", 0)) > 0
        for row in batch.get("sources", [])
    )
    return {
        "schema_version": "coverage-campaign-v1",
        "evidence_kind": "OBSERVED_DATABASE_BEFORE_AFTER_NOT_CAUSAL",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": (
            "FAILED_OR_BLOCKED"
            if not batch_success
            else "BELOW_TARGET"
            if not meets_threshold
            else "NEEDS_BACKLOG_FOLLOWUP"
            if backlog_incomplete
            else "MEASURED"
        ),
        "batch_status": batch.get("status"),
        "minimum_net_unique_target": min_net_unique,
        "net_unique_target_met": meets_threshold,
        "has_incomplete_or_exhausted_backlog": backlog_incomplete,
        "database_as_of": {"before": before["as_of"], "after": after["as_of"]},
        "net_deltas": {
            "catalog": count_change,
            "field_counts": _delta(before["field_counts"], after["field_counts"]),
            "pipeline": _delta(before["pipeline"], after["pipeline"]),
            "global_funnel": _delta(before["global_funnel"], after["global_funnel"]),
        },
        "per_source": cohort_changes,
        "snapshots": {"before": before, "after": after},
        "batch": batch,
        "limitations": (
            "Before/after totals are observational, not causally attributable to the "
            "batch: concurrent writes, historical imports, merges or deactivations "
            "can change these counts. Per-source cohorts overlap: never sum them "
            "as global unique hackathons. An empty backlog is not global coverage. "
            "Unknown fields are unknown, not false."
        ),
    }


async def run_campaign(
    engine: Engine,
    sources: list[SourceDefinition],
    *,
    pages: int,
    versions: int,
    min_net_unique: int = 0,
    runner: Any = run_batch,
) -> dict[str, Any]:
    if min_net_unique < 0:
        raise ValueError("minimum net unique must be nonnegative")
    plan(sources, pages=pages, versions=versions)  # validates budgets first
    before = capture(engine, sources)
    batch = await runner(engine, sources, pages=pages, versions=versions)
    after = capture(engine, sources)
    return compare(before, after, batch, min_net_unique=min_net_unique)


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", dest="sources")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--max-versions", type=int, default=100)
    parser.add_argument("--min-net-unique", type=int, default=0)
    parser.add_argument("--require-drained", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    definitions = load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    try:
        selected = select_sources(definitions, args.sources)
        preview = plan(selected, pages=args.max_pages, versions=args.max_versions)
        if args.min_net_unique < 0:
            raise ValueError("minimum net unique must be nonnegative")
    except ValueError as exc:
        parser.error(str(exc))
    preview["campaign"] = {
        "evidence": "before/after database snapshots plus controlled batch",
        "min_net_unique": args.min_net_unique,
        "require_drained": args.require_drained,
        "additional_opt_in": "HIP_ALLOW_COVERAGE_CAMPAIGN=1 and APP_ENV=staging|test",
    }
    if not args.apply:
        print(json.dumps(preview, ensure_ascii=False, sort_keys=True))
        return 0
    current_env = dict(os.environ) if env is None else env
    try:
        require_campaign_apply(current_env)
    except ValueError as exc:
        parser.error(str(exc))
    engine = create_engine(
        _url(current_env["DATABASE_URL"]),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        result = asyncio.run(
            run_campaign(
                engine,
                selected,
                pages=args.max_pages,
                versions=args.max_versions,
                min_net_unique=args.min_net_unique,
            )
        )
    except Exception as exc:
        print(
            json.dumps({"status": "FAILED", "error_type": type(exc).__name__}),
            file=sys.stderr,
        )
        return 3
    finally:
        engine.dispose()
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    if result["batch_status"] != "SUCCEEDED" or not result["net_unique_target_met"]:
        return 1
    if args.require_drained and result["has_incomplete_or_exhausted_backlog"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
