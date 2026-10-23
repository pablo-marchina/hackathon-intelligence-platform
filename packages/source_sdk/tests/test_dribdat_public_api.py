"""Public, documented Dribdat /api/events.json parsing and canonical acceptance."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hackathon_intelligence.incremental import prepare_event
from hackathon_sources.dribdat_adapter import MAX_DRIBDAT_EVENTS, DribdatOpenEventsAdapter
from hackathon_sources.factory import build_adapter, live_adapter_keys
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus
from hackathon_sources.registry import load_source_definitions

ROOT = Path(__file__).resolve().parents[3]
API = "https://hack.glam.opendata.ch/api/events.json"


def approved_sources():
    return [
        source
        for source in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
        if source.key in {"dribdat_glamhack", "dribdat_imprs_astro"}
    ]


def response(payload: object, *, url: str = API) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="application/json",
        body=json.dumps(payload).encode(),
        fetched_at=datetime.now(UTC),
    )


def fixture():
    return {
        "events": [
            {
                "id": 13,
                "name": "GLAMhack 2026",
                "starts_at": "Thu, 12 Nov 2026 09:00:00 GMT",
                "ends_at": "Sat, 14 Nov 2026 16:00:00 GMT",
                "summary": "Swiss Open Cultural Data Hackathon",
                "location": "campo Winterthur",
                "hostname": "SKKG",
                "webpage_url": "https://opendata.ch/events/glamhack-2026/",
                "logo_url": "https://assets.example.com/glamhack.png",
                "has_finished": False,
                "hashtags": "#glamhack",
            },
            {
                "id": 9,
                "name": "Open Cultural Data Hackathon 2015",
                "starts_at": "Fri, 27 Feb 2015 09:00:00 GMT",
                "ends_at": "Sat, 28 Feb 2015 16:00:00 GMT",
                "summary": "First Open Cultural Data Hackathon",
                "location": "Bern",
                "hostname": "National Library",
                "webpage_url": "https://opendata.ch/2015",
            },
            {
                "id": 15,
                "name": "Ordinary Open Data Workshop",
                "starts_at": "Sat, 28 Feb 2026 16:00:00 GMT",
                "ends_at": "Sat, 28 Feb 2026 17:00:00 GMT",
            },
        ]
    }


def test_two_verified_organizer_instances_are_independently_authorized_and_rate_limited():
    sources = approved_sources()
    assert {source.key for source in sources} == {"dribdat_glamhack", "dribdat_imprs_astro"}
    for source in sources:
        assert source.ingestion_mode is IngestionMode.API
        assert source.permission_status is PermissionStatus.ALLOWED
        assert source.detail_extraction_enabled is True
        assert source.concurrency_limit == 1
        assert source.rate_limit_per_minute is not None
        assert source.rate_limit_per_minute <= 2
        assert source.durable_api_pages is False
        assert len(source.seed_urls) == 1
        assert str(source.seed_urls[0]) == f"https://{source.domain}/api/events.json"
        assert isinstance(build_adapter(source), DribdatOpenEventsAdapter)
        assert source.key in live_adapter_keys()


def test_historical_and_upcoming_editions_preserve_gmt_dates_and_source_evidence():
    adapter = DribdatOpenEventsAdapter(approved_sources()[0])
    records = asyncio.run(adapter.parse_events(response(fixture())))
    assert [record.source_event_id for record in records] == ["13", "9", "15"]
    upcoming = records[0]
    assert upcoming.starts_at_raw == "2026-11-12T09:00:00+00:00"
    assert upcoming.ends_at_raw == "2026-11-14T16:00:00+00:00"
    assert str(upcoming.official_url) == "https://opendata.ch/events/glamhack-2026/"
    assert upcoming.metadata["description"] == "Swiss Open Cultural Data Hackathon"
    assert upcoming.metadata["host_venue"] == "SKKG"
    assert upcoming.metadata["location_text"] == "campo Winterthur"
    assert upcoming.metadata["source_finished_flag"] is False
    candidates = [
        prepare_event(record.model_dump(mode="json"), source_key=record.source_key)
        for record in records
    ]
    assert candidates[0] is not None  # Explicit GLAMhack title, not all Dribdat records.
    assert candidates[1] is not None
    assert candidates[2] is None
    assert candidates[0].fields["short_description"][1] == "Swiss Open Cultural Data Hackathon"
    assert candidates[0].fields["short_description"][3].endswith("metadata.description")
    assert "registration_close_at" not in candidates[0].fields
    assert "eligible_worldwide" not in candidates[0].fields
    assert "prize_pool_usd" not in candidates[0].fields
    assert "is_free" not in candidates[0].fields
    assert candidates[0].start_at is not None and candidates[0].end_at is not None


def test_astro_germany_is_historical_with_original_url():
    astro = next(s for s in approved_sources() if s.key == "dribdat_imprs_astro")
    payload = {
        "events": [
            {
                "id": 1,
                "name": "IMPRS-Astro Hackathon 2024",
                "starts_at": "Tue, 21 May 2024 10:00:00 GMT",
                "ends_at": "Fri, 24 May 2024 17:00:00 GMT",
                "location": "Heidelberg, Germany",
                "webpage_url": "https://imprs-astro-hackathon.de",
            }
        ]
    }
    record = asyncio.run(
        build_adapter(astro).parse_events(response(payload, url=str(astro.seed_urls[0])))
    )[0]
    assert record.source_event_id == "1"
    assert record.starts_at_raw == "2024-05-21T10:00:00+00:00"
    assert record.metadata["location_text"] == "Heidelberg, Germany"
    assert prepare_event(record.model_dump(mode="json"), source_key=astro.key) is not None


def test_invalid_rows_and_unsafe_urls_do_not_poison_valid_hackathons():
    data = fixture()
    data["events"].extend(
        [
            {"id": True, "name": "Fake Hackathon"},
            {"id": "bad", "name": "Fake Hackathon"},
            {"id": 0, "name": "Fake Hackathon"},
            {"id": 13, "name": "Duplicate GLAMhack 2026"},
            {"id": 17, "name": False},
            {
                "id": 19,
                "name": "Safe Data Hackathon",
                "starts_at": "not a date",
                "ends_at": "Thu, 12 Nov 2026 12:00:00",  # No explicit timezone.
                "webpage_url": "https://user:password@example.org",
                "logo_url": "file:///etc/passwd",
                "has_finished": "false",
            },
        ]
    )
    records = asyncio.run(
        DribdatOpenEventsAdapter(approved_sources()[0]).parse_events(response(data))
    )
    assert len(records) == 4
    safe = records[-1]
    assert safe.official_url is None
    assert safe.starts_at_raw is None
    assert safe.ends_at_raw is None
    assert safe.metadata["logo_url"] is None
    assert safe.metadata["source_finished_flag"] is None


@pytest.mark.parametrize("payload", [{}, [], {"events": None}, {"events": {}}])
def test_missing_or_invalid_event_collections_are_empty(payload):
    adapter = DribdatOpenEventsAdapter(approved_sources()[0])
    assert asyncio.run(adapter.parse_events(response(payload))) == []


def test_oversized_feed_fails_explicitly_instead_of_dropping_late_editions():
    adapter = DribdatOpenEventsAdapter(approved_sources()[0])
    payload = {
        "events": [{"id": n + 1, "name": "Hackathon"} for n in range(MAX_DRIBDAT_EVENTS + 1)]
    }
    with pytest.raises(ValueError, match="exceeds"):
        asyncio.run(adapter.parse_events(response(payload)))
