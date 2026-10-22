"""Large RSS/sitemaps must retain every item without unbounded XML trees."""
from __future__ import annotations

import asyncio
import gzip
from datetime import UTC, datetime

import pytest
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.syndication import (
    SyndicatedEventAdapter, MAX_FEED_BYTES, MAX_FEED_LINKS,
    MAX_STREAM_XML_BYTES, MAX_STREAM_INPUT_BYTES,
)


def adapter(mode: IngestionMode) -> SyndicatedEventAdapter:
    return SyndicatedEventAdapter(SourceDefinition(
        key="campus", name="Campus", domain="university.example",
        source_family="campus", source_type="official_calendar",
        ingestion_mode=mode, permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True, seed_urls=["https://university.example/feed.xml"],
    ))


def parse(body: bytes, *, mode=IngestionMode.SITEMAP, offset=0, path="sitemap.xml.gz"):
    result = FetchResult(
        requested_url=f"https://university.example/{path}",
        final_url=f"https://university.example/{path}", body=body,
        status_code=200, content_type="application/xml", fetched_at=datetime.now(UTC),
    )
    return asyncio.run(adapter(mode).parse_listing_page(result, offset=offset))


def make_sitemap(count: int) -> bytes:
    # Extra harmless tag ensures the byte count exercises the streamed route.
    entries = (f'<url><loc>https://university.example/events/{i:05d}</loc>'
               f'<lastmod>2026-10-21</lastmod></url>' for i in range(count))
    return ("<urlset>" + "".join(entries) + "</urlset>").encode()


def test_uncompressed_multi_megabyte_sitemap_has_exact_continuation():
    body = make_sitemap(28000)
    assert MAX_FEED_BYTES < len(body) < MAX_STREAM_XML_BYTES
    page0 = parse(body)
    assert len(page0.links) == MAX_FEED_LINKS
    assert page0.has_more and page0.next_offset == 2500
    page1 = parse(body, offset=2500)
    assert len(page1.links) == MAX_FEED_LINKS
    assert str(page1.links[0].url).endswith("/02500")
    last = parse(body, offset=27500)
    assert len(last.links) == 500
    assert not last.has_more and last.next_offset == 28000


def test_gzipped_multi_megabyte_sitemap_has_same_page_boundaries():
    raw = make_sitemap(28000)
    compressed = gzip.compress(raw)
    assert len(compressed) < MAX_FEED_BYTES
    first = parse(compressed)
    assert len(first.links) == 2500 and first.has_more
    assert str(parse(compressed, offset=2500).links[0].url).endswith("/02500")


def test_long_rss_contains_feed_level_next_but_not_item_next():
    entries = ''.join(
        f'<item><title>Campus hackathon {i}</title>'
        f'<link>https://university.example/e/{i:05d}</link>'
        '<link rel="next" href="https://university.example/evil-item-page.xml"/>'
        '</item>' for i in range(25000)
    )
    raw = ('<rss><channel><link rel="next" '
           'href="https://university.example/next.xml"/>' + entries + '</channel></rss>').encode()
    assert MAX_FEED_BYTES < len(raw) < MAX_STREAM_XML_BYTES
    first = parse(raw, mode=IngestionMode.RSS, path="feed.xml")
    assert len(first.links) == 2500 and first.has_more
    final = parse(raw, mode=IngestionMode.RSS, offset=22500, path="feed.xml")
    assert len(final.links) == 2501 and not final.has_more
    assert final.links[-1].discovery_method == "rss_pagination"
    assert str(final.links[-1].url) == "https://university.example/next.xml"


@pytest.mark.parametrize("payload", [
    b'<urlset><url><loc>https://university.example/e</loc></url>' + b' ' * (MAX_STREAM_XML_BYTES + 200) + b'</urlset>',
    gzip.compress(b'<urlset><url><loc>https://university.example/e</loc></url>' + b' ' * (MAX_STREAM_XML_BYTES + 200) + b'</urlset>'),
    gzip.compress(b'<urlset/>')[:-2] + b'broken',
    b'<urlset><!--' + b' ' * (MAX_FEED_BYTES + 23) + b'--><!DOCT' + b'YPE urlset><url/></urlset>',
    b'<urlset><!--' + b' ' * (MAX_FEED_BYTES + 23) + b'--><!ENTITY x "x"><url/></urlset>',
    b'<urlset><!--' + b' ' * (MAX_FEED_BYTES + 23) + b'--><url></urlset>',
])
def test_oversized_malformed_and_unsafe_inputs_yield_no_links(payload):
    page = parse(payload)
    assert not page.links and not page.has_more and page.next_offset == 0


def test_oversized_compressed_input_rejected_without_decompressing():
    assert not parse(b"\x1f\x8b" + b"a" * MAX_STREAM_INPUT_BYTES).links


def test_stream_sitemap_filters_external_host_even_when_large():
    raw = make_sitemap(28000).replace(
        b'https://university.example/events/00001',
        b'https://127.0.0.1/events/00001',
    )
    page = parse(raw)
    assert len(page.links) == MAX_FEED_LINKS - 1
    assert all('university.example' in str(item.url) for item in page.links)


def test_large_xml_rejects_excessive_depth_and_tag_count():
    # The leading comment forces the bounded XMLPullParser path.
    pad = b"<!--" + b"." * (MAX_FEED_BYTES + 10) + b"-->"
    deep = b"<urlset>" + pad + b"<x>" * 40 + b"</x>" * 40 + b"</urlset>"
    assert not parse(deep).links
    many = b"<urlset>" + pad + b"<x/>" * 300001 + b"</urlset>"
    assert len(many) < MAX_STREAM_XML_BYTES
    assert not parse(many).links

def test_xml_declaration_or_dtd_split_on_stream_boundary_is_rejected():
    # Ensure a forbidden DTD split across the 32 KiB read boundary is caught.
    # A leading comment makes the document long enough to use streaming.
    prefix = b"<urlset>" + b"<!--" + b"." * (MAX_FEED_BYTES + 1) + b"-->"
    alignment = (32768 - (len(prefix) % 32768) - len(b"<!DOCT")) % 32768
    dtd = prefix + b" " * alignment + b"<!DOCTYPE urlset><url/></urlset>"
    assert len(dtd) > MAX_FEED_BYTES
    assert not parse(dtd).links


def test_utf16_dtd_rejected_by_xml_parser_in_small_and_large_docs():
    payload = '<!DOCTYPE urlset [<!ENTITY e "inside">]><urlset><url><loc>&e;</loc></url></urlset>'
    assert not parse(payload.encode('utf-16')).links
    # UTF-16 uses null bytes between ASCII characters, so raw-byte marker
    # scanning alone would NOT catch this malicious declaration.
    padded = '<urlset>' + (' ' * (MAX_FEED_BYTES // 2 + 100)) + '<!DOCTYPE urlset><url/></urlset>'
    assert len(padded.encode('utf-16')) > MAX_FEED_BYTES
    assert not parse(padded.encode('utf-16')).links
