from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class RecrawlInput:
    event_start_at: datetime | None
    registration_close_at: datetime | None
    historical_change_probability: float
    user_interest: float
    source_value: float
    source_min_interval: timedelta = timedelta(hours=1)


@dataclass(frozen=True)
class RecrawlPlan:
    priority: float
    interval: timedelta
    reason: str


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def plan_recrawl(value: RecrawlInput, *, now: datetime | None = None) -> RecrawlPlan:
    now = now or datetime.now(UTC)
    urgency = 0.15
    reason = "normal cadence"
    deadlines = [d for d in (value.registration_close_at, value.event_start_at) if d and d >= now]
    if deadlines:
        nearest = min(deadlines) - now
        if nearest <= timedelta(days=1):
            urgency, reason = 1.0, "deadline/start within 24h"
        elif nearest <= timedelta(days=7):
            urgency, reason = 0.8, "deadline/start within 7d"
        elif nearest <= timedelta(days=30):
            urgency, reason = 0.55, "deadline/start within 30d"
    priority = (
        0.35 * urgency
        + 0.30 * _clamp(value.historical_change_probability)
        + 0.15 * _clamp(value.user_interest)
        + 0.20 * _clamp(value.source_value)
    )
    if priority >= 0.8:
        interval = timedelta(hours=1)
    elif priority >= 0.6:
        interval = timedelta(hours=3)
    elif priority >= 0.4:
        interval = timedelta(hours=8)
    elif priority >= 0.2:
        interval = timedelta(days=1)
    else:
        interval = timedelta(days=3)
    interval = max(interval, value.source_min_interval)
    return RecrawlPlan(round(priority, 4), interval, reason)
