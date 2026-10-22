from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .adapter import SourceAdapter
from .models import FetchResult, SourceDefinition, SourceEventRecord
from .persistent_runner import SourcePausedError
from .source_switch import ActivationCheck


class Fetcher(Protocol):
    async def fetch(
        self,
        source: SourceDefinition,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult: ...


@dataclass
class SourceRunResult:
    source_key: str
    fetched_pages: int = 0
    discovered_links: int = 0
    records: list[SourceEventRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    paused: bool = False

    @property
    def status(self) -> str:
        if self.paused:
            return "CANCELLED"
        if self.errors and not self.fetched_pages:
            return "FAILED"
        if self.errors:
            return "PARTIAL"
        return "SUCCEEDED"


async def run_source_once(
    adapter: SourceAdapter,
    fetcher: Fetcher,
    *,
    max_pages: int = 20,
    is_active: ActivationCheck | None = None,
) -> SourceRunResult:
    """Run one source adapter with bounded pagination.

    External/detail URLs emitted by a source are intentionally not fetched here. They are
    separate discovery candidates and must be assigned their own source/policy first.
    """

    # Preflight happens before discover(), because even discovery can issue IO.
    if is_active is not None and not is_active(adapter.definition):
        raise SourcePausedError(f"source {adapter.definition.key} is paused")
    output = SourceRunResult(source_key=adapter.definition.key)
    queue = [str(item.url) async for item in adapter.discover()]
    seen: set[str] = set()

    while queue and output.fetched_pages < max_pages:
        if is_active is not None and not is_active(adapter.definition):
            output.paused = True
            break
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            fetched = await fetcher.fetch(adapter.definition, url)
            output.fetched_pages += 1
            output.records.extend(await adapter.parse_events(fetched))
            discovered = await adapter.parse_listing(fetched)
            output.discovered_links += len(discovered)
            for item in discovered:
                candidate = str(item.url)
                if candidate not in seen and candidate not in queue:
                    # Only same-source/API pagination belongs in this runner. External URLs
                    # stay as discovery output and are not implicitly granted fetch permission.
                    from urllib.parse import urlsplit

                    host = (urlsplit(candidate).hostname or "").lower()
                    allowed = {
                        adapter.definition.domain.lower(),
                        *(h.lower() for h in adapter.definition.allowed_hosts),
                    }
                    if host in allowed:
                        queue.append(candidate)
        except Exception as exc:  # source isolation: one bad page does not crash the worker
            output.errors.append(f"{url}: {type(exc).__name__}: {exc}")

    return output
