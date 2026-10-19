from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    from _local_packages import add_repo_packages

    add_repo_packages(Path(__file__).resolve().parents[1])
    from hackathon_operations.evals import run_all

    result = run_all(Path(__file__).resolve().parents[1])
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
