"""Versioned projection of source-owned schema.org evidence into W2 field observations.

This is deliberately pure: it grants no permission to fetch any source and does
not merge event editions. The original and normalized values are retained
separately. Unverified fields stay unknown rather than becoming False or zero.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

PROJECTION_VERSION = "jsonld-projection-v1"


@dataclass(frozen=True)
class ProjectedObservation:
    field_path: str
    original: Any
    normalized: Any
    evidence_locator: str
    confidence: float = 0.95


def _url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme.lower() not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        return value.strip()
    except ValueError:
        return None


def _utc_datetime(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None  # timezone is unknown; do not invent UTC for a registration deadline
        return timestamp.astimezone(UTC).isoformat()
    except ValueError:
        return None


def _price(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,12}(?:\.\d{1,2})?", value):
        return None  # Do not guess separators, currencies or exchange rates.
    try:
        dec = Decimal(value)
        return str(dec) if dec.is_finite() and dec >= 0 else None
    except InvalidOperation:
        return None


def project_structured_metadata(payload: dict[str, Any]) -> list[ProjectedObservation]:
    """Only emit data actually present in a structured HTML adapter record.

    Output field names are those supported by the canonical field selection
    mechanism. Relationship/taxonomy values are evidence only until a dedicated
    relational reconciler can validate and normalize their identities.
    """
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("extraction_method") != "schema_org_json_ld":
        return []
    specs: dict[str, tuple[str, Callable[[Any], Any]]] = {
        "description": (
            "short_description",
            lambda v: v[:2000] if isinstance(v, str) and v.strip() else None,
        ),
        "language": (
            "language",
            lambda v: v.strip()[:35] if isinstance(v, str) and v.strip() else None,
        ),
        "image_url": ("image_url", _url),
        "prize_details_text": (
            "prize_details_text",
            lambda v: v.strip()[:2000] if isinstance(v, str) and v.strip() else None,
        ),
        "registration_url": ("registration_url", _url),
        "registration_valid_through": ("offer_valid_through", _utc_datetime),
        "ticket_price": ("participation_fee", _price),
        "ticket_currency": (
            "participation_fee_currency",
            lambda v: v.upper() if isinstance(v, str) and re.fullmatch(r"[A-Za-z]{3}", v) else None,
        ),
        "is_accessible_for_free": ("is_free", lambda v: v if type(v) is bool else None),
        "organizers": (
            "organizers",
            lambda v: (
                v[:20]
                if isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v[:20])
                else None
            ),
        ),
        "sponsors": (
            "sponsors",
            lambda v: (
                v[:20]
                if isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v[:20])
                else None
            ),
        ),
        "maximum_attendee_capacity": (
            "capacity",
            lambda v: v if type(v) is int and 0 < v <= 10_000_000 else None,
        ),
        "event_status": (
            "source_event_status",
            lambda v: v[:100] if isinstance(v, str) and v.strip() else None,
        ),
    }
    observations: list[ProjectedObservation] = []
    for source_name, (field_path, normalize) in specs.items():
        original = metadata.get(source_name)
        normalized = normalize(original)
        if normalized is None or normalized == []:
            continue
        observations.append(
            ProjectedObservation(
                field_path=field_path,
                original=original,
                normalized=normalized,
                evidence_locator=f"source_event_ref.payload.metadata.{source_name}",
            )
        )
    return observations
