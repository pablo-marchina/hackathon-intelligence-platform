from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from hackathon_api.advanced_filters import compile_expression
from hackathon_api.schemas import (
    CanonicalChangeSummary,
    EligibilitySummary,
    ExternalLinkSummary,
    HackathonDetail,
    HackathonFilters,
    HackathonListItem,
    HackathonPage,
    JudgingCriterionSummary,
    LocationSummary,
    OrganizationSummary,
    PhaseSummary,
    PrizeSummary,
    RequirementSummary,
    ResourceSummary,
    SourceEvidenceSummary,
    TaxonomyItem,
)
from sqlalchemy import Connection, text


@dataclass(frozen=True)
class QuerySpec:
    sql: str
    params: dict[str, Any]


def _query_fingerprint(filters: HackathonFilters) -> str:
    p = filters.model_dump(exclude={"cursor", "limit"}, mode="json")
    p["q"] = (filters.q or "").strip().lower()
    return hashlib.sha256(
        json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def encode_cursor(row: dict[str, Any], filters: HackathonFilters | None = None) -> str:
    filters = filters or HackathonFilters()
    payload = {
        "v": 1,
        "sort": filters.sort,
        "f": _query_fingerprint(filters),
        "rank": float(row.get("rank_score") or 0),
        "quality": float(row.get("quality_sort") if row.get("quality_sort") is not None else -1),
        "start": row.get("start_sort").isoformat()
        if row.get("start_sort") and hasattr(row.get("start_sort"), "isoformat")
        else str(row.get("start_sort") or "infinity"),
        "deadline": row.get("deadline_sort").isoformat()
        if row.get("deadline_sort") and hasattr(row.get("deadline_sort"), "isoformat")
        else str(row.get("deadline_sort") or "infinity"),
        "id": str(row["id"]),
    }
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode())
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if payload.get("v") != 1 or not payload.get("id"):
        raise ValueError("unsupported cursor")
    return payload


def _registration_predicate(states: list[str], params: dict[str, Any]) -> str | None:
    parts = []
    if "OPEN" in states:
        parts.append(
            "(h.status::text='REGISTRATION_OPEN' OR ((h.registration_open_at IS NULL OR h.registration_open_at <= now()) AND h.registration_close_at >= now()))"
        )
    if "UPCOMING" in states:
        parts.append("(h.status::text='REGISTRATION_UPCOMING' OR h.registration_open_at > now())")
    if "CLOSED" in states:
        parts.append("(h.registration_close_at < now() OR h.status::text='REGISTRATION_CLOSED')")
    if "UNKNOWN" in states:
        parts.append(
            "(h.registration_open_at IS NULL AND h.registration_close_at IS NULL AND h.status::text NOT IN ('REGISTRATION_OPEN','REGISTRATION_CLOSED'))"
        )
    return f"({' OR '.join(parts)})" if parts else None


def _cursor_predicate(filters, rank, quality, start, deadline, params):
    if not filters.cursor:
        return None
    cur = decode_cursor(filters.cursor)
    if cur.get("sort") != filters.sort or cur.get("f") != _query_fingerprint(filters):
        raise ValueError("cursor does not match current filters")
    params.update(
        cursor_id=cur["id"],
        cursor_rank=float(cur.get("rank", 0)),
        cursor_quality=float(cur.get("quality", -1)),
        cursor_start=cur.get("start", "infinity"),
        cursor_deadline=cur.get("deadline", "infinity"),
    )
    if filters.sort == "relevance" and filters.q:
        return f"({rank}<:cursor_rank OR ({rank}=:cursor_rank AND {quality}<:cursor_quality) OR ({rank}=:cursor_rank AND {quality}=:cursor_quality AND {start}>CAST(:cursor_start AS timestamptz)) OR ({rank}=:cursor_rank AND {quality}=:cursor_quality AND {start}=CAST(:cursor_start AS timestamptz) AND h.id>CAST(:cursor_id AS uuid)))"
    if filters.sort in {"relevance", "quality"}:
        return f"({quality}<:cursor_quality OR ({quality}=:cursor_quality AND {start}>CAST(:cursor_start AS timestamptz)) OR ({quality}=:cursor_quality AND {start}=CAST(:cursor_start AS timestamptz) AND h.id>CAST(:cursor_id AS uuid)))"
    if filters.sort == "deadline":
        return f"({deadline}>CAST(:cursor_deadline AS timestamptz) OR ({deadline}=CAST(:cursor_deadline AS timestamptz) AND {quality}<:cursor_quality) OR ({deadline}=CAST(:cursor_deadline AS timestamptz) AND {quality}=:cursor_quality AND h.id>CAST(:cursor_id AS uuid)))"
    return f"({start}>CAST(:cursor_start AS timestamptz) OR ({start}=CAST(:cursor_start AS timestamptz) AND {quality}<:cursor_quality) OR ({start}=CAST(:cursor_start AS timestamptz) AND {quality}=:cursor_quality AND h.id>CAST(:cursor_id AS uuid)))"


def build_hackathon_query(filters: HackathonFilters, *, target_id: str | None = None) -> QuerySpec:
    params = {"limit": filters.limit + 1}
    joins = ["LEFT JOIN locations l ON l.id=h.primary_location_id"]
    where = ["h.is_active=true"]
    if target_id is not None:
        params["target_id"] = target_id
        where.append("h.id=CAST(:target_id AS uuid)")
    if filters.q:
        params["q"] = filters.q
        joins.append("JOIN hackathon_search_documents sd ON sd.hackathon_id=h.id")
        where.append("sd.document @@ websearch_to_tsquery('simple',:q)")
    if filters.status:
        params["statuses"] = filters.status
        where.append("h.status::text=ANY(:statuses)")
    if filters.event_type:
        params["event_types"] = filters.event_type
        where.append("h.event_type::text=ANY(:event_types)")
    if filters.format:
        params["formats"] = filters.format
        where.append("h.format::text=ANY(:formats)")
    if p := _registration_predicate(list(filters.registration_state), params):
        where.append(p)
    if filters.country_code:
        params["country_codes"] = filters.country_code
        params["country_code"] = filters.country_code[0] if len(filters.country_code) == 1 else None
        where.append("l.country_code=ANY(:country_codes)")
    if filters.city:
        params["city"] = filters.city
        where.append("lower(l.city)=lower(:city)")
    for attr, col in [
        ("start_from", "h.start_at >= :start_from"),
        ("start_to", "h.start_at <= :start_to"),
        ("deadline_from", "h.registration_close_at >= :deadline_from"),
        ("deadline_to", "h.registration_close_at <= :deadline_to"),
    ]:
        val = getattr(filters, attr)
        if val is not None:
            params[attr] = val
            where.append(col)
    if filters.free_only:
        where.append("h.is_free=true")
    if filters.min_prize_usd is not None:
        params["min_prize"] = filters.min_prize_usd
        where.append(
            "EXISTS (SELECT 1 FROM prize_pools pp0 WHERE pp0.hackathon_id=h.id AND pp0.normalized_usd_amount>=:min_prize)"
        )
    if filters.max_prize_usd is not None:
        params["max_prize"] = filters.max_prize_usd
        where.append(
            "EXISTS (SELECT 1 FROM prize_pools pp0 WHERE pp0.hackathon_id=h.id AND pp0.normalized_usd_amount IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM prize_pools pp1 WHERE pp1.hackathon_id=h.id AND pp1.normalized_usd_amount>:max_prize)"
        )
    for attr, sql, name in [
        (
            "track",
            "EXISTS (SELECT 1 FROM hackathon_tracks ht JOIN tracks t ON t.id=ht.track_id WHERE ht.hackathon_id=h.id AND t.slug=ANY(:tracks))",
            "tracks",
        ),
        (
            "technology",
            "EXISTS (SELECT 1 FROM hackathon_technologies hx JOIN technologies tx ON tx.id=hx.technology_id WHERE hx.hackathon_id=h.id AND tx.slug=ANY(:technologies))",
            "technologies",
        ),
        (
            "organizer",
            "EXISTS (SELECT 1 FROM hackathon_organizations ho JOIN organizations o ON o.id=ho.organization_id WHERE ho.hackathon_id=h.id AND ho.role::text IN ('ORGANIZER','CO_ORGANIZER','HOST') AND o.slug=ANY(:organizers))",
            "organizers",
        ),
        (
            "sponsor",
            "EXISTS (SELECT 1 FROM hackathon_organizations ho JOIN organizations o ON o.id=ho.organization_id WHERE ho.hackathon_id=h.id AND ho.role::text IN ('SPONSOR','PRIZE_PARTNER','TECHNOLOGY_PARTNER') AND o.slug=ANY(:sponsors))",
            "sponsors",
        ),
    ]:
        val = getattr(filters, attr)
        if val:
            params[name] = val
            where.append(sql)
    if filters.team_size is not None:
        params["team_size"] = filters.team_size
        where.append(
            "EXISTS (SELECT 1 FROM eligibility_rules er WHERE er.hackathon_id=h.id AND (er.minimum_team_size IS NULL OR er.minimum_team_size<=:team_size) AND (er.maximum_team_size IS NULL OR er.maximum_team_size>=:team_size))"
        )
    if filters.student_required is not None:
        params["student_required"] = filters.student_required
        where.append(
            "EXISTS (SELECT 1 FROM eligibility_rules er WHERE er.hackathon_id=h.id AND er.student_required=:student_required)"
        )
    if filters.individual_allowed is not None:
        params["individual_allowed"] = filters.individual_allowed
        where.append(
            "EXISTS (SELECT 1 FROM eligibility_rules er WHERE er.hackathon_id=h.id AND er.individual_allowed=:individual_allowed)"
        )
    if filters.eligible_country:
        params["eligible_country"] = filters.eligible_country
        where.append(
            "(h.eligible_worldwide=true OR EXISTS (SELECT 1 FROM eligibility_countries ec WHERE ec.hackathon_id=h.id AND ec.country_code=:eligible_country AND ec.allowed=true))"
        )
    expression = getattr(filters, "expression", None)
    if expression is not None:
        where.append(compile_expression(expression, params))
    freshness = "CASE WHEN h.last_verified_at IS NULL THEN 0.0 ELSE GREATEST(0.0,1.0-LEAST(EXTRACT(EPOCH FROM (now()-h.last_verified_at))/86400.0,30.0)/30.0) END"
    rank = (
        f"ROUND((ts_rank_cd(sd.document,websearch_to_tsquery('simple',:q))+COALESCE(h.quality_score,0)/100.0*0.10+({freshness})*0.05)::numeric,6)"
        if filters.q
        else "0.0::numeric"
    )
    quality = "COALESCE(h.quality_score,-1)::numeric"
    start = "COALESCE(h.start_at,'infinity'::timestamptz)"
    deadline = "COALESCE(h.registration_close_at,'infinity'::timestamptz)"
    sort_map = {
        "relevance": f"{rank} DESC,{quality} DESC,{start} ASC,h.id ASC"
        if filters.q
        else f"{quality} DESC,{start} ASC,h.id ASC",
        "deadline": f"{deadline} ASC,{quality} DESC,h.id ASC",
        "start": f"{start} ASC,{quality} DESC,h.id ASC",
        "quality": f"{quality} DESC,{start} ASC,h.id ASC",
    }
    if cp := _cursor_predicate(filters, rank, quality, start, deadline, params):
        where.append(cp)
    sql = f"""SELECT h.id,h.slug,h.name,h.short_description,h.event_type::text AS event_type,h.status::text AS status,h.format::text AS format,h.start_at,h.end_at,h.registration_open_at,h.registration_close_at,l.city,l.state_region,l.country_code,h.is_free,h.quality_score,h.source_count,h.last_verified_at,(SELECT MAX(pp.normalized_usd_amount) FROM prize_pools pp WHERE pp.hackathon_id=h.id) AS prize_usd,{rank} AS rank_score,{quality} AS quality_sort,NULLIF({start},'infinity'::timestamptz) AS start_sort,NULLIF({deadline},'infinity'::timestamptz) AS deadline_sort FROM hackathons h {" ".join(joins)} WHERE {" AND ".join(where)} ORDER BY {sort_map[filters.sort]} LIMIT :limit"""
    return QuerySpec(sql, params)


_LIST_COLUMNS = """h.id,h.slug,h.name,h.short_description,h.event_type::text AS event_type,h.status::text AS status,h.format::text AS format,h.start_at,h.end_at,h.registration_open_at,h.registration_close_at,l.city,l.state_region,l.country_code,h.is_free,h.quality_score,h.source_count,h.last_verified_at,(SELECT MAX(pp.normalized_usd_amount) FROM prize_pools pp WHERE pp.hackathon_id=h.id) AS prize_usd,NULL::numeric AS rank_score"""


class HackathonRepository:
    def __init__(self, connection: Connection):
        self.connection = connection

    def search(self, filters: HackathonFilters) -> HackathonPage:
        spec = build_hackathon_query(filters)
        rows = [
            dict(r) for r in self.connection.execute(text(spec.sql), spec.params).mappings().all()
        ]
        more = len(rows) > filters.limit
        visible = rows[: filters.limit]
        return HackathonPage(
            items=[HackathonListItem.model_validate(r) for r in visible],
            next_cursor=encode_cursor(visible[-1], filters) if more and visible else None,
        )

    def by_slug(self, slug):
        row = (
            self.connection.execute(
                text(
                    "SELECT h.id,h.slug,h.name,h.subtitle,h.short_description,h.full_description,h.event_type::text AS event_type,h.status::text AS status,h.format::text AS format,h.start_at,h.end_at,h.registration_open_at,h.registration_close_at,h.registration_url,h.application_url,h.submission_url,h.official_url,h.eligible_worldwide,h.is_free,h.quality_score,h.source_count,h.last_verified_at,h.edition_label,h.edition_year,l.city,l.state_region,l.country_code,l.venue,l.country,l.timezone,es.slug AS series_slug,es.name AS series_name,(SELECT MAX(pp.normalized_usd_amount) FROM prize_pools pp WHERE pp.hackathon_id=h.id) AS prize_usd,NULL::numeric AS rank_score FROM hackathons h LEFT JOIN locations l ON l.id=h.primary_location_id LEFT JOIN event_series es ON es.id=h.event_series_id WHERE h.slug=:slug AND h.is_active=true"
                ),
                {"slug": slug},
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        b = dict(row)
        hid = b["id"]
        b["location"] = LocationSummary(
            venue=b.pop("venue", None),
            city=b.get("city"),
            state_region=b.get("state_region"),
            country=b.pop("country", None),
            country_code=b.get("country_code"),
            timezone=b.pop("timezone", None),
        )
        b["organizations"] = [
            OrganizationSummary.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT o.id,o.slug,o.name,ho.role::text AS role,o.website,o.logo_url FROM hackathon_organizations ho JOIN organizations o ON o.id=ho.organization_id WHERE ho.hackathon_id=:id ORDER BY ho.role::text,o.name"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        b["tracks"] = [
            TaxonomyItem.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT t.id,t.slug,t.name,NULL::text AS category FROM hackathon_tracks ht JOIN tracks t ON t.id=ht.track_id WHERE ht.hackathon_id=:id ORDER BY t.name"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        b["technologies"] = [
            TaxonomyItem.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT t.id,t.slug,t.name,t.category FROM hackathon_technologies ht JOIN technologies t ON t.id=ht.technology_id WHERE ht.hackathon_id=:id ORDER BY t.name"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        b["prizes"] = [
            PrizeSummary.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT p.title,p.placement,p.normalized_usd_amount,p.original_amount,p.original_currency,p.non_cash_description,pp.estimated FROM prizes p LEFT JOIN prize_pools pp ON pp.id=p.prize_pool_id WHERE p.hackathon_id=:id ORDER BY p.normalized_usd_amount DESC NULLS LAST,p.title"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        er = (
            self.connection.execute(
                text(
                    "SELECT minimum_age,maximum_age,student_required,professional_allowed,high_school_allowed,undergraduate_allowed,graduate_allowed,individual_allowed,team_required,minimum_team_size,maximum_team_size,eligibility_raw_text FROM eligibility_rules WHERE hackathon_id=:id"
                ),
                {"id": hid},
            )
            .mappings()
            .first()
        )
        b["eligibility"] = (
            EligibilitySummary.model_validate(
                {**dict(er), "eligible_worldwide": b.get("eligible_worldwide")}
            )
            if er
            else None
        )
        b["phases"] = [
            PhaseSummary.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT name,phase_type::text AS phase_type,starts_at,ends_at,description FROM event_phases WHERE hackathon_id=:id ORDER BY sort_order,starts_at NULLS LAST"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        b["resources"] = [
            ResourceSummary.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT resource_type::text AS resource_type,name,description,url FROM event_resources WHERE hackathon_id=:id ORDER BY name"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        b["judging"] = [
            JudgingCriterionSummary.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT name,description,weight FROM judging_criteria WHERE hackathon_id=:id ORDER BY sort_order,name"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        b["requirements"] = [
            RequirementSummary.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT requirement_type::text AS requirement_type,name,description,required FROM requirements WHERE hackathon_id=:id ORDER BY sort_order,id"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        b["links"] = [
            ExternalLinkSummary.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT link_type,url,is_official FROM external_links WHERE hackathon_id=:id ORDER BY is_official DESC NULLS LAST,link_type"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        b["recent_changes"] = [
            CanonicalChangeSummary(
                field_path=r["field_path"],
                change_type=r["change_type"],
                old_value=r["old_value_json"],
                new_value=r["new_value_json"],
                detected_at=r["detected_at"],
            )
            for r in self.connection.execute(
                text(
                    "SELECT field_path,change_type,old_value_json,new_value_json,detected_at FROM canonical_changes WHERE hackathon_id=:id AND meaningful=true ORDER BY detected_at DESC LIMIT 10"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        b["evidence"] = [
            SourceEvidenceSummary.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT s.name AS source_name,so.field_path,so.observed_at,so.extraction_confidence AS confidence,so.evidence_text FROM canonical_field_selections cfs JOIN source_observations so ON so.id=cfs.selected_observation_id LEFT JOIN sources s ON s.id=so.source_id WHERE cfs.hackathon_id=:id AND cfs.active=true ORDER BY cfs.field_path LIMIT 40"
                ),
                {"id": hid},
            )
            .mappings()
            .all()
        ]
        return HackathonDetail.model_validate(b)

    def active_slug_for_retired(self, slug):
        r = (
            self.connection.execute(
                text(
                    "SELECT active.slug FROM hackathons retired JOIN hackathons active ON active.id=retired.merged_into_id WHERE retired.slug=:slug AND retired.is_active=false AND active.is_active=true LIMIT 1"
                ),
                {"slug": slug},
            )
            .mappings()
            .first()
        )
        return str(r["slug"]) if r else None
