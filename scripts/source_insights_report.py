#!/usr/bin/env python3
"""Read-only historical source diagnostics from a migrated PostgreSQL database.

Usage: python scripts/source_insights_report.py --recent-days 14 --format markdown
No crawling, permission changes or scheduler updates are performed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api/src'))
sys.path.insert(0, str(ROOT / 'packages/shared/src'))


def render_markdown(snapshot: dict) -> str:
    lines = [
        '# Hackathon source diagnostics — live database', '',
        f"As of: **{snapshot['as_of']}**, rolling window: **{snapshot['recent_window_days']} days**", '',
        '> Crawl run counts are historical, but grouped rejection reasons are a current-state snapshot.',
        '> New source references may overlap and do not equal unique newly announced hackathons.',
        '> Triage scores are advisory only. No new sources are enabled.', '',
        '| Source | Authorization | Runs (success/fail/partial) | New refs | Ready URLs | Current issues | Triage | Suggested action |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: | --- |',
    ]
    for item in snapshot['sources']:
        runs = item['runs_in_window']
        errors = sum(item['current_rejection_reasons'].values())
        score = item['triage_score'] if item['triage_score'] is not None else 'N/A'
        name = str(item['name']).replace('|', '\\|').replace('\n', ' ')
        action = str(item['suggested_action']).replace('|', '')
        lines.append(
            f"| {name} | {item['permission_status']} | "
            f"{runs['run_count']} ({runs['succeeded']}/{runs['failed']}/{runs['partial']}) | "
            f"{item['new_references_in_window']} | {item['ready_urls_now']} | "
            f"{errors} | {score} | {action} |"
        )
    lines += ['', '## Most recent crawl executions', '',
              '| UTC started | Source ID | Status | Fetched pages | Parsed records | Reported failures |',
              '| --- | --- | --- | ---: | ---: | ---: |']
    for run in snapshot['recent_runs']:
        lines.append(f"| {run['started_at']} | {run['source_id']} | {run['status']} | "
                     f"{run['fetched_count']} | {run['parsed_count']} | {run['failed_count']} |")
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format', choices=('json', 'markdown'), default='markdown')
    parser.add_argument('--recent-days', type=int, default=7)
    parser.add_argument('--run-limit', type=int, default=40)
    args = parser.parse_args()
    if not 1 <= args.recent_days <= 90:
        parser.error('--recent-days must be between 1 and 90')
    if not 1 <= args.run_limit <= 200:
        parser.error('--run-limit must be between 1 and 200')
    from hackathon_api.db import get_engine
    from hackathon_api.repositories.source_insights import SourceInsightsRepository
    try:
        with get_engine().connect() as connection:
            snapshot = SourceInsightsRepository(connection).snapshot(
                recent_days=args.recent_days, run_limit=args.run_limit,
            )
    except Exception as exc:
        print(f'Source diagnostics failed ({type(exc).__name__}); no test or historical fallback.', file=sys.stderr)
        return 2
    if args.format == 'json':
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(snapshot), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
