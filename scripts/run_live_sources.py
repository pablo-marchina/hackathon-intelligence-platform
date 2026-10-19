#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import create_engine

from hackathon_sources.factory import build_adapter, live_adapter_keys
from hackathon_sources.fetcher import FetchEngine
from hackathon_sources.persistent_runner import (
    SourceNotAuthorizedError,
    SourcePausedError,
    run_source_persisted,
)
from hackathon_sources.registry import load_source_definitions
from hackathon_sources.runner import run_source_once
from hackathon_sources.rest_gate import RestrictedGateClient, SupabaseGateClient, run_source_rest_gate
from hackathon_sources.source_switch import check_postgres_active


def _sqlalchemy_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run explicitly allowed live source adapters once")
    parser.add_argument("--source", action="append", dest="sources", help="source key; repeatable")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="fetch/parse only; by default DATABASE_URL enables crawl/raw/ref persistence",
    )
    parser.add_argument(
        "--prefer-rest",
        action="store_true",
        help="use REST persistence even if DATABASE_URL is set; PostgreSQL still checks pause",
    )
    parser.add_argument(
        "--allow-unmanaged-fetch",
        action="store_true",
        help="UNSAFE: explicitly permit diagnostic/REST collection without a PostgreSQL pause check",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    definitions = load_source_definitions(root / "db/seeds/sources_v0_1.json")
    selected = set(args.sources or live_adapter_keys())
    definitions = [definition for definition in definitions if definition.key in selected]

    database_url = os.getenv("DATABASE_URL")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")
    gate_write_url = os.getenv("GATE_WRITE_URL")
    gate_token = os.getenv("GATE_TOKEN")
    rest_configured = bool((supabase_url and supabase_key) or (gate_write_url and gate_token))
    if args.prefer_rest and args.no_persist:
        parser.error("--prefer-rest cannot be combined with --no-persist")
    if args.prefer_rest and not rest_configured:
        parser.error("--prefer-rest requires REST gateway credentials")
    sql_persist = bool(database_url) and not args.no_persist and not args.prefer_rest
    rest_persist = rest_configured and not args.no_persist and not sql_persist
    if not (sql_persist or rest_persist) and not args.no_persist:
        raise SystemExit(
            "DATABASE_URL, SUPABASE_URL+SUPABASE_ANON_KEY, or GATE_WRITE_URL+GATE_TOKEN is required for the live persistence gate "
            "(or pass --no-persist)"
        )

    # Diagnostics and REST gateways must also respect the pause flag. If they
    # cannot consult it, opt-in is required (never silently bypass an operator).
    unmanaged = not sql_persist
    if unmanaged and not database_url:
        if not args.allow_unmanaged_fetch:
            parser.error(
                "a DATABASE_URL source pause check is required for non-SQL fetch modes; "
                "use --allow-unmanaged-fetch only in an isolated diagnostic environment"
            )
        if os.getenv("APP_ENV", "development").lower() not in {"development", "test"}:
            parser.error("unmanaged source collection is forbidden in staging and production")
    engine = create_engine(_sqlalchemy_url(database_url), pool_pre_ping=True) if database_url else None
    activation_check = (
        (lambda source: check_postgres_active(engine, source))
        if engine is not None and unmanaged
        else None
    )
    exit_code = 0
    try:
        async with FetchEngine() as fetcher:
            if rest_persist:
                if gate_write_url and gate_token:
                    sink_context = RestrictedGateClient(gate_write_url, gate_token)
                    backend_name = "restricted_gate"
                else:
                    assert supabase_url and supabase_key
                    sink_context = SupabaseGateClient(supabase_url, supabase_key)
                    backend_name = "supabase_rest"
                async with sink_context as sink:
                    for definition in definitions:
                        adapter = build_adapter(definition)
                        try:
                            result = await run_source_rest_gate(
                                adapter, fetcher, sink, max_pages=args.max_pages,
                                is_active=activation_check,
                            )
                        except SourcePausedError:
                            print(json.dumps({"source": definition.key, "status": "SKIPPED_PAUSED"}))
                            continue
                        payload = {
                            "source": result.source_key,
                            "crawl_run_id": result.crawl_run_id,
                            "status": result.status,
                            "fetched_pages": result.fetched_pages,
                            "persisted_documents": result.persisted_documents,
                            "persisted_event_refs": result.persisted_event_refs,
                            "records": len(result.records),
                            "discovered_links": result.discovered_links,
                            "errors": result.errors,
                            "persistence_backend": backend_name,
                        }
                        if result.status == "FAILED":
                            exit_code = 1
                        print(json.dumps(payload, ensure_ascii=False))
            else:
                for definition in definitions:
                    adapter = build_adapter(definition)
                    if sql_persist:
                        assert engine is not None
                        with engine.connect() as connection:
                            try:
                                result = await run_source_persisted(
                                    adapter,
                                    fetcher,
                                    connection,
                                    max_pages=args.max_pages,
                                )
                            except SourcePausedError:
                                print(json.dumps({"source": definition.key, "status": "SKIPPED_PAUSED"}))
                                continue
                            except SourceNotAuthorizedError:
                                print(json.dumps({"source": definition.key, "status": "SKIPPED_POLICY"}))
                                continue
                        payload = {
                            "source": result.source_key,
                            "crawl_run_id": result.crawl_run_id,
                            "status": result.status,
                            "fetched_pages": result.fetched_pages,
                            "persisted_documents": result.persisted_documents,
                            "persisted_event_refs": result.persisted_event_refs,
                            "records": len(result.records),
                            "discovered_links": result.discovered_links,
                            "errors": result.errors,
                            "persistence_backend": "postgresql",
                        }
                        if result.status == "FAILED":
                            exit_code = 1
                    else:
                        try:
                            result = await run_source_once(
                                adapter, fetcher, max_pages=args.max_pages,
                                is_active=activation_check,
                            )
                        except SourcePausedError:
                            print(json.dumps({"source": definition.key, "status": "SKIPPED_PAUSED"}))
                            continue
                        payload = {
                            "source": result.source_key,
                            "status": result.status,
                            "fetched_pages": result.fetched_pages,
                            "records": len(result.records),
                            "discovered_links": result.discovered_links,
                            "errors": result.errors,
                        }
                        if result.status == "FAILED":
                            exit_code = 1
                    print(json.dumps(payload, ensure_ascii=False))
    finally:
        if engine is not None:
            engine.dispose()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
