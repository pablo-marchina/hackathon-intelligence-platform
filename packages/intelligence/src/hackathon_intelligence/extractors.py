from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import ExtractedEvent, FieldEvidence

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "title"),
    "description": ("description", "full_description", "summary"),
    "start_at": ("start_at", "starts_at", "starts_at_raw", "startDate"),
    "end_at": ("end_at", "ends_at", "ends_at_raw", "endDate"),
    "official_url": ("official_url", "url", "website"),
    "registration_url": ("registration_url", "register_url", "application_url"),
    "location_name": ("location_name", "location", "venue"),
    "organizer_name": ("organizer_name", "organizer", "host"),
    "event_type": ("event_type", "type"),
    "format": ("format", "format_raw", "mode"),
    "prize_text": ("prize_text", "prize", "prizes"),
    "eligibility_text": ("eligibility_text", "eligibility", "eligibility_raw"),
}


def _first(payload: dict[str, Any], aliases: tuple[str, ...]) -> Any | None:
    for key in aliases:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def extract_structured_event(
    payload: dict[str, Any],
    *,
    observed_at: datetime,
    method: str = "API",
    method_version: str = "structured-core-v1",
) -> ExtractedEvent:
    """Extract only values explicitly present in structured source data.

    Missing values remain ``None``. Every assertion carries method, evidence
    locator and per-field confidence so downstream provenance is complete.
    """
    values: dict[str, Any] = {}
    evidence: list[FieldEvidence] = []
    for field_path, aliases in _FIELD_ALIASES.items():
        value = _first(payload, aliases)
        if value is None:
            continue
        values[field_path] = value
        evidence.append(
            FieldEvidence(
                field_path=field_path,
                value=value,
                evidence_text=str(value)[:500],
                evidence_locator=f"payload.{next(k for k in aliases if payload.get(k) not in (None, '', [], {}))}",
                method=method,  # type: ignore[arg-type]
                confidence=0.99 if method in {"API", "JSON_LD", "EMBEDDED_JSON"} else 0.92,
                method_version=method_version,
                observed_at=observed_at,
            )
        )
    return ExtractedEvent(**values, evidence=evidence)


def missing_core_fields(event: ExtractedEvent) -> tuple[str, ...]:
    fields = (
        "name", "description", "start_at", "end_at", "registration_url",
        "official_url", "location_name", "organizer_name", "event_type",
        "format", "prize_text", "eligibility_text",
    )
    return tuple(field for field in fields if getattr(event, field, None) in (None, ""))
