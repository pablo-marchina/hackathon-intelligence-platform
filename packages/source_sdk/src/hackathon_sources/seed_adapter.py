from __future__ import annotations

from collections.abc import AsyncIterator

from .adapter import SourceAdapter
from .models import DiscoveredUrl


class SeedUrlAdapter(SourceAdapter):
    """Minimal adapter for sources whose first integration step is known seed URLs."""

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        for url in self.definition.seed_urls:
            yield DiscoveredUrl(
                url=url,
                source_key=self.definition.key,
                discovery_method="seed",
                depth=0,
                priority=self.definition.priority,
            )
