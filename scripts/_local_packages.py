"""Allow the repository's standalone verification scripts to run from a checkout.

Production entry points should use the installed Python package. This helper is
only for tools invoked by file path (``python scripts/foo.py``), for which
Python does not automatically include the repo's local ``src`` directories.
"""
from __future__ import annotations

import sys
from pathlib import Path


PACKAGE_SRCS = (
    "apps/api/src", "apps/worker/src", "packages/shared/src",
    "packages/source_sdk/src", "packages/intelligence/src",
    "packages/discovery/src", "packages/recommendation/src",
    "packages/alerts/src", "packages/operations/src",
)


def add_repo_packages(root: Path) -> None:
    for relative_path in PACKAGE_SRCS:
        src = str(root / relative_path)
        if src not in sys.path:
            sys.path.insert(0, src)
