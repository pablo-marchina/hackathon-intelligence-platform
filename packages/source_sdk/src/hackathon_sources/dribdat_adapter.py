"""Approved Dribdat installations' openly published /api/events.json feeds.

Source-specific approval and host allowlists are still enforced by the
existing PostgreSQL permission gate. Never crawl an event's external webpage.
"""

from __future__ import annotations

import json
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import HttpUrl, ValidationError

from .live_adapters import ApiSeedAdapter
from .models import FetchResult, SourceEventRecord

MAX_DRIBDAT_EVENTS = 2000


def _date(value: Any) -> str | None:
    """RFC 2822 dates from Dribdat; never guess a timezone or missing date."""
    if not isinstance(value, str) or len(value) > 120:
        return None
    try:
        parsed: datetime = parsedate_to_datetime(value)
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, IndexError):
        return None


def _url(value: Any) -> HttpUrl | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        return None
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        return HttpUrl(value.strip())
    except (ValidationError, ValueError):
        return None


def _label(value: Any, *, limit: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


class DribdatOpenEventsAdapter(ApiSeedAdapter):
    """Read Dribdat's public list of *visible*, unlocked events.

    Unlike a dedicated hackathon-only feed, a Dribdat installation may host
    non-hackathon activities. Preserve the title for normal event_kind
    classification; never tag every record as a hackathon by default.
    """

    async def parse_events(self, result: FetchResult) -> list[SourceEventRecord]:
        payload = json.loads(result.body)
        if not isinstance(payload, dict):
            return []
        rows = payload.get("events")
        if not isinstance(rows, list):
            return []
        if len(rows) > MAX_DRIBDAT_EVENTS:
            raise ValueError("Dribdat event count exceeds explicit safe limit")
        source_url = HttpUrl(result.final_url)
        records: list[SourceEventRecord] = []
        ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            identifier = row.get("id")
            if isinstance(identifier, bool) or not isinstance(identifier, (int, str)):
                continue
            event_id = str(identifier).strip()
            if not event_id.isdecimal() or len(event_id) > 20 or int(event_id) <= 0:
                continue
            if event_id in ids:
                # The feed must not produce multiple versions of the same
                # identity in a single page; later processing remains idempotent.
                continue
            name = _label(row.get("name"))
            if not name:
                continue
            ids.add(event_id)
            summary = _label(row.get("summary"), limit=2000)
            location = _label(row.get("location"), limit=300)
            host = _label(row.get("hostname"), limit=300)
            logo_url = _url(row.get("logo_url"))
            records.append(
                SourceEventRecord(
                    source_key=self.definition.key,
                    source_event_id=event_id,
                    name=name,
                    source_url=source_url,
                    official_url=_url(row.get("webpage_url")),
                    starts_at_raw=_date(row.get("starts_at")),
                    ends_at_raw=_date(row.get("ends_at")),
                    metadata={
                        "description": summary,
                        "location_text": location,
                        "host_venue": host,
                        "hashtags": _label(row.get("hashtags")),
                        "logo_url": str(logo_url) if logo_url else None,
                        "source_finished_flag": row.get("has_finished")
                        if isinstance(row.get("has_finished"), bool)
                        else None,
                    },
                )
            )
        return records
