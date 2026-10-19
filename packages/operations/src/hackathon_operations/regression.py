from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRunSample:
    fetched: int
    parsed: int
    valid_events: int
    failures: int = 0


@dataclass(frozen=True)
class RegressionSignal:
    severity: str
    code: str
    message: str


def detect_source_regression(
    previous: SourceRunSample,
    current: SourceRunSample,
    *,
    drop_ratio: float = 0.70,
) -> tuple[RegressionSignal, ...]:
    signals: list[RegressionSignal] = []
    if previous.valid_events >= 5:
        minimum_expected = previous.valid_events * (1 - drop_ratio)
        if current.valid_events < minimum_expected:
            signals.append(
                RegressionSignal(
                    severity="HIGH",
                    code="EVENT_COUNT_COLLAPSE",
                    message=(
                        f"valid events fell from {previous.valid_events} to {current.valid_events}"
                    ),
                )
            )
    if current.fetched >= 10 and current.parsed == 0:
        signals.append(
            RegressionSignal(
                severity="CRITICAL",
                code="PARSER_ZERO_OUTPUT",
                message="fetching succeeded but parser produced zero documents",
            )
        )
    if current.fetched > 0 and current.failures / current.fetched >= 0.5:
        signals.append(
            RegressionSignal(
                severity="HIGH",
                code="HIGH_FAILURE_RATE",
                message="at least half of fetch attempts failed",
            )
        )
    return tuple(signals)
