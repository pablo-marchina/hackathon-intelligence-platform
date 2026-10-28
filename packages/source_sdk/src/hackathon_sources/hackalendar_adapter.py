"""Hackalendar's explicitly public, free JSON listing of curated hackathons.

Publisher contract: https://hackalendar.com/api and https://hackalendar.com/agents
Read-only /api/events; never request the private API, registration links,
organizer pages or external URLs. Database-owned source approval is separate.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from pydantic import HttpUrl

from .live_adapters import ApiSeedAdapter, _valid_http_url
from .models import DiscoveredUrl, FetchResult, SourceEventRecord

MAX_PAGE_SIZE = 200
MAX_OFFSET = 100_000
API_HOST = "hackalendar.com"
API_PATH = "/api/events"


def _payload(result: FetchResult) -> tuple[list[Any], dict[str, Any]]:
    value = json.loads(result.body)
    if not isinstance(value, dict):
        raise ValueError("Hackalendar response must be an object")
    records = value.get("data")
    meta = value.get("meta")
    if not isinstance(records, list) or not isinstance(meta, dict):
        raise ValueError("Hackalendar response requires data[] and meta{}")
    if len(records) > MAX_PAGE_SIZE:
        raise ValueError("Hackalendar page exceeds published maximum")
    return records, meta


def _text(value: Any, *, limit: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def _date(value: Any) -> str | None:
    # Preserve the publisher's precision (YYYY-MM-DD or ISO with explicit time).
    return _text(value, limit=80)


def _page_url(url: str) -> tuple[str, int, int] | None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != API_HOST or parts.port not in (None, 443):
        return None
    if parts.username or parts.password or parts.path != API_PATH or parts.fragment:
        return None
    query = parse_qs(parts.query, keep_blank_values=True)
    if any(key not in {"limit", "offset"} for key in query):
        return None
    if any(len(values) != 1 for values in query.values()):
        return None
    try:
        raw_limit = query.get("limit", ["100"])[0]
        raw_offset = query.get("offset", ["0"])[0]
        if not raw_limit.isdecimal() or not raw_offset.isdecimal():
            return None
        limit, offset = int(raw_limit), int(raw_offset)
    except (ValueError, TypeError):
        return None
    if not 1 <= limit <= MAX_PAGE_SIZE or not 0 <= offset <= MAX_OFFSET:
        return None
    return parts.scheme, limit, offset


class HackalendarPublicEventsAdapter(ApiSeedAdapter):
    """Parse source-native curated events and follow bounded same-endpoint offsets."""

    async def parse_events(self, result: FetchResult) -> list[SourceEventRecord]:
        if _page_url(result.final_url) is None:
            raise ValueError("Hackalendar response URL is outside documented API")
        rows, _ = _payload(result)
        records: list[SourceEventRecord] = []
        ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            slug = _text(row.get("slug"), limit=200)
            name = _text(row.get("name"))
            if slug is None or name is None or slug in ids:
                continue
            ids.add(slug)
            location = row.get("location")
            city = _text(row.get("city"), limit=120)
            country = _text(row.get("country"), limit=120)
            if isinstance(location, dict):
                city = city or _text(location.get("city"), limit=120)
                country = country or _text(location.get("country"), limit=120)
            iso = _text(row.get("countryCode"), limit=2)
            country_code = iso.upper() if iso and len(iso) == 2 and iso.isalpha() else None
            themes = row.get("themes")
            tags = (
                [_text(item, limit=70) for item in themes[:24] if _text(item, limit=70)]
                if isinstance(themes, list)
                else []
            )
            registration_url = _valid_http_url(row.get("registrationUrl"))
            organizer_url = _valid_http_url(row.get("url"))
            official_url = registration_url or organizer_url
            metadata = {
                "registration_url": registration_url,
                "registration_deadline_raw": _date(row.get("registrationDeadline")),
                "deadline_note": _text(row.get("deadlineNote"), limit=500),
                "temporal_precision": row.get("temporalPrecision")
                if isinstance(row.get("temporalPrecision"), dict)
                else None,
                "timezone": _text(row.get("timezone"), limit=80),
                "description": _text(row.get("description"), limit=2000),
                "is_free": row.get("isFree") if type(row.get("isFree")) is bool else None,
                "prize_pool_raw": row.get("prizePool")
                if isinstance(row.get("prizePool"), (str, int, float))
                and not isinstance(row.get("prizePool"), bool)
                else None,
                "organizer": _text(row.get("organizer"), limit=300),
                "cancelled": row.get("cancelled") if type(row.get("cancelled")) is bool else None,
                "publisher_url": organizer_url,
                "picked": row.get("picked") if type(row.get("picked")) is bool else None,
            }
            records.append(
                SourceEventRecord(
                    source_key=self.definition.key,
                    source_event_id=slug,
                    name=name,
                    source_url=HttpUrl(result.final_url),
                    official_url=HttpUrl(official_url) if official_url is not None else None,
                    starts_at_raw=_date(row.get("startsAt")),
                    ends_at_raw=_date(row.get("endsAt")),
                    format_raw=_text(row.get("mode"), limit=30),
                    city=city,
                    country=country,
                    country_code=country_code,
                    tags=[t for t in tags if t is not None],
                    metadata=metadata,
                )
            )
        return records

    async def parse_listing(self, result: FetchResult) -> list[DiscoveredUrl]:
        parts = _page_url(result.final_url)
        if parts is None:
            raise ValueError("Hackalendar pagination must stay on the published API endpoint")
        _, limit, offset = parts
        _, meta = _payload(result)
        has_more = meta.get("hasMore")
        if has_more is not True:
            if has_more is False or has_more is None:
                return []
            raise ValueError("Hackalendar hasMore must be boolean")
        next_offset = meta.get("nextOffset")
        if (
            type(next_offset) is not int
            or next_offset <= offset
            or next_offset > MAX_OFFSET
            or next_offset - offset > limit
        ):
            raise ValueError("Hackalendar returned invalid pagination offset")
        # Construct the next request ourselves; never accept a publisher-
        # supplied arbitrary URL or silently follow registration links.
        url = urlunsplit(
            ("https", API_HOST, API_PATH, urlencode({"limit": limit, "offset": next_offset}), "")
        )
        return [
            DiscoveredUrl(
                url=HttpUrl(url),
                source_key=self.definition.key,
                discovery_method="api_pagination",
                parent_url=HttpUrl(result.final_url),
                depth=0,
                priority=self.definition.priority,
            )
        ]
