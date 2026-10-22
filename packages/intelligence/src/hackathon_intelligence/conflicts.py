from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .normalization import normalize_url_signature

IMPORTANT_FIELDS = {
    "registration_close_at": "HIGH",
    "submission_close_at": "HIGH",
    "start_at": "HIGH",
    "end_at": "HIGH",
    "prize_pool.normalized_usd_amount": "HIGH",
    "eligibility": "CRITICAL",
    "eligibility_text": "CRITICAL",
    "primary_location_id": "MEDIUM",
    "location": "MEDIUM",
    "official_url": "MEDIUM",
}


@dataclass(frozen=True)
class ConflictRecord:
    field_path: str
    severity: str
    observation_ids: tuple[int, ...]
    values: tuple[Any, ...]
    state: str = "OPEN"


def normalized_signature(value: Any, field_path: str | None = None) -> str:
    if isinstance(value, str):
        if field_path and (field_path == "official_url" or field_path.endswith("_url")):
            try:
                return normalize_url_signature(value)
            except ValueError:
                pass
        return " ".join(value.casefold().split())
    return repr(value)


def detect_conflict(field_path: str, values: Iterable[Any]) -> tuple[bool, str]:
    signatures = {
        normalized_signature(value, field_path=field_path) for value in values if value is not None
    }
    conflict = len(signatures) > 1
    severity = IMPORTANT_FIELDS.get(field_path, "LOW") if conflict else "LOW"
    return conflict, severity


def build_conflict_record(
    field_path: str, observations: Iterable[tuple[int, Any]]
) -> ConflictRecord | None:
    rows = [(int(obs_id), value) for obs_id, value in observations if value is not None]
    conflict, severity = detect_conflict(field_path, [value for _, value in rows])
    if not conflict:
        return None
    return ConflictRecord(
        field_path, severity, tuple(obs_id for obs_id, _ in rows), tuple(value for _, value in rows)
    )
