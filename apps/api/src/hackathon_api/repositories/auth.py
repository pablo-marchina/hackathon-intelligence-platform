from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hackathon_api.schemas import AuthUser
from sqlalchemy import Connection, text


class AuthRepository:
    def __init__(self, connection: Connection):
        self.connection = connection

    def upsert_identity(self, *, provider, provider_subject, verified_email, display_name):
        r = (
            self.connection.execute(
                text(
                    "SELECT u.id,u.email::text AS email,u.display_name,u.timezone,u.country_code,u.locale FROM user_identities i JOIN users u ON u.id=i.user_id WHERE i.provider::text=:p AND i.provider_subject=:s AND u.deleted_at IS NULL"
                ),
                {"p": provider, "s": provider_subject},
            )
            .mappings()
            .first()
        )
        if r:
            return AuthUser.model_validate(dict(r))
        u = None
        if verified_email:
            u = (
                self.connection.execute(
                    text(
                        "SELECT id,email::text AS email,display_name,timezone,country_code,locale FROM users WHERE email=:e AND deleted_at IS NULL"
                    ),
                    {"e": verified_email},
                )
                .mappings()
                .first()
            )
        if not u:
            if verified_email:
                u = (
                    self.connection.execute(
                        text(
                            "INSERT INTO users(email,display_name) VALUES(:e,:d) ON CONFLICT(email) DO UPDATE SET display_name=COALESCE(users.display_name,EXCLUDED.display_name),updated_at=now() RETURNING id,email::text AS email,display_name,timezone,country_code,locale"
                        ),
                        {"e": verified_email, "d": display_name},
                    )
                    .mappings()
                    .one()
                )
            else:
                u = (
                    self.connection.execute(
                        text(
                            "INSERT INTO users(email,display_name) VALUES(NULL,:d) RETURNING id,email::text AS email,display_name,timezone,country_code,locale"
                        ),
                        {"d": display_name},
                    )
                    .mappings()
                    .one()
                )
        self.connection.execute(
            text(
                "INSERT INTO user_identities(user_id,provider,provider_subject,provider_email) VALUES(:u,CAST(:p AS auth_provider),:s,:e) ON CONFLICT(provider,provider_subject) DO NOTHING"
            ),
            {"u": u["id"], "p": provider, "s": provider_subject, "e": verified_email},
        )
        return AuthUser.model_validate(dict(u))

    def create_session(
        self, *, user_id, provider, token_hash, csrf_hash, ttl_seconds, user_agent_hash, ip_hash
    ):
        self.connection.execute(
            text(
                "INSERT INTO auth_sessions(user_id,provider,token_hash,csrf_hash,expires_at,user_agent_hash,ip_hash) VALUES(:u,CAST(:p AS auth_provider),:t,:c,:x,:a,:i)"
            ),
            {
                "u": user_id,
                "p": provider,
                "t": token_hash,
                "c": csrf_hash,
                "x": datetime.now(UTC) + timedelta(seconds=ttl_seconds),
                "a": user_agent_hash,
                "i": ip_hash,
            },
        )

    def session_user(self, token_hash):
        r = (
            self.connection.execute(
                text(
                    "SELECT u.id,u.email::text AS email,u.display_name,u.timezone,u.country_code,u.locale,s.csrf_hash FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=:t AND s.revoked_at IS NULL AND s.expires_at>now() AND u.deleted_at IS NULL LIMIT 1"
                ),
                {"t": token_hash},
            )
            .mappings()
            .first()
        )
        if not r:
            return None
        d = dict(r)
        c = d.pop("csrf_hash")
        return AuthUser.model_validate(d), str(c)

    def revoke(self, token_hash):
        self.connection.execute(
            text(
                "UPDATE auth_sessions SET revoked_at=COALESCE(revoked_at,now()) WHERE token_hash=:t"
            ),
            {"t": token_hash},
        )

    def export_user(self, user_id):
        return {
            "user": dict(
                self.connection.execute(
                    text("SELECT id,email::text AS email,display_name FROM users WHERE id=:id"),
                    {"id": user_id},
                )
                .mappings()
                .one()
            )
        }

    def soft_delete_user(self, user_id):
        self.connection.execute(
            text(
                "UPDATE users SET email=NULL,display_name=NULL,deleted_at=now(),updated_at=now() WHERE id=:id"
            ),
            {"id": user_id},
        )
