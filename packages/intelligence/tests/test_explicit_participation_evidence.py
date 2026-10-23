"""Source-stated eligibility and awards remain evidence, not invented rules."""

from __future__ import annotations

import pytest
from hackathon_intelligence.incremental import prepare_event
from hackathon_intelligence.structured_projection import project_structured_metadata


def payload(**extra: object) -> dict[str, object]:
    return {
        "name": "Regional Student Hackathon 2027",
        "source_key": "hackclub_programs",
        "official_url": "https://organizer.example/2027",
        "eligibility_raw": "Open to 13+ student builders, solo or in teams.",
        "metadata": {"description": "Ship an app!", "status": "upcoming"},
        **extra,
    }


def test_explicit_eligibility_creates_provenance_not_guessed_eligibility_booleans() -> None:
    candidate = prepare_event(payload(), source_key="hackclub_programs")
    assert candidate is not None
    statement = candidate.fields["participation_requirements"]
    assert statement[0] == "Open to 13+ student builders, solo or in teams."
    assert statement[1] == statement[0]
    assert statement[2] == "API"
    assert statement[3] == "source_event_ref.payload.eligibility_raw"
    for ungrounded in (
        "student_required",
        "minimum_age",
        "maximum_age",
        "eligible_worldwide",
        "individual_allowed",
        "minimum_team_size",
        "maximum_team_size",
        "prize_pool_usd",
        "registration_close_at",
    ):
        assert ungrounded not in candidate.fields


@pytest.mark.parametrize("invalid", ["", "  ", None, False, 123, {}, []])
def test_invalid_eligibility_values_do_not_become_rules(invalid: object) -> None:
    candidate = prepare_event(payload(eligibility_raw=invalid), source_key="hackclub_programs")
    assert candidate is not None
    assert "participation_requirements" not in candidate.fields


def test_source_text_is_bounded_before_becoming_a_selected_field() -> None:
    candidate = prepare_event(payload(eligibility_raw="x" * 5000), source_key="hackclub_programs")
    assert candidate is not None
    assert len(candidate.fields["participation_requirements"][1]) == 2000


@pytest.mark.parametrize("invalid", [None, "", 123, {"award": "Cash"}, [], False])
def test_nonstring_prize_source_values_are_not_promoted(invalid: object) -> None:
    found = project_structured_metadata(
        {
            "metadata": {
                "extraction_method": "schema_org_json_ld",
                "prize_details_text": invalid,
            }
        }
    )
    assert all(item.field_path != "prize_details_text" for item in found)


def test_award_statement_does_not_mint_prize_currency_or_amount() -> None:
    found = {
        item.field_path: item
        for item in project_structured_metadata(
            {
                "metadata": {
                    "extraction_method": "schema_org_json_ld",
                    "prize_details_text": "Grand prize: custom 3D printed trophy",
                }
            }
        )
    }
    assert found["prize_details_text"].normalized == "Grand prize: custom 3D printed trophy"
    assert found["prize_details_text"].evidence_locator.endswith("prize_details_text")
    assert "prize_pool_usd" not in found
    assert "normalized_usd_amount" not in found
    assert "is_free" not in found
