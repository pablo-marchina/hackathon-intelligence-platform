from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import httpx

from .models import FetchResult, IngestionMode, SourceDefinition
from .network_guard import PinnedAsyncHTTPTransport, validate_destination
from .policy import may_fetch_details
from .robots import RobotsCache

MAX_SOURCE_BYTES = 8 * 1024 * 1024  # decoded payload ceiling (includes compressed responses)
REDIRECT_CODES = {301, 302, 303, 307, 308}


def robots_required(source: SourceDefinition, url: str) -> bool:
    """RSS feed endpoints are documented contracts; crawled detail pages are not.

    Redirect targets are checked separately against the configured seed URLs.
    API sources preserve their existing documented-contract behavior.
    """
    if source.ingestion_mode is IngestionMode.API:
        return False
    if source.ingestion_mode is IngestionMode.RSS:
        seeds = {str(seed).rstrip("/") for seed in source.seed_urls}
        return url.rstrip("/") not in seeds
    return True


class RateLimiter:
    def __init__(self) -> None:
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_request: dict[str, float] = {}

    async def wait(self, domain: str, per_minute: float | None) -> None:
        if not per_minute:
            return
        minimum_interval = 60.0 / per_minute
        async with self._locks[domain]:
            previous = self._last_request.get(domain)
            if previous is not None:
                remaining = minimum_interval - (time.monotonic() - previous)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request[domain] = time.monotonic()


class FetchEngine:
    def __init__(self, user_agent: str = "HackathonIntelligenceBot/0.1 (+contact-required)") -> None:
        self.user_agent = user_agent
        self.client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5"},
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
            transport=PinnedAsyncHTTPTransport(
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            ),
        )
        self.robots = RobotsCache(self.client, user_agent)
        self.rate_limiter = RateLimiter()
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    async def __aenter__(self) -> FetchEngine:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.client.aclose()

    async def fetch(
        self,
        source: SourceDefinition,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        decision = may_fetch_details(source)
        if not decision.allowed:
            raise PermissionError(decision.reason)

        domain = (urlsplit(url).hostname or "").lower()
        allowed_hosts = {source.domain.lower(), *(host.lower() for host in source.allowed_hosts)}
        if domain not in allowed_hosts:
            raise PermissionError(f"host {domain!r} is not allowed for source {source.key!r}")

        # Feed endpoints may follow their documented contract. HTML *detail* pages
        # discovered through RSS are crawled pages and MUST honor robots.txt.
        # (API behavior remains unchanged for explicitly documented API sources.)
        use_robots = robots_required(source, url)
        if use_robots:
            if not await self.robots.allowed(url, allowed_hosts=allowed_hosts):
                raise PermissionError("robots.txt disallows fetch for configured user-agent")

        semaphore = self._semaphores.setdefault(domain, asyncio.Semaphore(source.concurrency_limit))
        await self.rate_limiter.wait(domain, source.rate_limit_per_minute)
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        current_url = url
        async with semaphore:
            for _redirect in range(6):
                await validate_destination(current_url, allowed_hosts)
                # A feed endpoint redirect is a *new* destination, not an
                # implicitly authorized HTML detail page. Recheck robots.
                if use_robots or robots_required(source, current_url):
                    if not await self.robots.allowed(current_url, allowed_hosts=allowed_hosts):
                        raise PermissionError("robots.txt disallows redirect destination")
                # Stream bodies under an explicit decoded-size ceiling; get() buffers
                # the full response and can exhaust worker memory on malicious input.
                async with self.client.stream(
                    "GET", current_url, headers=headers, follow_redirects=False,
                ) as response:
                    if response.status_code in REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location:
                            raise PermissionError("redirect without Location header")
                        current_url = urljoin(current_url, location)
                        continue
                    content_length = response.headers.get("content-length")
                    if (
                        content_length
                        and content_length.isdecimal()
                        and int(content_length) > MAX_SOURCE_BYTES
                    ):
                        raise PermissionError("source document exceeds maximum size")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(body) + len(chunk) > MAX_SOURCE_BYTES:
                            raise PermissionError("source document exceeds maximum size")
                        body.extend(chunk)
                    break
            else:
                raise PermissionError("maximum redirect count exceeded")
        return FetchResult(
            requested_url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            body=bytes(body),
            fetched_at=datetime.now(UTC),
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            response_headers=dict(response.headers),
        )
