from __future__ import annotations

from hackathon_api.schemas import (
    EventSeriesDetail,
    HackathonListItem,
    OrganizationDetail,
    SourceListItem,
    TaxonomyItem,
)
from sqlalchemy import Connection, text

_LIST_COLUMNS = """h.id,h.slug,h.name,h.short_description,h.event_type::text AS event_type,h.status::text AS status,h.format::text AS format,h.start_at,h.end_at,h.registration_open_at,h.registration_close_at,l.city,l.state_region,l.country_code,h.is_free,h.quality_score,h.source_count,h.last_verified_at,(SELECT MAX(pp.normalized_usd_amount) FROM prize_pools pp WHERE pp.hackathon_id=h.id) AS prize_usd,NULL::numeric AS rank_score"""


class CatalogRepository:
    def __init__(self, connection: Connection):
        self.connection = connection

    def organizations(self, limit=100, cursor=None):
        params = {"limit": limit + 1}
        where = ""
        if cursor:
            params["cursor"] = cursor
            where = "WHERE o.slug>:cursor"
        rows = [
            dict(r)
            for r in self.connection.execute(
                text(
                    f"SELECT o.id,o.slug,o.name,o.organization_type,o.website,o.logo_url,o.country_code,o.industry,COUNT(DISTINCT ho.hackathon_id) FILTER (WHERE h.is_active=true) AS event_count FROM organizations o LEFT JOIN hackathon_organizations ho ON ho.organization_id=o.id LEFT JOIN hackathons h ON h.id=ho.hackathon_id {where} GROUP BY o.id ORDER BY o.slug ASC LIMIT :limit"
                ),
                params,
            )
            .mappings()
            .all()
        ]
        more = len(rows) > limit
        visible = rows[:limit]
        return visible, (str(visible[-1]["slug"]) if more and visible else None)

    def organization(self, slug):
        org = (
            self.connection.execute(
                text(
                    "SELECT id,slug,name,organization_type,website,logo_url,description,country_code,industry FROM organizations WHERE slug=:slug"
                ),
                {"slug": slug},
            )
            .mappings()
            .first()
        )
        if not org:
            return None
        oid = org["id"]
        roles = [
            str(r["role"])
            for r in self.connection.execute(
                text(
                    "SELECT DISTINCT role::text AS role FROM hackathon_organizations WHERE organization_id=:id ORDER BY role::text"
                ),
                {"id": oid},
            )
            .mappings()
            .all()
        ]
        events = [
            dict(r)
            for r in self.connection.execute(
                text(
                    f"SELECT {_LIST_COLUMNS} FROM hackathons h JOIN hackathon_organizations ho ON ho.hackathon_id=h.id LEFT JOIN locations l ON l.id=h.primary_location_id WHERE ho.organization_id=:id AND h.is_active=true ORDER BY h.start_at DESC NULLS LAST,h.id"
                ),
                {"id": oid},
            )
            .mappings()
            .all()
        ]
        up = []
        past = []
        for r in events:
            i = HackathonListItem.model_validate(r)
            (past if i.status in {"COMPLETED", "CANCELLED"} else up).append(i)
        d = dict(org)
        d.update(roles=roles, upcoming=up, past=past)
        return OrganizationDetail.model_validate(d)

    def series(self, slug):
        s = (
            self.connection.execute(
                text("SELECT id,slug,name,official_url FROM event_series WHERE slug=:slug"),
                {"slug": slug},
            )
            .mappings()
            .first()
        )
        if not s:
            return None
        ed = [
            HackathonListItem.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    f"SELECT {_LIST_COLUMNS} FROM hackathons h LEFT JOIN locations l ON l.id=h.primary_location_id WHERE h.event_series_id=:id AND h.is_active=true ORDER BY h.start_at DESC NULLS LAST,h.edition_year DESC NULLS LAST,h.id"
                ),
                {"id": s["id"]},
            )
            .mappings()
            .all()
        ]
        d = dict(s)
        d["editions"] = ed
        return EventSeriesDetail.model_validate(d)

    def list_series(self, limit=100):
        return [
            dict(r)
            for r in self.connection.execute(
                text(
                    "SELECT es.id,es.slug,es.name,es.official_url,COUNT(h.id) FILTER (WHERE h.is_active=true) AS edition_count FROM event_series es LEFT JOIN hackathons h ON h.event_series_id=es.id GROUP BY es.id ORDER BY es.name LIMIT :limit"
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        ]

    def taxonomy(self, kind):
        sql = (
            "SELECT id,slug,name,NULL::text AS category FROM tracks ORDER BY name"
            if kind == "tracks"
            else "SELECT id,slug,name,category FROM technologies ORDER BY name"
        )
        return [
            TaxonomyItem.model_validate(dict(r))
            for r in self.connection.execute(text(sql)).mappings().all()
        ]

    def sources(self, limit=200):
        return [
            SourceListItem.model_validate(dict(r))
            for r in self.connection.execute(
                text(
                    "SELECT id,name,domain::text AS domain,source_family,source_type,ingestion_mode::text AS ingestion_mode,health::text AS health,trust_score,last_success_at FROM sources WHERE active=true ORDER BY priority DESC,name LIMIT :limit"
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        ]
