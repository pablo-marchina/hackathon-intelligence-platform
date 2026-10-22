from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

PREFERENCE_SCHEMA_VERSION = "w5-preferences-v1"


@dataclass(frozen=True)
class PreferenceProfile:
    preferred_formats: tuple[str, ...] = ()
    preferred_country_codes: tuple[str, ...] = ()
    travel_country_codes: tuple[str, ...] = ()
    tracks: tuple[str, ...] = ()
    technologies: tuple[str, ...] = ()
    event_statuses: tuple[str, ...] = ()
    student_status: str | None = None
    experience_level: str | None = None
    team_preference: str | None = None
    minimum_prize_usd: float | None = None
    free_only: bool | None = None
    available_from: date | None = None
    available_until: date | None = None
    hard_constraints: dict[str, Any] = field(default_factory=dict)
    soft_preferences: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> PreferenceProfile:
        def tup(*names: str) -> tuple[str, ...]:
            for name in names:
                raw = value.get(name)
                if raw is not None:
                    return tuple(str(x) for x in raw)
            return ()

        def to_date(raw: Any) -> date | None:
            if raw is None or isinstance(raw, date):
                return raw
            return date.fromisoformat(str(raw))

        return cls(
            preferred_formats=tup("preferred_formats"),
            preferred_country_codes=tup("preferred_country_codes"),
            travel_country_codes=tup("travel_country_codes"),
            tracks=tup("tracks", "preferred_track_slugs"),
            technologies=tup("technologies", "preferred_technology_slugs"),
            event_statuses=tup("event_statuses"),
            student_status=value.get("student_status"),
            experience_level=value.get("experience_level"),
            team_preference=value.get("team_preference"),
            minimum_prize_usd=value.get("minimum_prize_usd"),
            free_only=value.get("free_only"),
            available_from=to_date(value.get("available_from")),
            available_until=to_date(value.get("available_until")),
            hard_constraints=dict(value.get("hard_constraints") or {}),
            soft_preferences=dict(value.get("soft_preferences") or {}),
        )

    def canonical_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "preferred_formats",
            "preferred_country_codes",
            "travel_country_codes",
            "tracks",
            "technologies",
            "event_statuses",
        ):
            data[key] = sorted(set(data[key]))
        for key in ("available_from", "available_until"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        data["schema_version"] = PREFERENCE_SCHEMA_VERSION
        return data

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(payload.encode()).hexdigest()
