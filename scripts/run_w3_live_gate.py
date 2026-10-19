#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import ssl
import sys
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

USER_AGENT = "HackathonIntelligenceBot/0.1 (+research validation; respects robots.txt)"
TIMEOUT = 20
MAX_BYTES = 2_000_000
MAX_SITEMAP_URLS = 80
MAX_RELATED_LINKS = 30

TARGETS = [
    "https://www.spaceappschallenge.org/",
    "https://hackathon2026.assemblee-nationale.fr/",
    "https://presidential-hackathon.taiwan.gov.tw/",
    "https://www.aifestival.jp/2026",
]

EVENT_TERMS = ("hackathon", "hackatón", "ハッカソン", "해커톤", "黑客松", "總統盃黑客松")
RELATED_TERMS = (
    "register", "registration", "apply", "rules", "terms", "faq", "results", "winners",
    "projects", "challenges", "schedule", "inscri", "règlement", "登録", "規約", "报名", "規則",
)


@dataclass
class DomainResult:
    target: str
    robots_status: int | None
    robots_allowed: bool
    robots_sitemaps: int
    sitemap_candidates: int
    page_status: int | None
    page_bytes: int
    hackathon_like: bool
    related_links: int
    error: str | None = None


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def fetch(url: str) -> tuple[int, str, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml,text/xml,*/*;q=0.5"})
    context = ssl.create_default_context()
    with urlopen(request, timeout=TIMEOUT, context=context) as response:
        body = response.read(MAX_BYTES)
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        return response.status, body.decode(charset, errors="replace"), content_type


def robots_for(target: str) -> tuple[int | None, str, RobotFileParser, bool]:
    p = urlsplit(target)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    rp = RobotFileParser(robots_url)
    try:
        status, body, _ = fetch(robots_url)
        rp.parse(body.splitlines())
        return status, body, rp, rp.can_fetch(USER_AGENT, target)
    except Exception as exc:
        # Conservative behavior for live validation: if robots cannot be checked, do not fetch target.
        rp.parse(["User-agent: *", "Disallow: /"])
        return None, f"ERROR:{type(exc).__name__}", rp, False


def robots_sitemaps(body: str, base_url: str) -> list[str]:
    out=[]
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":",1)
        if key.strip().casefold() == "sitemap":
            url=urljoin(base_url,value.strip())
            if url.startswith(("http://","https://")) and url not in out:
                out.append(url)
    return out


def sitemap_urls(xml: str) -> list[str]:
    try:
        root=ET.fromstring(xml)
    except ET.ParseError:
        return []
    tag=root.tag.rsplit("}",1)[-1]
    out=[]
    if tag == "urlset":
        for item in root:
            for child in item:
                if child.tag.rsplit("}",1)[-1] == "loc" and child.text:
                    out.append(child.text.strip())
                    break
            if len(out) >= MAX_SITEMAP_URLS:
                break
    elif tag == "sitemapindex":
        for item in root:
            for child in item:
                if child.tag.rsplit("}",1)[-1] == "loc" and child.text:
                    out.append(child.text.strip())
                    break
            if len(out) >= 10:
                break
    return out


def is_hackathon_like(text: str) -> bool:
    low=re.sub(r"\s+"," ",text.casefold())
    return any(term.casefold() in low for term in EVENT_TERMS)


def related_count(html: str, base_url: str) -> int:
    parser=LinkParser(); parser.feed(html)
    seen=set(); count=0
    for href,text in parser.links:
        absolute=urljoin(base_url,href)
        if not absolute.startswith(("http://","https://")) or absolute in seen:
            continue
        hay=f"{text} {absolute}".casefold()
        if any(term.casefold() in hay for term in RELATED_TERMS):
            seen.add(absolute); count += 1
            if count >= MAX_RELATED_LINKS:
                break
    return count


def run_target(target: str) -> DomainResult:
    robots_status, robots_body, rp, allowed = robots_for(target)
    maps=robots_sitemaps(robots_body,target) if robots_status else []
    p=urlsplit(target); origin=f"{p.scheme}://{p.netloc}"
    for candidate in (f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"):
        if candidate not in maps:
            maps.append(candidate)
    sitemap_candidate_count=0
    # Never fetch a sitemap that robots disallows for this UA.
    for sitemap in maps[:4]:
        if not rp.can_fetch(USER_AGENT, sitemap):
            continue
        try:
            status, body, ctype=fetch(sitemap)
            if status < 400 and ("xml" in ctype.casefold() or body.lstrip().startswith("<")):
                sitemap_candidate_count += len(sitemap_urls(body))
        except Exception:
            pass
        if sitemap_candidate_count >= MAX_SITEMAP_URLS:
            sitemap_candidate_count=MAX_SITEMAP_URLS
            break
    if not allowed:
        return DomainResult(target,robots_status,False,len(maps),sitemap_candidate_count,None,0,False,0,"robots_disallow_or_unavailable")
    try:
        status, body, _=fetch(target)
        return DomainResult(target,robots_status,True,len(maps),sitemap_candidate_count,status,len(body.encode('utf-8')),is_hackathon_like(body),related_count(body,target))
    except Exception as exc:
        return DomainResult(target,robots_status,True,len(maps),sitemap_candidate_count,None,0,False,0,f"{type(exc).__name__}: {exc}")


def main() -> int:
    results=[run_target(target) for target in TARGETS]
    summary={
        "targets":len(results),
        "robots_checked":sum(r.robots_status is not None for r in results),
        "robots_allowed":sum(r.robots_allowed for r in results),
        "pages_fetched":sum(r.page_status is not None and r.page_status < 400 for r in results),
        "hackathon_like_pages":sum(r.hackathon_like for r in results),
        "related_links":sum(r.related_links for r in results),
        "sitemap_candidates_capped":sum(r.sitemap_candidates for r in results),
        "max_sitemap_urls_per_domain":MAX_SITEMAP_URLS,
        "results":[asdict(r) for r in results],
    }
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    checks=[
        summary["targets"] == 4,
        summary["robots_checked"] >= 3,
        summary["pages_fetched"] >= 2,
        summary["hackathon_like_pages"] >= 2,
        all(r.sitemap_candidates <= MAX_SITEMAP_URLS for r in results),
    ]
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
