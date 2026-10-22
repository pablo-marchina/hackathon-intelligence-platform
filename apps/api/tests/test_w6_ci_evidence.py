import json

import pytest

from scripts.ops.canonical_evidence import (controlled_receipt, multisource_receipt, write_ci_receipt)


def _runs():
    src = "ci_controlled_fixture"
    first = {"source": src, "status": "SUCCEEDED", "crawl_run_id": "run-fixture",
             "run_outcomes": {"CREATED": 1, "LINKED": 0, "UPDATED": 0, "REJECTED": 0},
             "catalog_delta_observed": 1, "pages_fetched": 2}
    repeat = {**first, "crawl_run_id": "run-repeat", "pages_fetched": 0,
              "catalog_delta_observed": 0,
              "run_outcomes": {"CREATED": 0, "LINKED": 0, "UPDATED": 0, "REJECTED": 0}}
    return src, first, repeat


def test_receipt_is_explicitly_synthetic():
    src, first, repeat = _runs()
    report = controlled_receipt(source=src, first=first, repeat=repeat)
    assert report["evidence_kind"].startswith("SYNTHETIC")
    assert report["real_hackathons_discovered"] is None
    assert report["first_run_outcomes"]["CREATED"] == 1
    assert report["catalog_delta_repeat"] == 0


@pytest.mark.parametrize("changed", [
    {"catalog_delta_observed": 1},
    {"pages_fetched": 2},
    {"status": "FAILED"},
    {"run_outcomes": {"CREATED": 2, "LINKED": 0, "UPDATED": 0, "REJECTED": 0}},
    {"run_outcomes": {"CREATED": True, "LINKED": 0, "UPDATED": 0, "REJECTED": 0}},
])
def test_rejects_false_pass_on_repeat(changed):
    src, first, repeat = _runs()
    repeat.update(changed)
    with pytest.raises(ValueError):
        controlled_receipt(source=src, first=first, repeat=repeat)


def test_receipt_is_opt_in_and_no_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("HIP_CI_EVIDENCE_DIR", raising=False)
    assert write_ci_receipt({"created": 1}, name="controlled_ingestion") is None
    monkeypatch.setenv("HIP_CI_EVIDENCE_DIR", str(tmp_path))
    path = write_ci_receipt({"evidence_kind": "SYNTHETIC", "created": 1}, name="controlled_ingestion")
    assert path is not None and json.loads(path.read_text())["created"] == 1
    with pytest.raises(ValueError):
        write_ci_receipt({}, name="../unsafe")


def test_source_must_be_ci_fixture():
    src, first, repeat = _runs()
    with pytest.raises(ValueError):
        controlled_receipt(source="public_real_site", first=first, repeat=repeat)


def test_multisource_cross_source_dedup_receipt():
    def entry(source_id, created=0, linked=0, updated=0, rejected=0):
        return dict(source_id=source_id, created=created, linked=linked,
                    updated=updated, rejected=rejected,
                    processed_versions=created+linked+updated+rejected)
    fixture = {"sources": [entry("a", 1, 0, 1, 1),
                           entry("b", 1, 1), entry("blocked")]}
    result = multisource_receipt(fixture, {"a": "a", "b": "b", "restricted": "blocked"})
    assert result["distinct_new_canonicals"] == 2
    assert result["source_outcomes"]["b"]["LINKED"] == 1
    assert result["real_hackathons_discovered"] is None
    fixture["sources"][-1]["created"] = 1
    fixture["sources"][-1]["processed_versions"] = 1
    with pytest.raises(ValueError):
        multisource_receipt(fixture, {"a": "a", "b": "b", "restricted": "blocked"})
