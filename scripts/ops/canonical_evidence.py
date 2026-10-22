"""Minimal, explicitly synthetic PostgreSQL E2E receipts for CI.

Never mix synthetic fixtures with production collection metrics. Receipts contain
only aggregate outcomes, a fake-source identifier, and the database revision;
no URLs, credentials, raw documents or actual user data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

OUTCOMES = ("CREATED", "LINKED", "UPDATED", "REJECTED")


def _counts(values: object) -> dict[str, int]:
    if not isinstance(values, dict) or set(values) != set(OUTCOMES):
        raise ValueError("expected all four canonical outcomes")
    counts: dict[str, int] = {}
    for key in OUTCOMES:
        value = values[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("canonical outcome counts must be nonnegative integers")
        counts[key] = value
    return counts


def controlled_receipt(
    *, source: str, first: dict[str, Any], repeat: dict[str, Any]
) -> dict[str, Any]:
    """Verify an isolated fake-HTTP crawl genuinely created exactly one canonical.

    This is a fixture-specific acceptance assertion, not a claim of live events.
    """
    if (
        not source.startswith("ci_controlled_")
        or first.get("source") != source
        or repeat.get("source") != source
    ):
        raise ValueError("only isolated controlled CI fixture sources are accepted")
    if first.get("status") != "SUCCEEDED" or repeat.get("status") != "SUCCEEDED":
        raise ValueError("controlled ingestion did not succeed")
    original = _counts(first.get("run_outcomes"))
    later = _counts(repeat.get("run_outcomes"))
    if original != {"CREATED": 1, "LINKED": 0, "UPDATED": 0, "REJECTED": 0}:
        raise ValueError("first crawl must create exactly one canonical")
    if (
        any(later.values())
        or first.get("catalog_delta_observed") != 1
        or repeat.get("catalog_delta_observed") != 0
    ):
        raise ValueError("repeat must not produce new canonical outcomes")
    if repeat.get("pages_fetched") != 0:
        raise ValueError("repeat unexpectedly fetched pages")
    return {
        "evidence_kind": "SYNTHETIC_FAKE_HTTP_POSTGRESQL_E2E",
        "real_hackathons_discovered": None,
        "source": source,
        "crawl_run_id": first.get("crawl_run_id"),
        "first_run_outcomes": original,
        "repeat_run_outcomes": later,
        "catalog_delta_first": 1,
        "catalog_delta_repeat": 0,
        "assertions": ["fetch_to_canonical", "idempotent_replay", "original_crawl_attribution"],
        "note": "Fixture uses .invalid domain and synthetic events; not evidence of external collection.",
    }


def write_ci_receipt(report: dict[str, Any], *, name: str) -> Path | None:
    """Opt-in local artifact; does not upload, alter the DB, or send requests."""
    directory = os.environ.get("HIP_CI_EVIDENCE_DIR")
    if not directory:
        return None
    if not name.isascii() or not name.replace("_", "").isalnum():
        raise ValueError("invalid fixture name")
    path = Path(directory) / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def multisource_receipt(snapshot: dict[str, Any], source_ids: dict[str, str]) -> dict[str, Any]:
    """Validate cross-source CREATE/LINK/UPDATE/REJECT and distinct attribution."""
    if set(source_ids) != {"a", "b", "restricted"}:
        raise ValueError("expected a, b and restricted fixture sources")
    rows = {row["source_id"]: row for row in snapshot.get("sources", [])}
    try:
        a, b, denied = (rows[source_ids[key]] for key in ("a", "b", "restricted"))
    except (KeyError, TypeError) as exc:
        raise ValueError("missing CI source attribution") from exc
    for row in (a, b, denied):
        _counts({key.upper(): row[key.lower()] for key in OUTCOMES})
    if (a["created"], a["updated"], a["rejected"]) != (1, 1, 1):
        raise ValueError("source a did not create/update/reject as expected")
    if (b["created"], b["linked"], b["rejected"]) != (1, 1, 0):
        raise ValueError("source b did not create/link as expected")
    if denied["processed_versions"] != 0:
        raise ValueError("restricted source produced a canonical outcome")
    return {
        "evidence_kind": "SYNTHETIC_FAKE_SOURCES_POSTGRESQL_E2E",
        "real_hackathons_discovered": None,
        "source_outcomes": {
            key: {outcome: int(rows[source_ids[key]][outcome.lower()]) for outcome in OUTCOMES}
            for key in ("a", "b", "restricted")
        },
        "distinct_new_canonicals": 2,
        "attributed_reference_outcomes": 5,
        "assertions": [
            "cross_source_dedup",
            "version_update",
            "rejection",
            "restricted_source_denied",
        ],
        "note": "Transaction is rolled back after fixture assertions; not a live catalog count.",
    }
