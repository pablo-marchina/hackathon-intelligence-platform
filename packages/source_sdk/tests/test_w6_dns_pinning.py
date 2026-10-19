"""Hermetic checks of connection-time DNS pinning and TLS host preservation."""
from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable, Iterable

import httpcore
import httpx
import pytest

from hackathon_sources.fetcher import FetchEngine
from hackathon_sources.models import IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.network_guard import (
    PinnedAsyncHTTPTransport,
    PublicIPNetworkBackend,
    resolve_public_addresses,
)


class FakeStream(httpcore.AsyncNetworkStream):
    def __init__(self) -> None:
        self.body = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
        self.written = b""
        self.tls_hostname: str | None = None

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        chunk, self.body = self.body[:max_bytes], self.body[max_bytes:]
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.written += buffer

    async def aclose(self) -> None:
        pass

    async def start_tls(
        self, ssl_context: object, server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.tls_hostname = server_hostname
        return self

    def get_extra_info(self, info: str) -> object:
        return None


class FakeDelegate(httpcore.AsyncNetworkBackend):
    def __init__(self, *, fail_first: bool = False) -> None:
        self.hosts: list[str] = []
        self.stream = FakeStream()
        self.fail_first = fail_first

    async def connect_tcp(
        self, host: str, port: int, timeout: float | None = None,
        local_address: str | None = None, socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.hosts.append(host)
        if self.fail_first and len(self.hosts) == 1:
            raise httpcore.ConnectError("first route failed")
        return self.stream

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("Unix sockets are never acceptable")

    async def sleep(self, seconds: float) -> None:
        pass


def dns(monkeypatch: pytest.MonkeyPatch, answer: Callable[[], list[str]]) -> None:
    """Fake only DNS, not the security checks or HTTP request pipeline."""
    async def lookup(self: asyncio.AbstractEventLoop, host: str, port: int, **kw: object):
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET,
             socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in answer()
        ]
    monkeypatch.setattr("asyncio.base_events.BaseEventLoop.getaddrinfo", lookup)


def source() -> SourceDefinition:
    return SourceDefinition(
        key="pinned", name="Pinned", domain="example.org", source_family="example",
        source_type="platform", ingestion_mode=IngestionMode.API,
        permission_status=PermissionStatus.ALLOWED, detail_extraction_enabled=True,
    )


def test_transport_uses_pinned_numeric_ip_and_preserves_tls_sni_and_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dns(monkeypatch, lambda: ["8.8.8.8"])
    delegate = FakeDelegate()

    async def scenario() -> None:
        transport = PinnedAsyncHTTPTransport()
        assert isinstance(transport._pool._network_backend, PublicIPNetworkBackend)
        transport._pool._network_backend = PublicIPNetworkBackend(delegate)
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            response = await client.get("https://example.org/events")
            assert response.status_code == 200
            assert response.content == b"OK"

    asyncio.run(scenario())
    assert delegate.hosts == ["8.8.8.8"]
    assert delegate.stream.tls_hostname == "example.org"
    assert b"Host: example.org\r\n" in delegate.stream.written


def test_dns_rebinding_to_private_address_after_preflight_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([["8.8.8.8"], ["169.254.169.254"]])
    dns(monkeypatch, lambda: next(answers))
    delegate = FakeDelegate()

    async def scenario() -> None:
        async with FetchEngine() as fetcher:
            assert isinstance(fetcher.client._transport, PinnedAsyncHTTPTransport)
            fetcher.client._transport._pool._network_backend = PublicIPNetworkBackend(delegate)
            with pytest.raises(PermissionError, match="private/reserved"):
                await fetcher.fetch(source(), "https://example.org/api")

    asyncio.run(scenario())
    assert delegate.hosts == []


def test_mixed_dns_response_is_never_used_to_dial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dns(monkeypatch, lambda: ["8.8.8.8", "10.0.0.1"])
    delegate = FakeDelegate()

    async def scenario() -> None:
        backend = PublicIPNetworkBackend(delegate)
        with pytest.raises(PermissionError, match="private/reserved"):
            await backend.connect_tcp("example.org", 443)

    asyncio.run(scenario())
    assert delegate.hosts == []


def test_failed_public_ip_connection_only_falls_back_to_another_public_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dns(monkeypatch, lambda: ["8.8.8.8", "1.1.1.1"])
    delegate = FakeDelegate(fail_first=True)

    async def scenario() -> None:
        await PublicIPNetworkBackend(delegate).connect_tcp("example.org", 443)

    asyncio.run(scenario())
    assert delegate.hosts == ["8.8.8.8", "1.1.1.1"]


def test_dns_failure_is_not_treated_as_public(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unreachable(self, host: str, port: int, **kw: object):
        raise socket.gaierror("failure")
    monkeypatch.setattr("asyncio.base_events.BaseEventLoop.getaddrinfo", unreachable)
    with pytest.raises(PermissionError, match="DNS lookup failed"):
        asyncio.run(resolve_public_addresses("example.org"))


def test_unsafe_literal_and_ports_are_rejected_before_connect() -> None:
    delegate = FakeDelegate()

    async def scenario() -> None:
        backend = PublicIPNetworkBackend(delegate)
        with pytest.raises(PermissionError, match="private/reserved"):
            await backend.connect_tcp("127.0.0.1", 443)
        with pytest.raises(PermissionError, match="port 443"):
            await backend.connect_tcp("8.8.8.8", 8443)
        with pytest.raises(PermissionError, match="Unix-domain"):
            await backend.connect_unix_socket("/tmp/postgres.sock")
        with pytest.raises(PermissionError, match="local address"):
            await backend.connect_tcp("8.8.8.8", 443, local_address="127.0.0.1")

    asyncio.run(scenario())
    assert delegate.hosts == []


def test_fetch_engine_shares_one_pinned_transport_with_robots() -> None:
    async def scenario() -> None:
        async with FetchEngine() as fetcher:
            assert fetcher.robots.client is fetcher.client
            assert isinstance(fetcher.client._transport, PinnedAsyncHTTPTransport)
            assert isinstance(
                fetcher.client._transport._pool._network_backend, PublicIPNetworkBackend,
            )
            assert fetcher.client._trust_env is False

    asyncio.run(scenario())
