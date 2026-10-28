"""Offline documented Hackalendar API contracts; never perform live HTTP here."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hackathon_intelligence.incremental import prepare_event
from hackathon_sources.factory import build_adapter, live_adapter_keys
from hackathon_sources.hackalendar_adapter import HackalendarPublicEventsAdapter
from hackathon_sources.models import (
    FetchResult,
    IngestionMode,
    PermissionStatus,
    SourceDefinition,
)
from hackathon_sources.registry import load_source_definitions
from hackathon_sources.runner import run_source_once

API = "https://hackalendar.com/api/events?limit=200&offset=0"
ROOT = Path(__file__).resolve().parents[3]


def source() -> SourceDefinition:
    return SourceDefinition(
        key="hackalendar",
        name="Hackalendar",
        domain="hackalendar.com",
        source_family="hackalendar",
        source_type="curated_hackathon_public_api",
        ingestion_mode=IngestionMode.API,
        permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        durable_api_pages=True,
        allowed_hosts=["hackalendar.com"],
        seed_urls=[API],
    )


def response(
    data: list[object],
    *,
    url: str = API,
    more: bool = False,
    next_offset: int | None = None,
) -> FetchResult:
    payload: dict[str, object] = {
        "data": data,
        "meta": {"hasMore": more, "nextOffset": next_offset, "total": len(data)},
    }
    return fetched(payload, url=url)


def fetched(payload: object, *, url: str = API) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="application/json",
        body=json.dumps(payload).encode("utf-8"),
        fetched_at=datetime(2026, 10, 28, tzinfo=UTC),
    )


def event(**changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "slug": "treehacks-2027",
        "name": "TreeHacks 2027",
        "url": "https://treehacks.com/",
        "registrationUrl": "https://treehacks.com/apply",
        "startsAt": "2027-02-12",
        "endsAt": "2027-02-14",
        "timezone": None,
        "location": "Stanford, California",
        "countryCode": "US",
        "mode": "in_person",
        "themes": ["ai", "student"],
        "isFree": True,
        "prizePool": None,
        "registrationDeadline": None,
        "cancelled": False,
    }
    result.update(changes)
    return result


def test_documented_source_registered_for_persistent_api_pagination() -> None:
    sources = load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    official = next(s for s in sources if s.key == "hackalendar")
    assert official.ingestion_mode is IngestionMode.API
    assert official.permission_status is PermissionStatus.ALLOWED
    assert official.durable_api_pages
    assert official.allowed_hosts == ["hackalendar.com"]
    assert official.seed_urls[0].path == "/api/events"
    assert "hackalendar" in live_adapter_keys()
    assert isinstance(build_adapter(official), HackalendarPublicEventsAdapter)


def test_curated_hackathon_events_preserve_identity_and_unknown_precision() -> None:
    adapter = HackalendarPublicEventsAdapter(source())
    rows = asyncio.run(
        adapter.parse_events(
            response(
                [
                    event(),
                    event(slug="ai-builder-2026", name="AI Builder Weekend",
                          startsAt="2026-11-01T09:00:00+01:00", endsAt=None,
                          registrationDeadline="2026-10-30T23:00:00+01:00",
                          location={"city": "Paris", "country": "France"},
                          countryCode="FR", isFree=False,
                          registrationUrl="javascript:alert(1)", prizePool="Unknown"),
                    event(slug="treehacks-2027", name="same edition again"),
                    event(slug=None, name="Missing slug"),
                    {"slug": "unlabeled", "name": ""},
                ]
            )
        )
    )
    assert len(rows) == 2
    first, second = rows
    assert first.source_event_id == "treehacks-2027"
    assert str(first.official_url) == "https://treehacks.com/apply"
    assert first.starts_at_raw == "2027-02-12"
    assert first.ends_at_raw == "2027-02-14"
    assert first.metadata["timezone"] is None
    assert first.metadata["is_free"] is True
    assert first.country_code == "US"
    assert first.tags == ["ai", "student"]
    assert second.source_event_id == "ai-builder-2026"
    assert second.ends_at_raw is None
    assert second.city == "Paris"
    assert second.country == "France"
    assert second.metadata["registration_url"] is None
    assert second.metadata["is_free"] is False
    assert second.metadata["registration_deadline_raw"] == "2026-10-30T23:00:00+01:00"


def test_curated_publisher_classification_does_not_whitelist_generic_sources() -> None:
    adapter = HackalendarPublicEventsAdapter(source())
    record = asyncio.run(adapter.parse_events(response([event(name="AI Builder Weekend")])))[0]
    data = record.model_dump(mode="json")
    assert prepare_event(data, source_key="untrusted_events") is None
    candidate = prepare_event(data, source_key="hackalendar")
    assert candidate is not None and candidate.event_type == "hackathon"
    assert candidate.fields["registration_url"][1] == "https://treehacks.com/apply"
    assert candidate.fields["is_free"][1] is True
    assert "registration_close_at" not in candidate.fields
    assert candidate.start_at is not None


def test_explicit_timezone_registration_deadline_becomes_canonical_observation() -> None:
    adapter = HackalendarPublicEventsAdapter(source())
    records = asyncio.run(
        adapter.parse_events(
            response([event(registrationDeadline="2026-11-10T23:59:00+01:00",
                            prizePool="Cash prizes TBD")])
        )
    )
    candidate = prepare_event(records[0].model_dump(mode="json"), source_key="hackalendar")
    assert candidate is not None
    assert candidate.fields["registration_close_at"][1] == "2026-11-10T23:59:00+01:00"
    assert candidate.fields["prize_details_text"][1] == "Cash prizes TBD"
    assert "participation_fee" not in candidate.fields

    undated = asyncio.run(
        adapter.parse_events(
            response([event(registrationDeadline="2026-11-10",
                            isFree=None, registrationUrl=None)])
        )
    )[0]
    other = prepare_event(undated.model_dump(mode="json"), source_key="hackalendar")
    assert other is not None
    assert "registration_close_at" not in other.fields
    assert "is_free" not in other.fields
    assert "registration_url" not in other.fields


def test_follow_only_documented_bounded_hackalendar_pagination() -> None:
    adapter = HackalendarPublicEventsAdapter(source())
    first = asyncio.run(adapter.parse_listing(response([event()], more=True, next_offset=1)))
    assert len(first) == 1
    assert str(first[0].url) == "https://hackalendar.com/api/events?limit=200&offset=1"
    assert first[0].discovery_method == "api_pagination"
    assert asyncio.run(
        adapter.parse_listing(response([event()], more=False, next_offset=None))
    ) == []


@pytest.mark.parametrize(
    "bad_offset",
    [-1, 0, 201, 100001, 1.0, True, "1", None],
)
def test_invalid_or_regressing_offsets_fail_closed(bad_offset: object) -> None:
    adapter = HackalendarPublicEventsAdapter(source())
    with pytest.raises(ValueError, match="offset"):
        asyncio.run(
            adapter.parse_listing(
                fetched({"data": [event()], "meta": {"hasMore": True, "nextOffset": bad_offset}})
            )
        )


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://attacker.example/api/events?limit=200&offset=0",
        "http://hackalendar.com/api/events?limit=200&offset=0",
        "https://hackalendar.com/api/private/subscriptions?limit=200&offset=0",
        "https://hackalendar.com/api/events?redirect=https://evil.test",
        "https://hackalendar.com/api/events?offset=0&offset=1",
    ],
)
def test_parser_refuses_other_hosts_routes_and_parameters(bad_url: str) -> None:
    adapter = HackalendarPublicEventsAdapter(source())
    with pytest.raises(ValueError, match="API|endpoint"):
        asyncio.run(adapter.parse_listing(response([event()], url=bad_url, more=True, next_offset=1)))


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"data": [], "meta": []},
        {"data": "not-a-list", "meta": {}},
        {"data": [{}] * 201, "meta": {}},
        {"events": [], "meta": {"hasMore": False}},
    ],
)
def test_schema_mismatch_does_not_claim_success(body: object) -> None:
    adapter = HackalendarPublicEventsAdapter(source())
    with pytest.raises(ValueError):
        asyncio.run(adapter.parse_events(fetched(body)))


def test_bounded_two_page_discovery_never_follows_organizer_urls() -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def fetch(self, definition: SourceDefinition, url: str) -> FetchResult:
            self.urls.append(url)
            if url == API:
                return response([event()], url=url, more=True, next_offset=1)
            if url.endswith("offset=1"):
                return response([event(slug="ai-builder-2027", name="AI Builder")], url=url)
            raise AssertionError("unexpected external fetch")

    fetcher = FakeFetcher()
    result = asyncio.run(
        run_source_once(HackalendarPublicEventsAdapter(source()), fetcher, max_pages=3)
    )
    assert result.status == "SUCCEEDED"
    assert result.fetched_pages == 2
    assert [r.source_event_id for r in result.records] == ["treehacks-2027", "ai-builder-2027"]
    assert fetcher.urls == [API, "https://hackalendar.com/api/events?limit=200&offset=1"]
