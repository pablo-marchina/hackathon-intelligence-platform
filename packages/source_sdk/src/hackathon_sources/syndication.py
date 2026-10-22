"""Policy-gated RSS/Atom and sitemap discovery with structured HTML extraction.

An XML feed supplies *candidate URLs*, not authoritative event records. Only
schema.org Event data on an independently authorized, fetched HTML page may
enter the canonical pipeline. Third-party links remain outside this source's
fetch scope, even if the feed advertises them.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from pydantic import ValidationError

from .models import DiscoveredUrl, FetchResult, IngestionMode
from .structured_html import StructuredHtmlEventAdapter

MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_FEED_LINKS = 2500
MAX_CHILD_SITEMAPS = 250


@dataclass(frozen=True)
class ListingPage:
    links: list[DiscoveredUrl]
    next_offset: int
    has_more: bool


def _element_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _allowed_url(candidate: str | None, base: str, allowed_hosts: set[str]) -> str | None:
    if not candidate or len(candidate) > 2048:
        return None
    try:
        url = urljoin(base, candidate.strip())
        parts = urlsplit(url)
        if (parts.scheme not in {'http', 'https'} or not parts.hostname or
                parts.username is not None or parts.password is not None or
                parts.hostname.lower() not in allowed_hosts):
            return None
        return url
    except ValueError:
        return None


class SyndicatedEventAdapter(StructuredHtmlEventAdapter):
    """Follow same-source XML listings, then parse only actual Event JSON-LD.

    Seed URLs and allowlisted hosts are configured by an operator and also
    checked by the fetcher, with permission_status=ALLOWED. This adapter never
    fetches anything itself and never grants access to third-party event URLs.
    """

    async def parse_listing(self, result: FetchResult) -> list[DiscoveredUrl]:
        """Compatibility method for nonpersistent runners (first bounded page)."""
        return (await self.parse_listing_page(result)).links

    async def parse_listing_page(
        self, result: FetchResult, *, offset: int = 0
    ) -> ListingPage:
        """Return one page; persistent runner checkpoints next_offset on commit.

        Offset counts all raw XML entries, including invalid/out-of-scope links,
        so filtering cannot make a continuation loop forever.
        """
        if offset < 0:
            raise ValueError("negative listing offset")
        empty = ListingPage([], 0, False)
        if self.definition.ingestion_mode not in (IngestionMode.RSS, IngestionMode.SITEMAP):
            return empty
        content_type = (result.content_type or '').lower()
        is_xml = any(t in content_type for t in ('xml', 'rss', 'atom'))
        if not is_xml and not result.body.lstrip().startswith(b'<?xml'):
            return empty
        if len(result.body) > MAX_FEED_BYTES:
            return empty
        # stdlib ElementTree does not resolve external entities, but reject all
        # DTDs/entities explicitly. We do not need them for feeds and sitemaps.
        body_casefold = result.body.upper()
        if b'<!DOCTYPE' in body_casefold or b'<!ENTITY' in body_casefold:
            return empty
        try:
            root = ET.fromstring(result.body)
        except ET.ParseError:
            return empty

        kind = _element_name(root.tag).lower()
        if kind not in {'rss', 'rdf', 'feed', 'urlset', 'sitemapindex'}:
            return empty
        if kind in {'rss', 'rdf', 'feed'} and self.definition.ingestion_mode != IngestionMode.RSS:
            return empty
        if kind in {'urlset', 'sitemapindex'} and self.definition.ingestion_mode != IngestionMode.SITEMAP:
            return empty

        hosts = {self.definition.domain.lower(), *(h.lower() for h in self.definition.allowed_hosts)}
        result_links: list[DiscoveredUrl] = []
        seen: set[str] = set()
        max_links = MAX_CHILD_SITEMAPS if kind == 'sitemapindex' else MAX_FEED_LINKS
        if kind == 'rss' or kind == 'rdf':
            elements = (node for node in root.iter() if _element_name(node.tag) == 'item')
        elif kind == 'feed':
            elements = (node for node in root if _element_name(node.tag) == 'entry')
        else:
            child_tag = 'sitemap' if kind == 'sitemapindex' else 'url'
            elements = (node for node in root if _element_name(node.tag) == child_tag)

        items = list(elements)  # bounded by MAX_FEED_BYTES before XML parsing
        end = min(len(items), offset + max_links)
        for item in items[offset:end]:
            url: str | None = None
            title: str | None = None
            for node in item:
                name = _element_name(node.tag)
                if name == 'title':
                    title = (node.text or '').strip()[:200] or None
                elif name in {'loc', 'link'}:
                    if name == 'link' and node.attrib.get('rel', 'alternate') != 'alternate':
                        continue
                    raw_link = node.attrib.get('href') if name == 'link' else node.text
                    if not raw_link and name == 'link':
                        raw_link = node.text
                    url = _allowed_url(raw_link, result.final_url, hosts)
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                result_links.append(DiscoveredUrl(
                    url=url,
                    source_key=self.definition.key,
                    discovery_method=(
                        'sitemap_index' if kind == 'sitemapindex'
                        else 'sitemap' if kind == 'urlset' else 'rss_atom'
                    ),
                    parent_url=result.final_url,
                    depth=1,
                    priority=self.definition.priority,
                    metadata={'feed_title': title} if title else {},
                ))
            except ValidationError:
                continue
        return ListingPage(result_links, end, end < len(items))
