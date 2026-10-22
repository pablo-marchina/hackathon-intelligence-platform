"""Opt-in, transactional saved-search match producer.

Uses the public catalog's query compiler to avoid drift in search semantics.
The checkpoint, in-app event and email outbox entries commit together. A row
lock serializes concurrent Beat workers; retries are safe via dedup keys.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Connection, Engine, create_engine, text

from hackathon_alerts.events import new_match_alert
from hackathon_alerts.repository import AlertRepository
from hackathon_api.repositories.hackathons import build_hackathon_query
from hackathon_api.schemas import FILTER_SCHEMA_VERSION, HackathonFilters
from hackathon_shared.settings import get_settings


def _filters(raw: Mapping[str, Any], version: str) -> HackathonFilters | None:
    """Reject unsupported schemas/fields/cursors rather than silently broaden."""
    if version != FILTER_SCHEMA_VERSION or not isinstance(raw, dict):
        return None
    if set(raw) - set(HackathonFilters.model_fields):
        return None
    if raw.get("cursor"):
        return None
    try:
        return HackathonFilters.model_validate({**raw, "limit": 1, "cursor": None})
    except (ValidationError, ValueError, TypeError):
        return None


def _explicit_email_consent(conn: Connection, user_id: str) -> bool:
    """Use the same opt-in policy and user lock as every email delivery."""
    return AlertRepository(conn).authorized_email_recipient(
        user_id=user_id, alert_type="new_match", lock_user=True,
    ) is not None


def allow_new_match_email(
    conn: Connection, *, user_id: str, hackathon_id: str | None,
    payload: dict[str, Any],
) -> bool:
    """Revalidate a queued match against the *current* catalog and saved filters.

    Disabling/deleting a search, withdrawing consent, withdrawing a hackathon,
    or changing eligibility between queueing and delivery must suppress SMTP.
    FOR SHARE locks the search and the matching catalog row through SMTP's
    enclosing transaction, serializing their updates with the send decision.
    """
    try:
        search_id = str(UUID(str(payload.get("saved_search_id"))))
        event_id = str(UUID(str(hackathon_id)))
    except (TypeError, ValueError, AttributeError):
        return False
    if not _explicit_email_consent(conn, user_id):
        return False
    search = conn.execute(text("""
        SELECT filters,filter_schema_version FROM saved_searches
        WHERE id=CAST(:search_id AS uuid)
          AND user_id=CAST(:user_id AS uuid)
          AND notifications_enabled=true
        FOR SHARE
    """), {"search_id": search_id, "user_id": user_id}).mappings().first()
    if not search:
        return False
    return _match_saved_search(
        conn, hackathon_id=event_id, raw_filters=search["filters"],
        schema_version=search["filter_schema_version"], lock_target=True,
    ) is True


def _match_saved_search(
    conn: Connection, *, hackathon_id: str, raw_filters: dict[str, Any],
    schema_version: str, lock_target: bool = False,
) -> bool | None:
    filters = _filters(raw_filters, schema_version)
    if filters is None:
        return None
    spec = build_hackathon_query(filters, target_id=hackathon_id)
    # Prepared params are supplied by SQLAlchemy; no saved-search SQL interpolation.
    # Lock only the canonical hackathon; joined/optional rows are not locked.
    # The public query is unchanged for normal matching and browsing.
    # The catalog SELECT projects sortable timestamp sentinels of PostgreSQL
    # 'infinity'. Psycopg cannot turn those into Python datetimes; matching
    # needs existence only, not the catalog's projected columns.
    _, sep, remainder = spec.sql.partition(" FROM hackathons h ")
    if not sep:
        raise RuntimeError("unexpected catalog query structure")
    sql = "SELECT 1" + sep + remainder + (" FOR SHARE OF h" if lock_target else "")
    return conn.execute(text(sql), spec.params).first() is not None


def process_new_matches(
    *, engine: Engine | None = None, batch_size: int | None = None,
    min_age_seconds: int | None = None, max_searches: int = 5000,
) -> dict[str, int]:
    """Scan new catalog events in deterministic keyset order.

    First run starts from the current database clock. A configurable commit
    grace period delays scanning to reduce visibility races with ingestion.
    If anything fails, checkpoint/event/delivery writes all roll back.
    """
    config = get_settings()
    if not config.alert_match_enabled:
        return {"disabled": 1}
    limit = config.alert_match_batch_size if batch_size is None else batch_size
    min_age = config.alert_match_min_age_seconds if min_age_seconds is None else min_age_seconds
    if not 1 <= limit <= 100 or not 0 <= min_age <= 3600 or not 1 <= max_searches <= 50_000:
        raise ValueError("invalid saved-search matching limits")
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(config.database_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            new_processor = conn.execute(text("""
                INSERT INTO alert_match_progress(processor_key,last_created_at)
                VALUES ('new_matches',clock_timestamp())
                ON CONFLICT(processor_key) DO NOTHING
                RETURNING processor_key
            """)).scalar_one_or_none()
            if new_processor:
                return {"initialized": 1, "scanned": 0, "created": 0}
            progress = conn.execute(text("""
                SELECT last_created_at,last_hackathon_id
                FROM alert_match_progress
                WHERE processor_key='new_matches' FOR UPDATE
            """)).one()
            events = conn.execute(text("""
                SELECT id::text AS id, slug, name, created_at, is_active
                FROM hackathons
                WHERE (created_at,id) > (:after_time,CAST(:after_id AS uuid))
                  AND created_at <= clock_timestamp()-make_interval(secs => :min_age)
                ORDER BY created_at,id LIMIT :limit
            """), {"after_time": progress[0], "after_id": str(progress[1]),
                   "min_age": min_age, "limit": limit}).mappings().all()
            if not events:
                return {"scanned": 0, "created": 0, "invalid_filters": 0}
            # Bound memory/CPU and fail without advancing the checkpoint if the
            # deployment outgrows this single-worker producer.
            searches = conn.execute(text("""
                SELECT s.id::text AS id,s.user_id::text AS user_id,
                       s.created_at,s.filters,s.filter_schema_version
                FROM saved_searches s JOIN users u ON u.id=s.user_id
                WHERE s.notifications_enabled=true AND u.deleted_at IS NULL
                ORDER BY s.id LIMIT :limit
            """), {"limit": max_searches + 1}).mappings().all()
            if len(searches) > max_searches:
                raise RuntimeError("saved-search producer capacity exceeded; checkpoint unchanged")
            repo = AlertRepository(conn)
            created = invalid = matched = 0
            for event in events:
                if not event["is_active"]:
                    continue
                for search in searches:
                    # No alert for catalog items predating the saved search.
                    if search["created_at"] > event["created_at"]:
                        continue
                    is_match = _match_saved_search(
                        conn, hackathon_id=event["id"],
                        raw_filters=search["filters"], schema_version=search["filter_schema_version"],
                    )
                    if is_match is None:
                        invalid += 1
                        continue
                    if not is_match:
                        continue
                    matched += 1
                    user_id = search["user_id"]
                    in_app = repo.channel_enabled(
                        user_id=user_id, channel="IN_APP", alert_type="new_match"
                    )
                    email = _explicit_email_consent(conn, user_id)
                    if not in_app and not email:
                        continue
                    candidate = new_match_alert(
                        user_id=user_id, hackathon_id=event["id"],
                        saved_search_id=search["id"], payload={"title": event["name"]},
                    )
                    event_id = repo.create_event(candidate)
                    if event_id is None:
                        continue
                    created += 1
                    if email:
                        repo.ensure_delivery(
                            alert_event_id=event_id, channel="EMAIL",
                            max_attempts=config.alert_max_attempts,
                        )
            last = events[-1]
            conn.execute(text("""
                UPDATE alert_match_progress
                SET last_created_at=:last_time,
                    last_hackathon_id=CAST(:last_id AS uuid),updated_at=now()
                WHERE processor_key='new_matches'
            """), {"last_time": last["created_at"], "last_id": last["id"]})
            return {"scanned": len(events), "matched": matched, "created": created,
                    "invalid_filters": invalid}
    finally:
        if owns_engine:
            engine.dispose()
