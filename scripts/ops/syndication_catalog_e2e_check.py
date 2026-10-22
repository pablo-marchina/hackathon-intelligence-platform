"""Isolated E2E drill: synthetic RSS -> durable backlog -> canonical -> search.

Runs against a throwaway PostgreSQL database ONLY. No real HTTP, third-party
source, Celery, email or scheduled crawl is activated. Never run in production.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from hackathon_api.repositories.hackathons import HackathonRepository
from hackathon_api.repositories.coverage import CoverageRepository
from hackathon_api.schemas import HackathonFilters
from hackathon_intelligence.incremental import CanonicalProcessor
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.persistent_runner import run_source_persisted
from hackathon_sources.syndication import SyndicatedEventAdapter


class FakeFetcher:
    def __init__(self, responses: dict[str, FetchResult]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    async def fetch(self, definition: SourceDefinition, url: str, **kwargs) -> FetchResult:
        # Absolutely no network access. Reject unexpected fetches.
        self.requests.append(url)
        return self.responses[url]


def _response(url: str, data: str, content_type: str) -> FetchResult:
    return FetchResult(requested_url=url, final_url=url, status_code=200,
                       body=data.encode('utf-8'), content_type=content_type,
                       fetched_at=datetime.now(UTC))


def verify() -> None:
    if os.environ.get('APP_ENV') != 'test' or os.environ.get('HIP_ALLOW_SYNDICATION_E2E') != '1':
        raise RuntimeError('explicit test environment and HIP_ALLOW_SYNDICATION_E2E=1 required')
    parsed = make_url(os.environ['DATABASE_URL'])
    if parsed.host not in {'localhost', '127.0.0.1'} or (
        parsed.database != 'hackathon' and '_test' not in (parsed.database or '')
    ):
        raise RuntimeError('refusing non-local or non-test PostgreSQL database')
    tag = uuid4().hex[:12]
    domain = f'{tag}.invalid'  # impossible to reach; FakeFetcher never performs HTTP
    root = f'https://{domain}/feed.xml'
    events = [f'https://{domain}/events/{i}' for i in range(3)]
    rss = '<rss><channel>' + ''.join(f'<item><link>{url}</link></item>' for url in events) + '</channel></rss>'
    responses = {root: _response(root, rss, 'application/rss+xml')}
    for i, url in enumerate(events):
        html = '<script type="application/ld+json">' + json.dumps({
            '@context': 'https://schema.org', '@type': 'Event',
            'name': f'CI Global Hackathon {tag} {i}',
            'startDate': f'2027-0{i+1}-15T10:00:00Z',
            'endDate': f'2027-0{i+1}-16T10:00:00Z',
            'url': url,
            'organizer': {'@type': 'Organization', 'name': 'Test University'},
        }) + '</script>'
        responses[url] = _response(url, html, 'text/html')
    adapter = SyndicatedEventAdapter(SourceDefinition(
        key=f'ci_{tag}', name=f'CI University {tag}', domain=domain,
        source_family=f'ci-syndication-{tag}', source_type='official_calendar',
        ingestion_mode=IngestionMode.RSS, permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True, seed_urls=[root],
    ))
    engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            initial = CoverageRepository(conn).snapshot()
            fetcher = FakeFetcher(responses)
            # Each run is deliberately too short to consume the whole feed.
            outcomes = [asyncio.run(run_source_persisted(adapter, fetcher, conn, max_pages=1))
                        for _ in range(4)]
            assert all(x.status == 'SUCCEEDED' for x in outcomes), outcomes
            assert [x.persisted_event_refs for x in outcomes] == [0, 1, 1, 1]
            assert fetcher.requests == [root] + events, fetcher.requests
            assert sum(x.discovered_links for x in outcomes) == 3
            source_id = conn.execute(text('''
                SELECT id::text FROM sources WHERE domain=:domain AND source_family=:family
            '''), {'domain': domain, 'family': f'ci-syndication-{tag}'}).scalar_one()
            staged = conn.execute(text('''
                SELECT count(*) FROM source_event_payloads p
                JOIN source_event_refs r ON p.source_event_ref_id=r.id
                WHERE r.source_id=CAST(:sid AS uuid)
            '''), {'sid': source_id}).scalar_one()
            assert staged == 3, staged
            processor = CanonicalProcessor(conn)
            results = [processor.process_one() for _ in range(3)]
            conn.commit()
            assert results == ['CREATED'] * 3, results
            assert processor.process_one() is None
            search = HackathonRepository(conn).search(HackathonFilters(q=tag, limit=25))
            assert len(search.items) == 3, [item.name for item in search.items]
            assert len({str(item.id) for item in search.items}) == 3
            # End-to-end measurements must reflect the real database, never
            # demo data or summed attribution across overlapping sources.
            measured = CoverageRepository(conn).snapshot()
            assert measured['catalog']['unique_active'] == initial['catalog']['unique_active'] + 3
            assert measured['growth']['new_active_canonicals'] == initial['growth']['new_active_canonicals'] + 3
            assert sum(day['new_active_canonicals'] for day in measured['growth']['daily']) == measured['growth']['new_active_canonicals']
            assert measured['pipeline']['staged_payloads'] == initial['pipeline']['staged_payloads'] + 3
            assert measured['pipeline']['awaiting_normalization'] == initial['pipeline']['awaiting_normalization']
            assert measured['field_completeness']['counts']['with_organizer'] >= initial['field_completeness']['counts']['with_organizer']
            own_source = next(row for row in measured['sources'] if row['source_id'] == source_id)
            assert own_source['attributed_unique_active'] == 3
            assert own_source['source_refs'] == 3
            assert own_source['new_source_refs_recent'] == 3
            assert own_source['new_attributed_canonicals_recent'] == 3
            # A subsequent collector run must not refresh DONE seeds prematurely.
            followup = asyncio.run(run_source_persisted(adapter, fetcher, conn, max_pages=2))
            assert followup.fetched_pages == 0
            assert len(fetcher.requests) == 4
            assert processor.process_one() is None
            print('E2E PASS: RSS -> backlog -> 3 source records -> 3 canonicals -> searchable results')
    finally:
        # This test is intended for an isolated throwaway DB in CI; discard it
        # after the job. No cleanup attempt may connect to production servers.
        engine.dispose()


if __name__ == '__main__':
    verify()
