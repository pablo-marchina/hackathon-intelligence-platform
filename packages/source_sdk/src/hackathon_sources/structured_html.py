"""Policy-gated extraction of factual schema.org Event data from HTML pages.

Only independently approved sources may discover and fetch same-host event pages.
External organizer and registration links are evidence, not crawl authorization.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup

from .models import DiscoveredUrl, FetchResult, SourceEventRecord
from .seed_adapter import SeedUrlAdapter

MAX_LD_SCRIPTS = 40
MAX_LD_SCRIPT_BYTES = 1_000_000
MAX_LD_NODES = 500
MAX_EVENTS_PER_PAGE = 100
MAX_LD_DEPTH = 8
MAX_HTML_ANCHORS = 1500
MAX_HTML_DISCOVERIES = 100
MAX_HTML_PAGINATION_HOPS = 50


def _scalar(value: Any) -> str | None:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return text[:2000] if text else None
    return None


def _label(value: Any) -> str | None:
    if isinstance(value, dict):
        return _scalar(value.get("name")) or _scalar(value.get("@id"))
    return _scalar(value)


def _labels(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [label for item in values if (label := _label(item))]


def _safe_url(value: Any, base: str) -> str | None:
    url = _scalar(value)
    if not url:
        return None
    absolute = urljoin(base, url)
    try:
        parsed = urlsplit(absolute)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        return absolute
    except ValueError:
        return None


def _types(value: Any) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {
        str(item).rsplit("/", 1)[-1].rsplit("#", 1)[-1].casefold()
        for item in values
        if isinstance(item, str)
    }


def _nodes(payload: Any) -> Iterator[dict[str, Any]]:
    """Walk only JSON-LD containers with strict depth/count limits."""
    stack: list[tuple[Any, int]] = [(payload, 0)]
    visited = 0
    while stack and visited < MAX_LD_NODES:
        value, depth = stack.pop()
        visited += 1
        if depth > MAX_LD_DEPTH:
            continue
        if isinstance(value, list):
            stack.extend((item, depth + 1) for item in reversed(value))
        elif isinstance(value, dict):
            yield value
            for key in ("@graph", "mainEntity", "itemListElement", "item", "subjectOf", "hasPart"):
                if key in value:
                    stack.append((value[key], depth + 1))


def _location(value: Any) -> tuple[dict[str, str], bool]:
    locations = value if isinstance(value, list) else [value]
    physical: dict[str, str] = {}
    virtual = False
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        if "virtuallocation" in _types(loc.get("@type")):
            virtual = True
            continue
        address = loc.get("address")
        if not isinstance(address, dict):
            address = loc if "postaladdress" in _types(loc.get("@type")) else {}
        fields = {
            "venue": _label(loc.get("name")),
            "city": _scalar(address.get("addressLocality")),
            "state_region": _scalar(address.get("addressRegion")),
            "country": _label(address.get("addressCountry")),
            "postal_code": _scalar(address.get("postalCode")),
        }
        physical = {k: v for k, v in fields.items() if v} or physical
    return physical, virtual


def _attendance(value: Any, *, physical: bool, virtual: bool) -> str | None:
    modes = _types(value)
    if "mixedeventattendancemode" in modes:
        return "HYBRID"
    if "onlineeventattendancemode" in modes:
        return "ONLINE"
    if "offlineeventattendancemode" in modes:
        return "IN_PERSON"
    if physical and virtual:
        return "HYBRID"
    if virtual:
        return "ONLINE"
    if physical:
        return "IN_PERSON"
    return None


def _keywords(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    tags: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        for part in re.split(r"[,;]", item):
            tag = part.strip()[:100]
            if tag and tag not in tags:
                tags.append(tag)
    return tags[:40]


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return {}


def _record(node: dict[str, Any], result: FetchResult, source_key: str) -> SourceEventRecord | None:
    if not (
        _types(node.get("@type"))
        & {"event", "hackathon", "educationevent", "businessevent", "festival", "competition"}
    ):
        return None
    name = _scalar(node.get("name"))
    if not name:
        return None
    description = _scalar(node.get("description")) or ""
    # Lazy: the API-only adapters also run with source_sdk installed standalone.
    from hackathon_discovery.classifier import classify_page

    classification = classify_page(title=name, text=description)
    if not classification.is_hackathon_like:
        return None
    place, virtual = _location(node.get("location"))
    offer = _first_dict(node.get("offers"))
    image = node.get("image")
    if isinstance(image, list):
        image = next((part for part in image if isinstance(part, str)), None)
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")
    organizer = _labels(node.get("organizer"))
    sponsors = _labels(node.get("sponsor"))
    audience = _first_dict(node.get("audience"))
    official = _safe_url(node.get("url"), result.final_url) or result.final_url
    # @id is often a *page* identifier reused each year; do not treat it as
    # an edition-stable external event ID for automatic entity merging.
    identifier = _label(node.get("identifier"))
    raw_meta: dict[str, Any] = {
        "extraction_method": "schema_org_json_ld",
        "schema_org_id": _scalar(node.get("@id")),
        "classification_version": classification.classifier_version,
        "classification_confidence": classification.confidence,
        "description": description[:2000] or None,
        "language": _scalar(node.get("inLanguage")),
        "is_accessible_for_free": (
            node.get("isAccessibleForFree")
            if isinstance(node.get("isAccessibleForFree"), bool)
            else None
        ),
        "maximum_attendee_capacity": (
            node.get("maximumAttendeeCapacity")
            if isinstance(node.get("maximumAttendeeCapacity"), int)
            and not isinstance(node.get("maximumAttendeeCapacity"), bool)
            else None
        ),
        "image_url": _safe_url(image, result.final_url),
        "organizers": organizer[:20],
        "sponsors": sponsors[:20],
        "registration_url": _safe_url(offer.get("url"), result.final_url),
        "ticket_price": _scalar(offer.get("price")),
        "ticket_currency": _scalar(offer.get("priceCurrency")),
        "registration_valid_through": _scalar(offer.get("validThrough")),
        "event_status": _label(node.get("eventStatus")),
        "location": place,
        "same_as": [
            link
            for raw in _labels(node.get("sameAs"))[:15]
            if (link := _safe_url(raw, result.final_url))
        ],
    }
    return SourceEventRecord(
        source_key=source_key,
        source_event_id=identifier[:500] if identifier else None,
        name=name[:500],
        source_url=result.final_url,
        official_url=official,
        starts_at_raw=_scalar(node.get("startDate")),
        ends_at_raw=_scalar(node.get("endDate")),
        format_raw=_attendance(
            node.get("eventAttendanceMode"), physical=bool(place), virtual=virtual
        ),
        city=place.get("city"),
        state_region=place.get("state_region"),
        country=place.get("country"),
        country_code=place.get("country") if len(place.get("country", "")) == 2 else None,
        eligibility_raw=_scalar(audience.get("name")) or _scalar(audience.get("educationalRole")),
        tags=_keywords(node.get("keywords")),
        metadata=raw_meta,
    )


class StructuredHtmlEventAdapter(SeedUrlAdapter):
    """Extract Event JSON-LD and same-host listing links, only for permitted sources."""

    async def parse_listing(self, result: FetchResult) -> list[DiscoveredUrl]:
        """Discover bounded event/detail and genuine pagination links from HTML.

        External organizer/registration links are evidence, not crawl permission.
        The runner rechecks host, robots and the source's operator-owned approval.
        """
        if "html" not in (result.content_type or "").lower():
            return []
        soup = BeautifulSoup(result.body, "html.parser")
        allowed = {
            self.definition.domain.casefold(),
            *(host.casefold() for host in self.definition.allowed_hosts),
        }
        current = urlsplit(result.final_url)
        seen: set[str] = {urldefrag(result.final_url).url}
        results: list[DiscoveredUrl] = []
        unwanted = re.compile(
            r"^/(?:blog|news|articles?|docs?|help|faq|login|signup|account|"
            r"privacy|terms|jobs|careers|about|resources)(?:/|$)",
            re.I,
        )
        detail_path = re.compile(
            r"/(?:hackathons?|events?|challenges?|competitions?|ai-hackathons)/[^/?#]+",
            re.I,
        )
        event_label = re.compile(
            r"\b(?:hackathons?|buildathons?|datathons?|game\s+jams?|ideathons?)\b",
            re.I,
        )
        for anchor in soup.find_all("a", href=True, limit=MAX_HTML_ANCHORS):
            raw = anchor.get("href")
            if not isinstance(raw, str) or len(raw) > 2048:
                continue
            try:
                candidate = urldefrag(urljoin(result.final_url, raw)).url
                url = urlsplit(candidate)
                if (
                    url.scheme not in {"http", "https"}
                    or not url.hostname
                    or url.hostname.casefold() not in allowed
                    or url.username is not None
                    or url.password is not None
                    or url.port not in {None, 80, 443}
                    or len(candidate) > 2048
                    or unwanted.search(url.path)
                    or re.search(r"\.(?:png|jpe?g|gif|webp|svg|pdf|css|js|zip)$", url.path, re.I)
                    or candidate in seen
                ):
                    continue
            except ValueError:
                continue
            text = anchor.get_text(" ", strip=True)[:250]
            label = " ".join(text.split()).casefold()
            query = url.query.casefold()
            is_pagination = (
                url.path.rstrip("/") == current.path.rstrip("/")
                and bool(re.search(r"(?:^|&)(?:page|offset|cursor)=", query))
                and bool(re.fullmatch(r"(?:next|older|mais|próxim[ao]|\d+|[›»→]+)", label))
            )
            is_detail = bool(detail_path.search(url.path) or event_label.search(text))
            if not (is_detail or is_pagination):
                continue
            # An event listing query is not an event detail; discover it only via
            # a visible pagination control to prevent unbounded filter crawling.
            if (
                url.path.rstrip("/") == current.path.rstrip("/")
                and not is_pagination
            ):
                continue
            seen.add(candidate)
            results.append(
                DiscoveredUrl(
                    url=candidate,
                    source_key=self.definition.key,
                    discovery_method="html_pagination" if is_pagination else "html_event_link",
                    parent_url=result.final_url,
                    depth=1,
                    priority=self.definition.priority,
                )
            )
            if len(results) >= MAX_HTML_DISCOVERIES:
                break
        return results

    async def parse_events(self, result: FetchResult) -> list[SourceEventRecord]:
        if "html" not in (result.content_type or "").lower():
            return []
        soup = BeautifulSoup(result.body, "html.parser")
        records: list[SourceEventRecord] = []
        seen: set[tuple[str, str | None, str | None]] = set()
        scripts = soup.find_all(
            "script",
            type=lambda t: bool(t and t.split(";")[0].strip().lower() == "application/ld+json"),
            limit=MAX_LD_SCRIPTS,
        )
        for script in scripts:
            raw = script.string or script.get_text()
            if not raw or len(raw.encode("utf-8")) > MAX_LD_SCRIPT_BYTES:
                continue
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                continue
            for node in _nodes(payload):
                record = _record(node, result, self.definition.key)
                if record is None:
                    continue
                fingerprint = (
                    str(record.official_url),
                    record.name.casefold(),
                    record.starts_at_raw,
                )
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                records.append(record)
                if len(records) == MAX_EVENTS_PER_PAGE:
                    return records
        return records
