"""Offline regression fixtures for independently authorized Dribdat organizers.

No live requests, project pages, participant data or organizer websites are fetched.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hackathon_intelligence.incremental import prepare_event
from hackathon_sources.dribdat_adapter import DribdatOpenEventsAdapter
from hackathon_sources.factory import build_adapter, live_adapter_keys
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus
from hackathon_sources.registry import load_source_definitions
from hackathon_sources.runner import run_source_once

ROOT = Path(__file__).resolve().parents[3]
NEW = {
    "dribdat_hack4socialgood": "bd.hack4socialgood.ch",
    "dribdat_echtzeit": "make.echtzeitkultur.org",
}


def sources():
    registry = load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
    return [source for source in registry if source.key in NEW]


def response(key: str, items: list[object]) -> FetchResult:
    url = f"https://{NEW[key]}/api/events.json"
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="application/json",
        body=json.dumps({"events": items}).encode(),
        fetched_at=datetime(2026, 10, 28, tzinfo=UTC),
    )


def event(identifier: int, name: str, **extra: object) -> dict[str, object]:
    return {
        "id": identifier,
        "name": name,
        "starts_at": "Fri, 27 Mar 2026 09:00:00 +0100",
        "ends_at": "Sat, 28 Mar 2026 17:00:00 +0100",
        "summary": "Publicly described organizer activity",
        "hashtags": "",
        "webpage_url": "https://example.org/event",
        **extra,
    }


def test_two_new_sources_are_independently_approved_and_host_scoped() -> None:
    found = sources()
    assert len(found) == 2
    assert set(NEW) <= live_adapter_keys()
    for source in found:
        assert source.permission_status is PermissionStatus.ALLOWED
        assert source.ingestion_mode is IngestionMode.API
        assert source.detail_extraction_enabled and source.discovery_enabled
        assert source.durable_api_pages is False
        assert source.allowed_hosts == [NEW[source.key]]
        assert source.seed_urls[0].host == NEW[source.key]
        assert source.seed_urls[0].path == "/api/events.json"
        assert isinstance(build_adapter(source), DribdatOpenEventsAdapter)


@pytest.mark.parametrize(
    "source_key,event_name,accepted",
    [
        ("dribdat_hack4socialgood", "Hack4SocialGood 2026", True),
        ("dribdat_hack4socialgood", "Hack4SocialGood 2020", True),
        ("dribdat_hack4socialgood", "Data Toolbox", False),
        ("dribdat_hack4socialgood", "Hack4SocialGood Workshop 2026", False),
        ("dribdat_echtzeit", "GRAFFATHON 2022", True),
        ("dribdat_echtzeit", "ECHTZEIT GRAFFATHON", False),
        ("dribdat_echtzeit", "COSIN 2025", False),
        ("dribdat_echtzeit", "DEMOLAB 2025", False),
        ("dribdat_echtzeit", "Public Game Jam 2025", True),
    ],
)
def test_publisher_scoped_hackathon_labels_without_generic_workshop_false_positives(
    source_key: str, event_name: str, accepted: bool
) -> None:
    definition = next(source for source in sources() if source.key == source_key)
    adapter = build_adapter(definition)
    fetched = response(source_key, [event(24, event_name)])
    records = asyncio.run(adapter.parse_events(fetched))
    assert len(records) == 1
    candidate = prepare_event(records[0].model_dump(mode="json"), source_key=source_key)
    assert (candidate is not None) is accepted
    if accepted:
        assert candidate is not None and candidate.event_type in {"hackathon", "game_jam"}
        assert candidate.fields["short_description"][1] == "Publicly described organizer activity"
        assert candidate.start_at is not None
        assert candidate.end_at is not None


def test_publisher_name_does_not_grant_global_hackathon_classifier_override() -> None:
    assert prepare_event(
        {"name": "Hack4SocialGood 2026"}, source_key="dribdat_echtzeit"
    ) is None
    assert prepare_event(
        {"name": "GRAFFATHON 2022"}, source_key="dribdat_hack4socialgood"
    ) is None


def test_same_page_duplicate_ids_are_dropped_without_fusing_other_editions() -> None:
    definition = next(s for s in sources() if s.key == "dribdat_hack4socialgood")
    adapter = build_adapter(definition)
    rows = asyncio.run(
        adapter.parse_events(
            response(
                "dribdat_hack4socialgood",
                [
                    event(7, "Hack4SocialGood 2025", hashtags="#hackathon"),
                    event(7, "Duplicate entry"),
                    event(8, "Hack4SocialGood 2026"),
                    event(True, "Invalid boolean ID"),
                ],
            )
        )
    )
    assert [(r.source_event_id, r.name) for r in rows] == [
        ("7", "Hack4SocialGood 2025"),
        ("8", "Hack4SocialGood 2026"),
    ]
    assert rows[0].tags == ["hackathon"]


def test_publisher_api_does_not_automatically_fetch_external_webpage_links() -> None:
    definition = next(s for s in sources() if s.key == "dribdat_echtzeit")

    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def fetch(self, source, url: str) -> FetchResult:
            self.urls.append(url)
            return response(
                "dribdat_echtzeit",
                [event(7, "GRAFFATHON 2022", webpage_url="https://external.org/landing")],
            )

    fetcher = FakeFetcher()
    outcome = asyncio.run(run_source_once(build_adapter(definition), fetcher, max_pages=3))
    assert outcome.status == "SUCCEEDED"
    assert outcome.fetched_pages == 1
    assert fetcher.urls == ["https://make.echtzeitkultur.org/api/events.json"]
    assert str(outcome.records[0].official_url) == "https://external.org/landing"


def test_registration_and_event_status_remain_unknown_for_dribdat_sources() -> None:
    definition = next(s for s in sources() if s.key == "dribdat_hack4socialgood")
    adapter = build_adapter(definition)
    record = asyncio.run(
        adapter.parse_events(
            response("dribdat_hack4socialgood", [event(2, "Hack4SocialGood 2026")])
        )
    )[0]
    candidate = prepare_event(record.model_dump(mode="json"), source_key=definition.key)
    assert candidate is not None
    assert "registration_close_at" not in candidate.fields
    assert "is_free" not in candidate.fields
    assert "registration_state" not in candidate.fields
