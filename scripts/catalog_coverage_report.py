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
sys.path.insert(0, str(ROOT / 'apps/api/src'))
sys.path.insert(0, str(ROOT / 'packages/shared/src'))


def render_markdown(snapshot: dict[str, object]) -> str:
    catalog = snapshot['catalog']
    sources = snapshot['source_counts']
    assert isinstance(catalog, dict) and isinstance(sources, dict)
    lines = [
        '# Live hackathon catalog — coverage snapshot', '',
        f"As of **{snapshot['as_of']}** (database query, not W2 historical baseline)", '',
        f"- Unique active hackathons: **{catalog['unique_active']}**",
        f"- Upcoming (by start date): **{catalog['upcoming_by_date']}**",
        f"- Ongoing (by start/end): **{catalog['ongoing_by_date']}**",
        f"- Confirmed registration windows: **{catalog['confirmed_registration_window']}**",
        f"- Registration deadline unknown: **{catalog['unknown_registration_close']}**",
        f"- Database sources: **{sources['registered_in_database']}**",
        f"- Active and approved: **{sources['approved_and_active']}**",
        f"- Successful within {snapshot['recent_window_days']} days: **{sources['recent_success']}**", '',
        '> Per-source event counts overlap and must not be summed.', '',
        '| Source | Permission | Active | Recent success | Event references | Canonicals attributed |',
        '| --- | --- | --- | --- | ---: | ---: |',
    ]
    for item in snapshot['sources']:
        assert isinstance(item, dict)
        name = str(item['name']).replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {name} | {item['permission_status'] or 'UNKNOWN'} | "
                     f"{item['active']} | {item['recent_success']} | "
                     f"{item['source_refs']} | {item['attributed_unique_active']} |")
    return '\n'.join(lines) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format', choices=['json', 'markdown'], default='markdown')
    parser.add_argument('--recent-days', type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.recent_days <= 90:
        parser.error('--recent-days must be between 1 and 90')
    from hackathon_api.db import get_engine
    from hackathon_api.repositories.coverage import CoverageRepository
    try:
        with get_engine().connect() as connection:
            report = CoverageRepository(connection).snapshot(recent_days=args.recent_days)
    except Exception as exc:
        print(f'Live catalog query failed ({type(exc).__name__}). No historical fallback.', file=sys.stderr)
        return 2
    if args.format == 'json':
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(report), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
