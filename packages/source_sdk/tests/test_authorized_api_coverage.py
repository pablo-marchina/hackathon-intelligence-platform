"""Authorized source API partitions and historical catalogue coverage.

All HTTP is replaced by synthetic fixtures. External official URLs are stored
as source evidence only: no cross-origin fetch is attempted.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path

from hackathon_intelligence.incremental import normalize_format, prepare_event
from hackathon_sources.factory import build_adapter
from hackathon_sources.live_adapters import AgendaTechBrasilAdapter
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.registry import load_source_definitions
from hackathon_sources.runner import run_source_once

ROOT = Path(__file__).resolve().parents[3]


def source(key: str) -> SourceDefinition:
    return next(
        definition
        for definition in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
        if definition.key == key
    )


def response(payload: object, url: str) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="application/json",
        body=json.dumps(payload).encode("utf-8"),
        fetched_at=datetime.now(UTC),
    )


def test_documented_event_status_partitions_are_bounded_and_same_source() -> None:
    entry = source("hackclub_programs")
    assert entry.permission_status is PermissionStatus.ALLOWED
    assert entry.ingestion_mode is IngestionMode.API
    assert entry.allowed_hosts == ["hackclub.com"]
    assert len(entry.seed_urls) == 3
    assert {str(u).split("status=", 1)[1].split("&", 1)[0] for u in entry.seed_urls} == {
        "upcoming",
        "ongoing",
        "ended",
    }
    assert all(
        str(u).startswith("https://hackclub.com/api/v1/events?status=")
        and str(u).endswith("&limit=100")
        for u in entry.seed_urls
    )


def test_status_partitioned_adapter_yields_distinct_events_and_real_hybrid_format() -> None:
    entry = source("hackclub_programs")
    names = {
        "upcoming": "AI Hackathon",
        "ongoing": "Climate Datathon",
        "ended": "Global Buildathon",
    }

    class StubFetcher:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def fetch(self, definition, url, *, etag=None, last_modified=None):
            assert definition.key == "hackclub_programs"
            self.calls.append(url)
            status = url.split("status=", 1)[1].split("&", 1)[0]
            return response(
                {
                    "data": [
                        {
                            "id": f"rec-{status}",
                            "slug": f"{status}-2026",
                            "name": names[status],
                            "description": "Hackathon project building and team awards.",
                            "url": f"https://{status}.example.org",
                            "startDate": "2026-09-01",
                            "endDate": "2026-09-05",
                            "status": status,
                            "format": "both",
                            "projectTypes": ["Software"],
                            "requirements": "Open to students",
                            "inPerson": {"location": "São Paulo, Brazil"},
                        }
                    ],
                    "meta": {"count": 1, "generatedAt": "2026-10-22T00:00:00Z"},
                },
                url,
            )

    fetcher = StubFetcher()
    result = asyncio.run(run_source_once(build_adapter(entry), fetcher, max_pages=3))
    assert result.status == "SUCCEEDED"
    assert result.fetched_pages == 3
    assert len(fetcher.calls) == 3
    assert {str(x.official_url) for x in result.records} == {
        "https://upcoming.example.org/",
        "https://ongoing.example.org/",
        "https://ended.example.org/",
    }
    assert {x.source_event_id for x in result.records} == {
        "rec-upcoming",
        "rec-ongoing",
        "rec-ended",
    }
    for record in result.records:
        candidate = prepare_event(record.model_dump(mode="json"), source_key=record.source_key)
        assert candidate is not None
        assert candidate.event_format == "HYBRID"
        assert candidate.fields["short_description"][1] == (
            "Hackathon project building and team awards."
        )
        assert candidate.fields["source_event_status"][1] in {"upcoming", "ongoing", "ended"}
        assert candidate.fields["participation_requirements"][1] == "Open to students"
        assert candidate.fields["participation_requirements"][3].endswith("eligibility_raw")
        assert candidate.fields["source_event_status"][3].endswith("metadata.status")
    # Seed URLs are the only fetched URLs: organizer sites are not crawled.
    assert all(url.startswith("https://hackclub.com/api/v1/events?") for url in fetcher.calls)


def test_source_status_does_not_claim_registration_is_open() -> None:
    raw = {
        "source_key": "hackclub_programs",
        "name": "Regional Hackathon",
        "starts_at_raw": "2026-11-11",
        "format_raw": "in-person",
        "metadata": {"status": "upcoming", "description": "A local event."},
    }
    candidate = prepare_event(raw, source_key="hackclub_programs")
    assert candidate is not None
    assert candidate.event_format == "IN_PERSON"
    assert "status" not in candidate.fields
    assert candidate.fields["source_event_status"][1] == "upcoming"
    assert "registration_close_at" not in candidate.fields
    assert normalize_format("both") == "HYBRID"


def agenda_document() -> dict:
    return {
        "eventos": [
            {
                "ano": 2025,
                "arquivado": True,
                "meses": [
                    {
                        "mes": "fevereiro",
                        "arquivado": True,
                        "eventos": [
                            {
                                "nome": "Archived Global Hackathon",
                                "data": ["12", "13"],
                                "url": "https://historic.example.org/",
                                "tipo": "online",
                            },
                            {
                                "nome": "Archived Workshop",
                                "data": ["14"],
                                "url": "https://workshop.example.org/",
                                "tipo": "online",
                            },
                        ],
                    },
                ],
            },
            {
                "ano": 2026,
                "arquivado": False,
                "meses": [
                    {
                        "mes": "outubro",
                        "arquivado": False,
                        "eventos": [
                            {
                                "nome": "São Paulo Hackathon",
                                "data": ["12", "not-a-day", "13", "40", "12"],
                                "url": "https://event.example.org/",
                                "tipo": "presencial",
                            },
                            {
                                "nome": "Student Datathon",
                                "data": ["12", "13"],
                                "url": None,
                                "tipo": "online",
                            },
                            {
                                "nome": "Game Jam Weekend",
                                "data": ["12", "13"],
                                "url": None,
                                "tipo": "online",
                            },
                        ],
                    },
                ],
            },
        ]
    }


def _agenda_result(payload: dict) -> FetchResult:
    url = "https://api.github.com/repos/agenda-tech-brasil/agenda-tech-brasil/contents/src/db/database.json"
    wrapper = {
        "encoding": "base64",
        "content": base64.b64encode(json.dumps(payload).encode()).decode(),
    }
    return response(wrapper, url)


def test_archived_years_and_months_are_retained_with_provenance() -> None:
    adapter = AgendaTechBrasilAdapter(source("agenda_tech_brasil"))
    records = asyncio.run(adapter.parse_events(_agenda_result(agenda_document())))
    assert len(records) == 5
    historic = next(record for record in records if record.name == "Archived Global Hackathon")
    assert historic.starts_at_raw == "2025-02-12"
    assert historic.ends_at_raw == "2025-02-13"
    assert historic.metadata["archived"] is True
    assert historic.source_event_id
    event = prepare_event(historic.model_dump(mode="json"), source_key=historic.source_key)
    assert event is not None
    assert event.event_type == "hackathon"
    # The canonical classification still excludes generic non-hackathon listings.
    other = next(record for record in records if record.name == "Archived Workshop")
    assert prepare_event(other.model_dump(mode="json"), source_key=other.source_key) is None


def test_event_identity_is_stable_when_days_change_and_distinguishes_same_date() -> None:
    adapter = AgendaTechBrasilAdapter(source("agenda_tech_brasil"))
    payload = agenda_document()
    before = asyncio.run(adapter.parse_events(_agenda_result(payload)))
    ids = {record.name: record.source_event_id for record in before}
    payload["eventos"][1]["meses"][0]["eventos"][0]["data"] = ["11", "12", "13"]
    after = asyncio.run(adapter.parse_events(_agenda_result(payload)))
    assert {record.name: record.source_event_id for record in after} == ids
    assert len(set(ids.values())) == len(ids)
    missing_site = next(record for record in before if record.name == "Student Datathon")
    assert missing_site.official_url is None
    assert (
        missing_site.source_event_id
        != next(record for record in before if record.name == "Game Jam Weekend").source_event_id
    )
    assert (
        prepare_event(missing_site.model_dump(mode="json"), source_key=missing_site.source_key)
        is not None
    )
    active = next(record for record in before if record.name == "São Paulo Hackathon")
    assert active.metadata["days"] == ["12", "13"]
    assert active.metadata["archived"] is False


def test_broken_calendar_children_or_dates_do_not_break_other_editions() -> None:
    adapter = AgendaTechBrasilAdapter(source("agenda_tech_brasil"))
    for payload in (
        {"eventos": None},
        {"eventos": [{"ano": 2026, "meses": None}]},
        {"eventos": [{"ano": "not-year", "meses": []}]},
        {"eventos": [{"ano": 2200, "meses": []}]},
    ):
        assert asyncio.run(adapter.parse_events(_agenda_result(payload))) == []

    payload = agenda_document()
    payload["eventos"][1]["meses"][0]["eventos"][0]["data"] = ["not-a-day"]
    records = asyncio.run(adapter.parse_events(_agenda_result(payload)))
    event = next(record for record in records if record.name == "São Paulo Hackathon")
    assert event.starts_at_raw is None
    assert event.ends_at_raw is None
    assert event.source_event_id is not None
