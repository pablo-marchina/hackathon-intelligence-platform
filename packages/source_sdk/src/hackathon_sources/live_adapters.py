from __future__ import annotations

import base64
import hashlib
import json
import unicodedata
from collections.abc import AsyncIterator, Iterable
from datetime import date
from typing import Any

from .adapter import SourceAdapter
from .models import DiscoveredUrl, FetchResult, SourceEventRecord


def _json(result: FetchResult) -> Any:
    return json.loads(result.body.decode("utf-8"))


def _valid_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value.startswith(("https://", "http://")) else None


class ApiSeedAdapter(SourceAdapter):
    """Base adapter for a documented API with one or more explicit seed endpoints."""

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        for url in self.definition.seed_urls:
            yield DiscoveredUrl(
                url=url,
                source_key=self.definition.key,
                discovery_method="documented_api",
                depth=0,
                priority=self.definition.priority,
            )


class HackClubHackathonsAdapter(ApiSeedAdapter):
    """Official Hack Club high-school hackathon directory API."""

    async def parse_events(self, result: FetchResult) -> list[SourceEventRecord]:
        payload = _json(result)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        records: list[SourceEventRecord] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            website = _valid_http_url(row.get("website"))
            location = row.get("location") if isinstance(row.get("location"), dict) else {}
            records.append(
                SourceEventRecord(
                    source_key=self.definition.key,
                    source_event_id=str(row.get("id")) if row.get("id") is not None else None,
                    name=str(row["name"]),
                    source_url=result.final_url,
                    official_url=website,
                    starts_at_raw=row.get("starts_at"),
                    ends_at_raw=row.get("ends_at"),
                    format_raw=row.get("modality"),
                    city=location.get("city"),
                    state_region=location.get("province"),
                    country=location.get("country"),
                    country_code=location.get("country_code"),
                    metadata={
                        "logo_url": row.get("logo_url"),
                        "banner_url": row.get("banner_url"),
                        "apac": row.get("apac"),
                    },
                )
            )
        return records

    async def parse_listing(self, result: FetchResult) -> list[DiscoveredUrl]:
        payload = _json(result)
        discovered: list[DiscoveredUrl] = []
        if isinstance(payload, dict):
            next_url = _valid_http_url(
                (payload.get("links") or {}).get("next")
                if isinstance(payload.get("links"), dict)
                else None
            )
            if next_url:
                discovered.append(
                    DiscoveredUrl(
                        url=next_url,
                        source_key=self.definition.key,
                        discovery_method="api_pagination",
                        parent_url=result.final_url,
                        depth=0,
                        priority=self.definition.priority,
                    )
                )
        return discovered


class HackClubProgramsAdapter(ApiSeedAdapter):
    """Official Hack Club Programs API; classifier decides which records are hackathon-like."""

    async def parse_events(self, result: FetchResult) -> list[SourceEventRecord]:
        payload = _json(result)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        records: list[SourceEventRecord] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            records.append(
                SourceEventRecord(
                    source_key=self.definition.key,
                    source_event_id=str(row.get("id")) if row.get("id") is not None else None,
                    name=str(row["name"]),
                    source_url=result.final_url,
                    official_url=_valid_http_url(row.get("url")),
                    starts_at_raw=row.get("startDate"),
                    ends_at_raw=row.get("endDate"),
                    format_raw=row.get("format"),
                    eligibility_raw=row.get("requirements"),
                    tags=[str(item) for item in row.get("projectTypes", []) if item],
                    metadata={
                        "slug": row.get("slug"),
                        "status": row.get("status"),
                        "announced_at": row.get("announcedAt"),
                        "description": (
                            row.get("description", "")[:2000]
                            if isinstance(row.get("description"), str)
                            else None
                        ),
                        "in_person": row.get("inPerson"),
                    },
                )
            )
        return records


class HackClubCommunityEventsAdapter(ApiSeedAdapter):
    """Official JSON API behind events.hackclub.com."""

    async def parse_events(self, result: FetchResult) -> list[SourceEventRecord]:
        payload = _json(result)
        if isinstance(payload, dict):
            rows = payload.get("data", payload.get("events", []))
        else:
            rows = payload
        if not isinstance(rows, list):
            return []
        records: list[SourceEventRecord] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("title") or row.get("name")
            if not name:
                continue
            slug = row.get("slug")
            official = f"https://events.hackclub.com/{slug}" if slug else None
            records.append(
                SourceEventRecord(
                    source_key=self.definition.key,
                    source_event_id=str(row.get("id")) if row.get("id") is not None else None,
                    name=str(name),
                    source_url=result.final_url,
                    official_url=official,
                    starts_at_raw=row.get("start"),
                    ends_at_raw=row.get("end"),
                    tags=[str(item) for item in row.get("tags", []) if item],
                    metadata={"leader": row.get("leader"), "ama": row.get("ama")},
                )
            )
        return records


_MONTHS_PT = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}


def _agenda_events(payload: dict[str, Any]) -> Iterable[tuple[int, str, dict[str, Any], bool]]:
    """Include archival editions: archived != prohibited or irrelevant.

    The canonical classifier will still reject non-hackathon events. Preserve
    archived status as source metadata instead of treating it as invisibility.
    """
    years = payload.get("eventos")
    if not isinstance(years, list):
        return
    for year_block in years:
        if not isinstance(year_block, dict):
            continue
        try:
            year = int(year_block["ano"])
        except (KeyError, TypeError, ValueError):
            continue
        if year < 1990 or year > 2100:
            continue
        months = year_block.get("meses")
        if not isinstance(months, list):
            continue
        for month_block in months:
            if not isinstance(month_block, dict):
                continue
            month_name = str(month_block.get("mes", "")).strip().lower()
            events = month_block.get("eventos")
            if not isinstance(events, list):
                continue
            archived = (
                year_block.get("arquivado") is True
                or month_block.get("arquivado") is True
            )
            for event in events:
                if isinstance(event, dict):
                    yield year, month_name, event, archived


class AgendaTechBrasilAdapter(ApiSeedAdapter):
    """Factual event metadata from Agenda Tech Brasil through GitHub Contents API."""

    async def parse_events(self, result: FetchResult) -> list[SourceEventRecord]:
        wrapper = _json(result)
        if not isinstance(wrapper, dict):
            return []
        content = wrapper.get("content")
        if not isinstance(content, str):
            return []
        if wrapper.get("encoding") == "base64":
            content = base64.b64decode(content).decode("utf-8")
        payload = json.loads(content)
        if not isinstance(payload, dict):
            return []

        records: list[SourceEventRecord] = []
        for year, month_name, event, archived in _agenda_events(payload):
            name = event.get("nome")
            website = _valid_http_url(event.get("url"))
            if not isinstance(name, str) or not name.strip():
                continue
            # A public calendar listing is valid evidence even when the
            # organizer never supplied an external website.
            month = _MONTHS_PT.get(month_name)
            raw_days = event.get("data", [])
            days: list[int] = []
            if month and isinstance(raw_days, list):
                for day in raw_days[:40]:
                    if isinstance(day, bool) or not isinstance(day, (int, str)):
                        continue
                    try:
                        day_number = int(day)
                        date(year, month, day_number)
                    except (TypeError, ValueError):
                        continue
                    if day_number not in days:
                        days.append(day_number)
            days.sort()
            starts = (
                date(year, month, days[0]).isoformat() if month and days else None
            )
            ends = (
                date(year, month, days[-1]).isoformat() if month and days else starts
            )
            # The public calendar does not publish immutable event IDs.
            # Stable across edits to days within a month, yet edition-scoped;
            # identical dates on a shared API endpoint must not collapse events.
            identity_fields = [
                year,
                month,
                unicodedata.normalize("NFKC", name).strip().casefold(),
                website,
            ]
            identifier = hashlib.sha256(
                json.dumps(identity_fields, ensure_ascii=False).encode()
            ).hexdigest()[:24]
            records.append(
                SourceEventRecord(
                    source_key=self.definition.key,
                    source_event_id=f"agenda-tech-{year}-{identifier}",
                    name=name.strip(),
                    source_url=result.final_url,
                    official_url=website,
                    starts_at_raw=starts,
                    ends_at_raw=ends,
                    format_raw=event.get("tipo"),
                    city=event.get("cidade") or None,
                    state_region=event.get("uf") or None,
                    country="Brazil",
                    country_code="BR",
                    metadata={
                        "year": year,
                        "month": month_name,
                        "days": [str(day).zfill(2) for day in days],
                        "archived": archived,
                    },
                )
            )
        return records
