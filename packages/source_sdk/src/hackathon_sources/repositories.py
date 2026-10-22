from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, text

from .hashing import sha256_bytes
from .models import FetchResult
from .url import canonicalize_url


@dataclass(frozen=True)
class FrontierItem:
    id: int
    canonical_url: str
    source_id: str | None


class CrawlRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def start_run(self, source_id: str) -> str:
        row = self.connection.execute(
            text("INSERT INTO crawl_runs(source_id) VALUES (:source_id) RETURNING id::text"),
            {"source_id": source_id},
        ).scalar_one()
        return str(row)

    def finish_run(self, run_id: str, *, status: str, stats: dict[str, Any]) -> None:
        self.connection.execute(
            text(
                """
                UPDATE crawl_runs
                   SET status = CAST(:status AS crawl_run_status),
                       finished_at = now(),
                       discovered_count = :discovered_count,
                       fetched_count = :fetched_count,
                       unchanged_count = :unchanged_count,
                       changed_count = :changed_count,
                       parsed_count = :parsed_count,
                       failed_count = :failed_count,
                       valid_event_count = :valid_event_count,
                       unique_event_count = :unique_event_count,
                       stats = CAST(:stats AS jsonb)
                 WHERE id = CAST(:run_id AS uuid)
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "discovered_count": int(stats.get("discovered_count", 0)),
                "fetched_count": int(stats.get("fetched_count", 0)),
                "unchanged_count": int(stats.get("unchanged_count", 0)),
                "changed_count": int(stats.get("changed_count", 0)),
                "parsed_count": int(stats.get("parsed_count", 0)),
                "failed_count": int(stats.get("failed_count", 0)),
                "valid_event_count": int(stats.get("valid_event_count", 0)),
                "unique_event_count": int(stats.get("unique_event_count", 0)),
                "stats": json.dumps(stats),
            },
        )


class FrontierRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def enqueue(
        self,
        *,
        url: str,
        source_id: str | None,
        discovered_by_source_id: str | None = None,
        discovery_query: str | None = None,
        depth: int = 0,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        canonical_url = canonicalize_url(url)
        from urllib.parse import urlsplit

        domain = urlsplit(canonical_url).hostname or ""
        row = self.connection.execute(
            text(
                """
                INSERT INTO url_frontier(
                    source_id, discovered_by_source_id, discovery_query, url,
                    canonical_url, domain, depth, priority, metadata
                ) VALUES (
                    CAST(:source_id AS uuid), CAST(:discovered_by_source_id AS uuid), :discovery_query,
                    :url, :canonical_url, :domain, :depth, :priority, CAST(:metadata AS jsonb)
                )
                ON CONFLICT(canonical_url) DO UPDATE SET
                    priority = GREATEST(url_frontier.priority, EXCLUDED.priority),
                    metadata = url_frontier.metadata || EXCLUDED.metadata
                RETURNING id
                """
            ),
            {
                "source_id": source_id,
                "discovered_by_source_id": discovered_by_source_id,
                "discovery_query": discovery_query,
                "url": url,
                "canonical_url": canonical_url,
                "domain": domain,
                "depth": depth,
                "priority": priority,
                "metadata": json.dumps(metadata or {}),
            },
        ).scalar_one()
        return int(row)

    def claim_next(self) -> FrontierItem | None:
        row = self.connection.execute(
            text(
                """
                WITH candidate AS (
                    SELECT id
                      FROM url_frontier
                     WHERE state IN ('DISCOVERED','QUEUED','FAILED')
                       AND (next_fetch_at IS NULL OR next_fetch_at <= now())
                     ORDER BY priority DESC, discovered_at ASC
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                )
                UPDATE url_frontier f
                   SET state = 'FETCHING', last_fetch_at = now()
                  FROM candidate
                 WHERE f.id = candidate.id
                RETURNING f.id, f.canonical_url, f.source_id::text
                """
            )
        ).mappings().first()
        if row is None:
            return None
        return FrontierItem(id=int(row["id"]), canonical_url=str(row["canonical_url"]), source_id=row["source_id"])

    def mark_result(self, frontier_id: int, *, state: str, terminal_reason: str | None = None) -> None:
        self.connection.execute(
            text(
                """
                UPDATE url_frontier
                   SET state = CAST(:state AS frontier_state),
                       terminal_reason = :terminal_reason,
                       retry_count = CASE WHEN :state = 'FAILED' THEN retry_count + 1 ELSE retry_count END
                 WHERE id = :frontier_id
                """
            ),
            {"frontier_id": frontier_id, "state": state, "terminal_reason": terminal_reason},
        )


class RawDocumentRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def has_content(self, canonical_url: str, content_hash: str) -> bool:
        return bool(
            self.connection.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM raw_documents WHERE canonical_url=:url AND content_hash=:hash)"
                ),
                {"url": canonical_url, "hash": content_hash},
            ).scalar_one()
        )

    def record_fetch_attempt(
        self,
        *,
        frontier_id: int | None,
        crawl_run_id: str | None,
        source_id: str | None,
        result: FetchResult,
        status: str,
        elapsed_ms: int | None = None,
    ) -> int:
        row = self.connection.execute(
            text(
                """
                INSERT INTO fetch_attempts(
                    frontier_id, crawl_run_id, source_id, requested_url, final_url,
                    status, http_status, content_type, etag, last_modified,
                    started_at, finished_at, elapsed_ms, response_headers
                ) VALUES (
                    :frontier_id, CAST(:crawl_run_id AS uuid), CAST(:source_id AS uuid),
                    :requested_url, :final_url, CAST(:status AS fetch_status), :http_status,
                    :content_type, :etag, :last_modified, :started_at, :finished_at,
                    :elapsed_ms, CAST(:response_headers AS jsonb)
                ) RETURNING id
                """
            ),
            {
                "frontier_id": frontier_id,
                "crawl_run_id": crawl_run_id,
                "source_id": source_id,
                "requested_url": result.requested_url,
                "final_url": result.final_url,
                "status": status,
                "http_status": result.status_code,
                "content_type": result.content_type,
                "etag": result.etag,
                "last_modified": result.last_modified,
                "started_at": result.fetched_at,
                "finished_at": datetime.now(UTC),
                "elapsed_ms": elapsed_ms,
                "response_headers": json.dumps(result.response_headers),
            },
        ).scalar_one()
        return int(row)

    def record_fetch_error(
        self,
        *,
        frontier_id: int | None,
        crawl_run_id: str | None,
        source_id: str | None,
        requested_url: str,
        started_at: datetime,
        error: Exception,
        elapsed_ms: int | None = None,
    ) -> int:
        row = self.connection.execute(
            text(
                """
                INSERT INTO fetch_attempts(
                    frontier_id, crawl_run_id, source_id, requested_url,
                    status, started_at, finished_at, elapsed_ms, error_category, error_message
                ) VALUES (
                    :frontier_id, CAST(:crawl_run_id AS uuid), CAST(:source_id AS uuid), :requested_url,
                    'FAILED', :started_at, now(), :elapsed_ms, :error_category, :error_message
                ) RETURNING id
                """
            ),
            {
                "frontier_id": frontier_id,
                "crawl_run_id": crawl_run_id,
                "source_id": source_id,
                "requested_url": requested_url,
                "started_at": started_at,
                "elapsed_ms": elapsed_ms,
                "error_category": type(error).__name__,
                "error_message": str(error)[:4000],
            },
        ).scalar_one()
        return int(row)

    def persist_document(
        self,
        *,
        source_id: str | None,
        fetch_attempt_id: int | None,
        result: FetchResult,
        object_storage_key: str | None,
        extracted_text: str | None = None,
        structured_data: dict[str, Any] | None = None,
    ) -> str:
        canonical_url = canonicalize_url(result.final_url)
        content_hash = sha256_bytes(result.body)
        row = self.connection.execute(
            text(
                """
                INSERT INTO raw_documents(
                    source_id, fetch_attempt_id, url, canonical_url, http_status,
                    content_type, content_hash, object_storage_key, extracted_text,
                    structured_data, fetched_at
                ) VALUES (
                    CAST(:source_id AS uuid), :fetch_attempt_id, :url, :canonical_url,
                    :http_status, :content_type, :content_hash, :object_storage_key,
                    :extracted_text, CAST(:structured_data AS jsonb), :fetched_at
                )
                ON CONFLICT(canonical_url, content_hash) DO UPDATE SET
                    fetch_attempt_id = COALESCE(EXCLUDED.fetch_attempt_id, raw_documents.fetch_attempt_id)
                RETURNING id::text
                """
            ),
            {
                "source_id": source_id,
                "fetch_attempt_id": fetch_attempt_id,
                "url": result.final_url,
                "canonical_url": canonical_url,
                "http_status": result.status_code,
                "content_type": result.content_type,
                "content_hash": content_hash,
                "object_storage_key": object_storage_key,
                "extracted_text": extracted_text,
                "structured_data": json.dumps(structured_data) if structured_data is not None else None,
                "fetched_at": result.fetched_at,
            },
        ).scalar_one()
        return str(row)


class SourcePayloadRepository:
    """Write one source-owned record atomically with its source_event_ref."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def stage(self, *, ref_id: str, source_id: str, raw_document_id: str | None,
              source_key: str, payload: dict[str, Any]) -> None:
        # Hash the event record, not the API page, so unrelated page changes do
        # not force expensive reprocessing of every event from that page.
        import hashlib
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        self.connection.execute(text("""
            INSERT INTO source_event_payloads (
                source_event_ref_id, source_id, source_key, raw_document_id, payload, content_hash
            ) VALUES (
                CAST(:ref_id AS uuid), CAST(:source_id AS uuid), :source_key,
                CAST(:raw_document_id AS uuid), CAST(:payload AS jsonb), :digest
            ) ON CONFLICT (source_event_ref_id) DO UPDATE SET
                raw_document_id=EXCLUDED.raw_document_id,
                payload=EXCLUDED.payload,
                content_hash=EXCLUDED.content_hash,
                observed_at=now(), last_error=NULL
            WHERE source_event_payloads.content_hash IS DISTINCT FROM EXCLUDED.content_hash
        """), {
            "ref_id": ref_id, "source_id": source_id, "source_key": source_key,
            "raw_document_id": raw_document_id, "payload": serialized, "digest": digest,
        })


class SourceEventRefRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def upsert(
        self,
        *,
        source_id: str,
        raw_document_id: str | None,
        source_event_id: str | None,
        source_url: str,
        official_url: str | None,
        edition_start: str | None = None,
    ) -> str:
        """Persist a stable source-level event reference without granting fetch permission.

        A single official URL can be reused by different editions. Distinguish
        records with source-assigned event IDs; when no ID exists, a dated edition
        gets a date discriminator. Keep `url` factual; only `normalized_url` is
        extended with internal disambiguators. This does not grant fetch rights.
        """
        from urllib.parse import quote, urlsplit, urlunsplit
        import hashlib
        import re

        factual_url = official_url or source_url
        normalized_url = canonicalize_url(factual_url)
        identity = None
        if source_event_id:
            # Bound untrusted external IDs while retaining stable identity.
            stable_id = source_event_id if len(source_event_id) <= 160 else hashlib.sha256(source_event_id.encode()).hexdigest()
            identity = ("__source_event_id", stable_id)
        elif isinstance(edition_start, str) and re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", edition_start):
            identity = ("__event_start", edition_start[:10])
        if identity:
            parts = urlsplit(normalized_url)
            suffix = f"{identity[0]}={quote(identity[1], safe='')}"
            query = f"{parts.query}&{suffix}" if parts.query else suffix
            normalized_url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))

        if source_event_id:
            existing = self.connection.execute(
                text(
                    """
                    SELECT id::text
                      FROM source_event_refs
                     WHERE source_id = CAST(:source_id AS uuid)
                       AND source_event_id = :source_event_id
                     ORDER BY last_seen_at DESC
                     LIMIT 1
                    """
                ),
                {"source_id": source_id, "source_event_id": source_event_id},
            ).scalar_one_or_none()
            if existing:
                self.connection.execute(
                    text(
                        """
                        UPDATE source_event_refs
                           SET raw_document_id = COALESCE(CAST(:raw_document_id AS uuid), raw_document_id),
                               url = :url, normalized_url = :normalized_url,
                               last_seen_at = now(), active = true
                         WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {
                        "id": existing,
                        "raw_document_id": raw_document_id,
                        "url": factual_url,
                        "normalized_url": normalized_url,
                    },
                )
                return str(existing)

        row = self.connection.execute(
            text(
                """
                INSERT INTO source_event_refs(
                    source_id, raw_document_id, source_event_id, url, normalized_url
                ) VALUES (
                    CAST(:source_id AS uuid), CAST(:raw_document_id AS uuid),
                    :source_event_id, :url, :normalized_url
                )
                ON CONFLICT(source_id, normalized_url) DO UPDATE SET
                    raw_document_id = COALESCE(EXCLUDED.raw_document_id, source_event_refs.raw_document_id),
                    source_event_id = COALESCE(EXCLUDED.source_event_id, source_event_refs.source_event_id),
                    url = EXCLUDED.url, last_seen_at = now(), active = true
                RETURNING id::text
                """
            ),
            {
                "source_id": source_id,
                "raw_document_id": raw_document_id,
                "source_event_id": source_event_id,
                "url": factual_url,
                "normalized_url": normalized_url,
            },
        ).scalar_one()
        return str(row)
