"""Source ingestion SDK used by all external source adapters."""

from .adapter import SourceAdapter
from .live_adapters import (
    AgendaTechBrasilAdapter,
    HackClubCommunityEventsAdapter,
    HackClubHackathonsAdapter,
    HackClubProgramsAdapter,
)
from .factory import build_adapter, live_adapter_keys
from .models import DiscoveredUrl, FetchResult, SourceDefinition, SourceEventRecord
from .runner import SourceRunResult, run_source_once

__all__ = [
    "AgendaTechBrasilAdapter",
    "DiscoveredUrl",
    "FetchResult",
    "HackClubCommunityEventsAdapter",
    "HackClubHackathonsAdapter",
    "HackClubProgramsAdapter",
    "SourceAdapter",
    "SourceRunResult",
    "SourceDefinition",
    "SourceEventRecord",
    "build_adapter",
    "live_adapter_keys",
    "run_source_once",
]
