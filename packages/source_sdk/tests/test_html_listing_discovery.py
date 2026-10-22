"""Authorized static HTML listings discover bounded same-site event links."""

import asyncio
from datetime import UTC, datetime

from hackathon_sources.models import FetchResult, SourceDefinition
from hackathon_sources.structured_html import (
    MAX_HTML_DISCOVERIES,
    StructuredHtmlEventAdapter,
)


def source():
    return SourceDefinition(
        key="organizer",
        name="Organizer",
        domain="events.example.org",
        source_family="organizer",
        source_type="hackathon_organizer",
        ingestion_mode="STATIC_HTML",
        permission_status="ALLOWED",
        detail_extraction_enabled=True,
        seed_urls=["https://events.example.org/hackathons"],
    )


def page(markup, url="https://events.example.org/hackathons?page=1"):
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        body=markup.encode(),
        fetched_at=datetime.now(UTC),
    )


def discover(markup):
    return asyncio.run(StructuredHtmlEventAdapter(source()).parse_listing(page(markup)))


def test_same_host_detail_links_and_visible_pagination():
    links = discover("""
      <a href="/events/ai-buildathon-2026">AI Buildathon 2026</a>
      <a href="/hackathons/green-2026">Details</a>
      <a href="/hackathons?page=2">Next</a>
      <a href="/hackathons?page=2">Next</a>
      <a href="https://external.example.org/events/interesting">External Hackathon</a>
      <a href="/blog/how-to-join-a-hackathon">Hackathon guide</a>
      <a href="/login">Hackathon registration</a>
      <a href="javascript:alert(1)">Hackathon 2026</a>
      <a href="/hackathons?page=9999">Filters</a>
    """)
    assert [str(x.url) for x in links] == [
        "https://events.example.org/events/ai-buildathon-2026",
        "https://events.example.org/hackathons/green-2026",
        "https://events.example.org/hackathons?page=2",
    ]
    assert [x.discovery_method for x in links] == [
        "html_event_link",
        "html_event_link",
        "html_pagination",
    ]
    assert all(
        str(link.parent_url) == "https://events.example.org/hackathons?page=1" for link in links
    )


def test_rejects_non_html():
    result = page('<a href="/events/hackathon">Hackathon</a>')
    result.content_type = "application/json"
    assert asyncio.run(StructuredHtmlEventAdapter(source()).parse_listing(result)) == []


def test_discoveries_are_capped_even_with_many_unique_links():
    links = "".join(f'<a href="/events/hackathon-{i}">Details</a>' for i in range(400))
    assert len(discover(links)) == MAX_HTML_DISCOVERIES


def test_unsafe_host_and_ports_do_not_qualify():
    links = discover("""
      <a href="https://events.example.org.evil.com/events/hackathon">Hackathon</a>
      <a href="https://evil.com@events.example.org/events/hackathon">Hackathon</a>
      <a href="https://events.example.org:8443/events/hackathon">Hackathon</a>
      <a href="https://events.example.org/events/safe#details">Hackathon</a>
    """)
    assert [str(link.url) for link in links] == ["https://events.example.org/events/safe"]
