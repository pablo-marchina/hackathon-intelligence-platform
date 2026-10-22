from __future__ import annotations

from collections.abc import Callable

from .adapter import SourceAdapter
from .live_adapters import (
    AgendaTechBrasilAdapter,
    HackClubCommunityEventsAdapter,
    HackClubHackathonsAdapter,
    HackClubProgramsAdapter,
)
from .models import IngestionMode, PermissionStatus, SourceDefinition
from .seed_adapter import SeedUrlAdapter
from .structured_html import StructuredHtmlEventAdapter
from .syndication import SyndicatedEventAdapter

AdapterFactory = Callable[[SourceDefinition], SourceAdapter]

_LIVE_ADAPTERS: dict[str, type[SourceAdapter]] = {
    "agenda_tech_brasil": AgendaTechBrasilAdapter,
    "hackclub_hackathons": HackClubHackathonsAdapter,
    "hackclub_programs": HackClubProgramsAdapter,
    "hackclub_community_events": HackClubCommunityEventsAdapter,
}


def build_adapter(definition: SourceDefinition) -> SourceAdapter:
    adapter_type = _LIVE_ADAPTERS.get(definition.key)
    if adapter_type is not None:
        return adapter_type(definition)
    if (
        definition.ingestion_mode in (IngestionMode.RSS, IngestionMode.SITEMAP)
        and definition.permission_status is PermissionStatus.ALLOWED
        and definition.detail_extraction_enabled
    ):
        return SyndicatedEventAdapter(definition)
    if (
        definition.ingestion_mode is IngestionMode.STATIC_HTML
        and definition.permission_status is PermissionStatus.ALLOWED
        and definition.detail_extraction_enabled
    ):
        return StructuredHtmlEventAdapter(definition)
    return SeedUrlAdapter(definition)


def live_adapter_keys() -> frozenset[str]:
    return frozenset(_LIVE_ADAPTERS)
