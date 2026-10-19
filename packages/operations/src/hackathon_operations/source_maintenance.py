"""DB-backed W6 source health, anomaly and recrawl planning.

This module intentionally performs no HTTP requests. The scheduler is opt-in and
claims are transactionally leased; disabled/unapproved sources are never dispatched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, text

from .regression import SourceRunSample, detect_source_regression
from .scheduler import RecrawlInput, RecrawlPlan, plan_recrawl

ALERT_COOLDOWN = timedelta(hours=24)
LEASE_DURATION = timedelta(minutes=30)


@dataclass(frozen=True)
class SourcePlan:
    priority: float
    interval: timedelta
    reason: str
    next_due_at: datetime


@dataclass(frozen=True)
class MaintenanceResult:
    sources_checked: int
    runs_processed: int
    reviews_created: int


def _recent_run_sample(run: dict[str, Any]) -> SourceRunSample:
    success = max(0, int(run["fetched_count"]))
    failure = max(0, int(run["failed_count"]))
    return SourceRunSample(
        fetched=success + failure,
        parsed=max(0, int(run["parsed_count"])),
        valid_events=max(0, int(run["valid_event_count"])),
        failures=failure,
    )


def build_source_plan(
    *,
    now: datetime,
    latest_run: dict[str, Any] | None,
    previous_due: datetime | None,
    last_processed_run_id: str | None,
    nearest_start: datetime | None,
    nearest_close: datetime | None,
    change_probability: float,
    priority: int,
    trust_score: float | None,
    minimum_interval_seconds: int | None,
    consecutive_failures: int = 0,
) -> SourcePlan:
    """Replanning without a new crawl never pushes an outstanding due time forward."""
    minimum = timedelta(seconds=max(3600, minimum_interval_seconds or 3600))
    value = (priority / 100 + (trust_score if trust_score is not None else 0.5)) / 2
    recrawl: RecrawlPlan = plan_recrawl(
        RecrawlInput(
            event_start_at=nearest_start,
            registration_close_at=nearest_close,
            historical_change_probability=change_probability,
            # Aggregate engagement is not available per source in W6 yet.
            user_interest=0.0,
            source_value=value,
            source_min_interval=minimum,
        ),
        now=now,
    )
    backoff = min(86400, 900 * (2 ** min(consecutive_failures, 7))) if consecutive_failures else 0
    interval = max(recrawl.interval, timedelta(seconds=backoff))
    reason = recrawl.reason + (f"; failure backoff {backoff}s" if backoff else "")
    if latest_run is None:
        due = previous_due or now
    else:
        finished = latest_run["finished_at"] or latest_run["started_at"]
        due = max(now, finished + interval)
        if previous_due is not None and str(latest_run["id"]) == last_processed_run_id:
            due = min(previous_due, due)
    return SourcePlan(recrawl.priority, interval, reason, due)


def _emit_regressions(
    conn: Connection,
    *,
    source_id: str,
    run: dict[str, Any],
    previous: dict[str, Any] | None,
    now: datetime,
) -> int:
    """Create a review once per active incident, with a cooldown for closed reviews."""
    if previous is None or previous["status"] not in {"SUCCEEDED", "PARTIAL"}:
        return 0
    signals = detect_source_regression(_recent_run_sample(previous), _recent_run_sample(run))
    created = 0
    for signal in signals:
        conn.execute(
            text("""
                INSERT INTO source_regression_state(source_id, signal_code)
                VALUES (CAST(:s AS uuid), :code) ON CONFLICT DO NOTHING
            """),
            {"s": source_id, "code": signal.code},
        )
        state = conn.execute(
            text("""
                SELECT last_seen_run_id::text AS last_seen_run_id, occurrence_count,
                       last_emitted_at, last_review_id::text AS last_review_id
                  FROM source_regression_state
                 WHERE source_id=CAST(:s AS uuid) AND signal_code=:code FOR UPDATE
            """),
            {"s": source_id, "code": signal.code},
        ).mappings().one()
        if state["last_seen_run_id"] == str(run["id"]):
            continue
        has_open_review = False
        if state["last_review_id"] is not None:
            has_open_review = bool(conn.execute(
                text("""
                    SELECT EXISTS (
                        SELECT 1 FROM review_queue_items
                         WHERE id=CAST(:id AS uuid) AND state IN ('OPEN','IN_REVIEW')
                    )
                """),
                {"id": state["last_review_id"]},
            ).scalar_one())
        within_cooldown = (
            state["last_emitted_at"] is not None
            and now - state["last_emitted_at"] < ALERT_COOLDOWN
        )
        review_id = state["last_review_id"]
        if not has_open_review and not within_cooldown:
            payload = {
                "code": signal.code,
                "message": signal.message,
                "source_id": source_id,
                "crawl_run_id": str(run["id"]),
                "previous_run_id": str(previous["id"]),
            }
            review_id = str(conn.execute(
                text("""
                    INSERT INTO review_queue_items(
                        queue_type, entity_type, entity_id, severity, payload
                    ) VALUES (
                        'SOURCE_ANOMALY', 'source', :entity_id,
                        CAST(:severity AS conflict_severity), CAST(:payload AS jsonb)
                    ) RETURNING id::text
                """),
                {
                    "entity_id": f"{source_id}:{signal.code}:{run['id']}",
                    "severity": signal.severity,
                    "payload": json.dumps(payload),
                },
            ).scalar_one())
            created += 1
        conn.execute(
            text("""
                UPDATE source_regression_state
                   SET last_seen_run_id=CAST(:run_id AS uuid), last_seen_at=:now,
                       occurrence_count=occurrence_count+1,
                       last_emitted_at=CASE WHEN :emitted THEN :now ELSE last_emitted_at END,
                       last_review_id=CAST(:review_id AS uuid)
                 WHERE source_id=CAST(:source_id AS uuid) AND signal_code=:code
            """),
            {
                "run_id": str(run["id"]), "now": now, "emitted": not has_open_review and not within_cooldown,
                "review_id": review_id, "source_id": source_id, "code": signal.code,
            },
        )
    return created


def maintain_sources(conn: Connection, *, now: datetime | None = None) -> MaintenanceResult:
    """Evaluate all known sources; caller commits/rolls back as one transaction.

    Under a second scheduler, row locks serialize maintenance per source. New run
    IDs are processed once, and missing DB/migrations fail closed (never fetch).
    """
    now = now or datetime.now(UTC)
    sources = conn.execute(text("""
        SELECT id::text AS id, active, priority, trust_score, refresh_interval_seconds
          FROM sources ORDER BY id
    """)).mappings().all()
    checked = processed = notices = 0
    for source in sources:
        sid = source["id"]
        conn.execute(text("""
            INSERT INTO source_operational_state(source_id)
            VALUES (CAST(:source_id AS uuid)) ON CONFLICT DO NOTHING
        """), {"source_id": sid})
        state = conn.execute(text("""
            SELECT next_due_at, last_processed_run_id::text AS last_processed_run_id
              FROM source_operational_state
             WHERE source_id=CAST(:source_id AS uuid) FOR UPDATE
        """), {"source_id": sid}).mappings().one()
        recent = [dict(row) for row in conn.execute(text("""
            SELECT id::text AS id, status::text AS status, started_at, finished_at,
                   fetched_count, changed_count, parsed_count, failed_count, valid_event_count
              FROM crawl_runs
             WHERE source_id=CAST(:source_id AS uuid) AND status <> 'RUNNING'
             ORDER BY started_at DESC, id DESC LIMIT 5
        """), {"source_id": sid}).mappings().all()]
        latest = recent[0] if recent else None
        last_run_id = state["last_processed_run_id"]
        new_run = latest is not None and latest["id"] != last_run_id
        if new_run:
            processed += 1
            previous = next((run for run in recent[1:] if run["status"] in {"SUCCEEDED", "PARTIAL"}), None)
            if latest["status"] in {"SUCCEEDED", "PARTIAL", "FAILED"}:
                notices += _emit_regressions(
                    conn, source_id=sid, run=latest, previous=previous, now=now,
                )
            # Automatic health is refreshed only on a new completed run.
            new_signals = (
                detect_source_regression(_recent_run_sample(previous), _recent_run_sample(latest))
                if previous is not None else ()
            )
            health = (
                "BROKEN" if latest["status"] == "FAILED" or any(s.severity == "CRITICAL" for s in new_signals)
                else "DEGRADED" if latest["status"] == "PARTIAL" or new_signals
                else "HEALTHY"
            )
            if latest["status"] != "CANCELLED":
                conn.execute(text("""
                UPDATE sources
                   SET health=CAST(:health AS source_health),
                       last_attempt_at=:attempt,
                       last_success_at=CASE WHEN :successful THEN :attempt ELSE last_success_at END,
                       last_error_at=CASE WHEN :failed THEN :attempt ELSE last_error_at END
                 WHERE id=CAST(:source_id AS uuid)
                """), {
                    "health": health, "attempt": latest["finished_at"] or latest["started_at"],
                    "successful": latest["status"] == "SUCCEEDED",
                    "failed": latest["status"] in {"FAILED", "PARTIAL"},
                    "source_id": sid,
                })
        deadlines = conn.execute(text("""
            SELECT MIN(h.start_at) FILTER (WHERE h.start_at >= :now) AS nearest_start,
                   MIN(h.registration_close_at) FILTER (WHERE h.registration_close_at >= :now) AS nearest_close
              FROM source_event_refs r JOIN hackathons h ON h.id = r.hackathon_id
             WHERE r.source_id=CAST(:source_id AS uuid) AND r.active AND h.is_active
               AND ((h.start_at >= :now) OR (h.registration_close_at >= :now))
        """), {"source_id": sid, "now": now}).mappings().one()
        # Only future milestones matter; a past value must not mask a later one.
        nearest_start = deadlines["nearest_start"]
        nearest_close = deadlines["nearest_close"]
        if nearest_start is not None and nearest_start < now:
            nearest_start = None
        if nearest_close is not None and nearest_close < now:
            nearest_close = None
        fetched = sum(int(r["fetched_count"]) for r in recent if r["status"] in {"SUCCEEDED", "PARTIAL"})
        changed = sum(int(r["changed_count"]) for r in recent if r["status"] in {"SUCCEEDED", "PARTIAL"})
        consecutive_failures = 0
        for run in recent:
            if run["status"] != "FAILED":
                break
            consecutive_failures += 1
        plan = build_source_plan(
            now=now, latest_run=latest,
            previous_due=state["next_due_at"], last_processed_run_id=last_run_id,
            nearest_start=nearest_start, nearest_close=nearest_close,
            change_probability=min(1.0, changed / fetched) if fetched else 0.0,
            priority=int(source["priority"]),
            trust_score=float(source["trust_score"]) if source["trust_score"] is not None else None,
            minimum_interval_seconds=source["refresh_interval_seconds"],
            consecutive_failures=consecutive_failures,
        )
        conn.execute(text("""
            UPDATE source_operational_state
               SET next_due_at=:next_due, recrawl_priority=:priority,
                   interval_seconds=:interval_seconds, plan_reason=:reason,
                   last_processed_run_id=CAST(:run_id AS uuid),
                   evaluated_at=:now, updated_at=:now
             WHERE source_id=CAST(:source_id AS uuid)
        """), {
            "next_due": plan.next_due_at if source["active"] else None,
            "priority": plan.priority, "interval_seconds": int(plan.interval.total_seconds()),
            "reason": plan.reason, "run_id": latest["id"] if latest else None,
            "now": now, "source_id": sid,
        })
        checked += 1
    return MaintenanceResult(checked, processed, notices)


def claim_due_source(
    conn: Connection, *, allowed_sources: list[tuple[str, str]], now: datetime | None = None,
) -> dict[str, str] | None:
    """Lease exactly one due source using PostgreSQL time, not worker wall time.

    The optional `now` is only for deterministic integration tests. Real workers
    always use one clock_timestamp() sampled inside the claiming statement.
    """
    if not allowed_sources:
        return None
    keys = [f"{domain.lower()}|{family}" for domain, family in allowed_sources]
    token = str(uuid4())
    row = conn.execute(text("""
        WITH clock AS MATERIALIZED (
            SELECT COALESCE(CAST(:now AS timestamptz), clock_timestamp()) AS tick
        ), eligible AS (
            SELECT st.source_id
              FROM source_operational_state st
              JOIN sources s ON s.id=st.source_id
              CROSS JOIN clock
             WHERE s.active AND s.discovery_enabled AND s.detail_extraction_enabled
               AND s.permission_status='ALLOWED'
               AND s.ingestion_mode NOT IN ('BLOCKED','DISCOVERY_ONLY','MANUAL')
               AND (lower(s.domain::text) || '|' || s.source_family) = ANY(:keys)
               AND st.next_due_at <= clock.tick
               AND (st.lease_until IS NULL OR st.lease_until <= clock.tick)
             ORDER BY st.recrawl_priority DESC, st.next_due_at ASC
             FOR UPDATE OF st SKIP LOCKED LIMIT 1
        )
        UPDATE source_operational_state st
           SET lease_token=CAST(:token AS uuid),
               lease_until=clock.tick + make_interval(secs => :lease_seconds),
               updated_at=clock.tick
          FROM eligible e, clock WHERE st.source_id=e.source_id
        RETURNING st.source_id::text AS source_id, st.lease_token::text AS lease_token
    """), {
        "keys": keys, "now": now,
        "token": token, "lease_seconds": int(LEASE_DURATION.total_seconds()),
    }).mappings().first()
    return dict(row) if row else None


def complete_lease(
    conn: Connection, *, source_id: str, lease_token: str, now: datetime | None = None,
    success: bool,
) -> bool:
    """Only an unexpired lease owner may complete a run and defer recrawl.

    Sampling database time inside this transaction is necessary: a skewed
    machine clock cannot extend the authority of a stale worker.
    """
    row = conn.execute(text("""
        WITH clock AS MATERIALIZED (
            SELECT COALESCE(CAST(:now AS timestamptz), clock_timestamp()) AS tick
        )
        UPDATE source_operational_state st
           SET lease_token=NULL, lease_until=NULL,
               next_due_at=clock.tick + make_interval(secs => CASE WHEN :success
                        THEN interval_seconds ELSE GREATEST(interval_seconds, 1800) END),
               updated_at=clock.tick
          FROM clock
         WHERE st.source_id=CAST(:source_id AS uuid) AND st.lease_token=CAST(:token AS uuid)
           AND st.lease_until > clock.tick
        RETURNING st.source_id
    """), {
        "source_id": source_id, "token": lease_token, "now": now, "success": success,
    }).first()
    return row is not None


def renew_lease(
    conn: Connection, *, source_id: str, lease_token: str, now: datetime | None = None,
) -> bool:
    """Extend an active lease only while the DB still permits ingestion.

    The optional explicit timestamp is for fixture tests; production always
    samples PostgreSQL's clock within the UPDATE statement.
    """
    row = conn.execute(text("""
        WITH clock AS MATERIALIZED (
            SELECT COALESCE(CAST(:now AS timestamptz), clock_timestamp()) AS tick
        )
        UPDATE source_operational_state st
           SET lease_until=clock.tick + make_interval(secs => :lease_seconds),
               updated_at=clock.tick
          FROM sources s, clock
         WHERE st.source_id=s.id AND st.source_id=CAST(:source_id AS uuid)
           AND st.lease_token=CAST(:token AS uuid) AND st.lease_until > clock.tick
           AND s.active AND s.discovery_enabled AND s.detail_extraction_enabled
           AND s.permission_status='ALLOWED'
           AND s.ingestion_mode NOT IN ('BLOCKED','DISCOVERY_ONLY','MANUAL')
        RETURNING st.source_id
    """), {
        "source_id": source_id, "token": lease_token, "now": now,
        "lease_seconds": int(LEASE_DURATION.total_seconds()),
    }).first()
    return row is not None
