from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from .models import DiscoveredUrl, FetchResult, SourceDefinition, SourceEventRecord


class SourceAdapter(ABC):
    """Common contract for source-specific discovery/parsing behavior."""

    definition: SourceDefinition

    def __init__(self, definition: SourceDefinition) -> None:
        self.definition = definition

    @abstractmethod
    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """Yield source-specific seed or listing URLs."""
        if False:
            yield  # pragma: no cover

    async def parse_listing(self, result: FetchResult) -> list[DiscoveredUrl]:
        """Return related/detail-page candidates found in a listing response."""
        return []

    async def parse_events(self, result: FetchResult) -> list[SourceEventRecord]:
        """Return source-native event records from a fetched API/feed/listing response."""
        return []

    async def healthcheck(self) -> dict[str, str]:
        return {"source": self.definition.key, "status": "configured"}
