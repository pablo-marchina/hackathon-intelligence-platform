#!/usr/bin/env python3
"""Isolated PostgreSQL catalog -> saved search -> outbox -> fake SMTP -> API.

No network discovery and no real SMTP. Requires a disposable localhost database
with all migrations applied. Designed as a release-gate CI job.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

from hackathon_api.repositories.personalization import PersonalizationRepository
from hackathon_api.schemas import FILTER_SCHEMA_VERSION
from hackathon_worker.alerts import deliver_pending_email, dispatch_due_emails
from hackathon_worker.matches import _explicit_email_consent, process_new_matches
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from scripts.ops.alert_outbox_integration_check import FakeSMTP, safe_database_url


def safe_match_database_url() -> str:
    if (
        os.environ.get("HIP_ALLOW_MATCH_DB_DRILL") != "1"
        or os.environ.get("ALERT_MATCH_ENABLED", "").lower() != "true"
    ):
        raise RuntimeError("match drill requires an explicit opt-in")
    return safe_database_url()


def verify() -> None:
    engine = create_engine(safe_match_database_url(), pool_pre_ping=True)
    users: list[str] = []
    hackathons: list[str] = []
    try:
        # Initial run establishes the baseline; never emit historic matches.
        assert process_new_matches(engine=engine)["initialized"] == 1
        with engine.begin() as conn:
            user = str(
                conn.execute(
                    text("""
                INSERT INTO users(email,display_name)
                VALUES (:email,'Search Match Fixture') RETURNING id::text
            """),
                    {"email": f"match-{uuid4().hex}@example.test"},
                ).scalar_one()
            )
            users.append(user)
            no_email = str(
                conn.execute(
                    text("""
                INSERT INTO users(email,display_name)
                VALUES (:email,'No Email Opt-in') RETURNING id::text
            """),
                    {"email": f"match-no-consent-{uuid4().hex}@example.test"},
                ).scalar_one()
            )
            users.append(no_email)
            # Explicit opt-in is required for email; app alerts are independent.
            conn.execute(
                text("""
                INSERT INTO alert_channel_preferences(user_id,channel,alert_type,enabled)
                VALUES (CAST(:user AS uuid),'EMAIL','new_match',true)
            """),
                {"user": user},
            )
            # Historical event exists BEFORE the saved search: never alert it.
            old = str(
                conn.execute(
                    text("""
                INSERT INTO hackathons(slug,name,normalized_name,format,is_free)
                VALUES (:slug,'Historical Match','historical match','ONLINE',true)
                RETURNING id::text
            """),
                    {"slug": f"old-match-{uuid4().hex}"},
                ).scalar_one()
            )
            hackathons.append(old)
        # A consent change must not commit while a sender holds its read guard.
        # This probes the actual PostgreSQL lock ordering (not just mocked SQL).
        with engine.connect() as consent_connection:
            consent_tx = consent_connection.begin()
            try:
                assert _explicit_email_consent(consent_connection, user)
                with engine.connect() as update_connection:
                    update_tx = update_connection.begin()
                    try:
                        update_connection.execute(text("SET LOCAL lock_timeout = '300ms'"))
                        try:
                            PersonalizationRepository(update_connection).set_alert_preference(
                                UUID(user),
                                "EMAIL",
                                "new_match",
                                False,
                            )
                        except DBAPIError as exc:
                            assert (
                                getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03"
                            ), exc
                        else:
                            raise AssertionError(
                                "consent revocation committed over an in-flight guard"
                            )
                    finally:
                        update_tx.rollback()
            finally:
                consent_tx.rollback()
        # Commit boundary is intentional: created_at ordering must be tested.
        with engine.begin() as conn:
            for person in users:
                conn.execute(
                    text("""
                    INSERT INTO saved_searches(
                        user_id,name,filters,filter_schema_version,notifications_enabled
                    ) VALUES (
                        CAST(:user AS uuid),'Online and free',CAST(:filters AS jsonb),
                        :version,true
                    )
                """),
                    {
                        "user": person,
                        "filters": '{"format":["ONLINE"],"free_only":true}',
                        "version": FILTER_SCHEMA_VERSION,
                    },
                )
        with engine.begin() as conn:
            matching = str(
                conn.execute(
                    text("""
                INSERT INTO hackathons(slug,name,normalized_name,format,is_free)
                VALUES (:slug,'New Online Match','new online match','ONLINE',true)
                RETURNING id::text
            """),
                    {"slug": f"new-match-{uuid4().hex}"},
                ).scalar_one()
            )
            hackathons.append(matching)
            unrelated = str(
                conn.execute(
                    text("""
                INSERT INTO hackathons(slug,name,normalized_name,format,is_free)
                VALUES (:slug,'Unrelated Offline','unrelated offline','IN_PERSON',false)
                RETURNING id::text
            """),
                    {"slug": f"negative-match-{uuid4().hex}"},
                ).scalar_one()
            )
            hackathons.append(unrelated)
        first = process_new_matches(engine=engine, min_age_seconds=0)
        assert first["scanned"] == 3, first
        assert first["created"] == 2 and first["matched"] == 2, first
        assert process_new_matches(engine=engine, min_age_seconds=0)["scanned"] == 0
        with engine.begin() as conn:
            rows = (
                conn.execute(
                    text("""
                SELECT a.id::text AS alert_id,a.user_id::text AS user_id,
                       a.hackathon_id::text AS hackathon_id,d.id::text AS delivery_id
                FROM alert_events a
                LEFT JOIN alert_deliveries d ON d.alert_event_id=a.id
                WHERE a.user_id IN (CAST(:user AS uuid), CAST(:no_email AS uuid))
                ORDER BY a.user_id
            """),
                    {"user": user, "no_email": no_email},
                )
                .mappings()
                .all()
            )
            assert len(rows) == 2
            assert all(r["hackathon_id"] == matching for r in rows)
            deliveries = [r["delivery_id"] for r in rows if r["delivery_id"]]
            assert len(deliveries) == 1, "email requires an affirmative preference"
            assert all(len(PersonalizationRepository(conn).alerts(UUID(u))) == 1 for u in users)
        messages: list[tuple[str, str]] = []
        assert dispatch_due_emails(engine=engine, publish=lambda d, t: messages.append((d, t))) == {
            "claimed": 1,
            "published": 1,
            "failed": 0,
        }
        smtp = FakeSMTP()
        delivery_id, token = messages[0]
        assert (
            deliver_pending_email(delivery_id, token, engine=engine, sender=smtp)["status"]
            == "SENT"
        )
        assert len(smtp.calls) == 1 and "New Online Match" in smtp.calls[0]["text"]
        assert (
            deliver_pending_email(delivery_id, token, engine=engine, sender=smtp)["status"]
            == "SKIPPED"
        )
        assert dispatch_due_emails(engine=engine, publish=lambda *_: None)["claimed"] == 0
        # A queued match must be rechecked against *current* eligibility before SMTP.
        # Both a changed filter outcome and a withdrawn catalog row must suppress it.
        for change in ("is_free=false", "is_active=false"):
            with engine.begin() as conn:
                stale_id = str(
                    conn.execute(
                        text("""
                    INSERT INTO hackathons(slug,name,normalized_name,format,is_free)
                    VALUES (:slug,'Outdated Match','outdated match','ONLINE',true)
                    RETURNING id::text
                """),
                        {"slug": f"stale-match-{uuid4().hex}"},
                    ).scalar_one()
                )
                hackathons.append(stale_id)
            new_matches = process_new_matches(engine=engine, min_age_seconds=0)
            assert new_matches["created"] == 2, new_matches
            with engine.begin() as conn:
                conn.execute(
                    text(f"UPDATE hackathons SET {change} WHERE id=CAST(:id AS uuid)"),
                    {"id": stale_id},
                )
            claims: list[tuple[str, str]] = []
            dispatch_due_emails(engine=engine, publish=lambda d, t, claims=claims: claims.append((d, t)))
            assert len(claims) == 1, claims
            result = deliver_pending_email(claims[0][0], claims[0][1], engine=engine, sender=smtp)
            assert result["status"] == "DISABLED", result
            assert len(smtp.calls) == 1, "ineligible hackathons must never reach SMTP"
        print(
            "W6 saved-search E2E: new-only, filtering, dedup, consent, stale item suppression, fake SMTP PASS"
        )
    finally:
        with engine.begin() as conn:
            for user in users:
                conn.execute(text("DELETE FROM users WHERE id=CAST(:id AS uuid)"), {"id": user})
            for hackathon in hackathons:
                conn.execute(
                    text("DELETE FROM hackathons WHERE id=CAST(:id AS uuid)"), {"id": hackathon}
                )
        engine.dispose()


if __name__ == "__main__":
    verify()
