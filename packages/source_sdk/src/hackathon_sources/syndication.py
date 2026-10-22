"""Policy-gated RSS/Atom and sitemap discovery with structured HTML extraction.

An XML feed supplies *candidate URLs*, not authoritative event records. Only
schema.org Event data on an independently authorized, fetched HTML page may
enter the canonical pipeline. Third-party links remain outside this source's
fetch scope, even if the feed advertises them.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from pydantic import ValidationError

from .models import DiscoveredUrl, FetchResult, IngestionMode
from .structured_html import StructuredHtmlEventAdapter

MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_FEED_LINKS = 250
MAX_CHILD_SITEMAPS = 20


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
        if self.definition.ingestion_mode not in (IngestionMode.RSS, IngestionMode.SITEMAP):
            return []
        content_type = (result.content_type or '').lower()
        is_xml = any(t in content_type for t in ('xml', 'rss', 'atom'))
        if not is_xml and not result.body.lstrip().startswith(b'<?xml'):
            return []
        if len(result.body) > MAX_FEED_BYTES:
            return []
        # stdlib ElementTree does not resolve external entities, but reject all
        # DTDs/entities explicitly. We do not need them for feeds and sitemaps.
        body_casefold = result.body.upper()
        if b'<!DOCTYPE' in body_casefold or b'<!ENTITY' in body_casefold:
            return []
        try:
            root = ET.fromstring(result.body)
        except ET.ParseError:
            return []

        kind = _element_name(root.tag).lower()
        if kind not in {'rss', 'rdf', 'feed', 'urlset', 'sitemapindex'}:
            return []
        if kind in {'rss', 'rdf', 'feed'} and self.definition.ingestion_mode != IngestionMode.RSS:
            return []
        if kind in {'urlset', 'sitemapindex'} and self.definition.ingestion_mode != IngestionMode.SITEMAP:
            return []

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

        for item in elements:
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
            if len(result_links) >= max_links:
                break
        return result_links
