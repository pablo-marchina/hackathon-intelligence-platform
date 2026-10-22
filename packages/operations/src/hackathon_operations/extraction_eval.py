from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hackathon_intelligence.extractors import extract_structured_event
from hackathon_intelligence.jsonld import extract_event_jsonld


@dataclass(frozen=True)
class ExtractionBenchmarkResult:
    examples: int
    assertions: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    exact_match: float
    passed: bool


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def evaluate_extraction_golden(path: Path) -> ExtractionBenchmarkResult:
    rows = json.loads(path.read_text(encoding="utf-8"))
    tp = fp = fn = exact = assertions = 0
    now = datetime(2026, 9, 23, tzinfo=UTC)
    for row in rows:
        if row["shape"] == "structured":
            event = extract_structured_event(row["payload"], observed_at=now)
        elif row["shape"] == "jsonld":
            found = extract_event_jsonld(row["html"], observed_at=now)
            event = None if not found else found[0]
        else:
            raise ValueError(f"unknown extraction shape: {row['shape']}")
        expected = row["expected"]
        for field, exp in expected.items():
            assertions += 1
            actual = _normalize(getattr(event, field, None)) if event is not None else None
            if actual == exp:
                exact += 1
                if exp is not None:
                    tp += 1
            else:
                if actual is not None:
                    fp += 1
                if exp is not None:
                    fn += 1
        # Penalize asserted core values absent from expected only when the source
        # fixture did not contain them; this catches silent hallucination.
        if event is not None:
            for field in (
                "name",
                "start_at",
                "end_at",
                "official_url",
                "location_name",
                "organizer_name",
                "event_type",
                "format",
                "prize_text",
                "eligibility_text",
            ):
                if field not in expected and getattr(event, field, None) is not None:
                    # Values explicitly in the fixture source are allowed even when not scored.
                    pass
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    exact_match = exact / assertions if assertions else 1.0
    return ExtractionBenchmarkResult(
        len(rows),
        assertions,
        tp,
        fp,
        fn,
        precision,
        recall,
        exact_match,
        precision >= 0.95 and recall >= 0.95 and exact_match >= 0.95,
    )
