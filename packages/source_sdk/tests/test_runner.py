from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from hackathon_sources.factory import build_adapter, live_adapter_keys
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.runner import run_source_once


class FakeFetcher:
    async def fetch(self, source, url, *, etag=None, last_modified=None):
        page = 2 if "page=2" in url else 1
        payload = {
            "data": [
                {
                    "id": f"event-{page}",
                    "name": f"Hack {page}",
                    "website": f"https://event{page}.example",
                    "starts_at": "2026-11-01T10:00:00Z",
                    "ends_at": "2026-11-01T20:00:00Z",
                    "modality": "online",
                    "location": {},
                }
            ],
            "links": {
                "next": "https://dash.hackathons.hackclub.com/api/v1/hackathons?page=2"
                if page == 1
                else None
            },
        }
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=json.dumps(payload).encode(),
            fetched_at=datetime.now(UTC),
        )


def source() -> SourceDefinition:
    return SourceDefinition(
        key="hackclub_hackathons",
        name="Hack Club Hackathons",
        domain="dash.hackathons.hackclub.com",
        source_family="hackclub_hackathons",
        source_type="hackathon_directory",
        ingestion_mode=IngestionMode.API,
        permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        seed_urls=["https://dash.hackathons.hackclub.com/api/v1/hackathons"],
    )


def test_live_adapter_registry_contains_all_authorized_public_api_sources() -> None:
    assert live_adapter_keys() == {
        "agenda_tech_brasil",
        "hackalendar",
        "hackclub_hackathons",
        "hackclub_programs",
        "hackclub_community_events",
        "hackclub_ysws_programs",
        "dribdat_glamhack",
        "dribdat_imprs_astro",
        "dribdat_opendata_ch",
        "dribdat_hack4socialgood",
        "dribdat_echtzeit",
    }


def test_runner_follows_bounded_same_host_api_pagination() -> None:
    output = asyncio.run(run_source_once(build_adapter(source()), FakeFetcher()))
    assert output.fetched_pages == 2
    assert [record.name for record in output.records] == ["Hack 1", "Hack 2"]
    assert output.errors == []
