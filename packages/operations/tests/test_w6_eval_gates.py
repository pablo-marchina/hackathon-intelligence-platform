from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from hackathon_operations.evals import run_all
from hackathon_operations.gates import evaluate_gates

ROOT = Path(__file__).resolve().parents[3]


def baseline():
    return json.loads((ROOT / "evals/gates/w6_baseline_v1.json").read_text())


def test_versioned_gates_accept_baseline_fixtures():
    assert evaluate_gates(run_all(ROOT), baseline())["passed"]


def test_eval_metric_score_regression_fails_gate():
    result = deepcopy(run_all(ROOT))
    result["metrics"][0]["value"] = 0.96
    assert not evaluate_gates(result, baseline())["passed"]


def test_eval_missing_metric_fails_closed():
    result = deepcopy(run_all(ROOT))
    result["metrics"] = result["metrics"][:-1]
    assert not evaluate_gates(result, baseline())["passed"]


def test_eval_duplicate_metric_fails_closed():
    result = deepcopy(run_all(ROOT))
    result["metrics"].append(deepcopy(result["metrics"][0]))
    assert not evaluate_gates(result, baseline())["passed"]


def test_eval_shrunk_golden_set_fails_gate():
    result = deepcopy(run_all(ROOT))
    result["discovery"]["examples"] = 10
    assert not evaluate_gates(result, baseline())["passed"]


def test_eval_false_auto_merge_fails_gate():
    result = deepcopy(run_all(ROOT))
    result["dedup"]["false_positive_auto_merges"] = 1
    assert not evaluate_gates(result, baseline())["passed"]


def test_eval_nonfinite_score_fails_closed():
    result = deepcopy(run_all(ROOT))
    result["metrics"][0]["value"] = float("nan")
    assert not evaluate_gates(result, baseline())["passed"]
