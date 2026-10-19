from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from .normalization import normalize_name, normalize_url_signature

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_EDITION_RE = re.compile(
    r"\b(?:spring|summer|fall|autumn|winter|edition|edicao|edição|season|vol|volume)\b",
    re.IGNORECASE,
)
_ORDINAL_RE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\b", re.IGNORECASE)


@dataclass(frozen=True)
class SeriesEvent:
    event_id: str
    name: str
    start_at: datetime | None
    official_url: str | None = None
    organizer: str | None = None
    city: str | None = None


@dataclass(frozen=True)
class SeriesAssignment:
    event_id: str
    series_key: str
    series_name: str
    edition_year: int | None
    edition_label: str | None
    confidence: float
    evidence: tuple[str, ...]


def _compact(value: str) -> str:
    return normalize_name(value).replace(" ", "")


def series_name_key(name: str) -> str:
    normalized = normalize_name(name)
    normalized = _YEAR_RE.sub(" ", normalized)
    normalized = _EDITION_RE.sub(" ", normalized)
    normalized = _ORDINAL_RE.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.replace(" ", "")


def _host(url: str | None) -> str | None:
    if not url:
        return None
    normalized = normalize_url_signature(url)
    return (urlsplit(normalized).hostname or "").lower() or None


def _sim(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, normalize_name(left), normalize_name(right)).ratio()


def _days(left: datetime | None, right: datetime | None) -> float | None:
    if not left or not right:
        return None
    return abs((left - right).total_seconds()) / 86400.0


def series_pair_score(left: SeriesEvent, right: SeriesEvent) -> tuple[float, tuple[str, ...]]:
    """Score whether two *distinct canonicals* are editions of one recurring series.

    Same-window events are intentionally excluded: they belong in duplicate resolution,
    not series linking. A recurring series needs evidence of recurrence across time.
    """

    distance = _days(left.start_at, right.start_at)
    if distance is not None and distance <= 14.0:
        return 0.0, ("same_event_window",)

    left_key = series_name_key(left.name)
    right_key = series_name_key(right.name)
    exact_series_name = bool(left_key and left_key == right_key)
    title_similarity = _sim(left.name, right.name)
    same_host = bool(_host(left.official_url) and _host(left.official_url) == _host(right.official_url))
    organizer_similarity = _sim(left.organizer, right.organizer)
    city_similarity = _sim(left.city, right.city)

    score = 0.0
    evidence: list[str] = []
    if exact_series_name:
        score += 0.62
        evidence.append("series_name_key")
    elif title_similarity >= 0.88:
        score += 0.46
        evidence.append("high_title_similarity")

    if same_host:
        score += 0.24
        evidence.append("same_official_host")
    if organizer_similarity >= 0.90:
        score += 0.09
        evidence.append("same_organizer")
    if city_similarity >= 0.90:
        score += 0.05
        evidence.append("same_city")

    # Name identity is mandatory. A reused corporate/domain host alone must never
    # create a recurring event series.
    if not exact_series_name and title_similarity < 0.88:
        return 0.0, tuple(evidence)

    return min(score, 1.0), tuple(evidence)


class _DSU:
    def __init__(self, ids: list[str]) -> None:
        self.parent = {item: item for item in ids}

    def find(self, item: str) -> str:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def infer_series(
    events: list[SeriesEvent], *, threshold: float = 0.70
) -> dict[str, list[SeriesAssignment]]:
    if not events:
        return {}

    dsu = _DSU([event.event_id for event in events])
    evidence_by_pair: dict[frozenset[str], tuple[float, tuple[str, ...]]] = {}
    for index, left in enumerate(events):
        for right in events[index + 1 :]:
            score, evidence = series_pair_score(left, right)
            if score >= threshold:
                dsu.union(left.event_id, right.event_id)
                evidence_by_pair[frozenset((left.event_id, right.event_id))] = (score, evidence)

    grouped: dict[str, list[SeriesEvent]] = defaultdict(list)
    for event in events:
        grouped[dsu.find(event.event_id)].append(event)

    result: dict[str, list[SeriesAssignment]] = {}
    for members in grouped.values():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda item: (item.start_at or datetime.max, item.event_id))
        key_counts: dict[str, int] = defaultdict(int)
        for member in members:
            key_counts[series_name_key(member.name)] += 1
        series_key = max(key_counts, key=lambda item: (key_counts[item], len(item)))
        representative = min(members, key=lambda item: (len(normalize_name(item.name)), item.name.casefold()))

        per_year: dict[int, list[SeriesEvent]] = defaultdict(list)
        for member in members:
            if member.start_at:
                per_year[member.start_at.year].append(member)

        assignments: list[SeriesAssignment] = []
        for member in members:
            year = member.start_at.year if member.start_at else None
            if year is None:
                edition_label = None
            elif len(per_year[year]) == 1:
                edition_label = str(year)
            else:
                ordered = sorted(per_year[year], key=lambda item: (item.start_at or datetime.max, item.event_id))
                ordinal = ordered.index(member) + 1
                month = member.start_at.month if member.start_at else 0
                edition_label = f"{year}-{month:02d}-{ordinal}"

            pair_scores = [
                score
                for pair, (score, _evidence) in evidence_by_pair.items()
                if member.event_id in pair
            ]
            pair_evidence = {
                item
                for pair, (_score, evidence) in evidence_by_pair.items()
                if member.event_id in pair
                for item in evidence
            }
            assignments.append(
                SeriesAssignment(
                    event_id=member.event_id,
                    series_key=series_key,
                    series_name=representative.name,
                    edition_year=year,
                    edition_label=edition_label,
                    confidence=max(pair_scores) if pair_scores else threshold,
                    evidence=tuple(sorted(pair_evidence)),
                )
            )
        result[series_key] = assignments
    return result
