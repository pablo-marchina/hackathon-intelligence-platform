from pathlib import Path

from hackathon_operations.dedup_eval import evaluate_dedup_golden


def test_auto_merge_precision_gate_exceeds_98_percent_with_statistical_support() -> None:
    root = Path(__file__).resolve().parents[3]
    result = evaluate_dedup_golden(root / "evals/dedup/golden_v0_1.json")
    assert result.pairs == 470
    assert result.same_event_pairs == 260
    assert result.different_event_pairs == 210
    assert result.auto_merges >= 200
    assert result.false_positive_auto_merges == 0
    assert result.review_predictions == 40
    assert result.review_same_event_pairs == 20
    assert result.review_different_event_pairs == 20
    assert result.review_precision == 0.5
    assert result.review_recall == 20 / 260
    assert result.precision > 0.98
    assert result.wilson_lower_95 > 0.98
    assert result.passed
