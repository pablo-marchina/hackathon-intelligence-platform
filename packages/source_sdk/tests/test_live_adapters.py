from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime

from hackathon_sources.live_adapters import (
    AgendaTechBrasilAdapter,
    HackClubCommunityEventsAdapter,
    HackClubHackathonsAdapter,
    HackClubProgramsAdapter,
)
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition


def definition(key: str, domain: str) -> SourceDefinition:
    return SourceDefinition(
        key=key,
        name=key,
        domain=domain,
        source_family=key,
        source_type="api",
        ingestion_mode=IngestionMode.API,
        permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        seed_urls=[f"https://{domain}/feed"],
    )


def result(payload: object, url: str = "https://example.com/feed") -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="application/json",
        body=json.dumps(payload).encode(),
        fetched_at=datetime.now(UTC),
    )


def test_hackclub_hackathons_parser_and_pagination() -> None:
    adapter = HackClubHackathonsAdapter(
        definition("hackclub_hackathons", "dash.hackathons.hackclub.com")
    )
    payload = {
        "data": [
            {
                "id": "abc",
                "name": "Example Hacks",
                "website": "https://example.org",
                "starts_at": "2026-10-03T10:00:00Z",
                "ends_at": "2026-10-04T18:00:00Z",
                "modality": "in_person",
                "location": {
                    "city": "Boston",
                    "province": "MA",
                    "country": "United States",
                    "country_code": "US",
                },
            }
        ],
        "links": {"next": "https://dash.hackathons.hackclub.com/api/v1/hackathons?page=2"},
    }
    fetched = result(payload, "https://dash.hackathons.hackclub.com/api/v1/hackathons")
    records = asyncio.run(adapter.parse_events(fetched))
    links = asyncio.run(adapter.parse_listing(fetched))
    assert records[0].name == "Example Hacks"
    assert records[0].country_code == "US"
    assert str(records[0].official_url) == "https://example.org/"
    assert "page=2" in str(links[0].url)


def test_hackclub_programs_parser_preserves_eligibility() -> None:
    adapter = HackClubProgramsAdapter(definition("hackclub_programs", "hackclub.com"))
    records = asyncio.run(
        adapter.parse_events(
            result(
                {
                    "data": [
                        {
                            "id": "rec-capitol",
                            "name": "Capitol",
                            "url": "https://capitol.hackclub.com/",
                            "startDate": "2026-09-06",
                            "endDate": "2026-10-11",
                            "format": "both",
                            "requirements": "U.S. Residents Only",
                            "projectTypes": ["Websites", "Software"],
                        }
                    ]
                },
                "https://hackclub.com/api/v1/events",
            )
        )
    )
    assert records[0].eligibility_raw == "U.S. Residents Only"
    assert records[0].tags == ["Websites", "Software"]


def test_hackclub_community_events_parser() -> None:
    adapter = HackClubCommunityEventsAdapter(
        definition("hackclub_community", "events.hackclub.com")
    )
    records = asyncio.run(
        adapter.parse_events(
            result(
                [
                    {
                        "id": "uuid",
                        "slug": "code-in-the-dark",
                        "title": "Code in the Dark",
                        "start": "2026-09-23T20:00:00Z",
                        "end": "2026-09-23T21:00:00Z",
                        "tags": ["workshop"],
                    }
                ],
                "https://events.hackclub.com/api/events/upcoming",
            )
        )
    )
    assert records[0].name == "Code in the Dark"
    assert str(records[0].official_url) == "https://events.hackclub.com/code-in-the-dark"


def test_agenda_tech_brasil_parser_skips_archived_years() -> None:
    inner = {
        "eventos": [
            {"ano": 2025, "arquivado": True, "meses": []},
            {
                "ano": 2026,
                "arquivado": False,
                "meses": [
                    {
                        "mes": "setembro",
                        "arquivado": False,
                        "eventos": [
                            {
                                "nome": "Brazil Hack",
                                "data": ["23", "24"],
                                "url": "https://example.com/hack",
                                "cidade": "São Paulo",
                                "uf": "SP",
                                "tipo": "presencial",
                            }
                        ],
                    }
                ],
            },
        ]
    }
    wrapper = {
        "encoding": "base64",
        "content": base64.b64encode(json.dumps(inner).encode()).decode(),
    }
    adapter = AgendaTechBrasilAdapter(definition("agenda_tech_brasil", "api.github.com"))
    records = asyncio.run(
        adapter.parse_events(
            result(
                wrapper,
                "https://api.github.com/repos/agenda-tech-brasil/agenda-tech-brasil/contents/src/db/database.json",
            )
        )
    )
    assert len(records) == 1
    assert records[0].starts_at_raw == "2026-09-23"
    assert records[0].ends_at_raw == "2026-09-24"
    assert records[0].country_code == "BR"
