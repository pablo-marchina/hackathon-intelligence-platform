"""Iteration 27: gzip sitemaps and bounded RSS/Atom page traversal."""
from __future__ import annotations

import asyncio
import gzip
import json
from datetime import UTC, datetime

import pytest

from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.runner import run_source_once
from hackathon_sources.syndication import (
    MAX_FEED_BYTES, _bounded_xml_body, SyndicatedEventAdapter,
)


def adapter(mode: IngestionMode = IngestionMode.RSS) -> SyndicatedEventAdapter:
    return SyndicatedEventAdapter(SourceDefinition(
        key='university', name='University', domain='university.example',
        source_family='official', source_type='official_calendar',
        ingestion_mode=mode, permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        seed_urls=[f'https://university.example/{"feed.xml" if mode is IngestionMode.RSS else "sitemap.xml.gz"}'],
    ))


def fetched(url: str, body: str | bytes, mime: str = 'application/xml') -> FetchResult:
    if isinstance(body, str):
        body = body.encode('utf-8')
    return FetchResult(requested_url=url, final_url=url, status_code=200,
                       content_type=mime, body=body, fetched_at=datetime.now(UTC))


def parse(result: FetchResult, mode: IngestionMode = IngestionMode.RSS, *, offset: int = 0):
    return asyncio.run(adapter(mode).parse_listing_page(result, offset=offset))


def test_atom_next_page_comes_after_regular_entries_and_filters_outside_links():
    url = 'https://university.example/feed.xml'
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>Hackathon</title><link href="/events/hackathon"/></entry>
        <link rel="next" href="?page=2"/>
        <link rel="next" href="https://external.example/steal"/>
    </feed>'''
    page = parse(fetched(url, xml, 'application/atom+xml'))
    assert [str(link.url) for link in page.links] == [
        'https://university.example/events/hackathon',
        'https://university.example/feed.xml?page=2',
    ]
    assert page.links[1].discovery_method == 'rss_pagination'
    assert page.links[1].metadata['is_feed_page'] is True
    assert not page.has_more


def test_rss_next_is_only_feed_level_and_not_item_level():
    url = 'https://university.example/feed.xml'
    xml = '''<rss xmlns:atom="http://www.w3.org/2005/Atom"><channel>
        <item><link>https://university.example/e/1</link>
          <atom:link rel="next" href="/fake-from-item.xml"/></item>
        <atom:link rel="next" href="/feed.xml?page=2"/>
    </channel></rss>'''
    links = parse(fetched(url, xml)).links
    assert [str(link.url) for link in links] == [
        'https://university.example/e/1',
        'https://university.example/feed.xml?page=2',
    ]


def test_pagination_enqueued_only_on_last_in_document_chunk():
    url = 'https://university.example/feed.xml'
    entries = ''.join(f'<item><link>/e/{i}</link></item>' for i in range(2501))
    xml = f'<rss xmlns:atom="http://www.w3.org/2005/Atom"><channel>{entries}<atom:link rel="next" href="/feed.xml?page=2"/></channel></rss>'
    first = parse(fetched(url, xml))
    second = parse(fetched(url, xml), offset=2500)
    assert len(first.links) == 2500 and first.has_more
    assert all(link.discovery_method != 'rss_pagination' for link in first.links)
    assert len(second.links) == 2 and not second.has_more
    assert second.links[-1].discovery_method == 'rss_pagination'
    assert second.next_offset == 2501


def test_gzip_sitemap_index_and_urlset_accepted_without_mime_hint():
    url = 'https://university.example/sitemap.xml.gz'
    xml = '<sitemapindex><sitemap><loc>https://university.example/events.xml.gz</loc></sitemap></sitemapindex>'
    result = fetched(url, gzip.compress(xml.encode()), 'application/octet-stream')
    assert [str(link.url) for link in parse(result, IngestionMode.SITEMAP).links] == [
        'https://university.example/events.xml.gz',
    ]
    child = fetched('https://university.example/events.xml.gz',
                    gzip.compress(b'<urlset><url><loc>https://university.example/2027/hack</loc></url></urlset>'),
                    'application/x-gzip')
    assert [str(link.url) for link in parse(child, IngestionMode.SITEMAP).links] == [
        'https://university.example/2027/hack',
    ]


@pytest.mark.parametrize('body', [
    gzip.compress(b'<urlset>' + b'A' * MAX_FEED_BYTES + b'</urlset>'),
    b'\x1f\x8b\x00\x00invalid-stream',
    gzip.compress(b'<!DOCTYPE urlset [<!ENTITY xx SYSTEM "file:///etc/shadow">]><urlset/>'),
    gzip.compress(b'<urlset><url><loc>https://evil.example/hack</loc></url></urlset>'),
])
def test_unsafe_compressed_sitemap_does_not_discover_urls(body: bytes):
    page = parse(fetched('https://university.example/sitemap.xml.gz', body, 'application/gzip'),
                 IngestionMode.SITEMAP)
    assert page.links == []


def test_gzip_decompression_is_capped_without_unbounded_read():
    body = gzip.compress(b' ' * (MAX_FEED_BYTES + 1000))
    assert len(body) < 4096  # a tiny compressed response can expand enormously
    assert _bounded_xml_body(body) is None


def test_rss_next_page_chain_reaches_event_jsonld_without_external_network():
    first = 'https://university.example/feed.xml'
    second = 'https://university.example/feed.xml?page=2'
    event = 'https://university.example/e/hack'
    xml1 = '''<rss xmlns:atom="http://www.w3.org/2005/Atom"><channel>
    <atom:link rel="next" href="?page=2"/>
    <item><link>https://external.example/disallowed</link></item></channel></rss>'''
    xml2 = '<rss><channel><item><link>' + event + '</link></item></channel></rss>'
    event_html = '<script type="application/ld+json">' + json.dumps({
        '@type': 'Event', 'name': 'University Hackathon 2027',
        'startDate': '2027-02-05T00:00:00Z', 'url': event,
    }) + '</script>'
    pages = {first: fetched(first, xml1), second: fetched(second, xml2),
             event: fetched(event, event_html, 'text/html')}

    class Fetcher:
        visited: list[str] = []
        async def fetch(self, definition, url, **kwargs):
            self.visited.append(url)
            return pages[url]

    fetcher = Fetcher()
    outcome = asyncio.run(run_source_once(adapter(), fetcher, max_pages=6))
    assert outcome.status == 'SUCCEEDED'
    assert fetcher.visited == [first, second, event]
    assert len(outcome.records) == 1
    assert outcome.records[0].name == 'University Hackathon 2027'


def test_gzip_sitemap_chain_finds_event_without_leaving_authorized_host():
    root = 'https://university.example/sitemap.xml.gz'
    child = 'https://university.example/events.xml.gz'
    event = 'https://university.example/hackathons/2027'
    offsite = 'https://external.example/other'
    root_xml = (f'<sitemapindex><sitemap><loc>{child}</loc></sitemap>'
                f'<sitemap><loc>{offsite}</loc></sitemap></sitemapindex>')
    child_xml = f'<urlset><url><loc>{event}</loc></url></urlset>'
    html = '<script type="application/ld+json">' + json.dumps({
        '@type': 'Event', 'name': 'Global University Hackathon 2027',
        'startDate': '2027-04-02T10:00:00Z', 'url': event,
        'organizer': {'@type': 'Organization', 'name': 'University Lab'},
    }) + '</script>'
    pages = {
        root: fetched(root, gzip.compress(root_xml.encode()), 'application/octet-stream'),
        child: fetched(child, gzip.compress(child_xml.encode()), 'application/x-gzip'),
        event: fetched(event, html, 'text/html'),
    }

    class Fetcher:
        visited: list[str] = []
        async def fetch(self, definition, url, **kwargs):
            self.visited.append(url)
            return pages[url]

    fetcher = Fetcher()
    run = asyncio.run(run_source_once(adapter(IngestionMode.SITEMAP), fetcher, max_pages=6))
    assert run.status == 'SUCCEEDED'
    assert fetcher.visited == [root, child, event]
    assert len(run.records) == 1
    assert run.records[0].metadata['organizers'] == ['University Lab']


def test_http_client_may_already_decompress_gzip_without_changing_content_type():
    # HTTPX handles Content-Encoding, but servers sometimes keep an opaque MIME.
    url = 'https://university.example/sitemap.xml.gz'
    xml = '<urlset><url><loc>https://university.example/hack2027</loc></url></urlset>'
    links = parse(fetched(url, xml, 'application/octet-stream'), IngestionMode.SITEMAP).links
    assert [str(link.url) for link in links] == ['https://university.example/hack2027']


def test_html_response_is_not_treated_as_xml_due_to_xml_url_suffix():
    url = 'https://university.example/sitemap.xml.gz'
    xml = '<urlset><url><loc>https://university.example/hack2027</loc></url></urlset>'
    assert parse(fetched(url, xml, 'text/html'), IngestionMode.SITEMAP).links == []
