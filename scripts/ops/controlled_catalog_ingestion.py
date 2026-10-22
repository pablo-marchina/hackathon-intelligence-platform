#!/usr/bin/env python3
"""Opt-in single-source crawl + canonicalization + auditable yield report.

Without --apply this command NEVER connects to a database or opens a network
socket. With --apply it requires an explicit environment gate and an authorized
source in BOTH the seed registry and PostgreSQL. It does not activate a scheduler.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text

from scripts._local_packages import add_repo_packages

ROOT = Path(__file__).resolve().parents[2]
add_repo_packages(ROOT)

from hackathon_intelligence.incremental import CanonicalProcessor  # noqa: E402
from hackathon_sources.factory import build_adapter  # noqa: E402
from hackathon_sources.fetcher import FetchEngine  # noqa: E402
from hackathon_sources.models import IngestionMode, PermissionStatus, SourceDefinition  # noqa: E402
from hackathon_sources.persistent_runner import (  # noqa: E402
    SourceNotAuthorizedError,
    SourcePausedError,
    run_source_persisted,
)
from hackathon_sources.registry import load_source_definitions  # noqa: E402
from hackathon_sources.source_switch import check_postgres_active  # noqa: E402

_OUTCOMES = ("CREATED", "LINKED", "UPDATED", "REJECTED")


def validate_selection(definitions: list[SourceDefinition], source_key: str) -> SourceDefinition:
    """Reject unapproved, inactive-capability and unknown sources before DB/network."""
    matches = [definition for definition in definitions if definition.key == source_key]
    if len(matches) != 1:
        raise ValueError("source key must identify exactly one registered source")
    source = matches[0]
    if (
        source.permission_status is not PermissionStatus.ALLOWED
        or not source.discovery_enabled
        or not source.detail_extraction_enabled
        or source.ingestion_mode
        in {IngestionMode.BLOCKED, IngestionMode.DISCOVERY_ONLY, IngestionMode.MANUAL}
        or not source.seed_urls
    ):
        raise ValueError("source is not approved for active detail collection in seed registry")
    return source


def require_explicit_apply(env: dict[str, str]) -> None:
    if env.get("HIP_ALLOW_LIVE_INGESTION") != "1":
        raise ValueError("HIP_ALLOW_LIVE_INGESTION=1 is required with --apply")
    if env.get("APP_ENV", "").lower() not in {"development", "test", "staging"}:
        raise ValueError(
            "explicit APP_ENV=development|test|staging is required; production is refused"
        )
    if not env.get("DATABASE_URL"):
        raise ValueError("DATABASE_URL is required with --apply")


def _url(value: str) -> str:
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def _unique_active(conn: Any) -> int:
    return int(
        conn.execute(
            text("""
        SELECT COUNT(*) FROM hackathons WHERE is_active AND merged_into_id IS NULL
    """)
        ).scalar_one()
    )


def _run_outcomes(conn: Any, crawl_run_id: str) -> dict[str, int]:
    # Original crawl ID is persisted on each staged payload; this avoids
    # incorrectly attributing unrelated canonical changes to this execution.
    rows = conn.execute(
        text("""
        SELECT outcome::text, COUNT(*) AS total
        FROM canonical_processing_outcomes
        WHERE crawl_run_id = CAST(:run_id AS uuid)
        GROUP BY outcome
    """),
        {"run_id": crawl_run_id},
    ).all()
    counts = {key: 0 for key in _OUTCOMES}
    for outcome, count in rows:
        if outcome in counts:
            counts[outcome] = int(count)
    return counts


async def run_once(
    engine: Engine,
    source: SourceDefinition,
    *,
    max_pages: int,
    max_versions: int,
    fetcher_factory: Any = FetchEngine,
) -> dict[str, Any]:
    """One limited crawl then one-source normalization, with DB-owned authorization.

    Commits each canonical version independently (the normalizer's documented
    contract). Failures do not roll back already-completed pages or versions;
    the emitted report is intentionally NOT an atomic all-or-nothing receipt.
    """
    if not 1 <= max_pages <= 20 or not 1 <= max_versions <= 1000:
        raise ValueError("page/version budgets out of bounds")
    # Fail closed BEFORE creating the fetcher or issuing a network request.
    if not check_postgres_active(engine, source):
        raise PermissionError("source is disabled, unregistered, or not authorized in PostgreSQL")
    with engine.connect() as conn:
        before = _unique_active(conn)
        conn.commit()  # end the read transaction before the network-bound crawl
        async with fetcher_factory() as fetcher:
            result = await run_source_persisted(
                build_adapter(source),
                fetcher,
                conn,
                max_pages=max_pages,
            )
        # A cancellation or failed fetch is visible; NEVER normalize after it.
        if result.status in {"FAILED", "CANCELLED"}:
            return {
                "source": source.key,
                "crawl_run_id": result.crawl_run_id,
                "status": result.status,
                "pages_fetched": result.fetched_pages,
                "references_persisted": result.persisted_event_refs,
                "versions_processed": 0,
                "catalog_before": before,
                "catalog_after": _unique_active(conn),
                "run_outcomes": _run_outcomes(conn, result.crawl_run_id),
            }
        processed = {key: 0 for key in _OUTCOMES}
        for _ in range(max_versions):
            # DB policy can change during a run; the processor checks it under
            # source lock. Do not switch to another source to fill the budget.
            if not check_postgres_active(engine, source):
                break
            with engine.begin() as tx:
                processor = CanonicalProcessor(tx, source_key=source.key)
                status = processor.process_one()
                exact = getattr(processor, "last_outcome", None) or status
            if status not in _OUTCOMES:
                break
            if exact not in _OUTCOMES:
                raise ValueError("normalizer returned unknown canonical outcome")
            processed[exact] += 1
        with engine.connect() as snapshot:
            after = _unique_active(snapshot)
            outcomes = _run_outcomes(snapshot, result.crawl_run_id)
        return {
            "source": source.key,
            "crawl_run_id": result.crawl_run_id,
            "status": result.status,
            "pages_fetched": result.fetched_pages,
            "references_persisted": result.persisted_event_refs,
            "versions_processed": sum(processed.values()),
            "processed_this_invocation": processed,
            "run_outcomes": outcomes,
            "catalog_before": before,
            "catalog_after": after,
            "catalog_delta_observed": after - before,
            "methodology": (
                "run_outcomes group by originating crawl_run_id; an old backlog may "
                "be normalized during this invocation. catalog_delta_observed may "
                "include concurrent writes and historical events, not newly announced hackathons."
            ),
        }


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, help="exact source key from db/seeds/sources_v0_1.json"
    )
    parser.add_argument(
        "--apply", action="store_true", help="explicitly permit live fetching and database writes"
    )
    parser.add_argument("--max-pages", type=int, default=3, help="1–20 pages this run")
    parser.add_argument("--max-versions", type=int, default=100, help="1–1000 normalized versions")
    args = parser.parse_args(argv)
    if not 1 <= args.max_pages <= 20 or not 1 <= args.max_versions <= 1000:
        parser.error("max-pages must be 1–20 and max-versions must be 1–1000")
    definitions = load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    try:
        source = validate_selection(definitions, args.source)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "PLAN_ONLY",
                    "source": source.key,
                    "max_pages": args.max_pages,
                    "max_versions": args.max_versions,
                    "writes": False,
                    "network": False,
                    "requirement": "--apply, HIP_ALLOW_LIVE_INGESTION=1, DATABASE_URL, DB approval",
                }
            )
        )
        return 0
    current_env = dict(os.environ) if env is None else env
    try:
        require_explicit_apply(current_env)
    except ValueError as exc:
        parser.error(str(exc))
    engine = create_engine(
        _url(current_env["DATABASE_URL"]), pool_pre_ping=True, connect_args={"connect_timeout": 5}
    )
    try:
        report = asyncio.run(
            run_once(engine, source, max_pages=args.max_pages, max_versions=args.max_versions)
        )
    except (PermissionError, SourcePausedError, SourceNotAuthorizedError) as exc:
        print(
            json.dumps({"status": "BLOCKED", "source": source.key, "reason": type(exc).__name__}),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        # Never include DB URLs, raw error messages, credentials or source HTML.
        print(
            json.dumps(
                {"status": "FAILED", "source": source.key, "error_type": type(exc).__name__}
            ),
            file=sys.stderr,
        )
        return 3
    finally:
        engine.dispose()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
