#!/usr/bin/env python3
"""Explicitly gated, sequential multi-source catalog collection with measured yield.

Default invocation is PLAN_ONLY: no PostgreSQL and no network. Even with
--apply, each source is checked against both approved seed policy and the
DB-owned authorization/pause switch before constructing an HTTP fetcher.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine

from scripts._local_packages import add_repo_packages

ROOT = Path(__file__).resolve().parents[2]
add_repo_packages(ROOT)

from hackathon_sources.models import SourceDefinition  # noqa: E402
from hackathon_sources.registry import load_source_definitions  # noqa: E402
from hackathon_sources.source_switch import check_postgres_active  # noqa: E402

from scripts.ops.controlled_catalog_ingestion import (  # noqa: E402
    _OUTCOMES,
    _unique_active,
    _url,
    require_explicit_apply,
    run_once,
    validate_selection,
)

MAX_SOURCES = 8
MAX_TOTAL_PAGES = 80
MAX_TOTAL_VERSIONS = 4000


def select_sources(
    definitions: list[SourceDefinition],
    requested: list[str] | None,
) -> list[SourceDefinition]:
    """Reject an entire bad selection before any DB or network operations."""
    keys = requested
    if keys is None:
        keys = [
            source.key
            for source in sorted(definitions, key=lambda s: (-s.priority, s.key))
            if source.permission_status.value == "ALLOWED"
        ]
    if not keys or len(keys) > MAX_SOURCES or len(set(keys)) != len(keys):
        raise ValueError("select 1 to 8 distinct approved source keys")
    return [validate_selection(definitions, key) for key in keys]


def validate_budget(sources: list[SourceDefinition], pages: int, versions: int) -> None:
    if not 1 <= pages <= 20 or not 1 <= versions <= 1000:
        raise ValueError("each source must have 1 to 20 pages and 1 to 1000 versions")
    if len(sources) * pages > MAX_TOTAL_PAGES or len(sources) * versions > MAX_TOTAL_VERSIONS:
        raise ValueError("batch-wide page or canonical-version budget exceeded")


def require_batch_apply(env: dict[str, str]) -> None:
    require_explicit_apply(env)
    if env.get("HIP_ALLOW_BATCH_INGESTION") != "1":
        raise ValueError("HIP_ALLOW_BATCH_INGESTION=1 is required for the multi-source batch")


def plan(sources: list[SourceDefinition], *, pages: int, versions: int) -> dict[str, Any]:
    validate_budget(sources, pages, versions)
    return {
        "status": "PLAN_ONLY",
        "sources": [
            {
                "key": source.key,
                "priority": source.priority,
                "seed_url_count": len(source.seed_urls),
            }
            for source in sources
        ],
        "max_pages_per_source": pages,
        "max_versions_per_source": versions,
        "max_total_pages": pages * len(sources),
        "max_total_versions": versions * len(sources),
        "network": False,
        "writes": False,
        "permission": "registry ALLOWED and per-run PostgreSQL operator approval required",
        "requirement": (
            "--apply, HIP_ALLOW_BATCH_INGESTION=1, HIP_ALLOW_LIVE_INGESTION=1, "
            "APP_ENV=development|staging|test, DATABASE_URL"
        ),
    }


def _outcome_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {key: 0 for key in _OUTCOMES}
    return {
        key: int(value[key]) if type(value.get(key)) is int and value[key] >= 0 else 0
        for key in _OUTCOMES
    }


def _catalog_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return _unique_active(connection)


async def run_batch(
    engine: Engine,
    sources: list[SourceDefinition],
    *,
    pages: int,
    versions: int,
    runner: Any = run_once,
) -> dict[str, Any]:
    """Execute sources sequentially, capturing per-source failures safely.

    All metrics are first-party DB observations. Run-attributed outcomes may
    involve older staged backlog; they are not proof of newly announced events.
    """
    validate_budget(sources, pages, versions)
    start_count = _catalog_count(engine)
    completed: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    for source in sources:
        # Each source may have been paused/revoked after the batch started.
        try:
            authorized = check_postgres_active(engine, source)
        except Exception as exc:
            completed.append(
                {"source": source.key, "status": "FAILED", "error_type": type(exc).__name__}
            )
            continue
        if not authorized:
            completed.append(
                {"source": source.key, "status": "BLOCKED", "reason": "DB_NOT_AUTHORIZED"}
            )
            continue
        try:
            result = await runner(engine, source, max_pages=pages, max_versions=versions)
            outcome = _outcome_counts(result.get("run_outcomes"))
            processed = _outcome_counts(result.get("processed_this_invocation"))
            for key in _OUTCOMES:
                totals[key] += outcome[key]
            completed.append(
                {
                    "source": source.key,
                    "status": result.get("status", "FAILED"),
                    "crawl_run_id": result.get("crawl_run_id"),
                    "pages_fetched": result.get("pages_fetched", 0),
                    "references_persisted": result.get("references_persisted", 0),
                    "versions_processed": result.get("versions_processed", 0),
                    "backlog_tracked": result.get("backlog_tracked"),
                    "backlog_by_state": result.get("backlog_by_state"),
                    "unprocessed_backlog_urls": result.get("unprocessed_backlog_urls"),
                    "budget_hit_with_pending_urls": result.get("budget_hit_with_pending_urls"),
                    "run_outcomes": outcome,
                    "processed_this_invocation": processed,
                    "catalog_delta_observed": result.get("catalog_delta_observed"),
                }
            )
        except PermissionError:
            completed.append(
                {"source": source.key, "status": "BLOCKED", "reason": "DB_NOT_AUTHORIZED"}
            )
        except Exception as exc:
            # Never serialize exception message: HTTP/DB exceptions can expose
            # database credentials, event HTML, tokens or connection URLs.
            completed.append(
                {"source": source.key, "status": "FAILED", "error_type": type(exc).__name__}
            )

    try:
        finish_count = _catalog_count(engine)
    except Exception:
        finish_count = None
    good = sum(row["status"] == "SUCCEEDED" for row in completed)
    status = "SUCCEEDED" if good == len(completed) else "PARTIAL" if good else "FAILED"
    return {
        "schema_version": "controlled-batch-v1",
        "evidence_kind": "AUTHORIZED_DB_MEASURED_IF_REAL_FETCH",
        "finished_at": datetime.now(UTC).isoformat(),
        "status": status,
        "sources_requested": len(sources),
        "sources_succeeded": good,
        "sources_blocked": sum(row["status"] == "BLOCKED" for row in completed),
        "sources_failed": sum(row["status"] == "FAILED" for row in completed),
        "sources_budget_limited": sum(
            row.get("budget_hit_with_pending_urls") is True for row in completed
        ),
        "catalog_before": start_count,
        "catalog_after": finish_count,
        "catalog_delta_observed": (
            finish_count - start_count if finish_count is not None else None
        ),
        "run_attributed_outcomes": _outcome_counts(dict(totals)),
        "sources": completed,
        "limitations": (
            "Run-attributed outcomes are source payloads, not independent "
            "unique hackathon counts. The observed catalog delta may include "
            "other writers and historical editions. Older backlog may be "
            "processed during this invocation. Neither is global coverage."
        ),
    }


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="repeat to select exact keys; omitted means all ALLOWED registry sources",
    )
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--max-versions", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    definitions = load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    try:
        selected = select_sources(definitions, args.sources)
        preview = plan(selected, pages=args.max_pages, versions=args.max_versions)
    except ValueError as exc:
        parser.error(str(exc))

    if not args.apply:
        print(json.dumps(preview, ensure_ascii=False, sort_keys=True))
        return 0
    current_env = dict(os.environ) if env is None else env
    try:
        require_batch_apply(current_env)
    except ValueError as exc:
        parser.error(str(exc))

    engine = create_engine(
        _url(current_env["DATABASE_URL"]),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        report = asyncio.run(
            run_batch(engine, selected, pages=args.max_pages, versions=args.max_versions)
        )
    except Exception as exc:
        # Nothing sensitive from the SQL/network error is printed.
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__}), file=sys.stderr)
        return 3
    finally:
        engine.dispose()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
