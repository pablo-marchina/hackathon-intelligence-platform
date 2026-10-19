from datetime import date

from hackathon_recommendation.eligibility import EventEligibility, Result, UserEligibilityProfile, evaluate
from hackathon_recommendation.preferences import PreferenceProfile
from hackathon_recommendation.ranking import EventSignals, UserPreferences, recommend


def test_preference_fingerprint_is_stable_and_missing_differs_from_false() -> None:
    a=PreferenceProfile.from_mapping({"preferred_formats":["ONLINE"],"free_only":None})
    b=PreferenceProfile.from_mapping({"preferred_formats":["ONLINE"],"free_only":False})
    c=PreferenceProfile.from_mapping({"preferred_formats":["ONLINE"],"free_only":None})
    assert a.fingerprint()==c.fingerprint()
    assert a.fingerprint()!=b.fingerprint()


def test_eligibility_covers_geo_student_professional_team_and_age() -> None:
    profile=UserEligibilityProfile(age=20,country_code="BR",is_student=True,education_level="UNDERGRADUATE",is_professional=False,planned_team_size=3)
    event=EventEligibility(minimum_age=18,maximum_age=30,student_required=True,professional_allowed=False,undergraduate_allowed=True,team_required=True,minimum_team_size=2,maximum_team_size=4,allowed_countries=frozenset({"BR","US"}))
    result=evaluate(profile,event)
    assert result.result is Result.ELIGIBLE
    assert len(result.rules)>=6


def test_ineligible_never_gets_score_and_same_snapshot_is_reproducible() -> None:
    prefs=UserPreferences(tracks=frozenset({"ai"}),formats=frozenset({"ONLINE"}),available_from=date(2026,9,1))
    event=EventSignals(tracks=frozenset({"ai"}),format="ONLINE",quality_score=90,start_date=date(2026,10,1))
    a=recommend(Result.ELIGIBLE,prefs,event)
    b=recommend(Result.ELIGIBLE,prefs,event)
    blocked=recommend(Result.INELIGIBLE,prefs,event)
    assert a.input_hash==b.input_hash and a.score==b.score
    assert blocked.score is None
