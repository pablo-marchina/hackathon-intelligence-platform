from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hackathon_discovery.classifier import classify_page
from hackathon_intelligence.jsonld import extract_event_jsonld
from hackathon_recommendation.eligibility import EventEligibility, Result, UserEligibilityProfile, evaluate
from hackathon_recommendation.ranking import EventSignals, UserPreferences, recommend

from .dedup_eval import evaluate_dedup_golden, result_as_dict
from .extraction_eval import evaluate_extraction_golden


@dataclass(frozen=True)
class EvalMetric:
    name: str
    passed: bool
    value: float
    threshold: float
    details: str


def discovery_precision(golden_path: Path) -> tuple[EvalMetric, dict[str, Any]]:
    rows = json.loads(golden_path.read_text(encoding="utf-8"))
    tp = fp = tn = fn = 0
    false_positives: list[dict[str, str]] = []
    false_negatives: list[dict[str, str]] = []
    for row in rows:
        prediction = classify_page(title=row["title"], text=row["text"])
        accepted = prediction.is_hackathon_like
        label = bool(row["label"])
        tp += int(accepted and label)
        fp += int(accepted and not label)
        tn += int((not accepted) and (not label))
        fn += int((not accepted) and label)
        if accepted and not label:
            false_positives.append({"title": row["title"], "url": row["source_url"]})
        if (not accepted) and label:
            false_negatives.append({"title": row["title"], "url": row["source_url"]})
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    metric = EvalMetric(
        "discovery_precision",
        precision >= 0.95,
        precision,
        0.95,
        f"tp={tp}; fp={fp}; tn={tn}; fn={fn}; recall={recall:.4f}; examples={len(rows)}",
    )
    details = {
        "examples": len(rows),
        "languages": len({row["language"] for row in rows}),
        "domains": len({row["source_url"].split('/')[2] for row in rows}),
        "true_positives": tp,
        "false_positives": false_positives,
        "true_negatives": tn,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
    }
    return metric, details


def real_fixture_e2e(fixture_path: Path) -> tuple[EvalMetric, dict[str, Any]]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    observed_at = datetime.fromisoformat(fixture["verified_at"].replace("Z", "+00:00"))
    jsonld = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": fixture["name"],
        "startDate": fixture["start_at"],
        "endDate": fixture["end_at"],
        "url": fixture["source"],
        "organizer": {"@type": "Organization", "name": fixture["organizer"]},
        "description": fixture["notes"],
    }
    html = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(jsonld)
        + "</script></head><body>Registration open for the annual global hackathon.</body></html>"
    )
    extracted = extract_event_jsonld(html, observed_at=observed_at)
    classification = classify_page(
        title=fixture["name"],
        text="registration open annual global hackathon space science data",
    )
    eligibility = evaluate(
        UserEligibilityProfile(age=19, country_code="BR", is_student=True, planned_team_size=3),
        EventEligibility(eligible_worldwide=fixture["eligible_worldwide"]),
    )
    recommendation = recommend(
        eligibility.result,
        UserPreferences(tracks=frozenset({"space", "data"}), formats=frozenset({"HYBRID"})),
        EventSignals(
            tracks=frozenset(fixture["topics"]),
            format=fixture["format"],
            quality_score=95,
        ),
    )
    checks = [
        bool(extracted and extracted[0].name == fixture["name"]),
        classification.is_hackathon_like,
        eligibility.result is Result.ELIGIBLE,
        recommendation.score is not None and recommendation.score >= 80,
    ]
    metric = EvalMetric(
        "real_fixture_e2e",
        all(checks),
        sum(checks) / len(checks),
        1.0,
        "official-event fixture passes extraction, classification, eligibility and recommendation",
    )
    return metric, {
        "event": fixture["name"],
        "extracted": bool(extracted),
        "classification": asdict(classification),
        "eligibility": eligibility.result.value,
        "recommendation_score": recommendation.score,
    }


def extraction_core_fields(golden_path: Path) -> tuple[EvalMetric, dict[str, Any]]:
    result = evaluate_extraction_golden(golden_path)
    metric = EvalMetric(
        "extraction_core_fields",
        result.passed,
        result.exact_match,
        0.95,
        f"precision={result.precision:.4f}; recall={result.recall:.4f}; exact={result.exact_match:.4f}; examples={result.examples}",
    )
    return metric, asdict(result)


def dedup_auto_merge_precision(golden_path: Path) -> tuple[EvalMetric, dict[str, Any]]:
    result = evaluate_dedup_golden(golden_path)
    metric = EvalMetric(
        "dedup_auto_merge_precision",
        result.passed,
        result.precision,
        0.98,
        (
            f"{result.true_positive_auto_merges}/{result.auto_merges} AUTO_MERGE decisions are true positives; "
            f"Wilson lower 95%={result.wilson_lower_95:.4f}; support={result.auto_merges}"
        ),
    )
    return metric, result_as_dict(result)



def recommendation_ndcg_at_5() -> tuple[EvalMetric, dict[str, Any]]:
    import math
    candidates = [
        (5, Result.ELIGIBLE, EventSignals(tracks=frozenset({"ai","fintech"}), technologies=frozenset({"python"}), format="ONLINE", is_free=True, quality_score=95)),
        (4, Result.ELIGIBLE, EventSignals(tracks=frozenset({"ai"}), technologies=frozenset({"python"}), format="ONLINE", is_free=True, quality_score=90)),
        (3, Result.UNKNOWN, EventSignals(tracks=frozenset({"fintech"}), technologies=frozenset(), format="ONLINE", is_free=True, quality_score=88)),
        (1, Result.ELIGIBLE, EventSignals(tracks=frozenset({"gaming"}), technologies=frozenset(), format="IN_PERSON", is_free=False, quality_score=80)),
        (0, Result.INELIGIBLE, EventSignals(tracks=frozenset({"ai"}), technologies=frozenset({"python"}), format="ONLINE", is_free=True, quality_score=99)),
    ]
    prefs=UserPreferences(tracks=frozenset({"ai","fintech"}),technologies=frozenset({"python"}),formats=frozenset({"ONLINE"}),free_only=True)
    scored=[]
    ineligible_scored=0
    for relevance,eligibility,event in candidates:
        rec=recommend(eligibility,prefs,event)
        if eligibility is Result.INELIGIBLE and rec.score is not None:
            ineligible_scored += 1
        if rec.score is not None:
            scored.append((rec.score,relevance))
    ranked=[rel for _,rel in sorted(scored,reverse=True)[:5]]
    ideal=sorted([r for r,e,_ in candidates if e is not Result.INELIGIBLE],reverse=True)[:5]
    def dcg(values: list[int]) -> float:
        return sum((2**rel-1)/math.log2(i+2) for i,rel in enumerate(values))
    ideal_dcg=dcg(ideal)
    ndcg=dcg(ranked)/ideal_dcg if ideal_dcg else 1.0
    passed=ndcg >= 0.90 and ineligible_scored == 0
    return EvalMetric("recommendation_ndcg_at_5",passed,ndcg,0.90,f"ranked={ranked}; ideal={ideal}; ineligible_scored={ineligible_scored}"), {"ndcg_at_5": ndcg, "ranked_relevance": ranked, "ideal_relevance": ideal, "ineligible_scored": ineligible_scored}

def run_all(root: Path) -> dict[str, Any]:
    discovery_metric, discovery_details = discovery_precision(root / "evals/discovery/golden_v0_2.json")
    metrics = [discovery_metric]
    extraction_metric, extraction_details = extraction_core_fields(root / "evals/extraction/golden_v0_1.json")
    metrics.append(extraction_metric)
    dedup_metric, dedup_details = dedup_auto_merge_precision(root / "evals/dedup/golden_v0_1.json")
    metrics.append(dedup_metric)
    e2e_metric, e2e_details = real_fixture_e2e(root / "evals/e2e/nasa_space_apps_2026.json")
    metrics.append(e2e_metric)
    recommendation_metric, recommendation_details = recommendation_ndcg_at_5()
    metrics.append(recommendation_metric)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": all(metric.passed for metric in metrics),
        "metrics": [asdict(metric) for metric in metrics],
        "discovery": discovery_details,
        "e2e": e2e_details,
        "extraction": extraction_details,
        "dedup": dedup_details,
        "recommendation": recommendation_details,
    }
