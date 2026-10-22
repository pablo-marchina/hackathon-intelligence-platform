from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Result(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class UserEligibilityProfile:
    age: int | None = None
    country_code: str | None = None
    is_student: bool | None = None
    education_level: str | None = None
    is_professional: bool | None = None
    planned_team_size: int | None = None


@dataclass(frozen=True)
class EventEligibility:
    minimum_age: int | None = None
    maximum_age: int | None = None
    student_required: bool | None = None
    professional_allowed: bool | None = None
    high_school_allowed: bool | None = None
    undergraduate_allowed: bool | None = None
    graduate_allowed: bool | None = None
    individual_allowed: bool | None = None
    team_required: bool | None = None
    minimum_team_size: int | None = None
    maximum_team_size: int | None = None
    eligible_worldwide: bool | None = None
    allowed_countries: frozenset[str] = field(default_factory=frozenset)
    blocked_countries: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RuleRationale:
    rule: str
    outcome: str
    reason: str


@dataclass(frozen=True)
class EligibilityEvaluation:
    result: Result
    hard_failures: tuple[str, ...]
    unknowns: tuple[str, ...]
    passes: tuple[str, ...]
    rules: tuple[RuleRationale, ...] = ()


def evaluate(profile: UserEligibilityProfile, event: EventEligibility) -> EligibilityEvaluation:
    failures: list[str] = []
    unknowns: list[str] = []
    passes: list[str] = []
    rules: list[RuleRationale] = []

    def add(rule: str, outcome: str, reason: str) -> None:
        rules.append(RuleRationale(rule, outcome, reason))
        (failures if outcome == "FAIL" else unknowns if outcome == "UNKNOWN" else passes).append(
            reason
        )

    if event.minimum_age is not None:
        if profile.age is None:
            add("minimum_age", "UNKNOWN", "age is required to verify minimum age")
        elif profile.age < event.minimum_age:
            add("minimum_age", "FAIL", f"minimum age is {event.minimum_age}")
        else:
            add("minimum_age", "PASS", "minimum age satisfied")
    if event.maximum_age is not None:
        if profile.age is None:
            add("maximum_age", "UNKNOWN", "age is required to verify maximum age")
        elif profile.age > event.maximum_age:
            add("maximum_age", "FAIL", f"maximum age is {event.maximum_age}")
        else:
            add("maximum_age", "PASS", "maximum age satisfied")
    if event.student_required is True:
        if profile.is_student is None:
            add("student_required", "UNKNOWN", "student status is required")
        elif not profile.is_student:
            add("student_required", "FAIL", "event requires student status")
        else:
            add("student_required", "PASS", "student requirement satisfied")
    if event.professional_allowed is False:
        if profile.is_professional is None:
            add("professional_allowed", "UNKNOWN", "professional status is required")
        elif profile.is_professional:
            add("professional_allowed", "FAIL", "professionals are not allowed")
        else:
            add("professional_allowed", "PASS", "professional restriction satisfied")
    education = (profile.education_level or "").upper().replace("-", "_").replace(" ", "_")
    edu_rule = {
        "HIGH_SCHOOL": event.high_school_allowed,
        "UNDERGRADUATE": event.undergraduate_allowed,
        "GRADUATE": event.graduate_allowed,
    }.get(education)
    if profile.is_student is True and education:
        if edu_rule is False:
            add("education_level", "FAIL", f"{education.lower()} students are not allowed")
        elif edu_rule is True:
            add("education_level", "PASS", f"{education.lower()} students are allowed")
    country = profile.country_code.upper() if profile.country_code else None
    allowed = {x.upper() for x in event.allowed_countries}
    blocked = {x.upper() for x in event.blocked_countries}
    if country and country in blocked:
        add("country", "FAIL", f"country {country} is explicitly blocked")
    elif allowed:
        if country is None:
            add("country", "UNKNOWN", "country is required to verify geographic eligibility")
        elif country not in allowed:
            add("country", "FAIL", f"country {country} is not in the allowlist")
        else:
            add("country", "PASS", "country allowlist satisfied")
    elif event.eligible_worldwide is True:
        add("country", "PASS", "event is worldwide")
    if event.team_required is True:
        if profile.planned_team_size is None:
            add("team_required", "UNKNOWN", "team size is required")
        elif profile.planned_team_size <= 1:
            add("team_required", "FAIL", "event requires a team")
        else:
            add("team_required", "PASS", "team requirement satisfied")
    if profile.planned_team_size is not None:
        size = profile.planned_team_size
        if event.minimum_team_size is not None:
            if size < event.minimum_team_size:
                add("minimum_team_size", "FAIL", f"minimum team size is {event.minimum_team_size}")
            else:
                add("minimum_team_size", "PASS", "minimum team size satisfied")
        if event.maximum_team_size is not None:
            if size > event.maximum_team_size:
                add("maximum_team_size", "FAIL", f"maximum team size is {event.maximum_team_size}")
            else:
                add("maximum_team_size", "PASS", "maximum team size satisfied")
        if size == 1 and event.individual_allowed is False:
            add("individual_allowed", "FAIL", "individual participation is not allowed")
    result = Result.INELIGIBLE if failures else Result.UNKNOWN if unknowns else Result.ELIGIBLE
    return EligibilityEvaluation(
        result, tuple(failures), tuple(unknowns), tuple(passes), tuple(rules)
    )
