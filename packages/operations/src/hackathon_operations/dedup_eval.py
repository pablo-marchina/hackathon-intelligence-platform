from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hackathon_intelligence.dedup import EventFingerprint, build_features, dedup_decision, duplicate_score


@dataclass(frozen=True)
class DedupBenchmarkResult:
    pairs: int
    same_event_pairs: int
    different_event_pairs: int
    auto_merges: int
    true_positive_auto_merges: int
    false_positive_auto_merges: int
    review_predictions: int
    review_same_event_pairs: int
    review_different_event_pairs: int
    review_precision: float
    review_recall: float
    precision: float
    recall: float
    wilson_lower_95: float
    threshold: float
    minimum_auto_merge_support: int
    passed: bool


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _fingerprint(payload: dict[str, Any]) -> EventFingerprint:
    return EventFingerprint(
        name=payload["name"],
        organizer=payload.get("organizer"),
        start_at=_dt(payload.get("start_at")),
        city=payload.get("city"),
        official_url=payload.get("official_url"),
        external_ids=tuple(payload.get("external_ids") or ()),
    )


def wilson_lower_bound(successes: int, trials: int, *, z: float = 1.959963984540054) -> float:
    if trials == 0:
        return 0.0
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * trials)) / trials) / denominator
    return max(0.0, center - margin)


def evaluate_dedup_golden(
    path: Path,
    *,
    auto_merge_threshold: float = 0.97,
    minimum_auto_merge_support: int = 200,
    required_precision: float = 0.98,
    required_wilson_lower: float = 0.98,
) -> DedupBenchmarkResult:
    rows = json.loads(path.read_text(encoding="utf-8"))
    true_positive = 0
    false_positive = 0
    positives = 0
    recovered_positives = 0
    negatives = 0
    review_predictions = 0
    review_same = 0
    review_different = 0

    for row in rows:
        label = bool(row["same_event"])
        positives += int(label)
        negatives += int(not label)
        left = _fingerprint(row["left"])
        right = _fingerprint(row["right"])
        score = duplicate_score(build_features(left, right))
        decision = dedup_decision(score, auto_merge_threshold=auto_merge_threshold)
        if decision == "AUTO_MERGE":
            if label:
                true_positive += 1
                recovered_positives += 1
            else:
                false_positive += 1
        elif decision == "REVIEW":
            review_predictions += 1
            if label:
                review_same += 1
            else:
                review_different += 1

    predicted_positive = true_positive + false_positive
    precision = true_positive / predicted_positive if predicted_positive else 0.0
    recall = recovered_positives / positives if positives else 1.0
    lower = wilson_lower_bound(true_positive, predicted_positive)
    review_precision = review_same / review_predictions if review_predictions else 0.0
    review_recall = review_same / positives if positives else 0.0
    passed = (
        predicted_positive >= minimum_auto_merge_support
        and false_positive == 0
        and precision > required_precision
        and lower > required_wilson_lower
    )
    return DedupBenchmarkResult(
        pairs=len(rows),
        same_event_pairs=positives,
        different_event_pairs=negatives,
        auto_merges=predicted_positive,
        true_positive_auto_merges=true_positive,
        false_positive_auto_merges=false_positive,
        review_predictions=review_predictions,
        review_same_event_pairs=review_same,
        review_different_event_pairs=review_different,
        review_precision=review_precision,
        review_recall=review_recall,
        precision=precision,
        recall=recall,
        wilson_lower_95=lower,
        threshold=auto_merge_threshold,
        minimum_auto_merge_support=minimum_auto_merge_support,
        passed=passed,
    )


def result_as_dict(result: DedupBenchmarkResult) -> dict[str, Any]:
    return asdict(result)
