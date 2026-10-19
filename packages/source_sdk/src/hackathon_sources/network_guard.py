"""Fail-closed URL validation and DNS-pinned outbound HTTPS transport.

The URL allowlist is checked before each redirect in the fetcher. The transport
also resolves and validates DNS *at connection time*, and passes a numeric IP
address to the underlying socket backend. This closes the preflight DNS
TOCTOU/rebinding gap while keeping HTTP Host, TLS SNI, and certificate checks
bound to the original hostname. Proxies and Unix-domain sockets are forbidden.

This adapter deliberately uses httpx's httpcore pool integration. Keep the
httpx/httpcore versions constrained and the transport contract tests in CI.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlsplit

import httpcore
import httpx


def _public(address: str) -> bool:
    """Reject loopback, link-local, private, reserved, and unspecified IPs."""
    return ipaddress.ip_address(address.split("%", 1)[0]).is_global


async def resolve_public_addresses(host: str, port: int = 443) -> tuple[str, ...]:
    """Return the complete public DNS answer set or refuse the connection.

    A mixture of public and private answers is not an acceptable fallback.
    DNS is deliberately resolved again during socket establishment.
    """
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM,
            )
        except (OSError, TimeoutError) as exc:
            raise PermissionError("destination DNS lookup failed; fail closed") from exc
        addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records))
    else:
        addresses = (str(literal),)
    if not addresses or not all(_public(address) for address in addresses):
        raise PermissionError("destination DNS includes private/reserved addresses")
    return addresses


async def validate_destination(url: str, allowed_hosts: set[str]) -> None:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise PermissionError("malformed destination URL") from exc
    if parsed.scheme != "https":
        raise PermissionError("only HTTPS destinations are permitted")
    if parsed.username or parsed.password or port not in (None, 443):
        raise PermissionError("URL userinfo or nonstandard port is not permitted")
    if not host or host not in allowed_hosts:
        raise PermissionError("destination host not on source allowlist")
    await resolve_public_addresses(host)


class PublicIPNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect to an IP returned by a fresh, fully validated DNS response.

    ``httpcore`` receives the *original* hostname in its connection object,
    so changing only the network backend's connect_tcp target retains normal
    HTTPS certificate validation, SNI and HTTP Host routing.
    """

    def __init__(self, delegate: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._delegate = delegate if delegate is not None else httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if port != 443:
            raise PermissionError("outbound connection must use port 443")
        if local_address is not None:
            raise PermissionError("outbound local address overrides are prohibited")
        addresses = await resolve_public_addresses(host, port)
        last_error: httpcore.ConnectError | httpcore.ConnectTimeout | None = None
        for address in addresses:
            try:
                return await self._delegate.connect_tcp(
                    address, port, timeout=timeout, local_address=None,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise PermissionError("Unix-domain sockets are not permitted for source fetches")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport with a DNS-pinning backend; no proxy support."""

    def __init__(self, *, limits: httpx.Limits | None = None) -> None:
        active_limits = limits or httpx.Limits(max_connections=32, max_keepalive_connections=16)
        super().__init__(trust_env=False, limits=active_limits)
        # HTTPX 0.28 uses httpcore.AsyncConnectionPool for direct transports.
        # HTTPX has no public hook to supply a network backend. Replacing the
        # brand-new empty pool is intentional and covered by contract tests.
        if not isinstance(self._pool, httpcore.AsyncConnectionPool):
            raise RuntimeError("unsupported HTTPX transport; refuse unsafe networking")
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            max_connections=active_limits.max_connections,
            max_keepalive_connections=active_limits.max_keepalive_connections,
            keepalive_expiry=active_limits.keepalive_expiry,
            network_backend=PublicIPNetworkBackend(),
        )
