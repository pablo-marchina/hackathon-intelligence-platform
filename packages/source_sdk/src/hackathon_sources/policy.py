from __future__ import annotations

from dataclasses import dataclass

from .models import IngestionMode, PermissionStatus, SourceDefinition


@dataclass(frozen=True)
class FetchPolicyDecision:
    allowed: bool
    reason: str


def may_fetch_details(source: SourceDefinition) -> FetchPolicyDecision:
    if source.ingestion_mode is IngestionMode.BLOCKED:
        return FetchPolicyDecision(False, "source ingestion mode is BLOCKED")
    if source.ingestion_mode is IngestionMode.DISCOVERY_ONLY:
        return FetchPolicyDecision(False, "source is discovery-only")
    if source.permission_status is PermissionStatus.RESTRICTED:
        return FetchPolicyDecision(False, "automation is restricted")
    if source.permission_status is PermissionStatus.PARTNER_REQUIRED:
        return FetchPolicyDecision(False, "partner/authorized access required")
    if source.permission_status is PermissionStatus.REVIEW_REQUIRED:
        return FetchPolicyDecision(False, "permission review required before automated fetch")
    if not source.detail_extraction_enabled:
        return FetchPolicyDecision(False, "detail extraction disabled")
    return FetchPolicyDecision(True, "fetch allowed by source policy")
