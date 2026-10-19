from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text

from .delivery import RetryPolicy
from .events import AlertCandidate


def saved_search_fingerprint(filters: dict[str, Any], schema_version: str) -> str:
    canonical = json.dumps(
        {"schema_version": schema_version, "filters": filters},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class EmailDispatchClaim:
    delivery_id: str
    token: str


class AlertRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create_event(self, candidate: AlertCandidate, *, alert_rule_id: str | None = None) -> str | None:
        row = self.connection.execute(
            text(
                """
                INSERT INTO alert_events(
                    user_id, alert_rule_id, hackathon_id, canonical_change_id,
                    alert_type, dedup_key, payload
                )
                VALUES (
                    CAST(:user_id AS uuid), CAST(:alert_rule_id AS uuid), CAST(:hackathon_id AS uuid),
                    :canonical_change_id, :alert_type, :dedup_key, CAST(:payload AS jsonb)
                )
                ON CONFLICT(user_id, dedup_key) DO NOTHING
                RETURNING id::text
                """
            ),
            {
                "user_id": candidate.user_id,
                "alert_rule_id": alert_rule_id,
                "hackathon_id": candidate.hackathon_id,
                "canonical_change_id": candidate.canonical_change_id,
                "alert_type": candidate.alert_type,
                "dedup_key": candidate.dedup_key,
                "payload": json.dumps(candidate.payload, default=str),
            },
        ).scalar_one_or_none()
        return str(row) if row is not None else None

    def save_search(
        self,
        *,
        user_id: str,
        name: str,
        filters: dict[str, Any],
        schema_version: str = "w4-filter-v1",
        original_query: str | None = None,
        notifications_enabled: bool = True,
    ) -> str:
        definition_hash = saved_search_fingerprint(filters, schema_version)
        row = self.connection.execute(
            text(
                """
                INSERT INTO saved_searches(
                    user_id, name, original_query, filter_schema_version, filters,
                    notifications_enabled, definition_hash
                )
                VALUES (
                    CAST(:user_id AS uuid), :name, :original_query, :schema_version,
                    CAST(:filters AS jsonb), :notifications_enabled, :definition_hash
                )
                RETURNING id::text
                """
            ),
            {
                "user_id": user_id,
                "name": name,
                "original_query": original_query,
                "schema_version": schema_version,
                "filters": json.dumps(filters, default=str),
                "notifications_enabled": notifications_enabled,
                "definition_hash": definition_hash,
            },
        ).scalar_one()
        return str(row)

    def authorized_email_recipient(
        self, *, user_id: str, alert_type: str, lock_user: bool = False,
    ) -> str | None:
        """Return the *current* address only after explicit, type-specific consent.

        A missing EMAIL preference is opt-out (unlike the legacy IN_APP default).
        The exact alert type takes precedence over a wildcard. During delivery,
        lock the users row *before* reading preferences in a separate statement:
        preference updates acquire FOR UPDATE on that same row. The second
        statement uses a fresh READ COMMITTED snapshot after any lock wait.
        Holding FOR SHARE through SMTP also freezes the recipient address.
        """
        user_query = """
            SELECT email::text FROM users
            WHERE id=CAST(:user_id AS uuid) AND deleted_at IS NULL
              AND NULLIF(email::text, '') IS NOT NULL
        """ + (" FOR SHARE" if lock_user else "")
        address = self.connection.execute(
            text(user_query), {"user_id": user_id},
        ).scalar_one_or_none()
        if not address:
            return None
        enabled = self.connection.execute(text("""
            SELECT enabled FROM alert_channel_preferences
            WHERE user_id=CAST(:user_id AS uuid) AND channel='EMAIL'
              AND alert_type IN (:alert_type, '*')
            ORDER BY CASE WHEN alert_type=:alert_type THEN 0 ELSE 1 END
            LIMIT 1
        """), {"user_id": user_id, "alert_type": alert_type}).scalar_one_or_none()
        return str(address) if enabled is True else None

    def channel_enabled(self, *, user_id: str, channel: str, alert_type: str) -> bool:
        if channel == "EMAIL":
            return self.authorized_email_recipient(
                user_id=user_id, alert_type=alert_type,
            ) is not None
        row = self.connection.execute(
            text(
                """
                SELECT enabled
                FROM alert_channel_preferences
                WHERE user_id=CAST(:user_id AS uuid)
                  AND channel=CAST(:channel AS alert_channel)
                  AND alert_type IN (:alert_type, '*')
                ORDER BY CASE WHEN alert_type=:alert_type THEN 0 ELSE 1 END
                LIMIT 1
                """
            ),
            {"user_id": user_id, "channel": channel, "alert_type": alert_type},
        ).scalar_one_or_none()
        return True if row is None else bool(row)

    def ensure_delivery(self, *, alert_event_id: str, channel: str, max_attempts: int = 4) -> str:
        row = self.connection.execute(
            text(
                """
                INSERT INTO alert_deliveries(alert_event_id, channel, max_attempts)
                VALUES (CAST(:event_id AS uuid), CAST(:channel AS alert_channel), :max_attempts)
                ON CONFLICT(alert_event_id, channel) DO UPDATE
                  SET max_attempts=GREATEST(alert_deliveries.max_attempts, EXCLUDED.max_attempts)
                RETURNING id::text
                """
            ),
            {"event_id": alert_event_id, "channel": channel, "max_attempts": max_attempts},
        ).scalar_one()
        return str(row)

    def claim_due_emails(self, *, limit: int, lease_seconds: int = 180) -> list[EmailDispatchClaim]:
        """Claim due emails atomically in PostgreSQL with SKIP LOCKED.

        Tokens fence delayed Celery messages after lease expiry/reclaim. A queue
        publication failure can release its own claim without touching a new owner.
        """
        if not 1 <= limit <= 100 or not 60 <= lease_seconds <= 900:
            raise ValueError("invalid alert dispatcher limits")
        rows = self.connection.execute(
            text("""
                WITH due AS (
                    SELECT id FROM alert_deliveries
                    WHERE channel='EMAIL' AND status='PENDING'
                      AND attempt_count < max_attempts
                      AND (next_attempt_at IS NULL OR next_attempt_at <= clock_timestamp())
                      AND (dispatch_expires_at IS NULL
                           OR dispatch_expires_at <= clock_timestamp())
                    ORDER BY next_attempt_at NULLS FIRST, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT :limit
                )
                UPDATE alert_deliveries AS d
                SET dispatch_token=gen_random_uuid(),
                    dispatch_expires_at=clock_timestamp() + make_interval(secs => :lease_seconds)
                FROM due WHERE d.id=due.id
                RETURNING d.id::text AS delivery_id, d.dispatch_token::text AS token
            """),
            {"limit": limit, "lease_seconds": lease_seconds},
        ).mappings().all()
        return [EmailDispatchClaim(delivery_id=r["delivery_id"], token=r["token"])
                for r in rows]

    def release_dispatch_claim(self, claim: EmailDispatchClaim) -> bool:
        """Release only our own claim; a stale publisher cannot release a new one."""
        return bool(self.connection.execute(
            text("""
                UPDATE alert_deliveries
                SET dispatch_token=NULL, dispatch_expires_at=NULL
                WHERE id=CAST(:id AS uuid) AND dispatch_token=CAST(:token AS uuid)
                  AND status='PENDING'
            """), {"id": claim.delivery_id, "token": claim.token},
        ).rowcount)

    def record_delivery_result(
        self,
        *,
        delivery_id: str,
        success: bool,
        provider_message_id: str | None = None,
        provider_response: dict[str, Any] | None = None,
        error_message: str | None = None,
        retry_policy: RetryPolicy = RetryPolicy(),
    ) -> str:
        attempt, max_attempts = self.connection.execute(
            text("SELECT attempt_count, max_attempts FROM alert_deliveries "
                 "WHERE id=CAST(:id AS uuid) FOR UPDATE"),
            {"id": delivery_id},
        ).one()
        next_count = int(attempt) + 1
        policy = RetryPolicy(
            max_attempts=min(retry_policy.max_attempts, int(max_attempts)),
            base_delay_seconds=retry_policy.base_delay_seconds,
            multiplier=retry_policy.multiplier,
        )
        delay = None if success else policy.delay_seconds(next_count)
        next_attempt = None if delay is None else datetime.now(UTC) + timedelta(seconds=delay)
        status = "SENT" if success else ("FAILED" if delay is None else "PENDING")
        self.connection.execute(
            text(
                """
                UPDATE alert_deliveries
                SET status=CAST(:status AS alert_delivery_status),
                    provider_message_id=:provider_message_id,
                    provider_response=CAST(:provider_response AS jsonb),
                    attempt_count=:attempt_count,
                    last_attempt_at=now(),
                    sent_at=CASE WHEN :success THEN now() ELSE sent_at END,
                    next_attempt_at=:next_attempt_at,
                    error_message=:error_message,
                    dispatch_token=NULL, dispatch_expires_at=NULL
                WHERE id=CAST(:id AS uuid)
                """
            ),
            {
                "id": delivery_id,
                "status": status,
                "provider_message_id": provider_message_id,
                "provider_response": json.dumps(provider_response or {}),
                "attempt_count": next_count,
                "success": success,
                "next_attempt_at": next_attempt,
                "error_message": error_message,
            },
        )
        return status
