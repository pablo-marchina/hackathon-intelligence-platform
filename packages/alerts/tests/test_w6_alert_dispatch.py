"""Unit contracts for the W6 opt-in alert outbox, without SMTP or PostgreSQL."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hackathon_alerts.delivery import RetryPolicy
from hackathon_alerts.repository import AlertRepository, EmailDispatchClaim
from hackathon_worker import alerts as worker


class SQLResult:
    def __init__(self, *, rows=None, row=None, count=1):
        self.rows = rows or []
        self.row = row
        self.rowcount = count

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.row

    def one(self):
        return self.row

    def scalar_one_or_none(self):
        return None


class Connection:
    def __init__(self, row=None):
        self.sql = []
        self.params = []
        self.row = row
        self.claim_rows = []

    def execute(self, query, params=None):
        sql = str(query)
        self.sql.append(sql)
        self.params.append(params or {})
        if "WITH due AS" in sql:
            return SQLResult(rows=self.claim_rows)
        if "SELECT d.id" in sql:
            return SQLResult(row=self.row)
        if "SELECT attempt_count, max_attempts" in sql:
            return SQLResult(row=(0, 2))
        return SQLResult()


class Engine:
    def __init__(self, conn):
        self.conn = conn
        self.calls = 0
        self.disposed = False

    @contextmanager
    def begin(self):
        self.calls += 1
        yield self.conn

    def dispose(self):
        self.disposed = True


class Sender:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def send(self, **data):
        self.calls.append(data)
        if self.error:
            raise self.error
        return "fake-smtp-id"


def config(enabled=True):
    return SimpleNamespace(
        alert_dispatch_enabled=enabled,
        alert_dispatch_batch_size=20,
        alert_dispatch_claim_seconds=180,
        alert_max_attempts=4,
        database_url="postgresql+psycopg://unused",
        web_base_url="https://example.test",
        smtp_host="localhost", smtp_port=25, smtp_from_address="test@example.test",
        smtp_username=None, smtp_password=None, smtp_starttls=False,
    )


def test_claim_is_bounded_due_fenced_and_skip_locked():
    conn = Connection()
    claim = EmailDispatchClaim(str(uuid4()), str(uuid4()))
    conn.claim_rows = [{"delivery_id": claim.delivery_id, "token": claim.token}]
    result = AlertRepository(conn).claim_due_emails(limit=2, lease_seconds=180)
    assert result == [claim]
    sql = conn.sql[-1]
    for fragment in ("FOR UPDATE SKIP LOCKED", "next_attempt_at <= clock_timestamp()",
                     "dispatch_expires_at <= clock_timestamp()", "attempt_count < max_attempts",
                     "status='PENDING'", "dispatch_token=gen_random_uuid()"):
        assert fragment in sql
    assert conn.params[-1] == {"limit": 2, "lease_seconds": 180}
    assert AlertRepository(conn).release_dispatch_claim(claim)
    assert "dispatch_token=CAST(:token AS uuid)" in conn.sql[-1]
    assert conn.params[-1]["token"] == claim.token


@pytest.mark.parametrize("limit,lease", [(0, 180), (101, 180), (1, 59), (1, 901)])
def test_claim_refuses_unbounded_work(limit, lease):
    with pytest.raises(ValueError):
        AlertRepository(Connection()).claim_due_emails(limit=limit, lease_seconds=lease)


def test_retry_is_limited_by_persisted_attempt_budget():
    conn = Connection()
    repo = AlertRepository(conn)
    status = repo.record_delivery_result(
        delivery_id=str(uuid4()), success=False, retry_policy=RetryPolicy(max_attempts=8),
    )
    assert status == "PENDING"
    assert conn.params[-1]["attempt_count"] == 1
    assert "dispatch_token=NULL" in conn.sql[-1]
    # Persistent maximum wins even if application configuration has a bigger budget.
    conn.execute = lambda query, params=None: SQLResult(row=(1, 2)) if "SELECT attempt_count" in str(query) else SQLResult()
    assert repo.record_delivery_result(delivery_id=str(uuid4()), success=False,
                                       retry_policy=RetryPolicy(max_attempts=8)) == "FAILED"


def test_dispatch_off_means_no_db_or_queue(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: config(False))
    assert worker.dispatch_due_emails(publish=lambda *_: pytest.fail("published")) == {"disabled": 1}


def test_dispatch_claims_then_publishes_and_releases_on_broker_error(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", config)
    conn = Connection()
    conn.claim_rows = [
        {"delivery_id": "1", "token": "t1"}, {"delivery_id": "2", "token": "t2"},
    ]
    engine = Engine(conn)
    sent = []

    def publish(delivery, token):
        sent.append((delivery, token))
        if delivery == "2":
            raise RuntimeError("broker-secret")

    result = worker.dispatch_due_emails(publish=publish, engine=engine, limit=2)
    assert result == {"claimed": 2, "published": 1, "failed": 1}
    assert sent == [("1", "t1"), ("2", "t2")]
    assert engine.calls == 2
    assert any("SET dispatch_token=NULL" in query for query in conn.sql)
    assert not engine.disposed


def test_delivery_requires_claim_token_and_due_time(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", config)
    sender = Sender()
    conn = Connection(row=None)
    result = worker.deliver_pending_email(str(uuid4()), str(uuid4()), engine=Engine(conn), sender=sender)
    assert result["status"] == "SKIPPED"
    assert sender.calls == []
    assert "d.dispatch_token=CAST(:token AS uuid)" in conn.sql[0]
    assert "d.dispatch_expires_at > clock_timestamp()" in conn.sql[0]
    assert "d.next_attempt_at <= clock_timestamp()" in conn.sql[0]
    assert "FOR UPDATE OF d" in conn.sql[0]


def test_deliver_off_does_not_query_or_send(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: config(False))
    assert worker.deliver_pending_email("d", "token")["status"] == "DISABLED"


def test_successful_delivery_renders_and_records(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", config)
    monkeypatch.setattr(AlertRepository, "authorized_email_recipient",
                        lambda *_args, **_kw: "test@example.test")
    conn = Connection(row={"id": "d", "alert_event_id": uuid4(), "user_id": uuid4(),
                           "alert_type": "deadline_changed", "payload": {"field_path": "start_at"},
                           "email": "test@example.test", "slug": "spring-hack", "name": "Spring Hack"})
    sender = Sender()
    result = worker.deliver_pending_email(str(uuid4()), str(uuid4()), sender=sender, engine=Engine(conn))
    assert result["status"] == "SENT"
    assert len(sender.calls) == 1
    assert "Spring Hack" in sender.calls[0]["subject"]
    assert "https://example.test/hackathons/spring-hack" in sender.calls[0]["text"]
    assert any("SET status=CAST(:status AS alert_delivery_status)" in sql for sql in conn.sql)


def test_smtp_exception_is_redacted_and_retryable(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", config)
    monkeypatch.setattr(AlertRepository, "authorized_email_recipient",
                        lambda *_args, **_kw: "test@example.test")
    conn = Connection(row={"id": "d", "alert_event_id": uuid4(), "user_id": uuid4(),
                           "alert_type": "deadline_changed", "payload": {},
                           "email": "test@example.test", "slug": None, "name": None})
    error = RuntimeError("secret-password-private")
    result = worker.deliver_pending_email(str(uuid4()), str(uuid4()), sender=Sender(error), engine=Engine(conn))
    assert result["status"] == "RETRY"
    assert "secret-password-private" not in str(result)
    assert conn.params[-1]["error_message"] == "RuntimeError"


def test_email_channel_disabled_fails_without_contacting_smtp(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", config)
    conn = Connection(row={"id": "d", "alert_event_id": uuid4(), "user_id": uuid4(),
                           "alert_type": "new_match", "payload": {}, "email": None,
                           "slug": None, "name": None})
    sender = Sender()
    assert worker.deliver_pending_email(str(uuid4()), str(uuid4()), sender=sender,
                                        engine=Engine(conn))["status"] == "DISABLED"
    assert not sender.calls
    assert conn.params[-1]["status"] == "FAILED"


def test_migration_declares_dispatch_columns_and_index():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[3] / "db/sql/0008_w6_alert_dispatch.sql").read_text()
    assert "dispatch_token uuid" in sql
    assert "dispatch_expires_at timestamptz" in sql
    assert "alert_delivery_due_idx" in sql


def test_claim_release_refuses_stale_token():
    class LostConnection(Connection):
        def execute(self, query, params=None):
            result = super().execute(query, params)
            result.rowcount = 0
            return result

    assert not AlertRepository(LostConnection()).release_dispatch_claim(
        EmailDispatchClaim(str(uuid4()), str(uuid4()))
    )


def test_opt_in_drill_refuses_production_and_remote_database(monkeypatch):
    from scripts.ops.alert_outbox_integration_check import safe_database_url

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("HIP_ALLOW_ALERT_DB_DRILL", "1")
    monkeypatch.setenv("ALERT_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("SOURCE_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/hackathon")
    with pytest.raises(RuntimeError):
        safe_database_url()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.production.internal/hackathon")
    with pytest.raises(RuntimeError):
        safe_database_url()


def test_saved_search_revoke_after_claim_prevents_smtp(monkeypatch):
    from hackathon_worker import matches

    monkeypatch.setattr(worker, "get_settings", config)
    monkeypatch.setattr(AlertRepository, "authorized_email_recipient",
                        lambda *_args, **_kw: "test@example.test")
    called = []
    monkeypatch.setattr(matches, "allow_new_match_email",
                        lambda _con, **kw: called.append(kw) or False)
    conn = Connection(row={"id": "d", "alert_event_id": uuid4(), "user_id": uuid4(),
                           "alert_type": "new_match", "payload": {"saved_search_id": str(uuid4())},
                           "hackathon_id": uuid4(), "email": "user@example.test", "slug": None, "name": "Event"})
    sender = Sender()
    result = worker.deliver_pending_email(str(uuid4()), str(uuid4()),
                                          sender=sender, engine=Engine(conn))
    assert result["status"] == "DISABLED"
    assert not sender.calls
    assert len(called) == 1
    assert called[0]["hackathon_id"] == str(conn.row["hackathon_id"])
    assert conn.params[-1]["status"] == "FAILED"


def test_valid_new_match_consent_allows_smtp(monkeypatch):
    from hackathon_worker import matches

    monkeypatch.setattr(worker, "get_settings", config)
    monkeypatch.setattr(AlertRepository, "authorized_email_recipient",
                        lambda *_args, **_kw: "user@example.test")
    monkeypatch.setattr(matches, "allow_new_match_email", lambda *_args, **_kwargs: True)
    conn = Connection(row={"id": "d", "alert_event_id": uuid4(), "user_id": uuid4(),
                           "alert_type": "new_match", "payload": {"saved_search_id": str(uuid4())},
                           "hackathon_id": uuid4(), "email": "user@example.test", "slug": None, "name": "Event"})
    sender = Sender()
    assert worker.deliver_pending_email(str(uuid4()), str(uuid4()),
                                        sender=sender, engine=Engine(conn))["status"] == "SENT"
    assert len(sender.calls) == 1
