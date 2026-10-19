from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class SourceHealthInput:
    active: bool
    permission_status: str | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    consecutive_failures: int = 0


def derive_source_health(value: SourceHealthInput, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if not value.active:
        return "BLOCKED"
    if value.permission_status in {"RESTRICTED", "PARTNER_REQUIRED"}:
        return "PERMISSION_REQUIRED"
    if value.consecutive_failures >= 5:
        return "BROKEN"
    if value.consecutive_failures >= 2:
        return "DEGRADED"
    if value.last_success_at and now - value.last_success_at <= timedelta(days=2):
        return "HEALTHY"
    return "UNKNOWN"
