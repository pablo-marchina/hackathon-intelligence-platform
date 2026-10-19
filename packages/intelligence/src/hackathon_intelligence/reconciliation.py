from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class CandidateObservation:
    observation_id: int
    value: Any
    authority: float
    extraction_confidence: float
    explicitness: float
    agreement: float
    observed_at: datetime


@dataclass(frozen=True)
class ReconciliationPolicy:
    authority_weight: float = 0.35
    freshness_weight: float = 0.25
    confidence_weight: float = 0.20
    explicitness_weight: float = 0.10
    agreement_weight: float = 0.10
    freshness_half_life_days: float = 30.0
    version: str = "reconcile-v1"

    def score(self, candidate: CandidateObservation, *, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        age_days = max((now - candidate.observed_at).total_seconds() / 86400.0, 0.0)
        freshness = 0.5 ** (age_days / self.freshness_half_life_days)
        return (
            self.authority_weight * candidate.authority
            + self.freshness_weight * freshness
            + self.confidence_weight * candidate.extraction_confidence
            + self.explicitness_weight * candidate.explicitness
            + self.agreement_weight * candidate.agreement
        )


FIELD_FAMILIES = {
    "registration_close_at": "rules_sensitive", "submission_close_at": "rules_sensitive",
    "prize_pool.normalized_usd_amount": "rules_sensitive", "eligibility": "rules_sensitive",
    "eligibility_text": "rules_sensitive", "start_at": "schedule", "end_at": "schedule",
    "location": "schedule", "primary_location_id": "schedule",
}

ROLE_AUTHORITY = {
    "official_rules": 1.00, "official_event": 0.97, "organizer": 0.92,
    "hosting_platform": 0.78, "trusted_aggregator": 0.62, "aggregator": 0.45,
    "community": 0.35, "search_discovery": 0.20, "unknown": 0.10,
}


def authority_for(field_path: str, source_role: str) -> float:
    base = ROLE_AUTHORITY.get(source_role, ROLE_AUTHORITY["unknown"])
    family = FIELD_FAMILIES.get(field_path, "generic")
    if family == "rules_sensitive" and source_role == "official_rules":
        return 1.0
    if family == "rules_sensitive" and source_role in {"aggregator", "community", "search_discovery"}:
        return max(0.05, base - 0.15)
    if family == "schedule" and source_role in {"official_event", "organizer"}:
        return min(1.0, base + 0.03)
    return base


def policy_for_field(field_path: str) -> ReconciliationPolicy:
    family = FIELD_FAMILIES.get(field_path, "generic")
    if family == "rules_sensitive":
        return ReconciliationPolicy(authority_weight=0.55, freshness_weight=0.15, confidence_weight=0.15, explicitness_weight=0.10, agreement_weight=0.05, version="reconcile-v2-rules")
    if family == "schedule":
        return ReconciliationPolicy(authority_weight=0.45, freshness_weight=0.25, confidence_weight=0.15, explicitness_weight=0.10, agreement_weight=0.05, version="reconcile-v2-schedule")
    return ReconciliationPolicy(version="reconcile-v2-generic")


def select_canonical(candidates: list[CandidateObservation], policy: ReconciliationPolicy, *, now: datetime | None = None) -> tuple[CandidateObservation, float]:
    if not candidates:
        raise ValueError("at least one candidate is required")
    ranked = [(candidate, policy.score(candidate, now=now)) for candidate in candidates]
    ranked.sort(key=lambda item: (item[1], item[0].observed_at, -item[0].observation_id), reverse=True)
    return ranked[0]
