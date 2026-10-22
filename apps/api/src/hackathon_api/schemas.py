from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

API_VERSION = "v1"
FILTER_SCHEMA_VERSION = "w4-filter-v1"


class PageMeta(BaseModel):
    api_version: str = API_VERSION
    filter_schema_version: str = FILTER_SCHEMA_VERSION
    total_hint: int | None = None


class LocationSummary(BaseModel):
    venue: str | None = None
    city: str | None = None
    state_region: str | None = None
    country: str | None = None
    country_code: str | None = None
    timezone: str | None = None


class OrganizationSummary(BaseModel):
    id: UUID
    slug: str
    name: str
    role: str | None = None
    website: str | None = None
    logo_url: str | None = None


class TaxonomyItem(BaseModel):
    id: UUID
    slug: str
    name: str
    category: str | None = None


class PrizeSummary(BaseModel):
    title: str | None = None
    placement: str | None = None
    normalized_usd_amount: float | None = None
    original_amount: float | None = None
    original_currency: str | None = None
    non_cash_description: str | None = None
    estimated: bool | None = None


class EligibilitySummary(BaseModel):
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
    eligibility_raw_text: str | None = None


class PhaseSummary(BaseModel):
    name: str
    phase_type: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    description: str | None = None


class ResourceSummary(BaseModel):
    resource_type: str
    name: str
    description: str | None = None
    url: str | None = None


class JudgingCriterionSummary(BaseModel):
    name: str
    description: str | None = None
    weight: float | None = None


class ExternalLinkSummary(BaseModel):
    link_type: str
    url: str
    is_official: bool | None = None


class RequirementSummary(BaseModel):
    requirement_type: str
    name: str | None = None
    description: str
    required: bool


class CanonicalChangeSummary(BaseModel):
    field_path: str
    change_type: str
    old_value: object | None = None
    new_value: object | None = None
    detected_at: datetime


class SourceEvidenceSummary(BaseModel):
    source_name: str | None = None
    field_path: str
    observed_at: datetime
    confidence: float | None = None
    evidence_text: str | None = None


class HackathonListItem(BaseModel):
    id: UUID
    slug: str
    name: str
    short_description: str | None = None
    event_type: str
    status: str
    format: str
    start_at: datetime | None = None
    end_at: datetime | None = None
    registration_open_at: datetime | None = None
    registration_close_at: datetime | None = None
    city: str | None = None
    state_region: str | None = None
    country_code: str | None = None
    prize_usd: float | None = None
    is_free: bool | None = None
    quality_score: float | None = None
    source_count: int
    last_verified_at: datetime | None = None
    rank_score: float | None = None


class HackathonDetail(HackathonListItem):
    subtitle: str | None = None
    full_description: str | None = None
    registration_url: str | None = None
    application_url: str | None = None
    submission_url: str | None = None
    official_url: str | None = None
    eligible_worldwide: bool | None = None
    location: LocationSummary | None = None
    organizations: list[OrganizationSummary] = Field(default_factory=list)
    tracks: list[TaxonomyItem] = Field(default_factory=list)
    technologies: list[TaxonomyItem] = Field(default_factory=list)
    prizes: list[PrizeSummary] = Field(default_factory=list)
    eligibility: EligibilitySummary | None = None
    phases: list[PhaseSummary] = Field(default_factory=list)
    resources: list[ResourceSummary] = Field(default_factory=list)
    judging: list[JudgingCriterionSummary] = Field(default_factory=list)
    requirements: list[RequirementSummary] = Field(default_factory=list)
    links: list[ExternalLinkSummary] = Field(default_factory=list)
    recent_changes: list[CanonicalChangeSummary] = Field(default_factory=list)
    evidence: list[SourceEvidenceSummary] = Field(default_factory=list)
    series_slug: str | None = None
    series_name: str | None = None
    edition_label: str | None = None
    edition_year: int | None = None
    api_version: str = API_VERSION


RegistrationState = Literal["OPEN", "UPCOMING", "CLOSED", "UNKNOWN"]
SortMode = Literal["relevance", "deadline", "start", "quality"]


class HackathonFilters(BaseModel):
    q: str | None = Field(default=None, max_length=300)
    status: list[str] = Field(default_factory=list)
    event_type: list[str] = Field(default_factory=list)
    format: list[str] = Field(default_factory=list)
    registration_state: list[RegistrationState] = Field(default_factory=list)
    country_code: list[str] = Field(default_factory=list)
    city: str | None = Field(default=None, max_length=120)
    start_from: datetime | None = None
    start_to: datetime | None = None
    deadline_from: datetime | None = None
    deadline_to: datetime | None = None
    free_only: bool = False
    min_prize_usd: float | None = Field(default=None, ge=0)
    max_prize_usd: float | None = Field(default=None, ge=0)
    track: list[str] = Field(default_factory=list)
    technology: list[str] = Field(default_factory=list)
    organizer: list[str] = Field(default_factory=list)
    sponsor: list[str] = Field(default_factory=list)
    team_size: int | None = Field(default=None, ge=1, le=1000)
    student_required: bool | None = None
    individual_allowed: bool | None = None
    eligible_country: str | None = Field(default=None, min_length=2, max_length=2)
    limit: int = Field(default=30, ge=1, le=100)
    cursor: str | None = None
    sort: SortMode = "relevance"

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country_codes(cls, value: object) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if value is None:
            return []
        return [str(item).upper() for item in value if item]

    @field_validator("eligible_country")
    @classmethod
    def normalize_eligible_country(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class HackathonPage(BaseModel):
    items: list[HackathonListItem]
    next_cursor: str | None
    meta: PageMeta = Field(default_factory=PageMeta)


class OrganizationDetail(BaseModel):
    id: UUID
    slug: str
    name: str
    organization_type: str | None = None
    website: str | None = None
    logo_url: str | None = None
    description: str | None = None
    country_code: str | None = None
    industry: str | None = None
    roles: list[str] = Field(default_factory=list)
    upcoming: list[HackathonListItem] = Field(default_factory=list)
    past: list[HackathonListItem] = Field(default_factory=list)
    api_version: str = API_VERSION


class EventSeriesDetail(BaseModel):
    id: UUID
    slug: str
    name: str
    official_url: str | None = None
    editions: list[HackathonListItem] = Field(default_factory=list)
    api_version: str = API_VERSION


class SourceListItem(BaseModel):
    id: UUID
    name: str
    domain: str | None = None
    source_family: str | None = None
    source_type: str | None = None
    ingestion_mode: str
    health: str
    trust_score: float | None = None
    last_success_at: datetime | None = None


class NaturalLanguageParseRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class NaturalLanguageParseResponse(BaseModel):
    original_query: str
    filters: HackathonFilters
    interpreted: list[str] = Field(default_factory=list)
    unsupported_constraints: list[str] = Field(default_factory=list)
    remaining_text: str | None = None
    schema_version: str = FILTER_SCHEMA_VERSION


class AuthUser(BaseModel):
    id: UUID
    email: str | None = None
    display_name: str | None = None
    timezone: str | None = None
    country_code: str | None = None
    locale: str | None = None


class UserProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    timezone: str | None = Field(default=None, max_length=100)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    locale: str | None = Field(default=None, max_length=16)
    preferred_formats: list[str] = Field(default_factory=list)
    preferred_country_codes: list[str] = Field(default_factory=list)
    travel_country_codes: list[str] = Field(default_factory=list)
    student_status: str | None = None
    experience_level: str | None = None
    team_preference: str | None = None
    minimum_prize_usd: float | None = Field(default=None, ge=0)
    available_from: date | None = None
    available_until: date | None = None


UserEventState = Literal[
    "SAVED",
    "INTERESTED",
    "APPLYING",
    "APPLIED",
    "ACCEPTED",
    "PARTICIPATING",
    "SUBMITTED",
    "COMPLETED",
    "NOT_INTERESTED",
]


class UserHackathonStateUpdate(BaseModel):
    state: UserEventState


class UserHackathonStateRecord(BaseModel):
    hackathon: HackathonListItem
    state: UserEventState
    updated_at: datetime


class UserLibraryPage(BaseModel):
    items: list[UserHackathonStateRecord]
    next_cursor: str | None = None
    api_version: str = API_VERSION


# W5 — recommendation and alerts
class PreferenceUpdate(BaseModel):
    preferred_formats: list[str] | None = None
    preferred_country_codes: list[str] | None = None
    travel_country_codes: list[str] | None = None
    student_status: str | None = None
    experience_level: str | None = None
    team_preference: str | None = None
    minimum_prize_usd: float | None = Field(default=None, ge=0)
    available_from: date | None = None
    available_until: date | None = None
    preferred_track_slugs: list[str] | None = None
    preferred_technology_slugs: list[str] | None = None
    hard_constraints: dict[str, object] | None = None
    soft_preferences: dict[str, object] | None = None


class PreferenceView(BaseModel):
    user_id: UUID
    preference_version: int
    preference_hash: str
    schema_version: str = "w5-preferences-v1"
    snapshot: dict[str, object]
    updated_at: datetime


class EligibilityView(BaseModel):
    result: Literal["ELIGIBLE", "INELIGIBLE", "UNKNOWN"]
    hard_failures: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    passes: list[str] = Field(default_factory=list)
    rules_version: str


class RecommendationItem(BaseModel):
    hackathon: HackathonListItem
    eligibility: EligibilityView
    score: float | None = None
    positive_reasons: list[str] = Field(default_factory=list)
    negative_reasons: list[str] = Field(default_factory=list)
    component_scores: dict[str, float] = Field(default_factory=dict)
    model_version: str
    input_hash: str
    preference_version: int


class RecommendationPage(BaseModel):
    items: list[RecommendationItem]
    generated_at: datetime


class SavedSearchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    original_query: str | None = Field(default=None, max_length=500)
    filters: dict[str, object]
    notifications_enabled: bool = True
    filter_schema_version: str = FILTER_SCHEMA_VERSION

    @model_validator(mode="after")
    def validate_saved_filters(self) -> SavedSearchCreate:
        """Only persist filters the alert matcher can actually evaluate.

        Search presentation fields have no meaning for a saved alert. A typo or
        unsupported version must fail at creation time, rather than appearing
        saved and silently producing zero notifications.
        """
        if self.filter_schema_version != FILTER_SCHEMA_VERSION:
            raise ValueError("unsupported saved-search filter schema version")
        unexpected = set(self.filters) - set(HackathonFilters.model_fields)
        if unexpected:
            raise ValueError("unknown saved-search filters: " + ", ".join(sorted(unexpected)))
        presentation = {"cursor", "limit", "sort"} & set(self.filters)
        if presentation:
            raise ValueError("pagination and sorting are not saved-search filters")
        validated = HackathonFilters.model_validate(self.filters)
        # Keep invalid enum labels / country codes from becoming permanent
        # monitors that can never match a catalog event.
        allowed = {
            "status": {
                "ANNOUNCED",
                "REGISTRATION_UPCOMING",
                "REGISTRATION_OPEN",
                "REGISTRATION_CLOSED",
                "UPCOMING",
                "ONGOING",
                "SUBMISSIONS_OPEN",
                "JUDGING",
                "RESULTS_PENDING",
                "COMPLETED",
                "CANCELLED",
                "POSTPONED",
                "UNKNOWN",
            },
            "format": {"ONLINE", "IN_PERSON", "HYBRID", "UNKNOWN"},
            "event_type": {
                "hackathon",
                "buildathon",
                "datathon",
                "game_jam",
                "ideathon",
                "innovation_challenge",
                "ai_challenge",
                "data_science_competition",
                "coding_competition",
                "security_ctf",
                "hardware_challenge",
                "startup_challenge",
                "open_innovation_challenge",
                "designathon",
                "quant_competition",
                "robotics_competition",
                "other",
            },
        }
        for field, choices in allowed.items():
            if any(value not in choices for value in getattr(validated, field)):
                raise ValueError(f"unsupported saved-search {field} value")
        for field in ("country_code", "track", "technology", "organizer", "sponsor"):
            values = getattr(validated, field)
            if len(values) > 50:
                raise ValueError(f"too many saved-search {field} values")
        if any(re.fullmatch(r"[A-Z]{2}", country) is None for country in validated.country_code):
            raise ValueError("invalid saved-search country code")
        # Normalize persisted JSON just like the public search API (e.g. BR
        # instead of br), with no pagination/sorting fields.
        self.filters = validated.model_dump(
            mode="json",
            exclude_defaults=True,
            exclude={"cursor", "limit", "sort"},
        )
        return self


class SavedSearchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    notifications_enabled: bool | None = None


class SavedSearchView(BaseModel):
    id: UUID
    name: str
    original_query: str | None = None
    filter_schema_version: str
    filters: dict[str, object]
    definition_hash: str
    notifications_enabled: bool
    duplicate_count: int = 1
    created_at: datetime
    updated_at: datetime


class AlertEventView(BaseModel):
    id: UUID
    hackathon_id: UUID | None = None
    hackathon_slug: str | None = None
    hackathon_name: str | None = None
    alert_type: str
    payload: dict[str, object]
    source_evidence: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime
    read_at: datetime | None = None


class AlertPreferenceView(BaseModel):
    alert_type: Literal["new_match"] = "new_match"
    in_app_enabled: bool
    email_enabled: bool


class AlertPreferenceUpdate(BaseModel):
    channel: Literal["IN_APP", "EMAIL"]
    alert_type: str = "*"
    enabled: bool
