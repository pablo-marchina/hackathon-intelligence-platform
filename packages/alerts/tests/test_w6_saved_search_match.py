"""The match producer is fully opt-in, bounded and uses the public search DSL."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from hackathon_api.schemas import FILTER_SCHEMA_VERSION
from hackathon_worker import matches


class Result:
    def __init__(self, *, scalar=None, row=None, rows=None):
        self.scalar = scalar
        self.row = row
        self.rows = rows if rows is not None else []

    def scalar_one_or_none(self):
        return self.scalar

    def scalar_one(self):
        return self.scalar

    def one(self):
        return self.row

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.row


class Connection:
    def __init__(self, *, init=False, events=None, searches=None):
        self.init = init
        self.events = events or []
        self.searches = searches or []
        self.statements = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        if "INSERT INTO alert_match_progress" in sql:
            return Result(scalar="new_matches" if self.init else None)
        if "FROM alert_match_progress" in sql:
            return Result(row=(datetime(2026, 10, 1, tzinfo=UTC), uuid4()))
        if "FROM hackathons\n" in sql:
            return Result(rows=self.events)
        if "FROM saved_searches s JOIN users u" in sql:
            return Result(rows=self.searches)
        if "SELECT enabled" in sql:
            return Result(scalar=None)
        return Result(scalar=True)


class Engine:
    def __init__(self, conn, error=None):
        self.conn = conn
        self.error = error
        self.begins = 0
        self.disposed = False

    @contextmanager
    def begin(self):
        self.begins += 1
        if self.error:
            raise self.error
        yield self.conn

    def dispose(self):
        self.disposed = True


def config(enabled=True):
    return SimpleNamespace(
        alert_match_enabled=enabled,
        alert_match_batch_size=25,
        alert_match_min_age_seconds=60,
        alert_max_attempts=4,
        database_url="postgresql+psycopg://test.invalid/db",
    )


def event(*, active=True):
    return {
        "id": str(uuid4()),
        "slug": "test-hackathon",
        "name": "Example Hack",
        "created_at": datetime(2026, 10, 15, tzinfo=UTC),
        "is_active": active,
    }


def search(*, filters=None, age_days=1, version=FILTER_SCHEMA_VERSION):
    return {
        "id": str(uuid4()),
        "user_id": str(uuid4()),
        "created_at": datetime(2026, 10, 15, tzinfo=UTC) - timedelta(days=age_days),
        "filters": filters if filters is not None else {"free_only": True},
        "filter_schema_version": version,
    }


def test_opt_out_performs_no_database_work(monkeypatch):
    monkeypatch.setattr(matches, "get_settings", lambda: config(False))
    assert matches.process_new_matches() == {"disabled": 1}


def test_first_run_bootstraps_from_db_clock_without_backfill(monkeypatch):
    monkeypatch.setattr(matches, "get_settings", config)
    conn = Connection(init=True)
    engine = Engine(conn)
    assert matches.process_new_matches(engine=engine) == {
        "initialized": 1,
        "scanned": 0,
        "created": 0,
    }
    assert engine.begins == 1
    assert all("FROM hackathons" not in sql for sql, _ in conn.statements)


@pytest.mark.parametrize(
    "batch,age,searches",
    [
        (0, 60, 100),
        (101, 60, 100),
        (1, -1, 100),
        (1, 3601, 100),
        (1, 60, 0),
        (1, 60, 50001),
    ],
)
def test_bounded_parameters(monkeypatch, batch, age, searches):
    monkeypatch.setattr(matches, "get_settings", config)
    with pytest.raises(ValueError):
        matches.process_new_matches(batch_size=batch, min_age_seconds=age, max_searches=searches)


@pytest.mark.parametrize(
    "raw,version,expected",
    [
        ({"free_only": True}, FILTER_SCHEMA_VERSION, True),
        ({"country_code": "br", "free_only": True}, FILTER_SCHEMA_VERSION, True),
        ({"cursor": "attacker"}, FILTER_SCHEMA_VERSION, False),
        ({"invented_filter": True}, FILTER_SCHEMA_VERSION, False),
        ({"min_prize_usd": -5}, FILTER_SCHEMA_VERSION, False),
        ({"free_only": True}, "unknown-schema-v77", False),
    ],
)
def test_saved_filters_fail_closed(raw, version, expected):
    assert (matches._filters(raw, version) is not None) is expected


def test_public_search_builder_is_reused_and_scoped_to_one_catalog_id(monkeypatch):
    actual = []

    class C:
        def execute(self, sql, params=None):
            actual.append((str(sql), params))
            return Result(row=("found",))

    target_id = str(uuid4())
    found = matches._match_saved_search(
        C(),
        hackathon_id=target_id,
        raw_filters={"free_only": True, "format": ["ONLINE"]},
        schema_version=FILTER_SCHEMA_VERSION,
    )
    assert found is True
    sql, params = actual[0]
    assert "h.id=CAST(:target_id AS uuid)" in sql
    assert "h.is_free=true" in sql
    assert "h.format::text=ANY(:formats)" in sql
    assert params["target_id"] == target_id
    assert params["limit"] == 2  # search's existing LIMIT + 1 convention


def test_transactional_creation_cursor_and_consent(monkeypatch):
    monkeypatch.setattr(matches, "get_settings", config)
    e = event()
    good = search()
    inactive = search(filters={"invalid": True})
    old = search(age_days=-1)
    conn = Connection(events=[e], searches=[good, inactive, old])
    engine = Engine(conn)
    seen = []

    monkeypatch.setattr(
        matches,
        "_match_saved_search",
        lambda _c, **kwargs: None if kwargs["raw_filters"].get("invalid") else True,
    )
    monkeypatch.setattr(matches, "_explicit_email_consent", lambda *_: True)

    class Repo:
        def __init__(self, c):
            assert c is conn

        def channel_enabled(self, **kwargs):
            return True

        def create_event(self, candidate):
            seen.append(candidate)
            return str(uuid4())

        def ensure_delivery(self, **kwargs):
            seen.append(kwargs)

    monkeypatch.setattr(matches, "AlertRepository", Repo)
    result = matches.process_new_matches(engine=engine)
    assert result == {"scanned": 1, "matched": 1, "created": 1, "invalid_filters": 1}
    assert len(seen) == 2
    assert seen[0].alert_type == "new_match"
    assert seen[0].payload["saved_search_id"] == good["id"]
    assert seen[1]["channel"] == "EMAIL"
    assert "UPDATE alert_match_progress" in conn.statements[-1][0]
    assert engine.begins == 1 and not engine.disposed


def test_inactive_event_advances_cursor_without_alert(monkeypatch):
    monkeypatch.setattr(matches, "get_settings", config)
    conn = Connection(events=[event(active=False)], searches=[search()])
    assert matches.process_new_matches(engine=Engine(conn)) == {
        "scanned": 1,
        "matched": 0,
        "created": 0,
        "invalid_filters": 0,
    }
    assert "UPDATE alert_match_progress" in conn.statements[-1][0]


def test_search_capacity_fails_without_advancing_cursor(monkeypatch):
    monkeypatch.setattr(matches, "get_settings", config)
    conn = Connection(events=[event()], searches=[search(), search()])
    with pytest.raises(RuntimeError, match="capacity"):
        matches.process_new_matches(engine=Engine(conn), max_searches=1)
    assert not any("UPDATE alert_match_progress" in sql for sql, _ in conn.statements)


def test_no_in_app_or_email_consent_does_not_create(monkeypatch):
    monkeypatch.setattr(matches, "get_settings", config)
    conn = Connection(events=[event()], searches=[search()])
    monkeypatch.setattr(matches, "_match_saved_search", lambda *_args, **_kw: True)
    monkeypatch.setattr(matches, "_explicit_email_consent", lambda *_args: False)

    class Repo:
        def __init__(self, conn):
            pass

        def channel_enabled(self, **kw):
            return False

        def create_event(self, *_):
            pytest.fail("no consent")

    monkeypatch.setattr(matches, "AlertRepository", Repo)
    assert matches.process_new_matches(engine=Engine(conn))["created"] == 0


def test_durable_cursor_and_claim_are_postgresql_serialized(monkeypatch):
    monkeypatch.setattr(matches, "get_settings", config)
    conn = Connection(events=[])
    assert matches.process_new_matches(engine=Engine(conn))["scanned"] == 0
    assert any("FOR UPDATE" in sql and "alert_match_progress" in sql for sql, _ in conn.statements)
    assert any("ORDER BY created_at,id LIMIT" in sql for sql, _ in conn.statements)
    assert any("clock_timestamp()-make_interval" in sql for sql, _ in conn.statements)


def test_duplicate_alert_does_not_generate_a_second_email_delivery(monkeypatch):
    monkeypatch.setattr(matches, "get_settings", config)
    conn = Connection(events=[event()], searches=[search()])
    monkeypatch.setattr(matches, "_match_saved_search", lambda *_args, **_kw: True)
    monkeypatch.setattr(matches, "_explicit_email_consent", lambda *_args: True)

    class Repo:
        def __init__(self, conn):
            pass

        def channel_enabled(self, **kw):
            return True

        def create_event(self, *_):
            return None  # Another worker inserted the same dedup key.

        def ensure_delivery(self, **kw):
            pytest.fail("existing alert must not get a new delivery")

    monkeypatch.setattr(matches, "AlertRepository", Repo)
    assert matches.process_new_matches(engine=Engine(conn))["created"] == 0


def test_explicit_email_requires_real_email_optin_not_default_fallback():
    class C:
        def __init__(self):
            self.count = 0

        def execute(self, stmt, params):
            sql = str(stmt)
            self.count += 1
            assert params == (
                {"user_id": "user-id"}
                if self.count == 1
                else {"user_id": "user-id", "alert_type": "new_match"}
            )
            if self.count == 1:
                assert "FROM users" in sql and "FOR SHARE" in sql
                assert "NULLIF(email::text" in sql
                return Result(scalar="user-id")
            assert "alert_channel_preferences" in sql
            assert "channel='EMAIL'" in sql
            assert "ORDER BY CASE WHEN alert_type=:alert_type" in sql
            return Result(scalar=False)

    c = C()
    assert matches._explicit_email_consent(c, "user-id") is False
    assert c.count == 2


def test_empty_event_scan_leaves_the_cursor_unchanged(monkeypatch):
    monkeypatch.setattr(matches, "get_settings", config)
    conn = Connection(events=[], searches=[search()])
    assert matches.process_new_matches(engine=Engine(conn))["scanned"] == 0
    assert not any("UPDATE alert_match_progress" in sql for sql, _ in conn.statements)
    assert not any("FROM saved_searches s" in sql for sql, _ in conn.statements)


def test_safe_integration_drill_fails_closed(monkeypatch):
    from scripts.ops.saved_search_match_integration_check import safe_match_database_url

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SOURCE_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ALERT_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("HIP_ALLOW_ALERT_DB_DRILL", "1")
    monkeypatch.setenv("HIP_ALLOW_MATCH_DB_DRILL", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/hip_test")
    monkeypatch.delenv("ALERT_MATCH_ENABLED", raising=False)
    with pytest.raises(RuntimeError):
        safe_match_database_url()
    monkeypatch.setenv("ALERT_MATCH_ENABLED", "true")
    assert safe_match_database_url().endswith("/hip_test")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError):
        safe_match_database_url()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.production.internal/hip_test")
    with pytest.raises(RuntimeError):
        safe_match_database_url()


def test_match_migration_and_readiness_are_coherent():
    from pathlib import Path

    import hackathon_api.main as api_main
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[3]
    head = ScriptDirectory.from_config(Config(str(root / "db/alembic.ini"))).get_current_head()
    assert head == "0014_typed_attribute_catalog"
    assert head == api_main.EXPECTED_DB_REVISION
    ddl = (root / "db/sql/0009_w6_alert_match.sql").read_text()
    assert "PRIMARY KEY" in ddl and "hackathons_match_scan_idx" in ddl
    assert "alert_match_progress" in ddl


@pytest.mark.parametrize("raw", [None, "invalid", "", "not-a-uuid", 123])
def test_missing_or_malformed_saved_search_reference_never_authorizes_email(raw):
    class C:
        def execute(self, *_):
            pytest.fail("bad search ID must be rejected before any DB query")

    assert not matches.allow_new_match_email(
        C(),
        user_id=str(uuid4()),
        hackathon_id=str(uuid4()),
        payload={"saved_search_id": raw},
    )


@pytest.mark.parametrize("raw", [None, "invalid", "", "not-a-uuid", 123])
def test_missing_or_malformed_hackathon_reference_never_authorizes_email(raw):
    class C:
        def execute(self, *_):
            pytest.fail("bad hackathon ID must be rejected before any DB query")

    assert not matches.allow_new_match_email(
        C(),
        user_id=str(uuid4()),
        hackathon_id=raw,
        payload={"saved_search_id": str(uuid4())},
    )


def test_revoked_email_consent_rejects_queued_match(monkeypatch):
    monkeypatch.setattr(matches, "_explicit_email_consent", lambda *_: False)

    class C:
        def execute(self, *_):
            pytest.fail("revocation must prevent even a saved-search lookup")

    assert not matches.allow_new_match_email(
        C(),
        user_id=str(uuid4()),
        hackathon_id=str(uuid4()),
        payload={"saved_search_id": str(uuid4())},
    )


def test_disabled_saved_search_rejects_queued_match(monkeypatch):
    monkeypatch.setattr(matches, "_explicit_email_consent", lambda *_: True)
    uid = str(uuid4())
    search_id = str(uuid4())

    class C:
        def execute(self, statement, params=None):
            assert "notifications_enabled=true" in str(statement)
            assert "FOR SHARE" in str(statement)
            assert "saved_searches" in str(statement)
            assert params == {"user_id": uid, "search_id": search_id}
            return Result(row=None)

    assert not matches.allow_new_match_email(
        C(),
        user_id=uid,
        hackathon_id=str(uuid4()),
        payload={"saved_search_id": search_id},
    )


@pytest.mark.parametrize("catalog_is_matching", [True, False])
def test_active_saved_search_requires_current_catalog_match(monkeypatch, catalog_is_matching):
    monkeypatch.setattr(matches, "_explicit_email_consent", lambda *_: True)
    uid = str(uuid4())
    search_id = str(uuid4())
    hackathon_id = str(uuid4())
    issued_sql = []

    class C:
        def execute(self, statement, params=None):
            sql = str(statement)
            issued_sql.append(sql)
            if "FROM saved_searches" in sql:
                assert params == {"user_id": uid, "search_id": search_id}
                return Result(
                    row={
                        "filters": {"free_only": True, "format": ["ONLINE"]},
                        "filter_schema_version": FILTER_SCHEMA_VERSION,
                    }
                )
            assert "h.id=CAST(:target_id AS uuid)" in sql
            assert "h.is_active=true" in sql
            assert "h.is_free=true" in sql
            assert "FOR SHARE OF h" in sql
            assert params["target_id"] == hackathon_id
            return Result(row=("active",) if catalog_is_matching else None)

    assert (
        matches.allow_new_match_email(
            C(),
            user_id=uid,
            hackathon_id=hackathon_id,
            payload={"saved_search_id": search_id},
        )
        is catalog_is_matching
    )
    assert len(issued_sql) == 2


def test_bad_stored_search_filters_fail_closed_at_delivery(monkeypatch):
    monkeypatch.setattr(matches, "_explicit_email_consent", lambda *_: True)

    class C:
        def execute(self, statement, params=None):
            assert "FROM saved_searches" in str(statement)
            return Result(
                row={
                    "filters": {"unknown": "unsafe"},
                    "filter_schema_version": FILTER_SCHEMA_VERSION,
                }
            )

    assert not matches.allow_new_match_email(
        C(),
        user_id=str(uuid4()),
        hackathon_id=str(uuid4()),
        payload={"saved_search_id": str(uuid4())},
    )


def test_target_lock_is_only_used_by_delivery(monkeypatch):
    issued = []

    class C:
        def execute(self, stmt, params):
            issued.append(str(stmt))
            return Result(row=("matched",))

    args = {
        "hackathon_id": str(uuid4()),
        "raw_filters": {"free_only": True},
        "schema_version": FILTER_SCHEMA_VERSION,
    }
    assert matches._match_saved_search(C(), **args) is True
    assert "FOR SHARE OF h" not in issued[0]
    assert matches._match_saved_search(C(), lock_target=True, **args) is True
    assert "FOR SHARE OF h" in issued[1]


def test_consent_guard_rejects_deleted_or_missing_user_without_querying_preferences():
    class C:
        def __init__(self):
            self.calls = 0

        def execute(self, statement, params):
            self.calls += 1
            assert "FOR SHARE" in str(statement)
            return Result(scalar=None)

    conn = C()
    assert matches._explicit_email_consent(conn, str(uuid4())) is False
    assert conn.calls == 1


def test_api_consent_revocation_locks_user_before_writing_preference():
    from hackathon_api.repositories.personalization import PersonalizationRepository

    class C:
        def __init__(self):
            self.executed = []

        def execute(self, query, params=None):
            self.executed.append((str(query), params))
            return Result(scalar="user")

    connection = C()
    user_id = uuid4()
    PersonalizationRepository(connection).set_alert_preference(
        user_id,
        "EMAIL",
        "new_match",
        False,
    )
    assert len(connection.executed) == 2
    assert "FROM users WHERE id=:user_id FOR UPDATE" in connection.executed[0][0]
    assert "INSERT INTO alert_channel_preferences" in connection.executed[1][0]
    assert connection.executed[0][1]["user_id"] == user_id
    assert connection.executed[1][1]["enabled"] is False
