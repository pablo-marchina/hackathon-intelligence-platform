#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    # File-path invocations do not automatically expose sibling package srcs.
    from _local_packages import add_repo_packages

    add_repo_packages(Path(__file__).resolve().parents[1])
    from hackathon_operations.gates import run_gated_evals

    report = run_gated_evals(Path(__file__).resolve().parents[1])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
