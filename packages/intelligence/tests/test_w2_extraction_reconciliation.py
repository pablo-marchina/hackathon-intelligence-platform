from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from hackathon_intelligence.changes import build_canonical_change
from hackathon_intelligence.conflicts import build_conflict_record
from hackathon_intelligence.extraction_pipeline import extract_with_llm_fallback
from hackathon_intelligence.extractors import extract_structured_event, missing_core_fields
from hackathon_intelligence.llm import validate_llm_fallback
from hackathon_intelligence.reconciliation import CandidateObservation, authority_for, policy_for_field, select_canonical
from hackathon_intelligence.taxonomy import CANONICAL_EVENT_TYPES, map_event_type, map_track, map_technology


def test_deterministic_extractor_is_evidence_first_and_null_safe() -> None:
    now = datetime.now(UTC)
    event = extract_structured_event({"name":"Hack X","starts_at_raw":"2026-10-01T10:00:00Z","prize":"$5,000"}, observed_at=now)
    assert event.name == "Hack X"
    assert event.start_at.isoformat() == "2026-10-01T10:00:00+00:00"
    assert event.prize_text == "$5,000"
    assert event.location_name is None
    assert all(item.method == "API" and item.confidence > .9 and item.evidence_locator for item in event.evidence)
    assert "location_name" in missing_core_fields(event)


@pytest.mark.asyncio
async def test_llm_fallback_only_receives_missing_fields_and_requires_evidence() -> None:
    seen = {}
    async def fake_llm(**kwargs):
        seen.update(kwargs)
        return {"fields":{"location_name":{"value":"São Paulo","evidence_text":"Location: São Paulo","evidence_locator":"body:42-61","confidence":.91}}}
    event = await extract_with_llm_fallback(
        {"name":"Hack X","starts_at_raw":"2026-10-01T10:00:00Z"},
        source_text="Hack X. Location: São Paulo", source_url="https://x.test", observed_at=datetime.now(UTC),
        llm_call=fake_llm, model_version="test-model-v1", prompt_version="extract-v3",
    )
    assert "name" not in seen["requested_fields"]
    assert event.name == "Hack X" and event.location_name == "São Paulo"
    llm_evidence = next(x for x in event.evidence if x.field_path == "location_name")
    assert llm_evidence.method == "LLM"
    assert "model=test-model-v1" in llm_evidence.method_version
    with pytest.raises(ValueError):
        validate_llm_fallback({"fields":{"location_name":{"value":"Paris","confidence":.9}}}, requested_fields=("location_name",), observed_at=datetime.now(UTC), model_version="m", prompt_version="p")


def test_taxonomy_preserves_source_labels_and_versions() -> None:
    event = map_event_type("Hack Day")
    track = map_track("IA")
    tech = map_technology("postgres")
    assert event.canonical_label in CANONICAL_EVENT_TYPES and event.canonical_label == "hackathon"
    assert track.source_label == "IA" and track.canonical_label == "Artificial Intelligence"
    assert tech.source_label == "postgres" and tech.canonical_label == "PostgreSQL"
    assert all(x.version and 0 <= x.confidence <= 1 for x in (event, track, tech))


def test_field_specific_reconciliation_prefers_official_rules_for_sensitive_fields() -> None:
    now = datetime.now(UTC)
    official = CandidateObservation(1,"2026-10-10",authority_for("registration_close_at","official_rules"),.97,1,.5,now-timedelta(days=2))
    aggregator = CandidateObservation(2,"2026-10-12",authority_for("registration_close_at","aggregator"),.99,1,.9,now)
    policy = policy_for_field("registration_close_at")
    winner, _ = select_canonical([aggregator, official], policy, now=now)
    assert winner.observation_id == 1
    assert policy.version == "reconcile-v2-rules"


def test_important_conflict_records_keep_all_observation_ids() -> None:
    samples = {
        "registration_close_at": [(1,"2026-10-01"),(2,"2026-10-02")],
        "prize_pool.normalized_usd_amount": [(3,10000),(4,15000)],
        "eligibility": [(5,"students only"),(6,"open to all")],
        "location": [(7,"Boston"),(8,"Cambridge")],
    }
    for field, obs in samples.items():
        conflict = build_conflict_record(field, obs)
        assert conflict is not None
        assert conflict.observation_ids == tuple(x[0] for x in obs)
        assert conflict.state == "OPEN"


def test_canonical_change_history_semantic_diff_and_types() -> None:
    equivalent = build_canonical_change("official_url","https://www.example.com/?utm_source=x","https://example.com/",previous_selection_id=1,new_selection_id=2)
    assert not equivalent.meaningful
    cases = [
        ("registration_close_at","DEADLINE"),("prize_pool.normalized_usd_amount","PRIZE"),("status","STATUS"),("location","VENUE"),("eligibility","ELIGIBILITY"),("name","GENERIC")
    ]
    for field, expected_type in cases:
        change = build_canonical_change(field,"old","new",previous_selection_id=10,new_selection_id=11)
        assert change.change_type == expected_type
        assert change.meaningful
        assert change.previous_selection_id == 10 and change.new_selection_id == 11
