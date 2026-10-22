"""Versioned, fail-closed evaluation gates for continuous integration."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def evaluate_gates(result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Fail on new/missing metrics, fixture shrinkage or significant score regressions.

    Baselines cannot prove online performance: these gates only monitor reproducible fixture
    regressions. Raising a floor or replacing a golden set requires a reviewed config change.
    """
    if config.get("schema_version") != 1 or not config.get("baseline_id"):
        raise ValueError("unsupported or missing evaluation baseline version")
    expected = config["metrics"]
    observed = result.get("metrics", [])
    names = [m.get("name") for m in observed]
    failures: list[str] = []
    if len(names) != len(set(names)) or set(names) != set(expected):
        failures.append("metric inventory changed or includes duplicate names")
    for name, gate in expected.items():
        measurements = [m for m in observed if m.get("name") == name]
        if len(measurements) != 1:
            failures.append(f"{name}: missing or duplicate metric")
            continue
        metric = measurements[0]
        floor = max(float(gate["floor"]), float(gate["baseline"]) - float(gate["maximum_drop"]))
        measured = float(metric["value"])
        if not metric.get("passed") or not math.isfinite(measured) or measured < floor:
            failures.append(
                f"{name}: {metric['value']} below required {floor} or internal gate failed"
            )
    for section, minimums in config.get("support_gates", {}).items():
        measurements = result.get(section, {})
        for name, minimum in minimums.items():
            measured = measurements.get(name)
            if (
                measured is None
                or not math.isfinite(float(measured))
                or float(measured) < float(minimum)
            ):
                failures.append(f"{section}.{name}: {measured} below required {minimum}")
    for section, exacts in config.get("exact_gates", {}).items():
        measurements = result.get(section, {})
        for name, expected_value in exacts.items():
            if measurements.get(name) != expected_value:
                failures.append(f"{section}.{name}: expected {expected_value}")
    if not result.get("passed"):
        failures.append("unified eval suite failed")
    return {"passed": not failures, "baseline_id": config["baseline_id"], "failures": failures}


def run_gated_evals(root: Path) -> dict[str, Any]:
    from .evals import run_all

    config_path = root / "evals/gates/w6_baseline_v1.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    result = run_all(root)
    return evaluate_gates(result, config)
