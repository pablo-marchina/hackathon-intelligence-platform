#!/usr/bin/env python3
"""Read-only true canonicalization outcomes from PostgreSQL (no mock fallback)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api/src'))
sys.path.insert(0, str(ROOT / 'packages/shared/src'))


def render_markdown(snapshot: dict) -> str:
    totals = snapshot['totals']
    lines = [
        '# Hackathon canonicalization yield — live PostgreSQL', '',
        f"As of **{snapshot['as_of']}** (last {snapshot['recent_window_days']} days)", '',
        '> Only results recorded after migration 0013 are counted. Created means new canonical',
        '> rows, **not** events announced recently. Sources are not automatically authorized.',
        '> Results are attributed to the original crawl ID only when the source provided it.', '',
        f"**{totals['created']} created**, **{totals['linked']} linked**, "
        f"**{totals['updated']} updated**, **{totals['rejected']} rejected** "
        f"({totals['unattributed']} lacking original crawl ID)", '',
        '| Source | Created | Linked | Updated | Rejected | Unattributed |',
        '| --- | ---: | ---: | ---: | ---: | ---: |',
    ]
    for source in snapshot['sources']:
        name = str(source['name']).replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {name} | {source['created']} | {source['linked']} | "
                     f"{source['updated']} | {source['rejected']} | {source['unattributed']} |")
    lines.extend(['', '## Processing by original crawl run (up to 100 groups)', '',
                  '| Crawl run | Source ID | Created | Linked | Updated | Rejected |',
                  '| --- | --- | ---: | ---: | ---: | ---: |'])
    for run in snapshot['runs']:
        lines.append(f"| {run['crawl_run_id']} | {run['source_id']} | "
                     f"{run['created']} | {run['linked']} | {run['updated']} | {run['rejected']} |")
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format', choices=('json', 'markdown'), default='markdown')
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.days <= 90:
        parser.error('--days must be in the range 1..90')
    from hackathon_api.db import get_engine
    from hackathon_api.repositories.canonical_yield import CanonicalYieldRepository
    try:
        with get_engine().connect() as conn:
            snapshot = CanonicalYieldRepository(conn).snapshot(days=args.days)
    except Exception as exc:
        print(f'Canonical yield report failed ({type(exc).__name__}); no synthetic fallback.', file=sys.stderr)
        return 2
    if args.format == 'json':
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(snapshot), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
