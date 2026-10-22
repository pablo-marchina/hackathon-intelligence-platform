"""Regression tests: no silent feed truncation with a durable listing cursor."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from hackathon_sources.backlog import SEED_REVISIT_SECONDS, BacklogClaim, SourceUrlBacklog
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.syndication import MAX_CHILD_SITEMAPS, MAX_FEED_LINKS, SyndicatedEventAdapter


def _adapter(mode=IngestionMode.RSS):
    return SyndicatedEventAdapter(
        SourceDefinition(
            key="university",
            name="Uni",
            domain="university.example",
            source_family="edu",
            source_type="calendar",
            ingestion_mode=mode,
            permission_status=PermissionStatus.ALLOWED,
            detail_extraction_enabled=True,
            seed_urls=["https://university.example/list.xml"],
        )
    )


def _result(xml: str) -> FetchResult:
    return FetchResult(
        requested_url="https://university.example/list.xml",
        final_url="https://university.example/list.xml",
        status_code=200,
        content_type="application/xml",
        body=xml.encode(),
        fetched_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "mode,number,size,wrapper",
    [
        (IngestionMode.RSS, MAX_FEED_LINKS + 27, "item", "rss"),
        (IngestionMode.SITEMAP, MAX_FEED_LINKS + 5, "url", "urlset"),
        (IngestionMode.SITEMAP, MAX_CHILD_SITEMAPS + 17, "sitemap", "sitemapindex"),
    ],
)
def test_large_document_pages_have_exactly_all_candidate_urls(mode, number, size, wrapper):
    entries = "".join(
        f"<{size}><loc>https://university.example/hack-{i}</loc></{size}>"
        if size != "item"
        else f"<item><link>https://university.example/hack-{i}</link></item>"
        for i in range(number)
    )
    page = _result(f"<{wrapper}>{entries}</{wrapper}>")
    assert len(page.body) < 2 * 1024 * 1024
    adapter = _adapter(mode)
    offset, urls, page_count = 0, [], 0
    while True:
        window = asyncio.run(adapter.parse_listing_page(page, offset=offset))
        urls.extend(str(item.url) for item in window.links)
        page_count += 1
        if not window.has_more:
            break
        assert window.next_offset > offset
        offset = window.next_offset
    assert page_count == 2
    assert urls == [f"https://university.example/hack-{i}" for i in range(number)]


def test_invalid_links_advance_raw_offset_and_cannot_loop():
    entries = (
        "<item><link>https://evil.example/outside</link></item>" * MAX_FEED_LINKS
        + "<item><link>https://university.example/one</link></item>"
        + "<item><link>javascript:attack</link></item>"
    )
    doc = _result("<rss>" + entries + "</rss>")
    first = asyncio.run(_adapter().parse_listing_page(doc))
    assert first.links == [] and first.next_offset == MAX_FEED_LINKS and first.has_more
    second = asyncio.run(_adapter().parse_listing_page(doc, offset=first.next_offset))
    assert [str(link.url) for link in second.links] == ["https://university.example/one"]
    assert second.next_offset == MAX_FEED_LINKS + 2 and not second.has_more


def test_xml_changed_between_pages_restarts_first_page_not_stale_offset():
    # The runner computes content hashes before selecting the cursor; a changed
    # feed must be replayed from zero. Previously checkpointed URLs are deduped.
    adapter = _adapter(IngestionMode.SITEMAP)
    a = _result(
        "<urlset>"
        + "".join(
            f"<url><loc>https://university.example/{i}</loc></url>"
            for i in range(MAX_FEED_LINKS + 1)
        )
        + "</urlset>"
    )
    b = _result("<urlset><url><loc>https://university.example/new</loc></url></urlset>")
    old_digest = sha256(a.body).hexdigest()
    new_digest = sha256(b.body).hexdigest()
    old = BacklogClaim(
        1, "https://university.example/list.xml", "token", "SEED", MAX_FEED_LINKS, old_digest
    )
    offset = old.listing_offset if old.listing_hash == new_digest else 0
    assert offset == 0
    page = asyncio.run(adapter.parse_listing_page(b, offset=offset))
    assert [str(x.url) for x in page.links] == ["https://university.example/new"]
    assert not page.has_more


def test_seed_interval_and_fencing_are_explicit_in_sql():
    import inspect

    code = inspect.getsource(SourceUrlBacklog)
    assert SEED_REVISIT_SECONDS >= 300
    assert "CASE WHEN kind = 'DISCOVERED' THEN 0 ELSE 1 END" in code
    assert "source_url_backlog.next_fetch_at <= clock_timestamp()" in code
    assert "claim_until > clock_timestamp()" in code
    assert "listing_offset=CASE WHEN :has_more THEN :offset ELSE 0 END" in code
    assert "listing_hash=CASE WHEN :has_more THEN :digest ELSE NULL END" in code


def test_bad_listing_continuation_is_rejected_before_sql():
    class Dummy:
        def execute(self, *args, **kwargs):
            raise AssertionError("no SQL should be executed for invalid cursor")

    backlog = SourceUrlBacklog(Dummy())
    claim = BacklogClaim(3, "https://university.example/feed.xml", "token", "SEED", 10, "same")
    with pytest.raises(ValueError):
        backlog.succeed(claim, has_more=True, next_offset=10, listing_hash="same")
    with pytest.raises(ValueError):
        backlog.succeed(claim, has_more=True, next_offset=-1, listing_hash="new")
    with pytest.raises(ValueError):
        backlog.succeed(claim, has_more=True, next_offset=2, listing_hash=None)


def test_migration_and_api_readiness_track_new_fields():
    root = Path(__file__).resolve().parents[3]
    ddl = (root / "db/sql/0012_syndication_continuation.sql").read_text()
    assert "listing_offset" in ddl and "listing_hash" in ddl
    from hackathon_api.main import EXPECTED_DB_REVISION

    assert EXPECTED_DB_REVISION == "0013_canonical_yield"
