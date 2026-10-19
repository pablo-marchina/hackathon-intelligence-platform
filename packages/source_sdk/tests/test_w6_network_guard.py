import asyncio
import socket
import pytest
from hackathon_sources.network_guard import validate_destination


def test_denied_urls_before_dns():
    for url in ('http://source.test/a', 'https://127.0.0.1/a',
                'https://source.test:8080/a', 'https://user:pass@source.test/a',
                'https://outside.test/a'):
        with pytest.raises(PermissionError):
            asyncio.run(validate_destination(url, {'source.test'}))


def test_private_dns_is_blocked(monkeypatch):
    async def fake_dns(self, host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.2', 443))]
    monkeypatch.setattr(asyncio.AbstractEventLoop, 'getaddrinfo', fake_dns, raising=False)
    # Patch active loop implementation, not AbstractEventLoop.
    loop = asyncio.new_event_loop()
    try:
        monkeypatch.setattr(loop, 'getaddrinfo', fake_dns.__get__(loop))
        with pytest.raises(PermissionError, match='private/reserved'):
            loop.run_until_complete(validate_destination('https://source.test/a', {'source.test'}))
    finally:
        loop.close()


def test_public_dns(monkeypatch):
    loop = asyncio.new_event_loop()
    async def fake_dns(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
    try:
        monkeypatch.setattr(loop, 'getaddrinfo', fake_dns)
        loop.run_until_complete(validate_destination('https://source.test/a', {'source.test'}))
    finally:
        loop.close()
