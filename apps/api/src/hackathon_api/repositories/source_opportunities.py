"""Source-level marginal coverage and metadata gaps, read-only and admin-only.

Every cohort is DISTINCT (source_id, canonical_hackathon_id). Two raw source
references from one source to the same hackathon are not two opportunities.
Shared canonical records are not added to compute global unique coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text

FIELDS = (
    "missing_official_url",
    "missing_start_or_end",
    "unknown_format",
    "missing_registration_deadline",
    "missing_registration_url",
    "unknown_cost",
    "unknown_worldwide_eligibility",
    "missing_description",
    "missing_organizer",
    "missing_prize_details",
)


def _count(value: Any) -> int:
    return int(value or 0)


class SourceOpportunityRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def snapshot(
        self, *, as_of: datetime | None = None, days: int = 30, limit: int = 100
    ) -> dict[str, Any]:
        if not 1 <= days <= 90:
            raise ValueError("days must be in 1..90")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be in 1..100")
        now = as_of or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("as_of must include timezone")
        now = now.astimezone(UTC)
        since = now - timedelta(days=days)

        # DISTINCT removes multiple event references for the same source and
        # canonical ID. Never include inactive/merged hackathons in opportunity
        # totals, nor count active refs with no attached canonical as events.
        rows = (
            self.connection.execute(
                text("""
                    WITH source_events AS (
                        SELECT DISTINCT r.source_id, r.hackathon_id
                          FROM source_event_refs r
                          JOIN hackathons h ON h.id=r.hackathon_id
                         WHERE r.active AND r.source_id IS NOT NULL
                           AND h.is_active AND h.merged_into_id IS NULL
                    ), providers AS (
                        SELECT hackathon_id, COUNT(*) AS provider_count
                          FROM source_events GROUP BY hackathon_id
                    ), gap_summary AS (
                        SELECT se.source_id,
                               COUNT(*) AS attributed_unique_active,
                               COUNT(*) FILTER (WHERE p.provider_count=1) AS exclusive_canonicals,
                               COUNT(*) FILTER (WHERE p.provider_count>1) AS shared_canonicals,
                               COUNT(*) FILTER (WHERE h.official_url IS NULL)
                                   AS missing_official_url,
                               COUNT(*) FILTER (WHERE h.start_at IS NULL OR h.end_at IS NULL)
                                   AS missing_start_or_end,
                               COUNT(*) FILTER (WHERE h.format='UNKNOWN') AS unknown_format,
                               COUNT(*) FILTER (WHERE h.registration_close_at IS NULL)
                                   AS missing_registration_deadline,
                               COUNT(*) FILTER (WHERE h.registration_url IS NULL)
                                   AS missing_registration_url,
                               COUNT(*) FILTER (WHERE h.is_free IS NULL
                                   AND h.participation_fee IS NULL) AS unknown_cost,
                               COUNT(*) FILTER (WHERE h.eligible_worldwide IS NULL)
                                   AS unknown_worldwide_eligibility,
                               COUNT(*) FILTER (WHERE h.short_description IS NULL
                                   AND h.full_description IS NULL) AS missing_description,
                               COUNT(*) FILTER (WHERE NOT EXISTS (
                                   SELECT 1 FROM hackathon_organizations ho
                                    WHERE ho.hackathon_id=h.id
                                      AND ho.role IN ('ORGANIZER','CO_ORGANIZER','HOST')
                               )) AS missing_organizer,
                               COUNT(*) FILTER (WHERE NOT EXISTS (
                                   SELECT 1 FROM prize_pools pp
                                    WHERE pp.hackathon_id=h.id
                               ) AND NOT EXISTS (
                                   SELECT 1 FROM prizes pr
                                    WHERE pr.hackathon_id=h.id
                               ) AND NOT EXISTS (
                                   SELECT 1 FROM canonical_field_selections cfs
                                   JOIN source_observations obs
                                     ON obs.id=cfs.selected_observation_id
                                   WHERE cfs.hackathon_id=h.id
                                     AND cfs.field_path='prize_details_text'
                                     AND cfs.active
                                     AND jsonb_typeof(obs.normalized_value_json)='string'
                                     AND trim(obs.normalized_value_json #>> '{}') <> ''
                               )) AS missing_prize_details
                          FROM source_events se
                          JOIN providers p ON p.hackathon_id=se.hackathon_id
                          JOIN hackathons h ON h.id=se.hackathon_id
                         GROUP BY se.source_id
                    ), recent_outcomes AS (
                        SELECT source_id,
                               COUNT(*) FILTER (WHERE outcome='CREATED') AS recent_created,
                               COUNT(*) FILTER (WHERE outcome='LINKED') AS recent_linked,
                               COUNT(*) FILTER (WHERE outcome='UPDATED') AS recent_updated,
                               COUNT(*) FILTER (WHERE outcome='REJECTED') AS recent_rejected
                          FROM canonical_processing_outcomes
                         WHERE processed_at >= :since AND processed_at <= :as_of
                         GROUP BY source_id
                    )
                    SELECT s.id::text AS source_id, s.name,
                           s.permission_status, s.active, s.discovery_enabled,
                           s.detail_extraction_enabled, s.ingestion_mode::text AS ingestion_mode,
                           s.priority,
                           COALESCE(g.attributed_unique_active,0) AS attributed_unique_active,
                           COALESCE(g.exclusive_canonicals,0) AS exclusive_canonicals,
                           COALESCE(g.shared_canonicals,0) AS shared_canonicals,
                           COALESCE(g.missing_official_url,0) AS missing_official_url,
                           COALESCE(g.missing_start_or_end,0) AS missing_start_or_end,
                           COALESCE(g.unknown_format,0) AS unknown_format,
                           COALESCE(g.missing_registration_deadline,0)
                               AS missing_registration_deadline,
                           COALESCE(g.missing_registration_url,0) AS missing_registration_url,
                           COALESCE(g.unknown_cost,0) AS unknown_cost,
                           COALESCE(g.unknown_worldwide_eligibility,0)
                               AS unknown_worldwide_eligibility,
                           COALESCE(g.missing_description,0) AS missing_description,
                           COALESCE(g.missing_organizer,0) AS missing_organizer,
                           COALESCE(g.missing_prize_details,0) AS missing_prize_details,
                           COALESCE(o.recent_created,0) AS recent_created,
                           COALESCE(o.recent_linked,0) AS recent_linked,
                           COALESCE(o.recent_updated,0) AS recent_updated,
                           COALESCE(o.recent_rejected,0) AS recent_rejected
                      FROM sources s
                      LEFT JOIN gap_summary g ON g.source_id=s.id
                      LEFT JOIN recent_outcomes o ON o.source_id=s.id
                     ORDER BY exclusive_canonicals DESC, recent_created DESC, s.name
                     LIMIT :limit
                """),
                {"as_of": now, "since": since, "limit": limit},
            )
            .mappings()
            .all()
        )
        totals = (
            self.connection.execute(
                text("""
                    WITH source_events AS (
                        SELECT DISTINCT r.source_id, r.hackathon_id
                          FROM source_event_refs r
                          JOIN hackathons h ON h.id=r.hackathon_id
                         WHERE r.active AND r.source_id IS NOT NULL
                           AND h.is_active AND h.merged_into_id IS NULL
                    ), provider_count AS (
                        SELECT hackathon_id, COUNT(*) AS sources_count
                          FROM source_events GROUP BY hackathon_id
                    )
                    SELECT (SELECT COUNT(*) FROM hackathons h
                             WHERE h.is_active AND h.merged_into_id IS NULL)
                               AS total_active_canonicals,
                           COUNT(*) AS canonicals_with_active_source,
                           COUNT(*) FILTER (WHERE sources_count=1) AS exclusive_to_one_source,
                           COUNT(*) FILTER (WHERE sources_count>1) AS shared_across_sources
                      FROM provider_count
                """)
            )
            .mappings()
            .one()
        )
        sources: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            for key in (
                "priority",
                "attributed_unique_active",
                "exclusive_canonicals",
                "shared_canonicals",
                "recent_created",
                "recent_linked",
                "recent_updated",
                "recent_rejected",
                *FIELDS,
            ):
                data[key] = _count(data.get(key))
            cohort = data["attributed_unique_active"]
            data["exclusive_percent"] = (
                round(100 * data["exclusive_canonicals"] / cohort, 1) if cohort else None
            )
            data["known_registration_deadline_percent"] = (
                round(100 * (cohort - data["missing_registration_deadline"]) / cohort, 1)
                if cohort
                else None
            )
            data["known_cost_percent"] = (
                round(100 * (cohort - data["unknown_cost"]) / cohort, 1) if cohort else None
            )
            # Triaging does not authorize or execute crawling.
            eligible = bool(
                data["active"]
                and data["permission_status"] == "ALLOWED"
                and data["discovery_enabled"]
                and data["detail_extraction_enabled"]
                and data["ingestion_mode"] not in ("BLOCKED", "DISCOVERY_ONLY", "MANUAL")
            )
            data["eligible_for_controlled_collection"] = eligible
            if not eligible:
                data["suggested_next_step"] = "REVIEW_PERMISSIONS_OR_PAUSE"
            elif not cohort:
                data["suggested_next_step"] = "VALIDATE_WITH_CONTROLLED_COLLECTION"
            elif data["missing_registration_deadline"] or data["unknown_cost"]:
                data["suggested_next_step"] = "ENRICH_CANONICAL_FIELDS"
            elif data["exclusive_canonicals"]:
                data["suggested_next_step"] = "MEASURE_ADDITIONAL_UNIQUE_YIELD"
            else:
                data["suggested_next_step"] = "INVESTIGATE_SOURCE_OVERLAP"
            sources.append(data)
        global_counts = {
            key: _count(totals[key])
            for key in (
                "total_active_canonicals",
                "canonicals_with_active_source",
                "exclusive_to_one_source",
                "shared_across_sources",
            )
        }
        global_counts["without_active_source"] = (
            global_counts["total_active_canonicals"]
            - global_counts["canonicals_with_active_source"]
        )
        return {
            "as_of": now.isoformat(),
            "recent_window_days": days,
            "limit": limit,
            "global": global_counts,
            "sources": sources,
            "methodology": {
                "exclusive_canonicals": (
                    "Active non-merged canonical events linked to exactly one distinct "
                    "source through active references; not incremental yield or exclusivity "
                    "against the whole internet."
                ),
                "shared_canonicals": (
                    "Active canonical IDs with active references from 2+ distinct sources. "
                    "Each appears in multiple source rows but only once globally."
                ),
                "gaps": (
                    "Missing values in CURRENT canonical events attributed to a source; "
                    "not proof that the source failed to publish the data. UNKNOWN != false."
                ),
                "recent_outcomes": (
                    "Processing outcomes over the rolling UTC window; CREATED includes "
                    "historical imports and may later be merged. Not live-source coverage."
                ),
                "eligibility": (
                    "Diagnostic only; each actual crawl independently enforces registered "
                    "source and DB permissions, leases, rate limits, and operator pause."
                ),
                "global": (
                    "Distinct active canonical IDs across the entire DB; the per-source "
                    "rows are overlapping attribution cohorts and MUST NOT be summed."
                ),
            },
        }
