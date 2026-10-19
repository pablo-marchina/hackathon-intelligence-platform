from pathlib import Path

from hackathon_operations.extraction_eval import evaluate_extraction_golden


def test_extraction_golden_multilingual_platform_shapes() -> None:
    root = Path(__file__).resolve().parents[3]
    result = evaluate_extraction_golden(root / "evals/extraction/golden_v0_1.json")
    assert result.examples >= 8
    assert result.precision >= .95
    assert result.recall >= .95
    assert result.exact_match >= .95
    assert result.passed
