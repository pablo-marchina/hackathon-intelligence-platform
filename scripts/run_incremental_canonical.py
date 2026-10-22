#!/usr/bin/env python3
"""Explicit, off-by-default incremental normalizer (no crawling)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine

from scripts._local_packages import add_repo_packages

add_repo_packages(Path(__file__).resolve().parents[1])
from hackathon_intelligence.incremental import CanonicalProcessor  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Canonicalize already-collected, authorized source-event records"
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true", help="Required to write to the catalog")
    args = parser.parse_args()
    if not 1 <= args.limit <= 10_000:
        parser.error("--limit must be between 1 and 10000")
    if not args.apply:
        print(json.dumps({"status": "DISABLED", "reason": "pass --apply explicitly"}))
        return 0
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    summary: dict[str, int] = {}
    try:
        for _ in range(args.limit):
            with engine.begin() as connection:
                status = CanonicalProcessor(connection).process_one()
            if status is None or status == "SKIPPED_UNAUTHORIZED":
                if status:
                    summary[status] = summary.get(status, 0) + 1
                break
            summary[status] = summary.get(status, 0) + 1
        print(json.dumps(summary, sort_keys=True))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
