from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field
from datetime import date
from .eligibility import Result
MODEL_VERSION = "w5-explainable-v1"

@dataclass(frozen=True)
class UserPreferences:
    tracks: frozenset[str] = field(default_factory=frozenset)
    technologies: frozenset[str] = field(default_factory=frozenset)
    formats: frozenset[str] = field(default_factory=frozenset)
    country_codes: frozenset[str] = field(default_factory=frozenset)
    travel_country_codes: frozenset[str] = field(default_factory=frozenset)
    event_statuses: frozenset[str] = field(default_factory=frozenset)
    minimum_prize_usd: float | None = None
    free_only: bool = False
    experience_level: str | None = None
    team_preference: str | None = None
    available_from: date | None = None
    available_until: date | None = None

@dataclass(frozen=True)
class EventSignals:
    tracks: frozenset[str] = field(default_factory=frozenset)
    technologies: frozenset[str] = field(default_factory=frozenset)
    format: str | None = None
    country_code: str | None = None
    status: str | None = None
    prize_usd: float | None = None
    is_free: bool | None = None
    quality_score: float | None = None
    start_date: date | None = None
    end_date: date | None = None

@dataclass(frozen=True)
class Recommendation:
    score: float | None
    positive_reasons: tuple[str, ...]
    negative_reasons: tuple[str, ...]
    components: dict[str, float] = field(default_factory=dict)
    model_version: str = MODEL_VERSION
    input_hash: str | None = None

def _fingerprint(eligibility: Result, prefs: UserPreferences, event: EventSignals) -> str:
    def norm(v):
        if isinstance(v, (set, frozenset, tuple, list)): return sorted(v)
        if isinstance(v, date): return v.isoformat()
        return v
    payload = {"eligibility": eligibility.value, "prefs": {k: norm(v) for k, v in prefs.__dict__.items()}, "event": {k: norm(v) for k, v in event.__dict__.items()}, "version": MODEL_VERSION}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def recommend(eligibility: Result, prefs: UserPreferences, event: EventSignals) -> Recommendation:
    fp = _fingerprint(eligibility, prefs, event)
    if eligibility is Result.INELIGIBLE:
        return Recommendation(None, (), ("hard eligibility failure",), {}, MODEL_VERSION, fp)
    points = 0.0; possible = 0.0; positives: list[str] = []; negatives: list[str] = []; components: dict[str, float] = {}
    def component(name: str, weight: float, matched: float, positive: str | None = None, negative: str | None = None) -> None:
        nonlocal points, possible
        possible += weight; earned = weight * max(0.0, min(1.0, matched)); points += earned; components[name] = round(earned, 4)
        if positive and matched > 0: positives.append(positive)
        elif negative and matched <= 0: negatives.append(negative)
    if prefs.tracks:
        overlap = prefs.tracks & event.tracks; component("topics", 30, len(overlap) / max(1, len(prefs.tracks)), "matches preferred topics: " + ", ".join(sorted(overlap)) if overlap else None, "no preferred topic match")
    if prefs.technologies:
        overlap = prefs.technologies & event.technologies; component("technologies", 20, len(overlap) / max(1, len(prefs.technologies)), "matches technologies: " + ", ".join(sorted(overlap)) if overlap else None, "no preferred technology match")
    if prefs.formats: component("format", 12, 1 if event.format in prefs.formats else 0, f"preferred format: {event.format}" if event.format in prefs.formats else None, f"format {event.format} is not preferred" if event.format else "event format unknown")
    acceptable = prefs.country_codes | prefs.travel_country_codes
    if acceptable: component("location", 10, 1 if event.country_code and event.country_code in acceptable else 0, "location matches travel preferences" if event.country_code in acceptable else None, "location outside preferred/travel countries")
    if prefs.event_statuses: component("status", 5, 1 if event.status in prefs.event_statuses else 0, "event status matches preference" if event.status in prefs.event_statuses else None, "event status not preferred")
    if prefs.minimum_prize_usd is not None:
        matched = 1 if event.prize_usd is not None and event.prize_usd >= prefs.minimum_prize_usd else 0; component("prize", 10, matched, "meets prize preference" if matched else None, "prize amount unknown" if event.prize_usd is None else "prize below preference")
    if prefs.free_only: component("free", 5, 1 if event.is_free is True else 0, "free participation" if event.is_free is True else None, "participation is not confirmed free")
    if prefs.available_from or prefs.available_until:
        okay = not (prefs.available_from and event.end_date and event.end_date < prefs.available_from) and not (prefs.available_until and event.start_date and event.start_date > prefs.available_until)
        component("availability", 3, 1 if okay else 0, "fits availability window" if okay else None, "outside availability window")
    quality = max(0.0, min(event.quality_score or 0.0, 100.0)) / 100.0; component("quality", 5, quality, "high-confidence event data" if quality >= .8 else None, None)
    if eligibility is Result.ELIGIBLE: positives.insert(0, "hard eligibility checks passed")
    else: negatives.insert(0, "some eligibility information is unknown")
    return Recommendation(round(points / possible * 100 if possible else 0, 2), tuple(positives[:5]), tuple(negatives[:5]), components, MODEL_VERSION, fp)
