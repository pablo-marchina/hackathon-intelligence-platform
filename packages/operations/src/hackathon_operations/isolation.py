from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceAvailability:
    source_id: str
    healthy: bool
    canonical_events_supported: int


@dataclass(frozen=True)
class FailureDrillResult:
    product_available: bool
    healthy_sources: int
    supported_events: int
    failed_sources: tuple[str, ...]


def evaluate_source_failure_isolation(sources: list[SourceAvailability]) -> FailureDrillResult:
    healthy = [source for source in sources if source.healthy]
    failed = tuple(source.source_id for source in sources if not source.healthy)
    supported_events = sum(source.canonical_events_supported for source in healthy)
    return FailureDrillResult(
        product_available=bool(healthy) and supported_events > 0,
        healthy_sources=len(healthy),
        supported_events=supported_events,
        failed_sources=failed,
    )
