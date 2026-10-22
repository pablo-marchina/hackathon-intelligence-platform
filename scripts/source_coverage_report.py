#!/usr/bin/env python3
"""Offline source coverage / onboarding audit; never contacts third-party websites.

Use the result to prioritize legal review and connector development. A source
listed here is NOT necessarily authorized or successfully collecting records.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/source_sdk/src"))
from hackathon_sources.models import IngestionMode, PermissionStatus, SourceDefinition  # noqa: E402
from hackathon_sources.registry import load_source_definitions  # noqa: E402

FETCH_MODES = {
    IngestionMode.API,
    IngestionMode.RSS,
    IngestionMode.SITEMAP,
    IngestionMode.STATIC_HTML,
    IngestionMode.BROWSER,
}


def audit(sources: list[SourceDefinition]) -> dict[str, object]:
    """Report configured and eligible connectors without claiming real-world coverage."""
    keys: set[str] = set()
    families: set[tuple[str, str]] = set()
    rows: list[dict[str, object]] = []
    errors: list[str] = []
    for source in sources:
        if source.key in keys:
            errors.append(f"duplicate source key: {source.key}")
        keys.add(source.key)
        identity = (source.domain, source.source_family)
        if identity in families:
            errors.append(f"duplicate domain/family identity: {identity}")
        families.add(identity)
        eligible = (
            source.permission_status is PermissionStatus.ALLOWED
            and source.ingestion_mode in FETCH_MODES
            and source.discovery_enabled
            and source.detail_extraction_enabled
        )
        if eligible and not source.seed_urls:
            errors.append(f"fetch-enabled source without seed URLs: {source.key}")
        rows.append(
            {
                "key": source.key,
                "name": source.name,
                "domain": source.domain,
                "source_type": source.source_type,
                "ingestion_mode": source.ingestion_mode.value,
                "permission_status": source.permission_status.value,
                "fetch_eligible_by_configuration": eligible,
                "seed_url_count": len(source.seed_urls),
                "priority": source.priority,
                "trust_score": source.trust_score,
                "review_needed": source.permission_status is not PermissionStatus.ALLOWED,
            }
        )
    rows.sort(key=lambda item: (-int(item["priority"]), str(item["key"])))
    permissions = Counter(str(item["permission_status"]) for item in rows)
    modes = Counter(str(item["ingestion_mode"]) for item in rows)
    return {
        "source_count": len(rows),
        "fetch_eligible_by_configuration": sum(
            bool(item["fetch_eligible_by_configuration"]) for item in rows
        ),
        "requires_permission_review": sum(bool(item["review_needed"]) for item in rows),
        "permission_breakdown": dict(sorted(permissions.items())),
        "mode_breakdown": dict(sorted(modes.items())),
        "sources": rows,
        "validation_errors": errors,
        "live_coverage_verified": False,
        "note": "Configuration audit only. Actual fetched events require live runs, provenance and deduplication.",
    }


def markdown(report: dict[str, object]) -> str:
    lines = [
        "# Hackathon source coverage — configuration audit",
        "",
        f"**Sources configured:** {report['source_count']}",
        f"**Eligible to fetch by configuration:** {report['fetch_eligible_by_configuration']}",
        f"**Require separate permission review:** {report['requires_permission_review']}",
        "",
        "> This is not an event count, license approval, or proof of live coverage.",
        "",
        "| Source | Domain | Permission | Mode | Eligible | Priority |",
        "| --- | --- | --- | --- | --- | ---: |",
    ]
    for item in report["sources"]:
        lines.append(
            f"| {item['name']} | {item['domain']} | {item['permission_status']} | "
            f"{item['ingestion_mode']} | {'yes' if item['fetch_eligible_by_configuration'] else 'no'} | {item['priority']} |"
        )
    if report["validation_errors"]:
        lines.extend(["", "## Invalid registry configuration"])
        lines.extend(f"- {e}" for e in report["validation_errors"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "db/seeds/sources_v0_1.json")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args()
    report = audit(load_source_definitions(args.registry))
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(markdown(report), end="")
    return 1 if report["validation_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
