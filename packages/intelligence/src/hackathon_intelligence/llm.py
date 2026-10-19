from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import ExtractedEvent, FieldEvidence


class StructuredEventExtractor(Protocol):
    """Provider-agnostic interface for W2 LLM fallback extraction."""
    async def extract(self, *, text: str, source_url: str, requested_fields: tuple[str, ...]) -> dict[str, Any]: ...


class LLMAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any
    evidence_text: str = Field(min_length=1)
    evidence_locator: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class LLMEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: dict[str, LLMAssertion]


ALLOWED_LLM_FIELDS = {
    "name", "description", "start_at", "end_at", "registration_url",
    "official_url", "location_name", "organizer_name", "event_type",
    "format", "prize_text", "eligibility_text",
}


def validate_llm_fallback(
    raw: dict[str, Any],
    *,
    requested_fields: tuple[str, ...],
    observed_at: datetime,
    model_version: str,
    prompt_version: str,
) -> ExtractedEvent:
    try:
        envelope = LLMEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise ValueError("invalid LLM structured output") from exc
    requested = set(requested_fields)
    values: dict[str, Any] = {}
    evidence: list[FieldEvidence] = []
    for field_path, assertion in envelope.fields.items():
        if field_path not in ALLOWED_LLM_FIELDS or field_path not in requested:
            raise ValueError(f"LLM asserted non-requested field: {field_path}")
        values[field_path] = assertion.value
        evidence.append(
            FieldEvidence(
                field_path=field_path,
                value=assertion.value,
                evidence_text=assertion.evidence_text,
                evidence_locator=assertion.evidence_locator,
                method="LLM",
                confidence=assertion.confidence,
                method_version=f"model={model_version};prompt={prompt_version}",
                observed_at=observed_at,
            )
        )
    return ExtractedEvent(**values, evidence=evidence)
