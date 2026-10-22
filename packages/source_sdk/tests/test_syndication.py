"""Iteration 19: permission-gated syndicated discovery to canonical-ready records."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from hackathon_sources.factory import build_adapter
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.runner import run_source_once
from hackathon_sources.seed_adapter import SeedUrlAdapter
from hackathon_sources.syndication import MAX_FEED_LINKS, SyndicatedEventAdapter


def source(mode: IngestionMode = IngestionMode.RSS, *, allowed: bool = True) -> SourceDefinition:
    return SourceDefinition(
        key='campus_feed', name='University events', domain='university.example',
        source_family='campus', source_type='official_calendar',
        ingestion_mode=mode,
        permission_status=PermissionStatus.ALLOWED if allowed else PermissionStatus.REVIEW_REQUIRED,
        detail_extraction_enabled=allowed,
        seed_urls=[f'https://university.example/{"feed.xml" if mode is IngestionMode.RSS else "sitemap.xml"}'],
    )


def response(url: str, text: str, content_type: str) -> FetchResult:
    return FetchResult(
        requested_url=url, final_url=url, content_type=content_type,
        body=text.encode(), status_code=200, fetched_at=datetime.now(UTC),
    )


class MemoryFetcher:
    def __init__(self, pages: dict[str, FetchResult]):
        self.pages = pages
        self.requests: list[str] = []

    async def fetch(self, definition: SourceDefinition, url: str, **kwargs) -> FetchResult:
        self.requests.append(url)
        return self.pages[url]


def test_factory_requires_explicit_approval():
    assert isinstance(build_adapter(source()), SyndicatedEventAdapter)
    assert isinstance(build_adapter(source(IngestionMode.SITEMAP)), SyndicatedEventAdapter)
    assert type(build_adapter(source(allowed=False))) is SeedUrlAdapter
    no_detail = source().model_copy(update={'detail_extraction_enabled': False})
    assert type(build_adapter(no_detail)) is SeedUrlAdapter


def test_rss_to_jsonld_record_with_external_urls_never_fetched():
    feed_url = 'https://university.example/feed.xml'
    event_url = 'https://university.example/events/hackathon'
    remote = 'https://external.example/hackathon'
    rss = f'''<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Campus</title>
      <item><title>Campus Hackathon</title><link>{event_url}</link></item>
      <item><title>External Hackathon</title><link>{remote}</link></item>
      <item><title>Duplicate</title><link>{event_url}</link></item>
    </channel></rss>'''
    page = '''<html><script type="application/ld+json">'''+json.dumps({
        '@type': 'Event', 'name': 'Campus Hackathon 2026',
        'startDate': '2026-11-20',
        'url': 'https://event.example/official',
        'offers': {'url': 'https://register.example/apply', 'price': '0'},
        'organizer': {'name': 'Campus Lab'},
    })+'''</script></html>'''
    fetcher = MemoryFetcher({
        feed_url: response(feed_url, rss, 'application/rss+xml'),
        event_url: response(event_url, page, 'text/html'),
    })
    run = asyncio.run(run_source_once(build_adapter(source()), fetcher))
    assert run.status == 'SUCCEEDED'
    assert run.fetched_pages == 2
    assert fetcher.requests == [feed_url, event_url]
    assert len(run.records) == 1
    record = run.records[0]
    assert record.name == 'Campus Hackathon 2026'
    assert record.metadata['organizers'] == ['Campus Lab']
    assert record.metadata['registration_url'] == 'https://register.example/apply'
    assert record.metadata['ticket_price'] == '0'
    assert str(record.official_url) == 'https://event.example/official'


def test_atom_alternate_namespace_and_same_host_restriction():
    url = 'https://university.example/feed.xml'
    atom = '''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>Weekend Hackathon</title>
      <link rel="self" href="https://university.example/private" />
      <link rel="alternate" href="/events/weekend" />
    </entry>
    <entry><title>External</title><link href="https://else.example/event" /></entry>
    </feed>'''
    links = asyncio.run(build_adapter(source()).parse_listing(response(url, atom, 'application/atom+xml')))
    assert len(links) == 1
    assert str(links[0].url) == 'https://university.example/events/weekend'
    assert links[0].discovery_method == 'rss_atom'


def test_sitemap_index_to_urlset_to_jsonld():
    root_url = 'https://university.example/sitemap.xml'
    child_url = 'https://university.example/events-sitemap.xml'
    event_url = 'https://university.example/hackathon/2026'
    index = f'''<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap><loc>{child_url}</loc></sitemap>
    <sitemap><loc>https://private.example/sitemap.xml</loc></sitemap></sitemapindex>'''
    urlset = f'''<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>{event_url}</loc></url>
    </urlset>'''
    page = '<script type="application/ld+json">'+json.dumps({
        '@type': 'Event', 'name': 'AI Hackathon 2026',
        'startDate': '2026-10-30T10:00:00Z',
    })+'</script>'
    fetcher = MemoryFetcher({
        root_url: response(root_url, index, 'application/xml'),
        child_url: response(child_url, urlset, 'text/xml'),
        event_url: response(event_url, page, 'text/html'),
    })
    run = asyncio.run(run_source_once(build_adapter(source(IngestionMode.SITEMAP)), fetcher))
    assert run.status == 'SUCCEEDED'
    assert fetcher.requests == [root_url, child_url, event_url]
    assert len(run.records) == 1
    assert run.records[0].starts_at_raw == '2026-10-30T10:00:00Z'


@pytest.mark.parametrize('document', [
    '<!DOCTYPE rss [<!ENTITY attack SYSTEM "file:///etc/passwd">]><rss/>',
    '<!ENTITY insecure><rss/>',
    '<notfeed><item><link>https://university.example/events</link></item></notfeed>',
    '<rss><channel><item><link>https://university.example/events</link></channel></rss>',
])
def test_invalid_or_unsafe_xml_is_rejected(document: str):
    result = response('https://university.example/feed.xml', document, 'application/xml')
    assert asyncio.run(build_adapter(source()).parse_listing(result)) == []


def test_xml_response_cap_and_no_html_listing_spoofing():
    url = 'https://university.example/feed.xml'
    huge = '<rss>' + ' ' * (2*1024*1024) + '</rss>'
    assert asyncio.run(build_adapter(source()).parse_listing(response(url, huge, 'application/rss+xml'))) == []
    body = '<rss><channel><item><link>https://university.example/a</link></item></channel></rss>'
    assert asyncio.run(build_adapter(source()).parse_listing(response(url, body, 'text/html'))) == []


def test_caps_and_disallowed_schemes_and_credentials():
    url = 'https://university.example/feed.xml'
    items = ''.join(f'<item><link>https://university.example/e/{n}</link></item>' for n in range(400))
    items += '<item><link>javascript:alert(1)</link></item>'
    items += '<item><link>https://user:password@university.example/event</link></item>'
    links = asyncio.run(build_adapter(source()).parse_listing(response(url, '<rss><channel>'+items+'</channel></rss>', 'application/rss+xml')))
    assert len(links) == MAX_FEED_LINKS
    assert all('university.example/e/' in str(link.url) for link in links)


def test_xml_feed_no_authoritative_events_without_html_jsonld():
    url = 'https://university.example/feed.xml'
    rss = '<rss><channel><item><title>Hackathon 2026</title><link>https://university.example/hack</link></item></channel></rss>'
    adapter = build_adapter(source())
    assert asyncio.run(adapter.parse_events(response(url, rss, 'application/rss+xml'))) == []


def test_robots_for_feed_details_not_seed_and_never_bypass_sitemap():
    from hackathon_sources.fetcher import robots_required

    rss = source(IngestionMode.RSS)
    assert not robots_required(rss, 'https://university.example/feed.xml')
    assert robots_required(rss, 'https://university.example/events/hackathon')
    assert robots_required(rss, 'https://university.example/feed.xml?page=2')
    assert robots_required(source(IngestionMode.SITEMAP), 'https://university.example/sitemap.xml')
    api = source(IngestionMode.RSS).model_copy(update={'ingestion_mode': IngestionMode.API})
    assert not robots_required(api, 'https://university.example/feed.xml')


def test_max_child_sitemaps_are_bounded():
    url = 'https://university.example/sitemap.xml'
    entries = ''.join(f'<sitemap><loc>https://university.example/map-{n}.xml</loc></sitemap>' for n in range(200))
    text = '<sitemapindex>'+entries+'</sitemapindex>'
    links = asyncio.run(build_adapter(source(IngestionMode.SITEMAP)).parse_listing(response(url, text, 'application/xml')))
    assert len(links) == 20


def test_feed_title_is_preserved_after_link():
    url = 'https://university.example/feed.xml'
    xml = '<rss><channel><item><link>https://university.example/events/1</link><title>Student Hackathon</title></item></channel></rss>'
    links = asyncio.run(build_adapter(source()).parse_listing(response(url, xml, 'application/xml')))
    assert links[0].metadata['feed_title'] == 'Student Hackathon'
