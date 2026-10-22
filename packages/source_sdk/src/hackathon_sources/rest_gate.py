from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .adapter import SourceAdapter
from .hashing import sha256_bytes
from .models import FetchResult, SourceEventRecord
from .persistent_runner import SourcePausedError
from .source_switch import ActivationCheck
from .url import canonicalize_url


@dataclass
class RestGateRunResult:
    source_key: str
    crawl_run_id: str
    fetched_pages: int = 0
    persisted_documents: int = 0
    persisted_event_refs: int = 0
    discovered_links: int = 0
    records: list[SourceEventRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    paused: bool = False

    @property
    def status(self) -> str:
        if self.paused:
            return "CANCELLED"
        if self.errors and not self.fetched_pages:
            return "FAILED"
        if self.errors:
            return "PARTIAL"
        return "SUCCEEDED"


class SupabaseGateClient:
    def __init__(self, base_url: str, anon_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {anon_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )

    async def __aenter__(self) -> SupabaseGateClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def insert(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(f"{self.base_url}/rest/v1/{table}", json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or not data:
            raise RuntimeError(f"unexpected Supabase response for {table}: {data!r}")
        return data[0]

    async def patch(self, table: str, row_id: str, payload: dict[str, Any]) -> None:
        response = await self.client.patch(
            f"{self.base_url}/rest/v1/{table}",
            params={"id": f"eq.{row_id}"},
            json=payload,
        )
        response.raise_for_status()


class RestrictedGateClient:
    """Temporary CI transport for the live gate.

    The runner still owns discovery, fetching, parsing, hashing, IDs and persistence payloads.
    This client only forwards insert/patch operations to a short-lived restricted write sink,
    so CI does not need a privileged database credential.
    """

    def __init__(self, write_url: str, gate_token: str) -> None:
        self.write_url = write_url
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "x-gate-token": gate_token,
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> RestrictedGateClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def _write(self, payload: dict[str, Any]) -> Any:
        response = await self.client.post(self.write_url, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError(f"restricted gate rejected write: {data!r}")
        return data.get("data")

    async def insert(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._write({"op": "insert", "table": table, "row": payload})
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
        return {}

    async def patch(self, table: str, row_id: str, payload: dict[str, Any]) -> None:
        await self._write({"op": "patch", "table": table, "id": row_id, "row": payload})


def _structured_payload(result: FetchResult) -> dict[str, Any] | list[Any] | None:
    content_type = (result.content_type or "").lower()
    if "json" not in content_type and not result.body.lstrip().startswith((b"{", b"[")):
        return None
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, (dict, list)) else None


async def run_source_rest_gate(
    adapter: SourceAdapter,
    fetcher: Any,
    sink: SupabaseGateClient,
    *,
    max_pages: int = 20,
    is_active: ActivationCheck | None = None,
) -> RestGateRunResult:
    # Never create a crawl run if the operator already paused the source.
    if is_active is not None and not is_active(adapter.definition):
        raise SourcePausedError(f"source {adapter.definition.key} is paused")
    run_id = str(uuid4())
    started_at = datetime.now(UTC)
    await sink.insert(
        "crawl_runs",
        {
            "id": run_id,
            "source_key": adapter.definition.key,
            "status": "RUNNING",
            "started_at": started_at.isoformat(),
            "stats": {},
        },
    )
    output = RestGateRunResult(source_key=adapter.definition.key, crawl_run_id=run_id)
    queue = [str(item.url) async for item in adapter.discover()]
    seen: set[str] = set()

    while queue and output.fetched_pages < max_pages:
        if is_active is not None and not is_active(adapter.definition):
            output.paused = True
            break
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        fetch_attempt_id = str(uuid4())
        attempt_started = datetime.now(UTC)
        monotonic_started = time.monotonic()
        try:
            fetched = await fetcher.fetch(adapter.definition, url)
            elapsed_ms = int((time.monotonic() - monotonic_started) * 1000)
            await sink.insert(
                "fetch_attempts",
                {
                    "id": fetch_attempt_id,
                    "crawl_run_id": run_id,
                    "source_key": adapter.definition.key,
                    "requested_url": url,
                    "final_url": fetched.final_url,
                    "status_code": fetched.status_code,
                    "content_type": fetched.content_type,
                    "fetched_at": fetched.fetched_at.isoformat(),
                    "elapsed_ms": elapsed_ms,
                    "error": None,
                    "headers": fetched.response_headers,
                },
            )
            if not 200 <= fetched.status_code < 400:
                output.errors.append(f"{url}: HTTP {fetched.status_code}")
                continue

            output.fetched_pages += 1
            raw_document_id = str(uuid4())
            canonical_url = canonicalize_url(fetched.final_url)
            await sink.insert(
                "raw_documents",
                {
                    "id": raw_document_id,
                    "crawl_run_id": run_id,
                    "fetch_attempt_id": fetch_attempt_id,
                    "source_key": adapter.definition.key,
                    "canonical_url": canonical_url,
                    "content_hash": sha256_bytes(fetched.body),
                    "content_type": fetched.content_type,
                    "http_status": fetched.status_code,
                    "fetched_at": fetched.fetched_at.isoformat(),
                    "structured_data": _structured_payload(fetched),
                    "body_base64": base64.b64encode(fetched.body).decode("ascii"),
                },
            )
            output.persisted_documents += 1

            records = await adapter.parse_events(fetched)
            output.records.extend(records)
            for record in records:
                await sink.insert(
                    "source_event_refs",
                    {
                        "id": str(uuid4()),
                        "crawl_run_id": run_id,
                        "raw_document_id": raw_document_id,
                        "source_key": adapter.definition.key,
                        "source_event_id": record.source_event_id,
                        "source_url": str(record.source_url),
                        "official_url": str(record.official_url) if record.official_url else None,
                        "name": record.name,
                        "payload": record.model_dump(mode="json"),
                    },
                )
                output.persisted_event_refs += 1

            discovered = await adapter.parse_listing(fetched)
            output.discovered_links += len(discovered)
            allowed_hosts = {
                adapter.definition.domain.lower(),
                *(host.lower() for host in adapter.definition.allowed_hosts),
            }
            for item in discovered:
                candidate = str(item.url)
                host = (urlsplit(candidate).hostname or "").lower()
                if host in allowed_hosts and candidate not in seen and candidate not in queue:
                    queue.append(candidate)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - monotonic_started) * 1000)
            try:
                await sink.insert(
                    "fetch_attempts",
                    {
                        "id": fetch_attempt_id,
                        "crawl_run_id": run_id,
                        "source_key": adapter.definition.key,
                        "requested_url": url,
                        "final_url": None,
                        "status_code": None,
                        "content_type": None,
                        "fetched_at": attempt_started.isoformat(),
                        "elapsed_ms": elapsed_ms,
                        "error": f"{type(exc).__name__}: {exc}"[:2000],
                        "headers": {},
                    },
                )
            except Exception:
                pass
            output.errors.append(f"{url}: {type(exc).__name__}: {exc}")

    await sink.patch(
        "crawl_runs",
        run_id,
        {
            "status": output.status,
            "finished_at": datetime.now(UTC).isoformat(),
            "stats": {
                "fetched_count": output.fetched_pages,
                "parsed_count": len(output.records),
                "failed_count": len(output.errors),
                "persisted_documents": output.persisted_documents,
                "persisted_event_refs": output.persisted_event_refs,
                "discovered_count": output.discovered_links,
            },
        },
    )
    return output
