from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Iterable, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, HttpUrl

from hackathon_sources.models import DiscoveredUrl
from hackathon_sources.url import canonicalize_url

SEARCH_RESULT_AUTHORITY = "DISCOVERY_ONLY"


class SearchResult(BaseModel):
    url: HttpUrl
    title: str
    snippet: str | None = None
    rank: int


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, limit: int = 20) -> list[SearchResult]: ...


@dataclass(frozen=True)
class SearchDiscoveryRecord:
    provider: str
    query: str
    rank: int
    url: str
    canonical_url: str
    domain: str
    title: str
    snippet: str | None
    discovered_at: datetime
    policy_decision: str
    action: str
    evidence_authority: str = SEARCH_RESULT_AUTHORITY

    @property
    def should_enter_frontier(self) -> bool:
        return self.action == "NEW_CANDIDATE" and self.policy_decision == "ALLOWED"


def ingest_search_results(
    results: Iterable[SearchResult],
    *,
    query: str,
    provider: str,
    policy_for_url: Callable[[str], str],
    existing_canonical_urls: set[str] | None = None,
    frontier_urls: set[str] | None = None,
    discovered_at: datetime | None = None,
) -> list[SearchDiscoveryRecord]:
    existing_canonical_urls = {canonicalize_url(url) for url in (existing_canonical_urls or set())}
    frontier_urls = {canonicalize_url(url) for url in (frontier_urls or set())}
    seen: set[str] = set()
    discovered_at = discovered_at or datetime.now(UTC)
    records: list[SearchDiscoveryRecord] = []
    for result in sorted(results, key=lambda item: item.rank):
        raw_url = str(result.url)
        canonical = canonicalize_url(raw_url)
        if canonical in seen:
            continue
        seen.add(canonical)
        policy = policy_for_url(canonical)
        if canonical in existing_canonical_urls:
            action = "KNOWN_CANONICAL"
        elif canonical in frontier_urls:
            action = "ALREADY_IN_FRONTIER"
        elif policy != "ALLOWED":
            action = "POLICY_BLOCKED"
        else:
            action = "NEW_CANDIDATE"
            frontier_urls.add(canonical)
        records.append(
            SearchDiscoveryRecord(
                provider=provider,
                query=query,
                rank=result.rank,
                url=raw_url,
                canonical_url=canonical,
                domain=(urlsplit(canonical).hostname or "").lower(),
                title=result.title,
                snippet=result.snippet,
                discovered_at=discovered_at,
                policy_decision=policy,
                action=action,
            )
        )
    return records


def to_discovered_urls(records: Iterable[SearchDiscoveryRecord], *, source_key: str = "open_web_search") -> list[DiscoveredUrl]:
    return [
        DiscoveredUrl(
            url=record.canonical_url,
            source_key=source_key,
            discovery_method="search",
            priority=max(0, 100 - record.rank),
            metadata={
                "query": record.query,
                "rank": record.rank,
                "provider": record.provider,
                "title": record.title,
                "snippet": record.snippet,
                "snippet_authority": record.evidence_authority,
                "policy_decision": record.policy_decision,
                "discovered_at": record.discovered_at.isoformat(),
            },
        )
        for record in records
        if record.should_enter_frontier
    ]
