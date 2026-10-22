"""Gate *new* mypy errors while keeping legacy debt measurable and visible.

This is intentionally NOT a claim that the codebase is type-clean. Errors
present in the reviewed baseline may remain, but new signatures or increased
counts fail CI. Run against the same source roots as the original gate.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs/quality/mypy-existing-errors.json"
SOURCE_ROOTS = ["apps/api/src", "apps/worker/src", "packages/shared/src"]
ERROR = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+)(?::\d+)?: error: "
    r"(?P<message>.+?)  \[(?P<code>[\w-]+)\]$"
)


def read_errors(output: str) -> Counter[tuple[str, str, str]]:
    errors: Counter[tuple[str, str, str]] = Counter()
    for line in output.splitlines():
        match = ERROR.match(line)
        if match:
            errors[(match["path"], match["code"], match["message"])] += 1
    return errors


def verify_baseline() -> int:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    allowed = Counter(
        {
            (entry["path"], entry["code"], entry["message"]): entry["count"]
            for entry in baseline["entries"]
        }
    )
    assert sum(allowed.values()) == baseline["total_errors"], "Invalid type debt baseline"

    command = [sys.executable, "-m", "mypy", *SOURCE_ROOTS]
    process = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    output = process.stdout + ("\n" + process.stderr if process.stderr else "")
    print(output, end="" if output.endswith("\n") else "\n")
    if process.returncode not in (0, 1):
        print("mypy crashed or has a configuration problem", file=sys.stderr)
        return 2

    current = read_errors(output)
    if process.returncode == 1 and not current:
        print("mypy failed without parseable diagnostic errors; refusing to pass", file=sys.stderr)
        return 2

    regressions = current - allowed
    resolved = allowed - current
    print(
        f"mypy baseline: {sum(allowed.values())} known errors; "
        f"{sum(current.values())} remaining; {sum(resolved.values())} resolved; "
        f"{sum(regressions.values())} new."
    )
    if regressions:
        print("NEW TYPING ERRORS (fail CI):", file=sys.stderr)
        for (path, code, message), count in sorted(regressions.items())[:40]:
            print(f"  {count} x {path} [{code}] {message}", file=sys.stderr)
        return 1

    print("PASS: no regressions in mypy errors. Known errors are still technical debt.")
    return 0


if __name__ == "__main__":
    sys.exit(verify_baseline())
