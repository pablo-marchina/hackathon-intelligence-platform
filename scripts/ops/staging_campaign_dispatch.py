"""Explicit, auditable GitHub Actions staging coverage dispatch.

This is a safer wrapper around scripts.ops.coverage_campaign, not a scheduler.
PLAN_ONLY requires no credentials/DB and never opens a socket.
APPLY requires staging-specific DB identity, current schema, operator approval
for *all* selected sources and independent per-run flags before any fetch.
Only whitelisted aggregate metrics are ever persisted in the public artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from hackathon_sources.models import SourceDefinition
from hackathon_sources.registry import load_source_definitions
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from scripts.ops.controlled_batch_ingestion import _url, plan, select_sources
from scripts.ops.coverage_campaign import require_campaign_apply, run_campaign

ROOT = Path(__file__).resolve().parents[2]
RECEIPT_SCHEMA = "staging-coverage-dispatch-v1"
CONFIRMATION = "RUN_STAGING_COVERAGE"
PROFILES: dict[str, tuple[str, ...]] = {
    "dribdat": ("dribdat_glamhack", "dribdat_imprs_astro"),
    "hackclub": (
        "hackclub_hackathons",
        "hackclub_programs",
        "hackclub_community_events",
        "hackclub_ysws_programs",
        "agenda_tech_brasil",
    ),
    "all": (
        "hackclub_hackathons",
        "hackclub_programs",
        "hackclub_community_events",
        "hackclub_ysws_programs",
        "agenda_tech_brasil",
        "dribdat_glamhack",
        "dribdat_imprs_astro",
    ),
}
PAGE_OPTIONS = (1, 3, 5)
VERSION_OPTIONS = (25, 100, 200)
TARGET_OPTIONS = (0, 1, 5)


class DispatchRefusal(ValueError):
    """Safe, non-secret reason code for a blocked workflow."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _selection(profile: str, pages: int, versions: int) -> tuple[list[SourceDefinition], dict]:
    if profile not in PROFILES or pages not in PAGE_OPTIONS or versions not in VERSION_OPTIONS:
        raise DispatchRefusal("INVALID_BOUNDED_SELECTION")
    definitions = load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    try:
        selected = select_sources(definitions, list(PROFILES[profile]))
        preview = plan(selected, pages=pages, versions=versions)
    except ValueError as exc:
        raise DispatchRefusal("REGISTRY_SELECTION_REJECTED") from exc
    return selected, preview


def validate_apply(
    env: dict[str, str], *, confirmation: str, db_name: str | None
) -> str:
    """Gate side effects before create_engine(), even when called outside Actions."""
    if confirmation != CONFIRMATION:
        raise DispatchRefusal("MANUAL_CONFIRMATION_REQUIRED")
    try:
        require_campaign_apply(env)
    except ValueError as exc:
        raise DispatchRefusal("CAMPAIGN_OPT_IN_MISSING") from exc
    if env.get("APP_ENV") != "staging":
        raise DispatchRefusal("STAGING_ONLY")
    expected = env.get("HIP_STAGING_EXPECTED_DB_NAME", "")
    if (
        not re.fullmatch(r"[a-z][a-z0-9_]{5,62}", expected)
        or "staging" not in expected
        or db_name != expected
    ):
        raise DispatchRefusal("STAGING_DATABASE_IDENTITY_MISMATCH")
    dsn = _url(env["DATABASE_URL"])
    try:
        url = make_url(dsn)
        if url.get_backend_name() != "postgresql" or url.database != expected:
            raise DispatchRefusal("STAGING_DATABASE_IDENTITY_MISMATCH")
    except (ValueError, TypeError) as exc:
        raise DispatchRefusal("INVALID_DATABASE_URL") from exc
    return dsn


def expected_revision() -> str:
    script = ScriptDirectory.from_config(Config(str(ROOT / "db/alembic.ini")))
    head = script.get_current_head()
    if not head:
        raise DispatchRefusal("NO_SCHEMA_HEAD")
    return head


def preflight(engine: Engine, selected: list[SourceDefinition], expected: str) -> dict:
    """One read-only transaction, all-or-nothing authorization before HTTP.

    The live collector also rechecks DB permission and pauses per source/page
    so a later revocation is still enforced after this early preflight.
    """
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        database = connection.execute(text("SELECT current_database()")).scalar_one()
        if database != expected:
            raise DispatchRefusal("DATABASE_SERVER_IDENTITY_MISMATCH")
        head = expected_revision()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if revision != [head]:
            raise DispatchRefusal("SCHEMA_MIGRATION_NOT_CURRENT")
        for source in selected:
            permitted = connection.execute(
                text("""
                    SELECT active AND discovery_enabled AND detail_extraction_enabled
                           AND permission_status='ALLOWED'
                           AND ingestion_mode NOT IN ('BLOCKED','DISCOVERY_ONLY','MANUAL')
                      FROM sources
                     WHERE domain=:domain AND source_family=:family
                """),
                {"domain": source.domain, "family": source.source_family},
            ).scalar_one_or_none()
            if permitted is not True:
                raise DispatchRefusal("SOURCE_NOT_AUTHORIZED_IN_STAGING")
        return {"database_identity_verified": True, "schema_current": True,
                "source_permissions_checked": len(selected)}


def summarized_report(report: dict[str, Any], selected: list[SourceDefinition]) -> dict:
    """Explicit allowlist prevents accidentally uploading private event data or DSN."""
    batch = report.get("batch") or {}
    src = {row.get("source"): row for row in batch.get("sources", []) if isinstance(row, dict)}
    return {
        "status": report["status"],
        "evidence_kind": report["evidence_kind"],
        "batch_status": report["batch_status"],
        "net_unique_target_met": report["net_unique_target_met"],
        "minimum_net_unique_target": report["minimum_net_unique_target"],
        "has_incomplete_or_exhausted_backlog": report["has_incomplete_or_exhausted_backlog"],
        "has_unverified_backlog_sources": report["has_unverified_backlog_sources"],
        "catalog_net_deltas": {
            key: int(value) for key, value in report["net_deltas"]["catalog"].items()
        },
        "field_net_deltas": {
            key: int(value) for key, value in report["net_deltas"]["field_counts"].items()
        },
        "sources": [
            {
                "key": source.key,
                "status": src.get(source.key, {}).get("status", "NOT_EXECUTED"),
                "pages_fetched": src.get(source.key, {}).get("pages_fetched"),
                "versions_processed": src.get(source.key, {}).get("versions_processed"),
                "run_outcomes": {
                    key: int(value)
                    for key, value in (src.get(source.key, {}).get("run_outcomes") or {}).items()
                    if key in {"CREATED", "LINKED", "UPDATED", "REJECTED"}
                },
                "unprocessed_backlog_urls": src.get(source.key, {}).get(
                    "unprocessed_backlog_urls"
                ),
                "backlog_by_state": {
                    key: int(value)
                    for key, value in (src.get(source.key, {}).get("backlog_by_state") or {}).items()
                    if key in {"PENDING", "RETRY", "CLAIMED", "DONE", "EXHAUSTED"}
                },
                "attributed_active_delta": (
                    (report.get("per_source", {}).get(source.key, {}).get("net_deltas", {})
                     .get("attribution") or {}).get("attributed_unique_active")
                ),
            }
            for source in selected
        ],
        "limitations": (
            "Observed net catalog changes are not causally attributable to this run. "
            "Source attribution overlaps. Missing data is not false. "
            "A successful campaign does not demonstrate global completeness."
        ),
    }


def run_dispatch(
    *,
    mode: str,
    profile: str,
    confirmation: str,
    pages: int,
    versions: int,
    min_net_unique: int,
    require_drained: bool,
    env: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "mode": mode,
        "profile": profile,
        "source_keys": [],
        "status": "FAILED",
        "network_attempted": False,
        "database_accessed": False,
    }
    try:
        if mode not in {"plan", "apply"} or min_net_unique not in TARGET_OPTIONS:
            raise DispatchRefusal("INVALID_MODE_OR_TARGET")
        selected, preview = _selection(profile, pages, versions)
        receipt["source_keys"] = [item.key for item in selected]
        receipt["budgets"] = {
            "pages_per_source": pages, "versions_per_source": versions,
            "max_total_pages": preview["max_total_pages"],
            "max_total_versions": preview["max_total_versions"],
        }
        receipt["minimum_net_unique_target"] = min_net_unique
        receipt["require_drained"] = require_drained
        if mode == "plan":
            receipt["status"] = "PLAN_ONLY"
            return 0, receipt
        dsn = validate_apply(
            env,
            confirmation=confirmation,
            db_name=env.get("HIP_STAGING_EXPECTED_DB_NAME"),
        )
        engine = create_engine(dsn, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        try:
            receipt["database_accessed"] = True
            receipt["preflight"] = preflight(
                engine, selected, env["HIP_STAGING_EXPECTED_DB_NAME"]
            )
            # From here the existing collector performs its own transactional,
            # per-page authorization checks, even after an operator revokes a source.
            receipt["network_attempted"] = True
            report = asyncio.run(
                run_campaign(
                    engine, selected, pages=pages, versions=versions,
                    min_net_unique=min_net_unique
                )
            )
            safe = summarized_report(report, selected)
            receipt["results"] = safe
            receipt["status"] = safe["status"]
            gated = (
                report["batch_status"] != "SUCCEEDED"
                or not report["net_unique_target_met"]
                or (
                    require_drained
                    and (
                        report["has_incomplete_or_exhausted_backlog"]
                        or report["has_unverified_backlog_sources"]
                    )
                )
            )
            return (1 if gated else 0), receipt
        finally:
            engine.dispose()
    except DispatchRefusal as exc:
        receipt["status"] = "BLOCKED"
        receipt["reason_code"] = exc.code
        return 2, receipt
    except Exception as exc:
        # NEVER print exception text, URL, connection string, or unsanitized
        # source response to workflow logs/artifacts (public repo).
        receipt["status"] = "FAILED"
        receipt["error_type"] = type(exc).__name__
        return 3, receipt


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | nofollow, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(receipt, file, sort_keys=True, ensure_ascii=False, indent=2)
            file.write("\n")
    except BaseException:
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "apply"), default="plan")
    parser.add_argument("--profile", choices=tuple(PROFILES), default="dribdat")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--max-pages", type=int, choices=PAGE_OPTIONS, default=1)
    parser.add_argument("--max-versions", type=int, choices=VERSION_OPTIONS, default=25)
    parser.add_argument("--min-net-unique", type=int, choices=TARGET_OPTIONS, default=0)
    parser.add_argument("--require-drained", action="store_true")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    exit_code, receipt = run_dispatch(
        mode=args.mode, profile=args.profile, confirmation=args.confirm,
        pages=args.max_pages, versions=args.max_versions,
        min_net_unique=args.min_net_unique, require_drained=args.require_drained,
        env=dict(os.environ),
    )
    write_receipt(args.receipt, receipt)
    print(json.dumps({
        "status": receipt["status"], "profile": receipt["profile"],
        "source_count": len(receipt["source_keys"]), "exit_code": exit_code,
        "receipt_written": True,
    }))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
