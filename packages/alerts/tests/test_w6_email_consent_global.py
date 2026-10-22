"""Iteration 15: default-deny email, fresh recipients, and preference precedence.

Pure unit tests; the PostgreSQL integration drill verifies SQL execution and locks.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from hackathon_alerts.repository import AlertRepository
from hackathon_worker import alerts as worker


class Result:
    def __init__(self, *, scalar=None, row=None):
        self.value = scalar
        self.row = row

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def first(self):
        return self.row

    def one(self):
        return (0, 4)


class ConsentConnection:
    def __init__(self, *, email="current@example.test", prefs=None, event=None):
        self.email = email
        self.prefs = prefs or {}
        self.event = event
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        if "SELECT d.id" in sql:
            return Result(row=self.event)
        if "SELECT email::text FROM users" in sql:
            return Result(scalar=self.email)
        if "FROM alert_channel_preferences" in sql:
            ch = "EMAIL" if "channel='EMAIL'" in sql else params["channel"]
            exact = params["alert_type"]
            value = self.prefs.get((ch, exact), self.prefs.get((ch, "*")))
            return Result(scalar=value)
        return Result()


class Engine:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def begin(self):
        yield self.conn


class Sender:
    def __init__(self):
        self.calls = []

    async def send(self, **kwargs):
        self.calls.append(kwargs)
        return "sent-fixture"


def config():
    return SimpleNamespace(
        alert_dispatch_enabled=True,
        alert_max_attempts=4,
        web_base_url="https://example.test",
        smtp_host="localhost",
        smtp_port=25,
        smtp_from_address="sender@example.test",
        smtp_username=None,
        smtp_password=None,
        smtp_starttls=False,
        smtp_ssl=False,
    )


@pytest.mark.parametrize(
    "prefs,expected",
    [
        ({}, False),
        ({("EMAIL", "*"): True}, True),
        ({("EMAIL", "*"): False}, False),
        ({("EMAIL", "deadline_changed"): True}, True),
        ({("EMAIL", "deadline_changed"): False}, False),
        ({("EMAIL", "*"): True, ("EMAIL", "deadline_changed"): False}, False),
        ({("EMAIL", "*"): False, ("EMAIL", "deadline_changed"): True}, True),
    ],
)
def test_email_requires_explicit_type_or_wildcard_opt_in(prefs, expected):
    conn = ConsentConnection(prefs=prefs)
    enabled = AlertRepository(conn).channel_enabled(
        user_id=str(uuid4()),
        channel="EMAIL",
        alert_type="deadline_changed",
    )
    assert enabled is expected
    assert len(conn.calls) == 2
    assert "deleted_at IS NULL" in conn.calls[0][0]
    assert "ORDER BY CASE WHEN alert_type=:alert_type" in conn.calls[1][0]


@pytest.mark.parametrize("email", [None, ""])
def test_missing_email_short_circuits_preference_query(email):
    conn = ConsentConnection(email=email, prefs={("EMAIL", "*"): True})
    assert (
        AlertRepository(conn).authorized_email_recipient(
            user_id=str(uuid4()),
            alert_type="new_match",
            lock_user=True,
        )
        is None
    )
    assert len(conn.calls) == 1
    assert "FOR SHARE" in conn.calls[0][0]


def test_in_app_preserves_legacy_default_and_exact_precedence():
    conn = ConsentConnection()
    repo = AlertRepository(conn)
    assert repo.channel_enabled(user_id=str(uuid4()), channel="IN_APP", alert_type="new_match")
    conn.prefs = {("IN_APP", "*"): False, ("IN_APP", "new_match"): True}
    assert repo.channel_enabled(user_id=str(uuid4()), channel="IN_APP", alert_type="new_match")
    conn.prefs[("IN_APP", "new_match")] = False
    assert not repo.channel_enabled(user_id=str(uuid4()), channel="IN_APP", alert_type="new_match")


def test_delivery_without_consent_never_contacts_smtp(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", config)
    conn = ConsentConnection(
        email="user@example.test",
        prefs={},
        event={
            "alert_event_id": uuid4(),
            "user_id": uuid4(),
            "alert_type": "deadline_changed",
            "payload": {},
            "slug": "event",
            "name": "Event",
        },
    )
    sender = Sender()
    result = worker.deliver_pending_email(
        str(uuid4()), str(uuid4()), sender=sender, engine=Engine(conn)
    )
    assert result["status"] == "DISABLED"
    assert not sender.calls
    assert any("status=CAST(:status AS alert_delivery_status)" in sql for sql, _ in conn.calls)


def test_delivery_uses_current_address_and_locked_consent(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", config)
    conn = ConsentConnection(
        email="new@example.test",
        prefs={("EMAIL", "deadline_changed"): True},
        event={
            "alert_event_id": uuid4(),
            "user_id": uuid4(),
            "alert_type": "deadline_changed",
            "payload": {},
            "slug": "event",
            "name": "Event",
        },
    )
    sender = Sender()
    result = worker.deliver_pending_email(
        str(uuid4()), str(uuid4()), sender=sender, engine=Engine(conn)
    )
    assert result["status"] == "SENT"
    assert sender.calls[0]["to"] == "new@example.test"
    user_index = next(
        i for i, (sql, _) in enumerate(conn.calls) if "SELECT email::text FROM users" in sql
    )
    assert "FOR SHARE" in conn.calls[user_index][0]
    assert "alert_channel_preferences" in conn.calls[user_index + 1][0]
    assert "u.email::text" not in conn.calls[0][0], (
        "do not read stale recipients in the claim query"
    )


def test_delivery_specific_revocation_wins_over_wildcard(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", config)
    conn = ConsentConnection(
        prefs={("EMAIL", "*"): True, ("EMAIL", "deadline_changed"): False},
        event={
            "alert_event_id": uuid4(),
            "user_id": uuid4(),
            "alert_type": "deadline_changed",
            "payload": {},
            "slug": "event",
            "name": "Event",
        },
    )
    sender = Sender()
    assert (
        worker.deliver_pending_email(
            str(uuid4()), str(uuid4()), sender=sender, engine=Engine(conn)
        )["status"]
        == "DISABLED"
    )
    assert not sender.calls
