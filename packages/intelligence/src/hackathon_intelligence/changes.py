from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .conflicts import normalized_signature


@dataclass(frozen=True)
class CanonicalChange:
    field_path: str
    change_type: str
    previous_selection_id: int | None
    new_selection_id: int | None
    old_value: Any
    new_value: Any
    meaningful: bool


def is_meaningful_change(field_path: str, old: Any, new: Any) -> bool:
    if old is None and new is None:
        return False
    if normalized_signature(old, field_path=field_path) == normalized_signature(new, field_path=field_path):
        return False
    if field_path in {"full_description", "short_description", "description"}:
        old_words = set(str(old or "").casefold().split())
        new_words = set(str(new or "").casefold().split())
        union = old_words | new_words
        if union and len(old_words & new_words) / len(union) > 0.97:
            return False
    return True


def classify_change_type(field_path: str) -> str:
    if "deadline" in field_path or field_path in {"registration_close_at", "submission_close_at"}:
        return "DEADLINE"
    if field_path.startswith("prize") or "prize" in field_path:
        return "PRIZE"
    if field_path in {"status", "registration_status", "submission_status", "event_status"}:
        return "STATUS"
    if field_path in {"venue", "location", "primary_location_id", "city", "country"} or field_path.startswith("location."):
        return "VENUE"
    if field_path.startswith("eligibility"):
        return "ELIGIBILITY"
    return "GENERIC"


def build_canonical_change(
    field_path: str,
    old: Any,
    new: Any,
    *,
    previous_selection_id: int | None,
    new_selection_id: int | None,
) -> CanonicalChange:
    return CanonicalChange(
        field_path=field_path,
        change_type=classify_change_type(field_path),
        previous_selection_id=previous_selection_id,
        new_selection_id=new_selection_id,
        old_value=old,
        new_value=new,
        meaningful=is_meaningful_change(field_path, old, new),
    )
