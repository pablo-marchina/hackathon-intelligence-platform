"""Public official Hack Club YSWS API: only explicit hackathon editions are accepted.

The published source is a general programs directory; do not turn all YSWS
programs into hackathons, or mistake a financial URL for a prize.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hackathon_intelligence.incremental import prepare_event
from hackathon_sources.factory import build_adapter, live_adapter_keys
from hackathon_sources.live_adapters import HackClubYswsProgramsAdapter
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus
from hackathon_sources.registry import load_source_definitions

ROOT = Path(__file__).resolve().parents[3]
SOURCE = "hackclub_ysws_programs"
URL = "https://events-api.hackclub.dev/api/programs"


def definition():
    return next(
        src
        for src in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
        if src.key == SOURCE
    )


def response(payload: object) -> FetchResult:
    return FetchResult(
        requested_url=URL,
        final_url=URL,
        status_code=200,
        content_type="application/json",
        body=json.dumps(payload).encode(),
        fetched_at=datetime.now(UTC),
    )


def fixture() -> dict:
    return {
        "success": True,
        "count": 4,
        "data": [
            {
                "id": "recHackA",
                "Name": "International AI Hackathon 2027",
                "Start Date": "2027-05-18",
                "End Date": "2027-05-20",
                "Website URL": "https://ai.example.org/2027",
                "Icon – CDN Link": "https://cdn.example.org/logo.png",
                "HCB": "https://hcb.hackclub.com/example",
                "createdTime": "2026-10-23T00:00:00Z",
            },
            {
                "id": "recHackB",
                "Name": "Regional Climate Datathon",
                "Start Date": "2027-06-11",
                "End Date": "2027-06-13",
                "Website URL": "https://climate.example.org",
            },
            {
                "id": "recWorkshop",
                "Name": "Electronics Workshop",
                "Start Date": "2027-05-01",
                "End Date": "2027-05-02",
                "Website URL": "https://workshop.example.org",
            },
            {
                "id": "recProgram",
                "Name": "Stardance",
                "Start Date": "2027-05-11",
                "End Date": "2027-08-11",
                "Website URL": "https://stardance.example.org",
            },
        ],
    }


def test_official_public_read_only_source_has_conservative_limits_and_unique_family() -> None:
    source = definition()
    all_sources = load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    assert source.permission_status is PermissionStatus.ALLOWED
    assert source.ingestion_mode is IngestionMode.API
    assert source.allowed_hosts == ["events-api.hackclub.dev"]
    assert source.seed_urls == [source.seed_urls[0]]
    assert str(source.seed_urls[0]) == URL
    assert source.rate_limit_per_minute is not None
    assert source.rate_limit_per_minute <= 4
    assert source.concurrency_limit == 1
    assert source.durable_api_pages is False  # No published pagination.
    assert source.source_family not in {
        other.source_family for other in all_sources if other.key != SOURCE
    }
    assert SOURCE in live_adapter_keys()
    assert isinstance(build_adapter(source), HackClubYswsProgramsAdapter)


def test_adapter_follows_published_airtable_labels_and_keeps_public_fields() -> None:
    adapter = build_adapter(definition())
    records = asyncio.run(adapter.parse_events(response(fixture())))
    assert len(records) == 4
    assert len({record.source_event_id for record in records}) == 4
    a = records[0]
    assert a.source_event_id == "recHackA"
    assert a.name == "International AI Hackathon 2027"
    assert str(a.source_url) == URL
    assert str(a.official_url) == "https://ai.example.org/2027"
    assert a.starts_at_raw == "2027-05-18"
    assert a.ends_at_raw == "2027-05-20"
    assert a.metadata["icon_url"] == "https://cdn.example.org/logo.png"
    assert a.metadata["hcb_url"] == "https://hcb.hackclub.com/example"
    assert a.metadata["source_created_at"] == "2026-10-23T00:00:00Z"
    assert a.metadata["public_ysws_program"] is True
    # HCB is financial metadata, not a verified prize pool or registration fee.
    accepted = [
        prepare_event(record.model_dump(mode="json"), source_key=SOURCE) for record in records
    ]
    assert accepted[0] is not None
    assert accepted[1] is not None
    assert accepted[2] is None
    assert accepted[3] is None
    assert "prize_pool_usd" not in accepted[0].fields
    assert "registration_close_at" not in accepted[0].fields
    assert "student_required" not in accepted[0].fields
    assert "is_free" not in accepted[0].fields


def test_invalid_schema_does_not_create_false_events() -> None:
    adapter = build_adapter(definition())
    assert (
        asyncio.run(adapter.parse_events(response({"success": False, "data": fixture()["data"]})))
        == []
    )
    assert asyncio.run(adapter.parse_events(response({"data": fixture()["data"]}))) == []
    assert asyncio.run(adapter.parse_events(response({"success": True, "data": {}}))) == []
    invalid = fixture()
    invalid["data"] = [
        {"id": "", "Name": "Fake Hackathon"},
        {"id": "recFoo", "Name": None},
        {"id": "recWrong", "Name": 123},
        [],
        None,
    ]
    assert asyncio.run(adapter.parse_events(response(invalid))) == []


def test_malformed_optional_fields_do_not_poison_whole_source() -> None:
    adapter = build_adapter(definition())
    bad = {
        "success": True,
        "data": [
            {
                "id": "recSafe",
                "Name": "Global Hackathon",
                "Start Date": {"not": "a date"},
                "End Date": False,
                "Website URL": "https://",
                "Icon – CDN Link": "https://user:pass@example.org/private",
                "HCB": "http://",
            }
        ],
    }
    records = asyncio.run(adapter.parse_events(response(bad)))
    assert len(records) == 1
    assert records[0].official_url is None
    assert records[0].starts_at_raw is None
    assert records[0].ends_at_raw is None
    assert records[0].metadata["icon_url"] is None
    assert records[0].metadata["hcb_url"] is None
    assert prepare_event(records[0].model_dump(mode="json"), source_key=SOURCE) is not None


def test_overlarge_list_must_fail_not_silently_report_success() -> None:
    adapter = build_adapter(definition())
    payload = {
        "success": True,
        "data": [{"id": str(i), "Name": "AI Hackathon"} for i in range(5001)],
    }
    with pytest.raises(ValueError, match="bounded record budget"):
        asyncio.run(adapter.parse_events(response(payload)))
