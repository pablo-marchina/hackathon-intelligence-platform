from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from hackathon_alerts.repository import AlertRepository, saved_search_fingerprint
from hackathon_api.schemas import PreferenceUpdate, SavedSearchCreate, SavedSearchUpdate
from hackathon_recommendation.preferences import PreferenceProfile
from sqlalchemy import Connection, text


class PersonalizationRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def preference(self, user_id: UUID) -> dict[str, Any]:
        row = (
            self.connection.execute(
                text(
                    """
                SELECT p.user_id,p.version AS preference_version,p.snapshot_hash AS preference_hash,
                       p.updated_at,
                       jsonb_build_object(
                         'preferred_formats',p.preferred_formats,
                         'preferred_country_codes',p.preferred_country_codes,
                         'travel_country_codes',p.travel_country_codes,
                         'student_status',p.student_status,
                         'experience_level',p.experience_level,
                         'team_preference',p.team_preference,
                         'minimum_prize_usd',p.minimum_prize_usd,
                         'available_from',p.available_from,
                         'available_until',p.available_until,
                         'hard_constraints',p.hard_constraints,
                         'soft_preferences',p.soft_preferences,
                         'preferred_track_slugs',COALESCE((SELECT jsonb_agg(t.slug ORDER BY t.slug) FROM user_preferred_tracks upt JOIN tracks t ON t.id=upt.track_id WHERE upt.user_id=p.user_id),'[]'::jsonb),
                         'preferred_technology_slugs',COALESCE((SELECT jsonb_agg(t.slug ORDER BY t.slug) FROM user_preferred_technologies upt JOIN technologies t ON t.id=upt.technology_id WHERE upt.user_id=p.user_id),'[]'::jsonb)
                       ) AS snapshot
                FROM user_preferences p WHERE p.user_id=:user_id
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .first()
        )
        if row:
            result = dict(row)
            result["schema_version"] = "w5-preferences-v1"
            return result
        return {
            "user_id": user_id,
            "preference_version": 0,
            "preference_hash": hashlib.sha256(b"{}").hexdigest(),
            "schema_version": "w5-preferences-v1",
            "snapshot": {},
            "updated_at": datetime.now(UTC),
        }

    def update_preference(self, user_id: UUID, payload: PreferenceUpdate) -> dict[str, Any]:
        current = self.preference(user_id)["snapshot"]
        patch = payload.model_dump(exclude_unset=True)
        next_snapshot = {**current, **patch}
        profile = PreferenceProfile.from_mapping(next_snapshot)
        fingerprint = profile.fingerprint()
        version = int(
            self.connection.execute(
                text(
                    "SELECT COALESCE(MAX(version),0)+1 FROM user_preference_versions WHERE user_id=:user_id"
                ),
                {"user_id": user_id},
            ).scalar_one()
        )

        def j(value: Any) -> str:
            return json.dumps(value if value is not None else {}, default=str, sort_keys=True)

        self.connection.execute(
            text(
                """
                INSERT INTO user_preferences(
                    user_id,preferred_formats,preferred_country_codes,travel_country_codes,
                    student_status,experience_level,team_preference,minimum_prize_usd,
                    available_from,available_until,hard_constraints,soft_preferences,
                    version,snapshot_hash,updated_at
                ) VALUES (
                    :user_id,CAST(:preferred_formats AS event_format[]),:preferred_country_codes,:travel_country_codes,
                    :student_status,:experience_level,:team_preference,:minimum_prize_usd,
                    :available_from,:available_until,CAST(:hard_constraints AS jsonb),CAST(:soft_preferences AS jsonb),
                    :version,:snapshot_hash,now()
                )
                ON CONFLICT(user_id) DO UPDATE SET
                    preferred_formats=EXCLUDED.preferred_formats,
                    preferred_country_codes=EXCLUDED.preferred_country_codes,
                    travel_country_codes=EXCLUDED.travel_country_codes,
                    student_status=EXCLUDED.student_status,
                    experience_level=EXCLUDED.experience_level,
                    team_preference=EXCLUDED.team_preference,
                    minimum_prize_usd=EXCLUDED.minimum_prize_usd,
                    available_from=EXCLUDED.available_from,
                    available_until=EXCLUDED.available_until,
                    hard_constraints=EXCLUDED.hard_constraints,
                    soft_preferences=EXCLUDED.soft_preferences,
                    version=EXCLUDED.version,snapshot_hash=EXCLUDED.snapshot_hash,updated_at=now()
                """
            ),
            {
                "user_id": user_id,
                "preferred_formats": next_snapshot.get("preferred_formats"),
                "preferred_country_codes": next_snapshot.get("preferred_country_codes"),
                "travel_country_codes": next_snapshot.get("travel_country_codes"),
                "student_status": next_snapshot.get("student_status"),
                "experience_level": next_snapshot.get("experience_level"),
                "team_preference": next_snapshot.get("team_preference"),
                "minimum_prize_usd": next_snapshot.get("minimum_prize_usd"),
                "available_from": next_snapshot.get("available_from"),
                "available_until": next_snapshot.get("available_until"),
                "hard_constraints": j(next_snapshot.get("hard_constraints", {})),
                "soft_preferences": j(next_snapshot.get("soft_preferences", {})),
                "version": version,
                "snapshot_hash": fingerprint,
            },
        )
        self.connection.execute(
            text(
                """
                INSERT INTO user_preference_versions(user_id,version,snapshot_hash,snapshot)
                VALUES(:user_id,:version,:snapshot_hash,CAST(:snapshot AS jsonb))
                """
            ),
            {
                "user_id": user_id,
                "version": version,
                "snapshot_hash": fingerprint,
                "snapshot": j(profile.canonical_dict()),
            },
        )
        if "preferred_track_slugs" in patch:
            self.connection.execute(
                text("DELETE FROM user_preferred_tracks WHERE user_id=:user_id"),
                {"user_id": user_id},
            )
            self.connection.execute(
                text(
                    "INSERT INTO user_preferred_tracks(user_id,track_id) SELECT :user_id,id FROM tracks WHERE slug=ANY(:slugs) ON CONFLICT DO NOTHING"
                ),
                {"user_id": user_id, "slugs": patch.get("preferred_track_slugs") or []},
            )
        if "preferred_technology_slugs" in patch:
            self.connection.execute(
                text("DELETE FROM user_preferred_technologies WHERE user_id=:user_id"),
                {"user_id": user_id},
            )
            self.connection.execute(
                text(
                    "INSERT INTO user_preferred_technologies(user_id,technology_id) SELECT :user_id,id FROM technologies WHERE slug=ANY(:slugs) ON CONFLICT DO NOTHING"
                ),
                {"user_id": user_id, "slugs": patch.get("preferred_technology_slugs") or []},
            )
        return self.preference(user_id)

    def saved_searches(self, user_id: UUID) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.connection.execute(
                text(
                    """
                SELECT s.id,s.name,s.original_query,s.filter_schema_version,s.filters,s.definition_hash,
                       s.notifications_enabled,s.created_at,s.updated_at,
                       COUNT(*) OVER(PARTITION BY s.user_id,s.definition_hash)::int AS duplicate_count
                FROM saved_searches s WHERE s.user_id=:user_id ORDER BY s.updated_at DESC,s.id
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        ]

    def create_saved_search(self, user_id: UUID, payload: SavedSearchCreate) -> dict[str, Any]:
        definition_hash = saved_search_fingerprint(payload.filters, payload.filter_schema_version)
        row = self.connection.execute(
            text(
                """
                INSERT INTO saved_searches(user_id,name,original_query,filter_schema_version,filters,notifications_enabled,definition_hash)
                VALUES(:user_id,:name,:original_query,:schema_version,CAST(:filters AS jsonb),:notifications_enabled,:definition_hash)
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "name": payload.name,
                "original_query": payload.original_query,
                "schema_version": payload.filter_schema_version,
                "filters": json.dumps(payload.filters, sort_keys=True),
                "notifications_enabled": payload.notifications_enabled,
                "definition_hash": definition_hash,
            },
        ).scalar_one()
        created = self.saved_search_by_id(user_id, row)
        assert created is not None
        return created

    def saved_search_by_id(self, user_id: UUID, search_id: UUID) -> dict[str, Any] | None:
        rows = (
            self.connection.execute(
                text(
                    """
                SELECT s.id,s.name,s.original_query,s.filter_schema_version,s.filters,s.definition_hash,
                       s.notifications_enabled,s.created_at,s.updated_at,
                       (SELECT COUNT(*) FROM saved_searches x WHERE x.user_id=s.user_id AND x.definition_hash=s.definition_hash)::int AS duplicate_count
                FROM saved_searches s WHERE s.user_id=:user_id AND s.id=:id
                """
                ),
                {"user_id": user_id, "id": search_id},
            )
            .mappings()
            .first()
        )
        return dict(rows) if rows else None

    def update_saved_search(
        self, user_id: UUID, search_id: UUID, payload: SavedSearchUpdate
    ) -> dict[str, Any] | None:
        values = payload.model_dump(exclude_unset=True)
        if not values:
            return self.saved_search_by_id(user_id, search_id)
        sets = []
        params = {"user_id": user_id, "id": search_id}
        for key, value in values.items():
            sets.append(f"{key}=:{key}")
            params[key] = value
        self.connection.execute(
            text(
                f"UPDATE saved_searches SET {','.join(sets)},updated_at=now() WHERE user_id=:user_id AND id=:id"
            ),
            params,
        )
        return self.saved_search_by_id(user_id, search_id)

    def delete_saved_search(self, user_id: UUID, search_id: UUID) -> bool:
        return bool(
            self.connection.execute(
                text("DELETE FROM saved_searches WHERE user_id=:user_id AND id=:id"),
                {"user_id": user_id, "id": search_id},
            ).rowcount
        )

    def alerts(self, user_id: UUID, limit: int = 50) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.connection.execute(
                text("""
            SELECT a.id,a.hackathon_id,h.slug AS hackathon_slug,h.name AS hackathon_name,a.alert_type,a.payload,
                   COALESCE(a.payload->'source_evidence','[]'::jsonb) AS source_evidence,a.created_at,a.read_at
            FROM alert_events a LEFT JOIN hackathons h ON h.id=a.hackathon_id
            WHERE a.user_id=:user_id ORDER BY a.created_at DESC LIMIT :limit
        """),
                {"user_id": user_id, "limit": limit},
            )
            .mappings()
            .all()
        ]

    def mark_alert_read(self, user_id: UUID, alert_id: UUID) -> bool:
        return bool(
            self.connection.execute(
                text(
                    "UPDATE alert_events SET read_at=COALESCE(read_at,now()) WHERE user_id=:user_id AND id=:id"
                ),
                {"user_id": user_id, "id": alert_id},
            ).rowcount
        )

    def set_alert_preference(
        self, user_id: UUID, channel: str, alert_type: str, enabled: bool
    ) -> None:
        # Serialize preference revocation with an in-flight SMTP consent check.
        # Both operations lock the same users row before reading/writing consent.
        self.connection.execute(
            text("SELECT id FROM users WHERE id=:user_id FOR UPDATE"),
            {"user_id": user_id},
        ).scalar_one()
        self.connection.execute(
            text("""
            INSERT INTO alert_channel_preferences(user_id,channel,alert_type,enabled)
            VALUES(:user_id,CAST(:channel AS alert_channel),:alert_type,:enabled)
            ON CONFLICT(user_id,channel,alert_type) DO UPDATE SET enabled=EXCLUDED.enabled,updated_at=now()
        """),
            {"user_id": user_id, "channel": channel, "alert_type": alert_type, "enabled": enabled},
        )

    def new_match_notification_preferences(self, user_id: UUID) -> dict[str, object]:
        """Return effective channel states, not potentially misleading defaults.

        IN_APP uses the normal repository default. EMAIL is opt-in: both an
        address and an affirmative preference are needed; an explicit denial
        of new_match overrides a wildcard grant.
        """
        email_consent = AlertRepository(self.connection).channel_enabled(
            user_id=str(user_id),
            channel="EMAIL",
            alert_type="new_match",
        )
        return {
            "alert_type": "new_match",
            "in_app_enabled": AlertRepository(self.connection).channel_enabled(
                user_id=str(user_id), channel="IN_APP", alert_type="new_match"
            ),
            "email_enabled": email_consent,
        }
