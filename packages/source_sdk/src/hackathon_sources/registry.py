from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Connection, text

from .models import SourceDefinition


class SourceRegistryRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def get_source_id(self, source: SourceDefinition) -> str:
        row = self.connection.execute(
            text(
                """
                SELECT id::text
                  FROM sources
                 WHERE domain = :domain AND source_family = :source_family
                 LIMIT 1
                """
            ),
            {"domain": source.domain, "source_family": source.source_family},
        ).scalar_one_or_none()
        if row is None:
            raise LookupError(f"source {source.key!r} is not registered")
        return str(row)

    def is_active(self, source_id: str) -> bool:
        """Read the operator-controlled switch from PostgreSQL, never from adapter defaults."""
        active = self.connection.execute(
            text("SELECT active FROM sources WHERE id = CAST(:id AS uuid)"),
            {"id": source_id},
        ).scalar_one_or_none()
        # Deleted or unknown sources must fail closed.
        return active is True

    def is_fetch_authorized(self, source_id: str) -> bool:
        """Check DB-owned permissions separately from the active/pause switch."""
        allowed = self.connection.execute(
            text("""
                SELECT active AND discovery_enabled AND detail_extraction_enabled
                       AND permission_status = 'ALLOWED'
                       AND ingestion_mode NOT IN ('BLOCKED', 'DISCOVERY_ONLY', 'MANUAL')
                  FROM sources WHERE id = CAST(:id AS uuid)
            """),
            {"id": source_id},
        ).scalar_one_or_none()
        return allowed is True

    def has_current_lease(self, source_id: str, lease_token: str) -> bool:
        current = self.connection.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1 FROM source_operational_state
                     WHERE source_id=CAST(:source_id AS uuid)
                       AND lease_token=CAST(:token AS uuid) AND lease_until > clock_timestamp()
                )
            """),
            {"source_id": source_id, "token": lease_token},
        ).scalar_one()
        return current is True

    def authorized_for_commit(self, source_id: str, *, lease_token: str | None = None) -> bool:
        """Fence a page commit against an operator pause and lease reassignment.

        Call in the SAME transaction immediately before writing final event refs.
        FOR SHARE conflicts with the admin's FOR UPDATE and with a competing
        lease UPDATE, so permission changes cannot slip between check and commit.
        Do not acquire these locks during network requests or async parsing.
        """
        permitted = self.connection.execute(
            text("""
                SELECT active AND discovery_enabled AND detail_extraction_enabled
                       AND permission_status='ALLOWED'
                       AND ingestion_mode NOT IN ('BLOCKED','DISCOVERY_ONLY','MANUAL')
                  FROM sources WHERE id=CAST(:id AS uuid) FOR SHARE
            """),
            {"id": source_id},
        ).scalar_one_or_none()
        if permitted is not True:
            return False
        if lease_token is None:
            return True
        # Use wall clock, not now(): a transaction can have begun long before
        # parsing completed. A PostgreSQL transaction's now() is fixed at BEGIN.
        still_owned = self.connection.execute(
            text("""
                SELECT lease_token=CAST(:token AS uuid)
                       AND lease_until > clock_timestamp()
                  FROM source_operational_state
                 WHERE source_id=CAST(:id AS uuid) FOR SHARE
            """),
            {"id": source_id, "token": lease_token},
        ).scalar_one_or_none()
        return still_owned is True

    def upsert(self, source: SourceDefinition) -> None:
        self.connection.execute(
            text(
                """
                INSERT INTO sources (
                    name, domain, source_family, source_type, ingestion_mode,
                    discovery_enabled, detail_extraction_enabled, permission_status,
                    priority, trust_score, refresh_interval_seconds,
                    rate_limit_per_minute, concurrency_limit, notes, config
                ) VALUES (
                    :name, :domain, :source_family, :source_type, CAST(:ingestion_mode AS source_ingestion_mode),
                    :discovery_enabled, :detail_extraction_enabled, :permission_status,
                    :priority, :trust_score, :refresh_interval_seconds,
                    :rate_limit_per_minute, :concurrency_limit, :notes, CAST(:config AS jsonb)
                )
                ON CONFLICT (domain, source_family) DO UPDATE SET
                    name = EXCLUDED.name,
                    source_type = EXCLUDED.source_type,
                    -- Preserve DB-owned operational/permission overrides. A source
                    -- import is not authorized to re-approve a restricted source.
                    priority = EXCLUDED.priority,
                    trust_score = EXCLUDED.trust_score,
                    notes = EXCLUDED.notes,
                    config = EXCLUDED.config,
                    updated_at = now()
                """
            ),
            {
                "name": source.name,
                "domain": source.domain,
                "source_family": source.source_family,
                "source_type": source.source_type,
                "ingestion_mode": source.ingestion_mode.value,
                "discovery_enabled": source.discovery_enabled,
                "detail_extraction_enabled": source.detail_extraction_enabled,
                "permission_status": source.permission_status.value,
                "priority": source.priority,
                "trust_score": source.trust_score,
                "refresh_interval_seconds": source.refresh_interval_seconds,
                "rate_limit_per_minute": source.rate_limit_per_minute,
                "concurrency_limit": source.concurrency_limit,
                "notes": source.notes,
                "config": json.dumps({"seed_urls": [str(url) for url in source.seed_urls], "allowed_hosts": source.allowed_hosts}),
            },
        )


def load_source_definitions(path: Path) -> list[SourceDefinition]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [SourceDefinition.model_validate(item) for item in payload]
