from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from .extractors import extract_structured_event, missing_core_fields
from .llm import validate_llm_fallback
from .models import ExtractedEvent


def merge_extracted(primary: ExtractedEvent, fallback: ExtractedEvent) -> ExtractedEvent:
    data = primary.model_dump(exclude={"evidence"})
    fallback_data = fallback.model_dump(exclude={"evidence"})
    for key, value in fallback_data.items():
        if data.get(key) in (None, "") and value not in (None, ""):
            data[key] = value
    return ExtractedEvent(**data, evidence=[*primary.evidence, *fallback.evidence])


async def extract_with_llm_fallback(
    payload: dict[str, Any],
    *,
    source_text: str,
    source_url: str,
    observed_at: datetime,
    llm_call: Callable[..., Awaitable[dict[str, Any]]],
    model_version: str,
    prompt_version: str,
) -> ExtractedEvent:
    deterministic = extract_structured_event(payload, observed_at=observed_at)
    missing = missing_core_fields(deterministic)
    if not missing:
        return deterministic
    raw = await llm_call(text=source_text, source_url=source_url, requested_fields=missing)
    fallback = validate_llm_fallback(
        raw,
        requested_fields=missing,
        observed_at=observed_at,
        model_version=model_version,
        prompt_version=prompt_version,
    )
    return merge_extracted(deterministic, fallback)
