from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup
from dateutil.parser import isoparse

from .models import ExtractedEvent, FieldEvidence

EVENT_TYPES = {"Event", "Hackathon", "BusinessEvent", "EducationEvent", "Festival"}


def _flatten_jsonld(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _flatten_jsonld(item)
        return
    if not isinstance(value, dict):
        return
    graph = value.get("@graph")
    if graph is not None:
        yield from _flatten_jsonld(graph)
    yield value


def _event_like(item: dict[str, Any]) -> bool:
    raw_type = item.get("@type")
    types = {raw_type} if isinstance(raw_type, str) else set(raw_type or [])
    if types & EVENT_TYPES:
        return True
    name = str(item.get("name", "")).lower()
    return "hackathon" in name or "buildathon" in name or "datathon" in name


def _safe_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = isoparse(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def extract_event_jsonld(
    html: str, *, observed_at: datetime, method_version: str = "jsonld-v1"
) -> list[ExtractedEvent]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[ExtractedEvent] = []
    for tag in soup.select('script[type="application/ld+json"]'):
        if not tag.string:
            continue
        try:
            payload = json.loads(tag.string)
        except json.JSONDecodeError:
            continue
        for item in _flatten_jsonld(payload):
            if not _event_like(item):
                continue
            location = item.get("location")
            location_name: str | None = None
            if isinstance(location, dict):
                location_name = location.get("name") or location.get("address")
                if isinstance(location_name, dict):
                    location_name = location_name.get("streetAddress")
            elif isinstance(location, str):
                location_name = location
            organizer = item.get("organizer")
            organizer_name: str | None = None
            if isinstance(organizer, dict):
                organizer_name = organizer.get("name")
            elif isinstance(organizer, str):
                organizer_name = organizer

            name = item.get("name") if isinstance(item.get("name"), str) else None
            description = (
                item.get("description") if isinstance(item.get("description"), str) else None
            )
            start_at = _safe_datetime(item.get("startDate"))
            end_at = _safe_datetime(item.get("endDate"))
            url = item.get("url") if isinstance(item.get("url"), str) else None

            evidence: list[FieldEvidence] = []
            for field_path, value in (
                ("name", name),
                ("full_description", description),
                ("start_at", start_at.isoformat() if start_at else None),
                ("end_at", end_at.isoformat() if end_at else None),
                ("official_url", url),
                ("location.name", location_name),
                ("organizer.name", organizer_name),
            ):
                if value is not None:
                    evidence.append(
                        FieldEvidence(
                            field_path=field_path,
                            value=value,
                            method="JSON_LD",
                            confidence=0.98,
                            method_version=method_version,
                            observed_at=observed_at,
                            evidence_locator='script[type="application/ld+json"]',
                        )
                    )
            events.append(
                ExtractedEvent(
                    name=name,
                    description=description,
                    start_at=start_at,
                    end_at=end_at,
                    official_url=url,
                    location_name=location_name,
                    organizer_name=organizer_name,
                    evidence=evidence,
                )
            )
    return events
