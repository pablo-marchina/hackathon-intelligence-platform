from __future__ import annotations

import base64
import json

from hackathon_api.schemas import (
    HackathonListItem,
    UserHackathonStateRecord,
    UserLibraryPage,
    UserProfileUpdate,
)
from sqlalchemy import Connection, text

_LIST_COLUMNS = """h.id,h.slug,h.name,h.short_description,h.event_type::text AS event_type,h.status::text AS status,h.format::text AS format,h.start_at,h.end_at,h.registration_open_at,h.registration_close_at,l.city,l.state_region,l.country_code,h.is_free,h.quality_score,h.source_count,h.last_verified_at,(SELECT MAX(pp.normalized_usd_amount) FROM prize_pools pp WHERE pp.hackathon_id=h.id) AS prize_usd,NULL::numeric AS rank_score"""


def _encode_library_cursor(u, h):
    return (
        base64.urlsafe_b64encode(
            json.dumps({"u": str(u), "h": str(h)}, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def _decode_library_cursor(c):
    try:
        return json.loads(base64.urlsafe_b64decode(c + "=" * (-len(c) % 4)).decode())
    except Exception as exc:
        raise ValueError("invalid library cursor") from exc


class UserRepository:
    def __init__(self, connection: Connection):
        self.connection = connection

    def profile(self, user_id):
        return dict(
            self.connection.execute(
                text(
                    "SELECT u.id,u.email::text AS email,u.display_name,u.timezone,u.country_code,u.locale,p.preferred_formats::text[] AS preferred_formats,p.preferred_country_codes,p.travel_country_codes,p.student_status,p.experience_level,p.team_preference,p.minimum_prize_usd,p.available_from,p.available_until FROM users u LEFT JOIN user_preferences p ON p.user_id=u.id WHERE u.id=:id AND u.deleted_at IS NULL"
                ),
                {"id": user_id},
            )
            .mappings()
            .one()
        )

    def update_profile(self, user_id, update: UserProfileUpdate):
        self.connection.execute(
            text(
                "UPDATE users SET display_name=:display_name,timezone=:timezone,country_code=:country_code,locale=:locale,updated_at=now() WHERE id=:id AND deleted_at IS NULL"
            ),
            {
                "id": user_id,
                "display_name": update.display_name,
                "timezone": update.timezone,
                "country_code": update.country_code.upper() if update.country_code else None,
                "locale": update.locale,
            },
        )
        return self.profile(user_id)

    def set_event_state(self, user_id, slug, state):
        r = (
            self.connection.execute(
                text(
                    "INSERT INTO user_hackathon_states(user_id,hackathon_id,state) SELECT :u,h.id,CAST(:s AS user_event_state) FROM hackathons h WHERE h.slug=:slug AND h.is_active=true ON CONFLICT(user_id,hackathon_id) DO UPDATE SET state=EXCLUDED.state,updated_at=now() RETURNING hackathon_id,state::text AS state,updated_at"
                ),
                {"u": user_id, "s": state, "slug": slug},
            )
            .mappings()
            .first()
        )
        return dict(r) if r else None

    def delete_event_state(self, user_id, slug):
        return bool(
            self.connection.execute(
                text(
                    "DELETE FROM user_hackathon_states s USING hackathons h WHERE s.user_id=:u AND s.hackathon_id=h.id AND h.slug=:slug"
                ),
                {"u": user_id, "slug": slug},
            ).rowcount
        )

    def library(self, user_id, states, limit, cursor):
        p = {"u": user_id, "limit": limit + 1}
        w = ["s.user_id=:u", "h.is_active=true"]
        if states:
            p["states"] = states
            w.append("s.state::text=ANY(:states)")
        if cursor:
            c = _decode_library_cursor(cursor)
            p.update(cu=c["u"], ch=c["h"])
            w.append(
                "(s.updated_at<CAST(:cu AS timestamptz) OR (s.updated_at=CAST(:cu AS timestamptz) AND h.id>CAST(:ch AS uuid)))"
            )
        rows = [
            dict(r)
            for r in self.connection.execute(
                text(
                    f"SELECT {_LIST_COLUMNS},s.state::text AS user_state,s.updated_at AS state_updated_at FROM user_hackathon_states s JOIN hackathons h ON h.id=s.hackathon_id LEFT JOIN locations l ON l.id=h.primary_location_id WHERE {' AND '.join(w)} ORDER BY s.updated_at DESC,h.id ASC LIMIT :limit"
                ),
                p,
            )
            .mappings()
            .all()
        ]
        more = len(rows) > limit
        recs = []
        for row in rows[:limit]:
            state = row.pop("user_state")
            updated = row.pop("state_updated_at")
            recs.append(
                UserHackathonStateRecord(
                    hackathon=HackathonListItem.model_validate(row), state=state, updated_at=updated
                )
            )
        return UserLibraryPage(
            items=recs,
            next_cursor=_encode_library_cursor(recs[-1].updated_at, recs[-1].hackathon.id)
            if more and recs
            else None,
        )
