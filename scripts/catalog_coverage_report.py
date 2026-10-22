#!/usr/bin/env python3
"""Read-only live catalog coverage snapshot. Requires a reachable, migrated PostgreSQL.

Usage: python scripts/catalog_coverage_report.py --format json
Do not silently fall back to demo/test or the historical W2 counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))
sys.path.insert(0, str(ROOT / "packages/shared/src"))


def render_markdown(snapshot: dict[str, object]) -> str:
    catalog = snapshot["catalog"]
    sources = snapshot["source_counts"]
    assert isinstance(catalog, dict) and isinstance(sources, dict)
    growth = snapshot["growth"]
    assert isinstance(growth, dict)
    field_completeness = snapshot["field_completeness"]
    pipeline = snapshot["pipeline"]
    assert isinstance(field_completeness, dict) and isinstance(pipeline, dict)
    rates = field_completeness["percentages"]
    assert isinstance(rates, dict)
    backlog = pipeline["backlog"]
    funnel = snapshot["conversion_funnel"]
    assert isinstance(funnel, dict)

    def pct(value: object) -> str:
        return f"{float(value):.1f}%" if value is not None else "N/A (no denominator)"

    assert isinstance(backlog, dict)

    def percent(field: str) -> str:
        value = rates[field]
        return f"{value:.1f}%" if value is not None else "N/A (empty catalog)"

    lines = [
        "# Live hackathon catalog — coverage snapshot",
        "",
        f"As of **{snapshot['as_of']}** (database query, not W2 historical baseline)",
        "",
        f"- Unique active hackathons: **{catalog['unique_active']}**",
        f"- New active canonical rows in last {growth['calendar_days']} UTC calendar days: **{growth['new_active_canonicals']}** (includes historical backfills)",
        f"- Upcoming (by start date): **{catalog['upcoming_by_date']}**",
        f"- Ongoing (by start/end): **{catalog['ongoing_by_date']}**",
        f"- Confirmed registration windows: **{catalog['confirmed_registration_window']}**",
        f"- Registration deadline unknown: **{catalog['unknown_registration_close']}**",
        f"- Database sources: **{sources['registered_in_database']}**",
        f"- Active and approved: **{sources['approved_and_active']}**",
        f"- Successful within {snapshot['recent_window_days']} days: **{sources['recent_success']}**",
        "",
        "## Daily growth (UTC; first recorded in catalog, not event start date)",
        "",
        "| UTC date | New active canonical rows |",
        "| --- | ---: |",
        *[f"| {day['date']} | {day['new_active_canonicals']} |" for day in growth["daily"]],
        "",
        "## Discovery and normalization",
        "",
        f"- Staged source records: **{pipeline['staged_payloads']}**",
        f"- Records awaiting normalization: **{pipeline['awaiting_normalization']}**",
        f"- Records with normalization errors: **{pipeline['normalization_errors']}**",
        f"- URLs ready for crawl: **{backlog['backlog_ready_now']}**",
        f"- URLs exhausted: **{backlog['backlog_exhausted']}**",
        f"- URLs with expired claims: **{backlog['backlog_expired_claims']}**",
        "",
        "## Source ingestion funnel (live cohorts; no cross-unit conversion)",
        "",
        f"- Source-scoped discovered URL tasks: **{funnel['discovered_url_tasks']}**",
        f"- Discovered URL tasks currently DONE: **{funnel['fetched_discovered_url_tasks']}** ({pct(funnel['fetch_percent'])} of discovered tasks)",
        f"- Active event references: **{funnel['active_references']}**",
        f"- References with durable payloads: **{funnel['staged_references']}** ({pct(funnel['staging_percent'])} of active refs)",
        f"- Legacy or unstaged references: **{funnel['legacy_unstaged_references']}**",
        f"- References awaiting normalization: **{funnel['pending_references']}**",
        f"- Accepted normalized references: **{funnel['accepted_references']}** ({pct(funnel['acceptance_percent'])} of staged refs)",
        f"- Rejected normalized references: **{funnel['rejected_references']}**",
        f"- Failed pending references: **{funnel['failed_pending_references']}**",
        f"- Accepted references linked to active, non-merged hackathons: **{funnel['linked_searchable_references']}**",
        f"- Accepted references without active canonical link: **{funnel['accepted_unlinked_references']}**",
        f"- DISTINCT hackathons from those links: **{funnel['unique_searchable_hackathons']}**",
        "",
        "> URL tasks and event references are DIFFERENT units; their counts are not a URL-to-event conversion rate.",
        "> Legacy event references without staged payloads remain counted as unstaged; links can overlap across sources.",
        "",
        "## Field completeness (unique active hackathons)",
        "",
        f"- Known format: **{percent('known_format')}**",
        f"- Registration URL: **{percent('with_registration_url')}**",
        f"- Registration deadline: **{percent('with_registration_deadline')}**",
        f"- Participation cost: **{percent('known_participation_cost')}**",
        f"- Worldwide eligibility: **{percent('known_worldwide_eligibility')}**",
        f"- Description: **{percent('with_description')}**",
        f"- Organizer: **{percent('with_organizer')}**",
        f"- Sponsor: **{percent('with_sponsor')}**",
        f"- Tracks: **{percent('with_tracks')}**",
        f"- Technologies: **{percent('with_technologies')}**",
        f"- Prizes: **{percent('with_prizes')}**",
        f"- Eligibility rules: **{percent('with_eligibility_rules')}**",
        "",
        "> Per-source event counts overlap and must not be summed.",
        "",
        "## Per-source event handoff",
        "",
        "| Source | Discovered tasks | Fetched tasks | References | Staged | Accepted | Rejected | Linked | Distinct hackathons |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        *[
            f"| {str(item['name']).replace('|', chr(92) + '|').replace(chr(10), ' ')} | {item['discovered_urls']} | {item['fetched_discovered_urls']} | {item['funnel']['active_references']} | {item['funnel']['staged_references']} | {item['funnel']['accepted_references']} | {item['funnel']['rejected_references']} | {item['funnel']['linked_searchable_references']} | {item['funnel']['unique_searchable_hackathons']} |"
            for item in snapshot["sources"]
        ],
        "",
        "| Source | Permission | Active | Recent success | Event references | Canonicals attributed | New refs (rolling) | New attributed canonicals (rolling) | URLs ready | URLs exhausted |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in snapshot["sources"]:
        assert isinstance(item, dict)
        name = str(item["name"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {name} | {item['permission_status'] or 'UNKNOWN'} | "
            f"{item['active']} | {item['recent_success']} | "
            f"{item['source_refs']} | {item['attributed_unique_active']} | "
            f"{item['new_source_refs_recent']} | {item['new_attributed_canonicals_recent']} | "
            f"{item['backlog_ready_now']} | {item['backlog_exhausted']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--recent-days", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.recent_days <= 90:
        parser.error("--recent-days must be between 1 and 90")
    from hackathon_api.db import get_engine
    from hackathon_api.repositories.coverage import CoverageRepository

    try:
        with get_engine().connect() as connection:
            report = CoverageRepository(connection).snapshot(recent_days=args.recent_days)
    except Exception as exc:
        print(
            f"Live catalog query failed ({type(exc).__name__}). No historical fallback.",
            file=sys.stderr,
        )
        return 2
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
