"""Policy-gated RSS/Atom and sitemap discovery with structured HTML extraction.

An XML feed supplies *candidate URLs*, not authoritative event records. Only
schema.org Event data on an independently authorized, fetched HTML page may
enter the canonical pipeline. Third-party links remain outside this source's
fetch scope, even if the feed advertises them.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from gzip import GzipFile
from io import BytesIO
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET
from zlib import error as ZlibError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import DefusedXMLParser
from defusedxml.ElementTree import fromstring as safe_xml_fromstring
from pydantic import ValidationError

from .models import DiscoveredUrl, FetchResult, IngestionMode
from .structured_html import StructuredHtmlEventAdapter

MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_FEED_LINKS = 2500
MAX_CHILD_SITEMAPS = 250
MAX_FEED_PAGE_HOPS = 100  # prevent unbounded next-page chains per seed
MAX_SITEMAP_INDEX_DEPTH = 8  # nested indexes must terminate even across restarts
# Large XML listings use a streaming parser instead of building a full tree.
# The existing 2 MiB fast path stays unchanged for backwards compatibility.
MAX_STREAM_XML_BYTES = 12 * 1024 * 1024
MAX_STREAM_INPUT_BYTES = 8 * 1024 * 1024
XML_STREAM_CHUNK = 32 * 1024
MAX_STREAM_DEPTH = 32
MAX_STREAM_ELEMENTS = 300_000


def _bounded_xml_body(body: bytes) -> bytes | None:
    """Accept plain or gzip sitemap XML, rejecting decompression bombs.

    FetchEngine may already decompress Content-Encoding; only the gzip magic
    determines whether another decompression pass is necessary.
    """
    if len(body) > MAX_FEED_BYTES:
        return None
    if body.startswith(b"\x1f\x8b"):
        try:
            with GzipFile(fileobj=BytesIO(body)) as zipped:
                body = zipped.read(MAX_FEED_BYTES + 1)
        except (OSError, EOFError, ZlibError):
            return None
        if len(body) > MAX_FEED_BYTES:
            return None
    return body


def _next_feed_page(root: ET.Element, kind: str, base: str, hosts: set[str]) -> str | None:
    """Only explicit feed-level rel=next links, never links from an event item.

    The normal backlog deduplicates URLs and rechecks authorization on fetch.
    """
    if kind not in {"feed", "rss", "rdf"}:
        return None
    channels = (
        [root]
        if kind == "feed"
        else [item for item in root if _element_name(item.tag) == "channel"]
    )
    for channel in channels:
        for item in channel:
            if (
                _element_name(item.tag) != "link"
                or item.attrib.get("rel", "").casefold().strip() != "next"
            ):
                continue
            href = item.attrib.get("href") or item.text
            next_url = _allowed_url(href, base, hosts)
            if next_url and next_url != base:
                return next_url
    return None


@dataclass(frozen=True)
class ListingPage:
    links: list[DiscoveredUrl]
    next_offset: int
    has_more: bool


def _element_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _allowed_url(candidate: str | None, base: str, allowed_hosts: set[str]) -> str | None:
    if not candidate or len(candidate) > 2048:
        return None
    try:
        url = urljoin(base, candidate.strip())
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.hostname.lower() not in allowed_hosts
        ):
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

    async def parse_listing_page(self, result: FetchResult, *, offset: int = 0) -> ListingPage:
        """Return one page; persistent runner checkpoints next_offset on commit.

        Offset counts all raw XML entries, including invalid/out-of-scope links,
        so filtering cannot make a continuation loop forever.
        """
        if offset < 0:
            raise ValueError("negative listing offset")
        empty = ListingPage([], 0, False)
        if self.definition.ingestion_mode not in (IngestionMode.RSS, IngestionMode.SITEMAP):
            return empty
        content_type = (result.content_type or "").lower()
        gzip_encoded = result.body.startswith(b"\x1f\x8b")
        is_xml = any(t in content_type for t in ("xml", "rss", "atom"))
        # HTTP clients may transparently decode Content-Encoding: gzip while
        # retaining application/octet-stream as the origin Content-Type. Trust
        # an XML-looking URL extension only for choosing the parser, never as
        # permission to follow its links or as proof the bytes are valid XML.
        xml_path = (
            urlsplit(result.final_url).path.lower().endswith((".xml", ".xml.gz", ".rss", ".atom"))
        )
        if "html" in content_type or "json" in content_type:
            return empty
        if not (is_xml or gzip_encoded or xml_path or result.body.lstrip().startswith(b"<?xml")):
            return empty
        body = _bounded_xml_body(result.body)
        if body is None:
            # Large documents are streamed with strict decompressed/input caps.
            # Offsets keep their meaning across the two parsing paths.
            return self._parse_large_listing(result, offset=offset)
        # stdlib ElementTree does not resolve external entities, but reject all
        # DTDs/entities explicitly. We do not need them for feeds and sitemaps.
        body_casefold = body.upper()
        if b"<!DOCTYPE" in body_casefold or b"<!ENTITY" in body_casefold:
            return empty
        try:
            root = safe_xml_fromstring(body, forbid_dtd=True)
        except (ET.ParseError, DefusedXmlException):
            return empty

        kind = _element_name(root.tag).lower()
        if kind not in {"rss", "rdf", "feed", "urlset", "sitemapindex"}:
            return empty
        if kind in {"rss", "rdf", "feed"} and self.definition.ingestion_mode != IngestionMode.RSS:
            return empty
        if (
            kind in {"urlset", "sitemapindex"}
            and self.definition.ingestion_mode != IngestionMode.SITEMAP
        ):
            return empty

        hosts = {
            self.definition.domain.lower(),
            *(h.lower() for h in self.definition.allowed_hosts),
        }
        result_links: list[DiscoveredUrl] = []
        seen: set[str] = set()
        max_links = MAX_CHILD_SITEMAPS if kind == "sitemapindex" else MAX_FEED_LINKS
        if kind == "rss" or kind == "rdf":
            elements = (node for node in root.iter() if _element_name(node.tag) == "item")
        elif kind == "feed":
            elements = (node for node in root if _element_name(node.tag) == "entry")
        else:
            child_tag = "sitemap" if kind == "sitemapindex" else "url"
            elements = (node for node in root if _element_name(node.tag) == child_tag)

        items = list(elements)  # bounded by MAX_FEED_BYTES before XML parsing
        end = min(len(items), offset + max_links)
        for item in items[offset:end]:
            url: str | None = None
            title: str | None = None
            for node in item:
                name = _element_name(node.tag)
                if name == "title":
                    title = (node.text or "").strip()[:200] or None
                elif name in {"loc", "link"}:
                    if name == "link" and node.attrib.get("rel", "alternate") != "alternate":
                        continue
                    raw_link = node.attrib.get("href") if name == "link" else node.text
                    if not raw_link and name == "link":
                        raw_link = node.text
                    url = _allowed_url(raw_link, result.final_url, hosts)
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                result_links.append(
                    DiscoveredUrl(
                        url=url,
                        source_key=self.definition.key,
                        discovery_method=(
                            "sitemap_index"
                            if kind == "sitemapindex"
                            else "sitemap"
                            if kind == "urlset"
                            else "rss_atom"
                        ),
                        parent_url=result.final_url,
                        depth=1,
                        priority=self.definition.priority,
                        metadata={"feed_title": title} if title else {},
                    )
                )
            except ValidationError:
                continue
        # An RSS/Atom publisher may paginate into *other XML documents*, unlike
        # the in-document listing_offset cursor used by large feeds. Enqueue a
        # next-page URL only after every entry in the current document has been
        # checkpointed. No third-party host gets fetch permission from the feed.
        if end == len(items):
            next_url = _next_feed_page(root, kind, result.final_url, hosts)
            if next_url:
                with contextlib.suppress(ValidationError):
                    result_links.append(
                        DiscoveredUrl(
                            url=next_url,
                            source_key=self.definition.key,
                            discovery_method="rss_pagination",
                            parent_url=result.final_url,
                            depth=1,
                            priority=self.definition.priority,
                            metadata={"is_feed_page": True},
                        )
                    )
        return ListingPage(result_links, end, end < len(items))

    def _parse_large_listing(self, result: FetchResult, *, offset: int) -> ListingPage:
        """Stream bounded XML without materializing all entries or expanded gzip.

        A failed, truncated or unsafe document yields no discoveries. The runner
        checkpoints discovered URLs and listing_offset only after a full parse.
        """
        empty = ListingPage([], 0, False)
        data = result.body
        if len(data) > MAX_STREAM_INPUT_BYTES:
            return empty
        compressed = data.startswith(b"\x1f\x8b")
        stream = GzipFile(fileobj=BytesIO(data)) if compressed else BytesIO(data)
        hosts = {
            self.definition.domain.lower(),
            *(h.lower() for h in self.definition.allowed_hosts),
        }
        # Expat-level DTD/entity rejection also covers UTF-16/UTF-8 encodings;
        # checking only raw ASCII sequences is not sufficient.
        parser = ET.XMLPullParser(
            events=("start", "end"),
            _parser=DefusedXMLParser(forbid_dtd=True),
        )
        stack: list[str] = []
        stack_nodes: list[ET.Element] = []
        kind: str | None = None
        next_url: str | None = None
        links: list[DiscoveredUrl] = []
        seen: set[str] = set()
        entries = 0
        total_bytes = 0
        elements_seen = 0
        # Carry a short suffix so <!DOCTYPE / <!ENTITY split across read
        # boundaries cannot bypass the prohibition on DTDs/entities.
        tail = b""
        try:
            with stream:
                while True:
                    chunk = stream.read(XML_STREAM_CHUNK)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_STREAM_XML_BYTES:
                        return empty
                    scanned = (tail + chunk).upper()
                    if b"<!DOCTYPE" in scanned or b"<!ENTITY" in scanned:
                        return empty
                    tail = scanned[-16:]
                    parser.feed(chunk)
                    for action, elem in parser.read_events():
                        name = _element_name(elem.tag).lower()
                        if action == "start":
                            elements_seen += 1
                            if (
                                elements_seen > MAX_STREAM_ELEMENTS
                                or len(stack) >= MAX_STREAM_DEPTH
                            ):
                                return empty
                            stack.append(name)
                            stack_nodes.append(elem)
                            if len(stack) == 1:
                                kind = name
                                if kind not in {"rss", "rdf", "feed", "urlset", "sitemapindex"}:
                                    return empty
                                if (
                                    kind in {"rss", "rdf", "feed"}
                                    and self.definition.ingestion_mode != IngestionMode.RSS
                                ) or (
                                    kind in {"urlset", "sitemapindex"}
                                    and self.definition.ingestion_mode != IngestionMode.SITEMAP
                                ):
                                    return empty
                            continue
                        parent = stack[-2] if len(stack) > 1 else None
                        # rel=next belongs to the feed/channel, never an entry.
                        if (
                            name == "link"
                            and elem.attrib.get("rel", "").casefold().strip() == "next"
                            and (
                                (kind == "feed" and parent == "feed")
                                or (kind in {"rss", "rdf"} and parent == "channel")
                            )
                        ):
                            candidate = _allowed_url(
                                elem.attrib.get("href") or elem.text, result.final_url, hosts
                            )
                            if candidate and candidate != result.final_url and not next_url:
                                next_url = candidate
                        entry_kind = (
                            "sitemap"
                            if kind == "sitemapindex"
                            else "url"
                            if kind == "urlset"
                            else "entry"
                            if kind == "feed"
                            else "item"
                        )
                        if name == entry_kind and parent in {
                            "channel",
                            "feed",
                            "urlset",
                            "sitemapindex",
                            "rdf",
                        }:
                            maximum = (
                                MAX_CHILD_SITEMAPS if kind == "sitemapindex" else MAX_FEED_LINKS
                            )
                            if offset <= entries < offset + maximum:
                                url: str | None = None
                                title: str | None = None
                                for child in elem:
                                    field = _element_name(child.tag)
                                    if field == "title":
                                        title = (child.text or "").strip()[:200] or None
                                    elif field in {"loc", "link"}:
                                        if (
                                            field == "link"
                                            and child.attrib.get("rel", "alternate") != "alternate"
                                        ):
                                            continue
                                        raw = (
                                            (child.attrib.get("href") or child.text)
                                            if field == "link"
                                            else child.text
                                        )
                                        url = _allowed_url(raw, result.final_url, hosts)
                                if url and url not in seen:
                                    seen.add(url)
                                    with contextlib.suppress(ValidationError):
                                        links.append(
                                            DiscoveredUrl(
                                                url=url,
                                                source_key=self.definition.key,
                                                discovery_method=(
                                                    "sitemap_index"
                                                    if kind == "sitemapindex"
                                                    else "sitemap"
                                                    if kind == "urlset"
                                                    else "rss_atom"
                                                ),
                                                parent_url=result.final_url,
                                                depth=1,
                                                priority=self.definition.priority,
                                                metadata={"feed_title": title} if title else {},
                                            )
                                        )
                            entries += 1
                            # Discard processed siblings to keep memory bounded.
                            elem.clear()
                            # Clear the direct parent as well. Otherwise empty
                            # item nodes accumulate in a large RSS channel.
                            if len(stack_nodes) > 1:
                                stack_nodes[-2].clear()
                        stack.pop()
                        stack_nodes.pop()
                parser.close()  # reject trailing malformed/truncated XML
        except (ET.ParseError, DefusedXmlException, OSError, EOFError, ZlibError, ValueError):
            return empty
        if kind is None or stack:
            return empty
        maximum = MAX_CHILD_SITEMAPS if kind == "sitemapindex" else MAX_FEED_LINKS
        end = min(entries, offset + maximum)
        has_more = end < entries
        if not has_more and next_url:
            with contextlib.suppress(ValidationError):
                links.append(
                    DiscoveredUrl(
                        url=next_url,
                        source_key=self.definition.key,
                        discovery_method="rss_pagination",
                        parent_url=result.final_url,
                        depth=1,
                        priority=self.definition.priority,
                        metadata={"is_feed_page": True},
                    )
                )
        return ListingPage(links, end, has_more)
