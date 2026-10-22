from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class AlertCandidate:
    alert_type: str
    hackathon_id: str
    user_id: str
    payload: dict[str, Any]
    dedup_key: str
    canonical_change_id: int | None = None


def dedup_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def new_match_alert(
    *, user_id: str, hackathon_id: str, saved_search_id: str, payload: dict[str, Any]
) -> AlertCandidate:
    return AlertCandidate(
        alert_type="new_match",
        hackathon_id=hackathon_id,
        user_id=user_id,
        payload={**payload, "saved_search_id": saved_search_id},
        dedup_key=dedup_key("new_match", user_id, saved_search_id, hackathon_id),
    )


def change_alert(
    *,
    user_id: str,
    hackathon_id: str,
    change_id: str,
    change_type: str,
    payload: dict[str, Any],
) -> AlertCandidate:
    mapping = {
        "deadline": "deadline_changed",
        "deadline_changed": "deadline_changed",
        "prize": "prize_changed",
        "prize_changed": "prize_changed",
        "cancelled": "cancellation_postponement",
        "canceled": "cancellation_postponement",
        "postponed": "cancellation_postponement",
        "status": "cancellation_postponement",
        "eligibility": "eligibility_changed",
        "eligibility_changed": "eligibility_changed",
    }
    alert_type = mapping.get(change_type.lower(), f"event_change:{change_type}")
    canonical_change_id = int(change_id) if str(change_id).isdigit() else None
    return AlertCandidate(
        alert_type=alert_type,
        hackathon_id=hackathon_id,
        user_id=user_id,
        payload={**payload, "change_id": str(change_id)},
        dedup_key=dedup_key("change", user_id, str(change_id)),
        canonical_change_id=canonical_change_id,
    )


def deadline_alerts(
    *,
    user_id: str,
    hackathon_id: str,
    deadline: datetime,
    timezone: str = "UTC",
    now: datetime | None = None,
    windows: tuple[timedelta, ...] = (timedelta(days=7), timedelta(days=1)),
    field_path: str = "registration_close_at",
    source_evidence: list[dict[str, Any]] | None = None,
) -> list[AlertCandidate]:
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    remaining = deadline - now
    try:
        local_deadline = deadline.astimezone(ZoneInfo(timezone))
    except Exception:
        timezone = "UTC"
        local_deadline = deadline.astimezone(UTC)

    result: list[AlertCandidate] = []
    for window in windows:
        if timedelta(0) <= remaining <= window:
            days = max(1, int(window.total_seconds() // 86400))
            label = f"{days}d"
            payload = {
                "deadline": deadline.isoformat(),
                "deadline_local": local_deadline.isoformat(),
                "timezone": timezone,
                "window": label,
                "field_path": field_path,
                "source_evidence": source_evidence or [],
            }
            result.append(
                AlertCandidate(
                    alert_type="deadline_approaching",
                    user_id=user_id,
                    hackathon_id=hackathon_id,
                    payload=payload,
                    dedup_key=dedup_key(
                        "deadline", user_id, hackathon_id, deadline.isoformat(), label
                    ),
                )
            )
    return result
