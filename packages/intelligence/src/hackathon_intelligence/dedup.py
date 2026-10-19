from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

from .normalization import normalize_name, normalize_url_signature


@dataclass(frozen=True)
class EventFingerprint:
    name: str
    organizer: str | None
    start_at: datetime | None
    city: str | None
    official_url: str | None = None
    external_ids: tuple[str, ...] = ()
    related_urls: tuple[str, ...] = ()
    text: str | None = None


@dataclass(frozen=True)
class DedupFeatures:
    exact_external_id: float
    exact_official_url: float
    title_similarity: float
    organizer_similarity: float
    date_similarity: float
    location_similarity: float
    url_graph_similarity: float = 0.0
    textual_similarity: float = 0.0
    date_distance_days: float | None = None


def similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, normalize_name(left), normalize_name(right)).ratio()


def build_features(left: EventFingerprint, right: EventFingerprint) -> DedupFeatures:
    exact_external_id = 1.0 if set(left.external_ids) & set(right.external_ids) else 0.0
    left_url = normalize_url_signature(left.official_url) if left.official_url else None
    right_url = normalize_url_signature(right.official_url) if right.official_url else None
    exact_official_url = 1.0 if left_url and left_url == right_url else 0.0
    title_similarity = similarity(left.name, right.name)
    organizer_similarity = similarity(left.organizer, right.organizer)
    date_distance_days: float | None = None
    if left.start_at and right.start_at:
        date_distance_days = abs((left.start_at - right.start_at).total_seconds()) / 86400.0
        date_similarity = max(0.0, 1.0 - min(date_distance_days, 30.0) / 30.0)
    else:
        date_similarity = 0.0
    location_similarity = similarity(left.city, right.city)
    left_graph = {normalize_url_signature(url) for url in left.related_urls if url}
    right_graph = {normalize_url_signature(url) for url in right.related_urls if url}
    if left_url:
        left_graph.add(left_url)
    if right_url:
        right_graph.add(right_url)
    union = left_graph | right_graph
    url_graph_similarity = (len(left_graph & right_graph) / len(union)) if union else 0.0
    textual_similarity = similarity(left.text, right.text)
    return DedupFeatures(
        exact_external_id,
        exact_official_url,
        title_similarity,
        organizer_similarity,
        date_similarity,
        location_similarity,
        url_graph_similarity,
        textual_similarity,
        date_distance_days,
    )


def duplicate_score(features: DedupFeatures) -> float:
    if features.exact_external_id:
        return 0.999
    if features.exact_official_url:
        # Recurring events frequently reuse one domain across editions. An exact
        # URL is therefore conclusive only when event dates are compatible.
        if features.date_distance_days is None:
            return 0.90
        if features.date_distance_days <= 14.0:
            return 0.999
        if features.date_distance_days <= 45.0:
            return 0.90
        return 0.70
    return (
        0.32 * features.title_similarity
        + 0.20 * features.organizer_similarity
        + 0.18 * features.date_similarity
        + 0.12 * features.location_similarity
        + 0.10 * features.url_graph_similarity
        + 0.08 * features.textual_similarity
    )


def dedup_decision(score: float, *, auto_merge_threshold: float = 0.97, review_threshold: float = 0.80) -> str:
    if score >= auto_merge_threshold:
        return "AUTO_MERGE"
    if score >= review_threshold:
        return "REVIEW"
    return "SEPARATE"


@dataclass(frozen=True)
class DedupCandidatePair:
    left_index: int
    right_index: int
    blocking_reasons: tuple[str, ...]
    features: DedupFeatures
    score: float
    decision: str


def _title_block_key(name: str) -> str:
    normalized = normalize_name(name)
    normalized = __import__("re").sub(r"\b(?:19|20)\d{2}\b", " ", normalized)
    return __import__("re").sub(r"[^a-z0-9]+", "", normalized)


def _month_buckets(start_at: datetime | None) -> tuple[str, ...]:
    if start_at is None:
        return ()
    year, month = start_at.year, start_at.month
    buckets = []
    for offset in (-1, 0, 1):
        m = month + offset
        y = year
        if m < 1:
            y -= 1; m = 12
        elif m > 12:
            y += 1; m = 1
        buckets.append(f"{y:04d}-{m:02d}")
    return tuple(buckets)


def generate_candidate_pairs(events: list[EventFingerprint]) -> list[DedupCandidatePair]:
    """Generate duplicate candidates from blocking indexes, never an all-pairs scan.

    Blocks cover deterministic identifiers plus fuzzy title, organizer/date and
    location/date signals. The downstream scorer still decides AUTO_MERGE,
    REVIEW or SEPARATE and preserves edition safeguards.
    """
    blocks: dict[str, list[int]] = defaultdict(list)
    for idx, event in enumerate(events):
        for external_id in event.external_ids:
            if external_id:
                blocks[f"external:{external_id}"] .append(idx)
        if event.official_url:
            blocks[f"url:{normalize_url_signature(event.official_url)}"].append(idx)
        title_key = _title_block_key(event.name)
        if title_key:
            blocks[f"title:{title_key}"].append(idx)
        for bucket in _month_buckets(event.start_at):
            if event.organizer:
                blocks[f"organizer-month:{normalize_name(event.organizer)}:{bucket}"].append(idx)
            if event.city:
                blocks[f"location-month:{normalize_name(event.city)}:{bucket}"].append(idx)
            # Prefix blocks catch small title spelling/punctuation variants without
            # expanding to all pairs. A 6-char prefix is intentionally conservative.
            if len(title_key) >= 6:
                blocks[f"title-prefix-month:{title_key[:6]}:{bucket}"].append(idx)

    reasons_by_pair: dict[tuple[int, int], set[str]] = defaultdict(set)
    for key, members in blocks.items():
        unique = sorted(set(members))
        if len(unique) < 2:
            continue
        reason = key.split(":", 1)[0]
        for pos, left_idx in enumerate(unique):
            for right_idx in unique[pos + 1 :]:
                reasons_by_pair[(left_idx, right_idx)].add(reason)

    out: list[DedupCandidatePair] = []
    for (left_idx, right_idx), reasons in sorted(reasons_by_pair.items()):
        features = build_features(events[left_idx], events[right_idx])
        score = duplicate_score(features)
        out.append(
            DedupCandidatePair(
                left_index=left_idx,
                right_index=right_idx,
                blocking_reasons=tuple(sorted(reasons)),
                features=features,
                score=score,
                decision=dedup_decision(score),
            )
        )
    return out
