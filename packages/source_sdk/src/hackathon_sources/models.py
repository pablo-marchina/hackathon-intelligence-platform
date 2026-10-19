from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class IngestionMode(StrEnum):
    API = "API"
    RSS = "RSS"
    SITEMAP = "SITEMAP"
    STATIC_HTML = "STATIC_HTML"
    BROWSER = "BROWSER"
    SEARCH_DISCOVERY = "SEARCH_DISCOVERY"
    MANUAL = "MANUAL"
    PARTNER = "PARTNER"
    DISCOVERY_ONLY = "DISCOVERY_ONLY"
    BLOCKED = "BLOCKED"


class PermissionStatus(StrEnum):
    ALLOWED = "ALLOWED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    RESTRICTED = "RESTRICTED"
    PARTNER_REQUIRED = "PARTNER_REQUIRED"


class SourceDefinition(BaseModel):
    key: str
    name: str
    domain: str
    source_family: str
    source_type: str
    ingestion_mode: IngestionMode
    permission_status: PermissionStatus = PermissionStatus.REVIEW_REQUIRED
    discovery_enabled: bool = True
    detail_extraction_enabled: bool = False
    priority: int = Field(default=50, ge=0, le=100)
    trust_score: float | None = Field(default=None, ge=0, le=1)
    refresh_interval_seconds: int | None = Field(default=None, ge=60)
    rate_limit_per_minute: float | None = Field(default=None, gt=0)
    concurrency_limit: int = Field(default=1, ge=1, le=32)
    seed_urls: list[HttpUrl] = []
    allowed_hosts: list[str] = []
    notes: str | None = None


class DiscoveredUrl(BaseModel):
    url: HttpUrl
    source_key: str
    discovery_method: str
    parent_url: HttpUrl | None = None
    depth: int = Field(default=0, ge=0)
    priority: int = 0
    metadata: dict[str, Any] = {}


class FetchResult(BaseModel):
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    body: bytes
    fetched_at: datetime
    etag: str | None = None
    last_modified: str | None = None
    response_headers: dict[str, str] = {}


class SourceEventRecord(BaseModel):
    """Structured factual record emitted directly by a source adapter.

    Values intentionally remain close to their source representation. W2 normalization
    converts these fields into canonical dates, locations, formats and eligibility rules.
    """

    source_key: str
    source_event_id: str | None = None
    name: str
    source_url: HttpUrl
    official_url: HttpUrl | None = None
    starts_at_raw: str | None = None
    ends_at_raw: str | None = None
    format_raw: str | None = None
    city: str | None = None
    state_region: str | None = None
    country: str | None = None
    country_code: str | None = None
    eligibility_raw: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}


class RawDocumentRecord(BaseModel):
    canonical_url: str
    content_hash: str
    normalized_text_hash: str | None = None
    object_storage_key: str | None = None
    content_type: str | None = None
    http_status: int | None = None
    metadata: dict[str, Any] = {}
