from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class FieldEvidence(BaseModel):
    field_path: str
    value: Any
    normalized_value: Any | None = None
    evidence_text: str | None = None
    evidence_locator: str | None = None
    method: Literal["API", "JSON_LD", "EMBEDDED_JSON", "DETERMINISTIC", "LLM", "MANUAL"]
    confidence: float = Field(ge=0, le=1)
    derived: bool = False
    method_version: str
    observed_at: datetime


class ExtractedEvent(BaseModel):
    name: str | None = None
    description: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    registration_url: str | None = None
    official_url: str | None = None
    location_name: str | None = None
    organizer_name: str | None = None
    event_type: str | None = None
    format: str | None = None
    prize_text: str | None = None
    eligibility_text: str | None = None
    evidence: list[FieldEvidence] = []
