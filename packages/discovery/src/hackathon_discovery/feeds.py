from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class FeedUrl:
    url: str
    last_modified: str | None = None
    title: str | None = None
    source_kind: str = "feed"


def origin_for(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme or 'https'}://{parts.netloc}"


def parse_robots_sitemaps(body: str, *, base_url: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().casefold() != "sitemap":
            continue
        absolute = urljoin(base_url, value.strip())
        if absolute.startswith(("http://", "https://")) and absolute not in seen:
            seen.add(absolute)
            result.append(absolute)
    return result


def common_discovery_urls(base_url: str) -> dict[str, list[str]]:
    origin = origin_for(base_url)
    return {
        "robots": [f"{origin}/robots.txt"],
        "sitemaps": [
            f"{origin}/sitemap.xml",
            f"{origin}/sitemap_index.xml",
            f"{origin}/sitemap-index.xml",
        ],
        "feeds": [f"{origin}/feed", f"{origin}/rss", f"{origin}/rss.xml", f"{origin}/atom.xml"],
    }


def parse_sitemap(
    xml: str, *, max_urls: int = 5_000, max_child_sitemaps: int = 100
) -> tuple[list[FeedUrl], list[str]]:
    root = ET.fromstring(xml)
    tag = root.tag.rsplit("}", 1)[-1]
    urls: list[FeedUrl] = []
    child_sitemaps: list[str] = []
    if tag == "urlset":
        for item in root:
            loc = None
            lastmod = None
            for child in item:
                name = child.tag.rsplit("}", 1)[-1]
                if name == "loc":
                    loc = (child.text or "").strip()
                elif name == "lastmod":
                    lastmod = (child.text or "").strip() or None
            if loc:
                urls.append(FeedUrl(loc, lastmod, source_kind="sitemap"))
                if len(urls) >= max_urls:
                    break
    elif tag == "sitemapindex":
        for item in root:
            for child in item:
                if child.tag.rsplit("}", 1)[-1] == "loc" and child.text:
                    child_sitemaps.append(child.text.strip())
                    break
            if len(child_sitemaps) >= max_child_sitemaps:
                break
    return urls, child_sitemaps


def parse_rss_atom(xml: str, *, max_entries: int = 1_000) -> list[FeedUrl]:
    root = ET.fromstring(xml)
    result: list[FeedUrl] = []
    for node in root.iter():
        name = node.tag.rsplit("}", 1)[-1]
        if name not in {"item", "entry"}:
            continue
        link: str | None = None
        title: str | None = None
        modified: str | None = None
        for child in node:
            child_name = child.tag.rsplit("}", 1)[-1]
            if child_name == "title" and child.text:
                title = child.text.strip()
            elif child_name in {"updated", "published", "pubDate"} and child.text and not modified:
                modified = child.text.strip()
            elif child_name == "link":
                href = child.attrib.get("href")
                if href:
                    link = href.strip()
                elif child.text:
                    link = child.text.strip()
        if link:
            result.append(FeedUrl(link, modified, title, "rss_atom"))
        if len(result) >= max_entries:
            break
    return result


def bounded_domain_candidates(entries: list[FeedUrl], *, domain: str, cap: int) -> list[FeedUrl]:
    normalized = domain.casefold().removeprefix("www.")
    result: list[FeedUrl] = []
    seen: set[str] = set()
    for entry in entries:
        host = (urlsplit(entry.url).hostname or "").casefold().removeprefix("www.")
        if host != normalized or entry.url in seen:
            continue
        seen.add(entry.url)
        result.append(entry)
        if len(result) >= cap:
            break
    return result
