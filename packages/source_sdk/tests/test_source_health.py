from datetime import UTC, datetime, timedelta

from hackathon_sources.health import SourceHealthInput, derive_source_health


def test_permission_required_wins() -> None:
    value = SourceHealthInput(True, "RESTRICTED", None, None)
    assert derive_source_health(value) == "PERMISSION_REQUIRED"


def test_recent_success_is_healthy() -> None:
    now = datetime.now(UTC)
    value = SourceHealthInput(True, "ALLOWED", now - timedelta(hours=1), None)
    assert derive_source_health(value, now=now) == "HEALTHY"


def test_repeated_failures_are_broken() -> None:
    value = SourceHealthInput(True, "ALLOWED", None, None, consecutive_failures=6)
    assert derive_source_health(value) == "BROKEN"
