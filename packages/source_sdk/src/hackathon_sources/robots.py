"""Robots policy retrieval via the same guarded transport rules as source pages.

Robots URLs are untrusted network requests. Never allow the HTTP client to follow
an unchecked redirect, and never silently allow crawling on transport failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .network_guard import validate_destination

MAX_ROBOTS_BYTES = 256 * 1024
MAX_ROBOTS_REDIRECTS = 5
_REDIRECTS = {301, 302, 303, 307, 308}


@dataclass
class RobotsEntry:
    parser: RobotFileParser
    body: str


class RobotsCache:
    def __init__(self, client: httpx.AsyncClient, user_agent: str) -> None:
        self.client = client
        self.user_agent = user_agent
        self._cache: dict[str, RobotsEntry] = {}

    async def allowed(self, url: str, *, allowed_hosts: set[str]) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        entry = self._cache.get(origin)
        if entry is None:
            robots_url = f"{origin}/robots.txt"
            current = robots_url
            for _ in range(MAX_ROBOTS_REDIRECTS + 1):
                # Validate *every hop*, including the initial robots.txt request.
                await validate_destination(current, allowed_hosts)
                async with self.client.stream("GET", current, follow_redirects=False) as response:
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise PermissionError("robots redirect missing Location")
                        current = urljoin(current, location)
                        continue
                    parser = RobotFileParser(robots_url)
                    if response.status_code in {404, 410}:
                        # An explicitly absent robots file permits crawling; other
                        # HTTP errors remain fail-closed, especially 5xx/403.
                        parser.parse([])
                        entry = RobotsEntry(parser, "")
                    elif response.status_code == 200:
                        content_length = response.headers.get("content-length")
                        if (
                            content_length
                            and content_length.isdecimal()
                            and int(content_length) > MAX_ROBOTS_BYTES
                        ):
                            raise PermissionError("robots policy exceeds maximum size")
                        body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            if len(body) + len(chunk) > MAX_ROBOTS_BYTES:
                                raise PermissionError("robots policy exceeds maximum size")
                            body.extend(chunk)
                        contents = body.decode("utf-8", errors="replace")
                        parser.parse(contents.splitlines())
                        entry = RobotsEntry(parser, contents)
                    else:
                        raise PermissionError("robots policy is unavailable")
                    break
            else:
                raise PermissionError("robots redirect limit exceeded")
            self._cache[origin] = entry
        return entry.parser.can_fetch(self.user_agent, url)
