"""W6 I13: prevent silently broken monitors and expose explicit email opt-in."""

from pathlib import Path

import pytest
from hackathon_api.main import app
from hackathon_api.repositories.personalization import PersonalizationRepository
from hackathon_api.schemas import FILTER_SCHEMA_VERSION, SavedSearchCreate
from pydantic import ValidationError


@pytest.mark.parametrize(
    "filters",
    [
        {"statuz": ["UPCOMING"]},
        {"status": "UPCOMING"},
        {"team_size": -2},
        {"limit": 100},
        {"cursor": "opaque"},
        {"sort": "deadline"},
        {"min_prize_usd": -5},
        {"registration_state": ["NOT_A_STATE"]},
        {"status": ["UPCOMNIG"]},
        {"event_type": ["not_an_event"]},
        {"format": ["OFFLINE"]},
        {"country_code": ["BRA"]},
        {"track": ["a"] * 51},
    ],
)
def test_invalid_monitor_filters_fail_when_saved(filters):
    with pytest.raises(ValidationError):
        SavedSearchCreate(name="My monitor", filters=filters)


def test_unsupported_saved_search_filter_version_is_rejected():
    with pytest.raises(ValidationError, match="unsupported saved-search"):
        SavedSearchCreate(name="Monitor", filters={}, filter_schema_version="future-v2")


def test_valid_filters_are_canonicalized_for_matching():
    payload = SavedSearchCreate(
        name="Monitor",
        filters={"country_code": ["br"], "eligible_country": "br", "free_only": True},
    )
    assert payload.filter_schema_version == FILTER_SCHEMA_VERSION
    assert payload.filters == {
        "country_code": ["BR"],
        "eligible_country": "BR",
        "free_only": True,
    }


def test_empty_filters_are_allowed_as_monitor_all():
    assert SavedSearchCreate(name="All", filters={}).filters == {}


def test_notification_preference_read_route_requires_auth():
    spec = app.openapi()["paths"]["/v1/me/alert-preferences"]
    assert "get" in spec and "put" in spec
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from hackathon_api.db import connection_dependency
    from hackathon_api.security import current_session

    def unauthenticated():
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[current_session] = unauthenticated
    app.dependency_overrides[connection_dependency] = lambda: object()
    try:
        response = TestClient(app).get("/v1/me/alert-preferences")
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(current_session, None)
        app.dependency_overrides.pop(connection_dependency, None)


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _Connection:
    def __init__(self, email_opt_in, in_app=None):
        self.email_opt_in = email_opt_in
        self.in_app = in_app
        self.queries = []

    def execute(self, statement, params):
        sql = str(statement)
        self.queries.append(sql)
        if "SELECT email::text FROM users" in sql:
            return _Scalar("user@example.test")
        if "channel='EMAIL'" in sql:
            return _Scalar(self.email_opt_in)
        if "SELECT enabled" in sql:
            return _Scalar(self.in_app)
        raise AssertionError(f"Unexpected query: {sql}")


def test_email_remains_off_without_explicit_consent():
    db = _Connection(email_opt_in=False)
    preferences = PersonalizationRepository(db).new_match_notification_preferences(
        "00000000-0000-0000-0000-000000000001"
    )
    assert preferences == {
        "alert_type": "new_match",
        "in_app_enabled": True,
        "email_enabled": False,
    }
    assert "deleted_at IS NULL" in db.queries[0]
    assert "NULLIF(email::text, '') IS NOT NULL" in db.queries[0]
    assert "channel='EMAIL'" in db.queries[1]
    assert "ORDER BY CASE WHEN alert_type=:alert_type" in db.queries[1]


def test_in_app_denial_and_explicit_email_enablement_are_independent():
    db = _Connection(email_opt_in=True, in_app=False)
    result = PersonalizationRepository(db).new_match_notification_preferences(
        "00000000-0000-0000-0000-000000000001"
    )
    assert result["email_enabled"] is True
    assert result["in_app_enabled"] is False


def test_frontend_cannot_silently_enable_email_without_pref_api():
    root = Path(__file__).resolve().parents[3]
    page = (root / "apps/web/app/saved-searches/page.tsx").read_text()
    component = (root / "apps/web/components/SavedSearchManager.tsx").read_text()
    assert "initialPreferences={preferences}" in page
    assert "if (busyChannel || !preferences) return" in component
    assert "alert_type: 'new_match'" in component
    assert "Email alerts (opt-in)" in component
    assert "cache: 'no-store'" in component


def test_authenticated_preferences_are_json_serialized_without_database(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from hackathon_api.db import connection_dependency
    from hackathon_api.security import current_session

    db = _Connection(email_opt_in=True, in_app=False)
    app.dependency_overrides[current_session] = lambda: SimpleNamespace(
        user=SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
    )
    app.dependency_overrides[connection_dependency] = lambda: db
    try:
        result = TestClient(app).get("/v1/me/alert-preferences")
        assert result.status_code == 200
        assert result.json() == {
            "alert_type": "new_match",
            "in_app_enabled": False,
            "email_enabled": True,
        }
    finally:
        app.dependency_overrides.pop(current_session, None)
        app.dependency_overrides.pop(connection_dependency, None)


def test_invalid_search_is_http_422_without_persisting():
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from hackathon_api.db import connection_dependency
    from hackathon_api.security import require_csrf

    class UnexpectedDatabase:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("invalid filter must not reach database")

    app.dependency_overrides[require_csrf] = lambda: SimpleNamespace(
        user=SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
    )
    app.dependency_overrides[connection_dependency] = lambda: UnexpectedDatabase()
    try:
        response = TestClient(app).post(
            "/v1/me/saved-searches",
            json={"name": "broken monitor", "filters": {"statuz": ["UPCOMING"]}},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(require_csrf, None)
        app.dependency_overrides.pop(connection_dependency, None)


def test_alert_inbox_uses_authenticated_csrf_read_endpoint():
    root = Path(__file__).resolve().parents[3]
    client = (root / "apps/web/components/AlertInbox.tsx").read_text()
    assert "credentials: 'include'" in client
    assert "'x-csrf-token': csrf()" in client
    assert "/read" in client
    assert "Mark as read" in client
    assert "response.ok" in client
