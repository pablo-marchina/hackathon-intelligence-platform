from pathlib import Path

from hackathon_operations.evals import run_all


def test_unified_eval_seed_suite_passes() -> None:
    root = Path(__file__).resolve().parents[3]
    result = run_all(root)
    assert result["passed"] is True
    assert {metric["name"] for metric in result["metrics"]} == {
        "discovery_precision",
        "extraction_core_fields",
        "dedup_auto_merge_precision",
        "real_fixture_e2e",
        "recommendation_ndcg_at_5",
    }
    assert result["dedup"]["precision"] > 0.98
    assert result["dedup"]["wilson_lower_95"] > 0.98
