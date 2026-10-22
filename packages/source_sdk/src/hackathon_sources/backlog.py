"""PostgreSQL-backed, source-scoped, token-fenced RSS/sitemap URL queue.

Approved seeds are re-polled only after their refresh window. Detail URLs
stay DONE until rediscovered and their refresh window has passed. Claim tokens
prevent stale workers from acknowledging another worker's item.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import Connection, text

from .url import canonicalize_url

CLAIM_SECONDS = 30 * 60
REVISIT_SECONDS = 24 * 60 * 60
SEED_REVISIT_SECONDS = 60 * 60  # do not re-fetch a successful seed every short worker run
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class BacklogClaim:
    id: int
    url: str
    token: str
    kind: str
    listing_offset: int = 0
    listing_hash: str | None = None


class SourceUrlBacklog:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def add(self, source_id: str, url: str, *, kind: str,
            priority: int = 0, metadata: dict | None = None) -> None:
        if kind not in {"SEED", "DISCOVERED"}:
            raise ValueError("unrecognized URL kind")
        canonical = canonicalize_url(url)
        parts = urlsplit(canonical)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
        ):
            raise ValueError("unsafe backlog URL")
        self.connection.execute(text("""
            INSERT INTO source_url_backlog (
                source_id, url, canonical_url, domain, kind, priority, metadata
            ) VALUES (
                CAST(:source_id AS uuid), :url, :canonical_url, :domain, :kind,
                :priority, CAST(:metadata AS jsonb)
            ) ON CONFLICT(source_id, canonical_url) DO UPDATE SET
                last_seen_at = clock_timestamp(),
                priority = GREATEST(source_url_backlog.priority, EXCLUDED.priority),
                metadata = source_url_backlog.metadata || EXCLUDED.metadata,
                kind = CASE WHEN source_url_backlog.kind = 'SEED' THEN 'SEED' ELSE EXCLUDED.kind END,
                state = CASE
                    WHEN source_url_backlog.state = 'CLAIMED' THEN 'CLAIMED'
                    WHEN source_url_backlog.state = 'DONE'
                         AND source_url_backlog.next_fetch_at <= clock_timestamp()
                    THEN 'PENDING'
                    ELSE source_url_backlog.state END,
                next_fetch_at = CASE
                    WHEN source_url_backlog.state = 'CLAIMED' THEN source_url_backlog.next_fetch_at
                    WHEN source_url_backlog.state = 'DONE'
                         AND source_url_backlog.next_fetch_at <= clock_timestamp()
                    THEN clock_timestamp()
                    ELSE source_url_backlog.next_fetch_at END,
                attempts = CASE
                    WHEN source_url_backlog.state = 'DONE'
                         AND source_url_backlog.next_fetch_at <= clock_timestamp() THEN 0
                    ELSE source_url_backlog.attempts END
        """), {"source_id": source_id, "url": url, "canonical_url": canonical,
               "domain": parts.hostname, "kind": kind, "priority": priority,
               "metadata": json.dumps(metadata or {})})

    def claim(self, source_id: str) -> BacklogClaim | None:
        # Retire expired claims that have exhausted their retry budget. Never
        # retain a CLAIMED row indefinitely after its worker has vanished.
        self.connection.execute(text("""
            UPDATE source_url_backlog
               SET state='EXHAUSTED', claim_token=NULL, claim_until=NULL,
                   last_error=COALESCE(last_error, 'lease expired after retry limit')
             WHERE source_id=CAST(:source_id AS uuid)
               AND state='CLAIMED' AND claim_until <= clock_timestamp()
               AND attempts >= :max_attempts
        """), {"source_id": source_id, "max_attempts": MAX_ATTEMPTS})
        token = str(uuid4())
        row = self.connection.execute(text("""
            WITH next_item AS (
                SELECT id FROM source_url_backlog
                 WHERE source_id = CAST(:source_id AS uuid)
                   AND (
                      (state IN ('PENDING', 'RETRY') AND next_fetch_at <= clock_timestamp())
                      OR (state = 'CLAIMED' AND claim_until <= clock_timestamp())
                   )
                   AND attempts < :max_attempts
                 ORDER BY CASE WHEN kind = 'DISCOVERED' THEN 0 ELSE 1 END,
                          priority DESC, first_seen_at, id
                 FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE source_url_backlog AS b
               SET state='CLAIMED', claim_token=CAST(:token AS uuid),
                   claim_until=clock_timestamp() + (:seconds * INTERVAL '1 second'),
                   attempts=b.attempts + 1
              FROM next_item
             WHERE b.id = next_item.id
            RETURNING b.id, b.url, b.kind, b.listing_offset, b.listing_hash
        """), {"source_id": source_id, "token": token,
               "seconds": CLAIM_SECONDS, "max_attempts": MAX_ATTEMPTS}).mappings().first()
        if row is None:
            return None
        return BacklogClaim(int(row["id"]), str(row["url"]), token, str(row["kind"]),
                            int(row["listing_offset"]), row["listing_hash"])

    def succeed(
        self, claim: BacklogClaim, *, next_offset: int = 0,
        listing_hash: str | None = None, has_more: bool = False
    ) -> bool:
        """Checkpoint XML discovery atomically with child URLs and page evidence."""
        if next_offset < 0 or (has_more and (
            next_offset == 0 or not listing_hash or
            (listing_hash == claim.listing_hash and next_offset <= claim.listing_offset)
        )):
            raise ValueError("invalid listing continuation")
        row = self.connection.execute(text("""
            UPDATE source_url_backlog SET
                state=CASE WHEN :has_more THEN 'PENDING' ELSE 'DONE' END,
                claim_token=NULL, claim_until=NULL,
                last_finished_at=clock_timestamp(), last_error=NULL, attempts=0,
                listing_offset=CASE WHEN :has_more THEN :offset ELSE 0 END,
                listing_hash=CASE WHEN :has_more THEN :digest ELSE NULL END,
                next_fetch_at=clock_timestamp() + (
                    CASE WHEN :has_more THEN 0 ELSE :revisit END * INTERVAL '1 second')
            WHERE id=:id AND claim_token=CAST(:token AS uuid)
              AND state='CLAIMED' AND claim_until > clock_timestamp()
            RETURNING id
        """), {"id": claim.id, "token": claim.token, "revisit": SEED_REVISIT_SECONDS if claim.kind == "SEED" else REVISIT_SECONDS,
               "offset": next_offset, "digest": listing_hash, "has_more": has_more}).scalar_one_or_none()
        return row is not None

    def fail(self, claim: BacklogClaim, *, reason: str, immediate: bool = False) -> bool:
        """A paused task becomes pending; expired/stolen claims cannot mutate the row."""
        row = self.connection.execute(text("""
            UPDATE source_url_backlog SET
                state=CASE WHEN :immediate THEN 'PENDING'
                           WHEN attempts >= :max_attempts THEN 'EXHAUSTED'
                           ELSE 'RETRY' END,
                claim_token=NULL, claim_until=NULL,
                last_error=:reason,
                next_fetch_at=clock_timestamp() + (
                    CASE WHEN :immediate THEN 0
                         ELSE LEAST(3600, 60 * POWER(2, LEAST(attempts - 1, 6))) END
                    )::double precision * INTERVAL '1 second'
            WHERE id=:id AND claim_token=CAST(:token AS uuid)
              AND state='CLAIMED' AND claim_until > clock_timestamp()
            RETURNING id
        """), {"id": claim.id, "token": claim.token, "reason": reason[:500],
               "immediate": immediate, "max_attempts": MAX_ATTEMPTS}).scalar_one_or_none()
        return row is not None

    def stats(self, source_id: str) -> dict[str, int]:
        rows = self.connection.execute(text("""
            SELECT state, count(*) AS n FROM source_url_backlog
            WHERE source_id=CAST(:source_id AS uuid) GROUP BY state
        """), {"source_id": source_id}).mappings()
        return {str(row["state"]): int(row["n"]) for row in rows}
