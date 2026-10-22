from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from hackathon_sources.models import SourceDefinition

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "source_coverage_report", ROOT / "scripts/source_coverage_report.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def definition(key, permission="REVIEW_REQUIRED", mode="STATIC_HTML", detail=True):
    return SourceDefinition(
        key=key,
        name=key,
        domain=f"{key}.example",
        source_family=key,
        source_type="official",
        ingestion_mode=mode,
        permission_status=permission,
        detail_extraction_enabled=detail,
        seed_urls=[f"https://{key}.example/event"],
    )


def test_audit_does_not_count_restricted_or_review_sources_as_eligible():
    data = MODULE.audit(
        [
            definition("allowed", "ALLOWED"),
            definition("review"),
            definition("restricted", "RESTRICTED"),
            definition("blocked", "ALLOWED", "DISCOVERY_ONLY"),
        ]
    )
    assert data["source_count"] == 4
    assert data["fetch_eligible_by_configuration"] == 1
    assert data["requires_permission_review"] == 2
    assert data["live_coverage_verified"] is False
    assert not data["validation_errors"]


def test_duplicate_identity_is_rejected():
    data = MODULE.audit([definition("same"), definition("same")])
    assert len(data["validation_errors"]) == 2


def test_source_coverage_cli_is_offline_and_separates_eligibility():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/source_coverage_report.py"), "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    report = json.loads(proc.stdout)
    assert report["source_count"] >= 20
    assert report["fetch_eligible_by_configuration"] == 4
    assert report["requires_permission_review"] >= 10
    assert not report["validation_errors"]
