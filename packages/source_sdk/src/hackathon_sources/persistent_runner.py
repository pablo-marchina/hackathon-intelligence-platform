from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlsplit

from sqlalchemy import Connection

from .adapter import SourceAdapter
from .models import FetchResult, SourceEventRecord
from .repositories import (
    CrawlRepository,
    FrontierRepository,
    RawDocumentRepository,
    SourceEventRefRepository,
    SourcePayloadRepository,
)
from .registry import SourceRegistryRepository


class SourcePausedError(RuntimeError):
    """An administrator disabled the source; do not open a network connection."""


class SourceNotAuthorizedError(RuntimeError):
    """Operator permission/ingestion policy does not authorize fetching."""


@dataclass
class PersistentSourceRunResult:
    source_key: str
    crawl_run_id: str
    fetched_pages: int = 0
    unchanged_pages: int = 0
    changed_pages: int = 0
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

    def stats(self) -> dict[str, Any]:
        unique_refs = {
            (record.source_event_id, str(record.official_url or record.source_url))
            for record in self.records
        }
        return {
            "discovered_count": self.discovered_links,
            "fetched_count": self.fetched_pages,
            "unchanged_count": self.unchanged_pages,
            "changed_count": self.changed_pages,
            "parsed_count": len(self.records),
            "failed_count": len(self.errors),
            "valid_event_count": len(self.records),
            "unique_event_count": len(unique_refs),
            "persisted_documents": self.persisted_documents,
            "persisted_event_refs": self.persisted_event_refs,
        }


def _structured_payload(result: FetchResult) -> dict[str, Any] | list[Any] | None:
    content_type = (result.content_type or "").lower()
    if "json" not in content_type and not result.body.lstrip().startswith((b"{", b"[")):
        return None
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, (dict, list)) else None


async def run_source_persisted(
    adapter: SourceAdapter,
    fetcher: Any,
    connection: Connection,
    *,
    max_pages: int = 20,
    lease_token: str | None = None,
    should_continue: Callable[[], bool] | None = None,
) -> PersistentSourceRunResult:
    """Run one authorized source and persist acquisition evidence transactionally by page.

    Network calls never grant policy to external event URLs: only source-owned pagination hosts are
    followed here. Raw API evidence, fetch attempts, crawl-run stats and source event references are
    persisted before the run is considered successful.
    """

    source_registry = SourceRegistryRepository(connection)
    source_registry.upsert(adapter.definition)
    connection.commit()
    source_id = source_registry.get_source_id(adapter.definition)
    if should_continue is not None and not should_continue():
        raise SourcePausedError("scheduled source run lost its lease")
    if not source_registry.is_active(source_id):
        raise SourcePausedError(f"source {adapter.definition.key} is paused")
    if not source_registry.is_fetch_authorized(source_id):
        raise SourceNotAuthorizedError(f"source {adapter.definition.key} is not authorized for fetch")
    if lease_token is not None and not source_registry.has_current_lease(source_id, lease_token):
        raise SourcePausedError("scheduled crawl lease no longer owned by this worker")

    crawl_repo = CrawlRepository(connection)
    frontier_repo = FrontierRepository(connection)
    raw_repo = RawDocumentRepository(connection)
    ref_repo = SourceEventRefRepository(connection)

    crawl_run_id = crawl_repo.start_run(source_id)
    connection.commit()
    output = PersistentSourceRunResult(source_key=adapter.definition.key, crawl_run_id=crawl_run_id)

    try:
        queue = [str(item.url) async for item in adapter.discover()]
    except Exception as exc:
        # Discovery errors must not strand a RUNNING crawl forever. The caller
        # still gets a persisted failure for monitoring and backoff.
        output.errors.append(f"discovery: {type(exc).__name__}: {exc}")
        crawl_repo.finish_run(crawl_run_id, status=output.status, stats=output.stats())
        connection.commit()
        return output
    seen: set[str] = set()

    while queue and output.fetched_pages < max_pages:
        if should_continue is not None and not should_continue():
            output.paused = True
            break
        if not source_registry.is_active(source_id):
            output.paused = True
            break
        if not source_registry.is_fetch_authorized(source_id):
            output.paused = True
            break
        if lease_token is not None and not source_registry.has_current_lease(source_id, lease_token):
            output.paused = True
            break
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)

        frontier_id = frontier_repo.enqueue(
            url=url,
            source_id=source_id,
            discovered_by_source_id=source_id,
            depth=0,
            priority=adapter.definition.priority,
            metadata={"source_key": adapter.definition.key, "discovery_method": "documented_api"},
        )
        connection.commit()

        started_at = datetime.now(UTC)
        started_monotonic = time.monotonic()
        try:
            fetched = await fetcher.fetch(adapter.definition, url)
            # A heartbeat or operator pause may have arrived DURING the HTTP request.
            # Do not persist content/refs fetched by a stale or unauthorized worker.
            def permitted_after_network() -> bool:
                return (
                    (should_continue is None or should_continue())
                    and source_registry.is_active(source_id)
                    and source_registry.is_fetch_authorized(source_id)
                    and (
                        lease_token is None
                        or source_registry.has_current_lease(source_id, lease_token)
                    )
                )

            if not permitted_after_network():
                output.paused = True
                frontier_repo.mark_result(
                    frontier_id, state="FAILED", terminal_reason="source paused or lease lost",
                )
                connection.commit()
                break
            elapsed_ms = int((time.monotonic() - started_monotonic) * 1000)
            fetch_status = "FETCHED" if 200 <= fetched.status_code < 400 else "FAILED"
            fetch_attempt_id = raw_repo.record_fetch_attempt(
                frontier_id=frontier_id,
                crawl_run_id=crawl_run_id,
                source_id=source_id,
                result=fetched,
                status=fetch_status,
                elapsed_ms=elapsed_ms,
            )

            if fetch_status == "FAILED":
                # Even status metadata must not be committed by a revoked owner.
                if not permitted_after_network() or not source_registry.authorized_for_commit(
                    source_id, lease_token=lease_token,
                ):
                    connection.rollback()
                    output.paused = True
                    frontier_repo.mark_result(
                        frontier_id, state="FAILED", terminal_reason="source paused or lease lost",
                    )
                    connection.commit()
                    break
                frontier_repo.mark_result(
                    frontier_id,
                    state="FAILED",
                    terminal_reason=f"HTTP {fetched.status_code}",
                )
                output.errors.append(f"{url}: HTTP {fetched.status_code}")
                connection.commit()
                continue

            from .hashing import sha256_bytes
            from .url import canonicalize_url

            canonical_url = canonicalize_url(fetched.final_url)
            content_hash = sha256_bytes(fetched.body)
            unchanged = raw_repo.has_content(canonical_url, content_hash)
            raw_document_id = raw_repo.persist_document(
                source_id=source_id,
                fetch_attempt_id=fetch_attempt_id,
                result=fetched,
                object_storage_key=None,
                structured_data=_structured_payload(fetched),
            )
            records = await adapter.parse_events(fetched)
            discovered = await adapter.parse_listing(fetched)
            # Parsing can yield to another coroutine. Recheck the lease and
            # policy BEFORE committing document/ref writes from this page.
            if not permitted_after_network() or not source_registry.authorized_for_commit(
                source_id, lease_token=lease_token,
            ):
                connection.rollback()
                output.paused = True
                frontier_repo.mark_result(
                    frontier_id, state="FAILED", terminal_reason="source paused or lease lost",
                )
                connection.commit()
                break

            # Keep the source/lease row locks until the page transaction commits.
            # No await occurs between this fence and the commit below.
            output.fetched_pages += 1
            output.persisted_documents += 1
            if unchanged:
                output.unchanged_pages += 1
            else:
                output.changed_pages += 1
            output.records.extend(records)
            payload_repo = SourcePayloadRepository(connection)
            for record in records:
                if record.source_key != adapter.definition.key:
                    raise ValueError("parser returned an event belonging to a different source")
                ref_id = ref_repo.upsert(
                    source_id=source_id,
                    raw_document_id=raw_document_id,
                    source_event_id=record.source_event_id,
                    source_url=str(record.source_url),
                    official_url=str(record.official_url) if record.official_url else None,
                    edition_start=record.starts_at_raw,
                )
                payload_repo.stage(
                    ref_id=ref_id, source_id=source_id, raw_document_id=raw_document_id,
                    source_key=record.source_key, payload=record.model_dump(mode="json"),
                )
                output.persisted_event_refs += 1

            output.discovered_links += len(discovered)
            for item in discovered:
                candidate = str(item.url)
                host = (urlsplit(candidate).hostname or "").lower()
                allowed_hosts = {
                    adapter.definition.domain.lower(),
                    *(host.lower() for host in adapter.definition.allowed_hosts),
                }
                if host in allowed_hosts and candidate not in seen and candidate not in queue:
                    queue.append(candidate)

            frontier_repo.mark_result(frontier_id, state="FETCHED")
            connection.commit()
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started_monotonic) * 1000)
            raw_repo.record_fetch_error(
                frontier_id=frontier_id,
                crawl_run_id=crawl_run_id,
                source_id=source_id,
                requested_url=url,
                started_at=started_at,
                error=exc,
                elapsed_ms=elapsed_ms,
            )
            frontier_repo.mark_result(frontier_id, state="FAILED", terminal_reason=str(exc)[:1000])
            output.errors.append(f"{url}: {type(exc).__name__}: {exc}")
            connection.commit()

    crawl_repo.finish_run(crawl_run_id, status=output.status, stats=output.stats())
    connection.commit()
    return output
