"""HTTP transport tests: robots SSRF, checked hops, and bounded memory."""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx
import pytest

import hackathon_sources.fetcher as fetcher_module
import hackathon_sources.robots as robots_module
from hackathon_sources.fetcher import FetchEngine, MAX_SOURCE_BYTES
from hackathon_sources.models import IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.robots import MAX_ROBOTS_BYTES, RobotsCache


async def destination_guard(url: str, allowed_hosts: set[str]) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.port not in (None, 443):
        raise PermissionError("unsafe destination blocked")


def fake_dns_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock only DNS for hermetic tests; keep security policy checks for both fetch paths.
    monkeypatch.setattr(robots_module, "validate_destination", destination_guard)
    monkeypatch.setattr(fetcher_module, "validate_destination", destination_guard)


def source(mode: IngestionMode = IngestionMode.STATIC_HTML) -> SourceDefinition:
    return SourceDefinition(
        key="guarded", name="Guarded", domain="example.org", source_family="guarded",
        source_type="platform", ingestion_mode=mode,
        permission_status=PermissionStatus.ALLOWED, detail_extraction_enabled=True,
    )


def test_robots_allows_only_checked_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns_guard(monkeypatch)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"Location": "https://localhost/internal"})
        raise AssertionError("SSRF redirected to unsafe host")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            robots = RobotsCache(client, "TestBot")
            with pytest.raises(PermissionError, match="unsafe destination"):
                await robots.allowed("https://example.org/", allowed_hosts={"example.org"})

    asyncio.run(scenario())
    assert requests == ["https://example.org/robots.txt"]


def test_robots_http_to_https_downgrade_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns_guard(monkeypatch)
    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"Location": "http://example.org/robots.txt"}),
        )) as client:
            with pytest.raises(PermissionError, match="unsafe destination"):
                await RobotsCache(client, "TestBot").allowed(
                    "https://example.org/events", allowed_hosts={"example.org"},
                )
    asyncio.run(scenario())


def test_robots_policy_denies_paths_and_can_be_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns_guard(monkeypatch)
    fetched: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\nAllow: /public\n")
    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            robots = RobotsCache(client, "TestBot")
            assert not await robots.allowed("https://example.org/private/a", allowed_hosts={"example.org"})
            assert await robots.allowed("https://example.org/public/a", allowed_hosts={"example.org"})
    asyncio.run(scenario())
    assert fetched == ["https://example.org/robots.txt"]


@pytest.mark.parametrize("status,allowed", [(404, True), (410, True), (403, False), (503, False)])
def test_robots_status_handling(monkeypatch: pytest.MonkeyPatch, status: int, allowed: bool) -> None:
    fake_dns_guard(monkeypatch)
    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status),
        )) as client:
            robots = RobotsCache(client, "TestBot")
            if allowed:
                assert await robots.allowed("https://example.org/a", allowed_hosts={"example.org"})
            else:
                with pytest.raises(PermissionError, match="unavailable"):
                    await robots.allowed("https://example.org/a", allowed_hosts={"example.org"})
    asyncio.run(scenario())


def test_robots_maximum_decoded_size_blocks_chunked_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns_guard(monkeypatch)
    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"a" * (MAX_ROBOTS_BYTES + 1)),
        )) as client:
            with pytest.raises(PermissionError, match="maximum size"):
                await RobotsCache(client, "TestBot").allowed(
                    "https://example.org/a", allowed_hosts={"example.org"},
                )
    asyncio.run(scenario())


def test_source_fetch_respects_robots_and_blocks_unsafe_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns_guard(monkeypatch)
    requests: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(302, headers={"Location": "https://localhost/metadata"})
    async def scenario() -> None:
        async with FetchEngine() as fetcher:
            await fetcher.client.aclose()
            fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            fetcher.robots = RobotsCache(fetcher.client, fetcher.user_agent)
            with pytest.raises(PermissionError, match="unsafe destination"):
                await fetcher.fetch(source(), "https://example.org/events")
    asyncio.run(scenario())
    assert requests == ["https://example.org/robots.txt", "https://example.org/events"]


def test_source_fetch_honors_robots_disallow(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns_guard(monkeypatch)
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /events\n")
    async def scenario() -> None:
        async with FetchEngine() as fetcher:
            await fetcher.client.aclose()
            fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            fetcher.robots = RobotsCache(fetcher.client, fetcher.user_agent)
            with pytest.raises(PermissionError, match="robots.txt disallows"):
                await fetcher.fetch(source(), "https://example.org/events")
    asyncio.run(scenario())


def test_large_api_response_refused_without_buffering(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns_guard(monkeypatch)
    async def scenario() -> None:
        async with FetchEngine() as fetcher:
            await fetcher.client.aclose()
            fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * (MAX_SOURCE_BYTES + 1)),
            ))
            with pytest.raises(PermissionError, match="maximum size"):
                await fetcher.fetch(source(IngestionMode.API), "https://example.org/api")
    asyncio.run(scenario())


def test_allowed_small_api_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns_guard(monkeypatch)
    async def scenario() -> None:
        async with FetchEngine() as fetcher:
            await fetcher.client.aclose()
            fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b'{"ok": true}'),
            ))
            result = await fetcher.fetch(source(IngestionMode.API), "https://example.org/api")
            assert result.body == b'{"ok": true}' and result.status_code == 200
    asyncio.run(scenario())
