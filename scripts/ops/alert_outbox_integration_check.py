#!/usr/bin/env python3
"""Database-backed alert -> outbox -> fake SMTP -> API repository drill.

No real email is sent. Requires disposable local PostgreSQL with Alembic head.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from hackathon_alerts.events import change_alert, new_match_alert
from hackathon_alerts.repository import AlertRepository
from hackathon_api.repositories.personalization import PersonalizationRepository
from hackathon_api.schemas import FILTER_SCHEMA_VERSION
from hackathon_worker.alerts import deliver_pending_email, dispatch_due_emails


class FakeSMTP:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail = fail

    async def send(self, **data: str) -> str:
        self.calls.append(data)
        if self.fail:
            raise RuntimeError("a fake private SMTP diagnostic")
        return "fake-smtp-message-id"


def safe_database_url() -> str:
    if (os.getenv("APP_ENV") != "test"
            or os.getenv("HIP_ALLOW_ALERT_DB_DRILL") != "1"
            or os.getenv("ALERT_DISPATCH_ENABLED", "").lower() != "true"
            or os.getenv("SOURCE_SCHEDULER_ENABLED", "false").lower() != "false"):
        raise RuntimeError("alert drill requires isolated test opt-in and disabled source scheduler")
    url = os.getenv("DATABASE_URL", "")
    parsed = urlsplit(url)
    if (parsed.scheme != "postgresql+psycopg"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.path not in {"/hackathon", "/hip_test"}
            or parsed.query or parsed.fragment):
        raise RuntimeError("alert drill requires a disposable localhost PostgreSQL database")
    for variable in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        if os.getenv(variable):
            raise RuntimeError(f"alert drill refuses inherited {variable} override")
    return url


def verify() -> None:
    engine = create_engine(safe_database_url(), pool_pre_ping=True)
    user_id = None
    hackathon_id = None
    try:
        with engine.begin() as con:
            user_id = str(con.execute(text("""
                INSERT INTO users(email, display_name)
                VALUES (:email,'Alert CI') RETURNING id::text
            """), {"email": f"w6-alert-{uuid4().hex}@example.test"}).scalar_one())
            hackathon_id = str(con.execute(text("""
                INSERT INTO hackathons(slug, name, normalized_name)
                VALUES (:slug, 'CI Fixture Hackathon', 'ci fixture hackathon')
                RETURNING id::text
            """), {"slug": f"ci-alert-{uuid4().hex}"}).scalar_one())
            saved_search_id = str(con.execute(text("""
                INSERT INTO saved_searches(user_id,name,filters,filter_schema_version,notifications_enabled)
                VALUES (CAST(:uid AS uuid),'Alert E2E fixture','{}'::jsonb,:version,true)
                RETURNING id::text
            """), {"uid": user_id, "version": FILTER_SCHEMA_VERSION}).scalar_one())
            con.execute(text("""
                INSERT INTO alert_channel_preferences(user_id,channel,alert_type,enabled)
                VALUES (CAST(:uid AS uuid),'EMAIL','new_match',true)
            """), {"uid": user_id})
            repo = AlertRepository(con)
            candidate = new_match_alert(
                user_id=user_id, hackathon_id=hackathon_id,
                saved_search_id=saved_search_id, payload={"title": "CI fixture"},
            )
            alert_id = repo.create_event(candidate)
            assert alert_id is not None
            assert repo.create_event(candidate) is None, "alert dedup key is not unique"
            delivery_id = repo.ensure_delivery(alert_event_id=alert_id, channel="EMAIL", max_attempts=2)
            assert repo.ensure_delivery(alert_event_id=alert_id, channel="EMAIL", max_attempts=2) == delivery_id

        first_messages: list[tuple[str, str]] = []
        result = dispatch_due_emails(engine=engine, publish=lambda did, token: first_messages.append((did, token)))
        assert result == {"claimed": 1, "published": 1, "failed": 0}
        first_id, old_token = first_messages[0]
        assert first_id == delivery_id
        assert dispatch_due_emails(engine=engine, publish=lambda *_: None)["claimed"] == 0
        assert deliver_pending_email(delivery_id, str(uuid4()), sender=FakeSMTP(), engine=engine)["status"] == "SKIPPED"

        failing = FakeSMTP(fail=True)
        assert deliver_pending_email(delivery_id, old_token, sender=failing, engine=engine)["status"] == "RETRY"
        assert len(failing.calls) == 1
        assert deliver_pending_email(delivery_id, old_token, sender=FakeSMTP(), engine=engine)["status"] == "SKIPPED"
        assert dispatch_due_emails(engine=engine, publish=lambda *_: None)["claimed"] == 0

        # Advance this *test fixture's* due time; no sleeping or external email.
        with engine.begin() as con:
            state = con.execute(text("""
                SELECT status::text, attempt_count, next_attempt_at, error_message
                FROM alert_deliveries WHERE id=CAST(:id AS uuid)
            """), {"id": delivery_id}).one()
            assert state[0] == "PENDING" and state[1] == 1 and state[2] is not None
            assert state[3] == "RuntimeError", "private error message persisted"
            con.execute(text("""
                UPDATE alert_deliveries SET next_attempt_at=clock_timestamp()-interval '1 second'
                WHERE id=CAST(:id AS uuid)
            """), {"id": delivery_id})

        second_messages: list[tuple[str, str]] = []
        assert dispatch_due_emails(engine=engine,
            publish=lambda did, token: second_messages.append((did, token)))["claimed"] == 1
        _, new_token = second_messages[0]
        assert new_token != old_token
        sent = FakeSMTP()
        assert deliver_pending_email(delivery_id, old_token, sender=sent, engine=engine)["status"] == "SKIPPED"
        assert deliver_pending_email(delivery_id, new_token, sender=sent, engine=engine)["status"] == "SENT"
        assert len(sent.calls) == 1 and "CI Fixture Hackathon" in sent.calls[0]["text"]
        assert deliver_pending_email(delivery_id, new_token, sender=sent, engine=engine)["status"] == "SKIPPED"
        assert dispatch_due_emails(engine=engine, publish=lambda *_: None)["claimed"] == 0
        with engine.begin() as con:
            status, count = con.execute(text("""
                SELECT status::text, attempt_count FROM alert_deliveries WHERE id=CAST(:id AS uuid)
            """), {"id": delivery_id}).one()
            assert status == "SENT" and count == 2
            visible = PersonalizationRepository(con).alerts(UUID(user_id))
            assert len(visible) == 1 and str(visible[0]["id"]) == alert_id
        # W6 iteration 15: every email type, not just new_match, must require
        # explicit opt-in. Verify this with real PostgreSQL preference rows.
        def queue_change(label: str) -> str:
            with engine.begin() as con:
                repo = AlertRepository(con)
                candidate = change_alert(
                    user_id=user_id, hackathon_id=hackathon_id,
                    change_id=f"consent-{label}-{uuid4()}",
                    change_type="deadline_changed", payload={},
                )
                alert_id = repo.create_event(candidate)
                assert alert_id
                return repo.ensure_delivery(alert_event_id=alert_id, channel="EMAIL")

        # No preference: outbox may contain a legacy email but cannot send it.
        first_change = queue_change("missing")
        with engine.begin() as con:
            assert not AlertRepository(con).channel_enabled(
                user_id=user_id, channel="EMAIL", alert_type="deadline_changed",
            )
        claims: list[tuple[str, str]] = []
        assert dispatch_due_emails(engine=engine, publish=lambda d, t: claims.append((d, t)))["claimed"] == 1
        assert claims[0][0] == first_change
        withheld = FakeSMTP()
        assert deliver_pending_email(*claims[0], engine=engine, sender=withheld)["status"] == "DISABLED"
        assert withheld.calls == []

        # A wildcard opt-in does not override an explicit type-specific opt-out.
        with engine.begin() as con:
            pref = PersonalizationRepository(con)
            pref.set_alert_preference(UUID(user_id), "EMAIL", "*", True)
            pref.set_alert_preference(UUID(user_id), "EMAIL", "deadline_changed", False)
        second_change = queue_change("denied")
        claims = []
        assert dispatch_due_emails(engine=engine, publish=lambda d, t: claims.append((d, t)))["claimed"] == 1
        assert claims[0][0] == second_change
        assert deliver_pending_email(*claims[0], engine=engine, sender=withheld)["status"] == "DISABLED"
        assert withheld.calls == []

        # Once explicitly authorized, the destination is fetched again under
        # the users lock. A changed address cannot be read from an old claim.
        current_email = f"updated-w6-{uuid4().hex}@example.test"
        with engine.begin() as con:
            PersonalizationRepository(con).set_alert_preference(
                UUID(user_id), "EMAIL", "deadline_changed", True,
            )
            con.execute(text("UPDATE users SET email=:email WHERE id=CAST(:id AS uuid)"),
                        {"email": current_email, "id": user_id})
        third_change = queue_change("authorized")
        claims = []
        assert dispatch_due_emails(engine=engine, publish=lambda d, t: claims.append((d, t)))["claimed"] == 1
        assert claims[0][0] == third_change
        delivered = FakeSMTP()
        assert deliver_pending_email(*claims[0], engine=engine, sender=delivered)["status"] == "SENT"
        assert len(delivered.calls) == 1 and delivered.calls[0]["to"] == current_email

        # An opt-out must wait for an in-flight delivery's shared user lock.
        with engine.connect() as guard_connection:
            guard_tx = guard_connection.begin()
            try:
                assert AlertRepository(guard_connection).authorized_email_recipient(
                    user_id=user_id, alert_type="deadline_changed", lock_user=True,
                ) == current_email
                with engine.connect() as update_connection:
                    update_tx = update_connection.begin()
                    try:
                        update_connection.execute(text("SET LOCAL lock_timeout = '300ms'"))
                        try:
                            PersonalizationRepository(update_connection).set_alert_preference(
                                UUID(user_id), "EMAIL", "deadline_changed", False,
                            )
                        except DBAPIError as exc:
                            assert getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03", exc
                        else:
                            raise AssertionError("consent revocation bypassed the delivery guard")
                    finally:
                        update_tx.rollback()
            finally:
                guard_tx.rollback()
        print("W6 alert outbox: dedup, due/retry, fencing, default-deny consent, "
              "fresh address, revocation lock, fake SMTP, API visibility PASS")
    finally:
        with engine.begin() as con:
            if user_id:
                con.execute(text("DELETE FROM users WHERE id=CAST(:id AS uuid)"), {"id": user_id})
            if hackathon_id:
                con.execute(text("DELETE FROM hackathons WHERE id=CAST(:id AS uuid)"), {"id": hackathon_id})
        engine.dispose()


if __name__ == "__main__":
    verify()
