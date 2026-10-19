"""Vercel-style authenticated, bounded diagnostics for live adapters.

The endpoint must remain disabled when a shared administrative source switch
cannot be verified (except an explicit dev/test-only diagnostic override).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
for path in [ROOT / "packages/source_sdk/src", ROOT / "packages/shared/src"]:
    sys.path.insert(0, str(path))

from hackathon_sources.factory import build_adapter, live_adapter_keys
from hackathon_sources.fetcher import FetchEngine
from hackathon_sources.persistent_runner import SourcePausedError
from hackathon_sources.registry import load_source_definitions
from hackathon_sources.runner import run_source_once
from hackathon_sources.source_switch import check_postgres_active


async def _run(selected: set[str], max_pages: int) -> list[dict[str, object]]:
    definitions = load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    definitions = [definition for definition in definitions if definition.key in selected]
    outputs: list[dict[str, object]] = []
    database_url = os.getenv("DATABASE_URL")
    engine = (
        create_engine(
            database_url.replace("postgresql://", "postgresql+psycopg://", 1),
            pool_pre_ping=True,
        )
        if database_url
        else None
    )
    if engine is None and (
        os.getenv("LIVE_SOURCES_ALLOW_UNMANAGED_FETCH") != "1"
        or os.getenv("APP_ENV", "development").lower() not in {"development", "test"}
    ):
        raise PermissionError("source switch unavailable; diagnostic fetch disabled")
    try:
        async with FetchEngine() as fetcher:
            for definition in definitions:
                try:
                    result = await run_source_once(
                        build_adapter(definition),
                        fetcher,
                        max_pages=max_pages,
                        is_active=(
                            (lambda source: check_postgres_active(engine, source))
                            if engine is not None
                            else None
                        ),
                    )
                except SourcePausedError:
                    outputs.append({"source": definition.key, "status": "SKIPPED_PAUSED"})
                    continue
                outputs.append(
                    {
                        "source": result.source_key,
                        "status": result.status,
                        "fetched_pages": result.fetched_pages,
                        "records": len(result.records),
                        "discovered_links": result.discovered_links,
                        "errors": result.errors,
                        "sample": [
                            {
                                "source_event_id": record.source_event_id,
                                "name": record.name,
                                "source_url": str(record.source_url),
                                "official_url": (
                                    str(record.official_url) if record.official_url else None
                                ),
                                "starts_at_raw": record.starts_at_raw,
                                "ends_at_raw": record.ends_at_raw,
                                "format_raw": record.format_raw,
                                "city": record.city,
                                "state_region": record.state_region,
                                "country": record.country,
                                "country_code": record.country_code,
                            }
                            for record in result.records[:5]
                        ],
                    }
                )
    finally:
        if engine is not None:
            engine.dispose()
    return outputs


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # This diagnostic endpoint performs real external IO and must not be public.
        # Keep the token exclusively in deployment secrets, never query parameters.
        expected = os.getenv("LIVE_SOURCES_GATE_TOKEN", "")
        provided = self.headers.get("x-gate-token", "")
        if not expected:
            self._json(503, {"error": "diagnostic gate disabled"})
            return
        if not provided or not hmac.compare_digest(provided, expected):
            self._json(401, {"error": "unauthorized"})
            return

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        requested = set(query.get("source", []))
        selected = (requested or set(live_adapter_keys())) & set(live_adapter_keys())
        try:
            max_pages = max(1, min(int(query.get("max_pages", ["2"])[0]), 5))
        except ValueError:
            max_pages = 2

        if not selected:
            self._json(400, {"error": "no valid source selected", "allowed": sorted(live_adapter_keys())})
            return
        try:
            result = asyncio.run(_run(selected, max_pages))
            self._json(200, {"ok": True, "results": result})
        except PermissionError:
            self._json(503, {"ok": False, "error": "source switch unavailable"})
        except Exception:
            self._json(500, {"ok": False, "error": "diagnostic fetch unavailable"})

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
