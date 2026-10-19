#!/usr/bin/env python3
"""W6 source-maintenance entry point (no network unless --dispatch is supplied)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine

from hackathon_operations.dispatcher import run_scheduler_cycle


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate source health and due recrawl scheduling")
    parser.add_argument("--dispatch", action="store_true", help="explicitly enable network collection")
    parser.add_argument("--limit", type=int, default=3, help="max sources claimed in this cycle (1-20)")
    parser.add_argument("--max-pages", type=int, default=5, help="max pages per source (1-20)")
    args = parser.parse_args()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        parser.error("DATABASE_URL is required; cannot schedule without central source state")
    if db_url.startswith("postgresql://"):
        db_url = "postgresql+psycopg://" + db_url.removeprefix("postgresql://")
    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        report = asyncio.run(run_scheduler_cycle(
            engine, root=Path(__file__).resolve().parents[2],
            dispatch=args.dispatch, limit=args.limit, max_pages=args.max_pages,
        ))
        print(json.dumps(asdict(report)))
        if report.failed:
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
