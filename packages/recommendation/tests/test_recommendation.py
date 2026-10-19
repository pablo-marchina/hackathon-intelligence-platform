from hackathon_recommendation.eligibility import EventEligibility, Result, UserEligibilityProfile, evaluate
from hackathon_recommendation.ranking import EventSignals, UserPreferences, recommend


def test_hard_failure_is_not_hidden_by_match_score() -> None:
    eligibility = evaluate(UserEligibilityProfile(age=17, country_code="BR"), EventEligibility(minimum_age=18))
    assert eligibility.result is Result.INELIGIBLE
    rec = recommend(eligibility.result, UserPreferences(tracks=frozenset({"AI"})), EventSignals(tracks=frozenset({"AI"})))
    assert rec.score is None
    assert "hard eligibility failure" in rec.negative_reasons


def test_unknown_is_distinct_from_ineligible() -> None:
    eligibility = evaluate(UserEligibilityProfile(country_code="BR"), EventEligibility(minimum_age=18))
    assert eligibility.result is Result.UNKNOWN


def test_explainable_soft_match() -> None:
    rec = recommend(
        Result.ELIGIBLE,
        UserPreferences(tracks=frozenset({"AI", "FinTech"}), formats=frozenset({"ONLINE"}), free_only=True),
        EventSignals(tracks=frozenset({"AI"}), format="ONLINE", is_free=True, quality_score=90),
    )
    assert rec.score is not None and rec.score > 70
    assert any("AI" in reason for reason in rec.positive_reasons)
