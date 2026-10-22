from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class EventQualityInput:
    has_name: bool
    has_dates: bool
    has_eligibility: bool
    has_prize: bool
    has_location: bool
    source_count: int
    last_verified_at: datetime | None
    open_conflicts: int = 0
    has_official_source: bool = False


@dataclass(frozen=True)
class QualitySnapshot:
    identity: float
    dates: float
    eligibility: float
    prize: float
    location: float
    sources: float
    freshness: float
    consistency: float
    overall: float


def _freshness_score(last_verified_at: datetime | None, now: datetime) -> float:
    if last_verified_at is None:
        return 0.0
    age = now - last_verified_at
    if age <= timedelta(hours=24):
        return 100.0
    if age <= timedelta(days=3):
        return 85.0
    if age <= timedelta(days=7):
        return 65.0
    if age <= timedelta(days=30):
        return 35.0
    return 10.0


def compute_quality(value: EventQualityInput, *, now: datetime | None = None) -> QualitySnapshot:
    now = now or datetime.now(UTC)
    source_score = min(
        100.0, value.source_count * 18.0 + (20.0 if value.has_official_source else 0.0)
    )
    consistency = max(0.0, 100.0 - value.open_conflicts * 25.0)
    parts = {
        "identity": 100.0 if value.has_name else 0.0,
        "dates": 100.0 if value.has_dates else 0.0,
        "eligibility": 100.0 if value.has_eligibility else 0.0,
        "prize": 100.0 if value.has_prize else 0.0,
        "location": 100.0 if value.has_location else 0.0,
        "sources": source_score,
        "freshness": _freshness_score(value.last_verified_at, now),
        "consistency": consistency,
    }
    weights = {
        "identity": 0.14,
        "dates": 0.18,
        "eligibility": 0.14,
        "prize": 0.08,
        "location": 0.08,
        "sources": 0.14,
        "freshness": 0.14,
        "consistency": 0.10,
    }
    overall = sum(parts[name] * weights[name] for name in parts)
    return QualitySnapshot(overall=round(overall, 2), **{k: round(v, 2) for k, v in parts.items()})
