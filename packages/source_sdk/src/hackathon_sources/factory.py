from __future__ import annotations

from collections.abc import Callable

from .adapter import SourceAdapter
from .live_adapters import (
    AgendaTechBrasilAdapter,
    HackClubCommunityEventsAdapter,
    HackClubHackathonsAdapter,
    HackClubProgramsAdapter,
)
from .models import SourceDefinition
from .seed_adapter import SeedUrlAdapter

AdapterFactory = Callable[[SourceDefinition], SourceAdapter]

_LIVE_ADAPTERS: dict[str, type[SourceAdapter]] = {
    "agenda_tech_brasil": AgendaTechBrasilAdapter,
    "hackclub_hackathons": HackClubHackathonsAdapter,
    "hackclub_programs": HackClubProgramsAdapter,
    "hackclub_community_events": HackClubCommunityEventsAdapter,
}


def build_adapter(definition: SourceDefinition) -> SourceAdapter:
    adapter_type = _LIVE_ADAPTERS.get(definition.key, SeedUrlAdapter)
    return adapter_type(definition)


def live_adapter_keys() -> frozenset[str]:
    return frozenset(_LIVE_ADAPTERS)
