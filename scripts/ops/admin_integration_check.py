"""PostgreSQL-backed W6 admin RBAC/audit/pause acceptance check.

CI runs this after Alembic upgrade/downgrade/upgrade. Requires an isolated DB.
Never run against production: it inserts transient authentication and source fixtures.
"""
from __future__ import annotations

import os
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from hackathon_api.auth import hash_secret
from hackathon_api.db import connection_dependency
from hackathon_api.main import app
from hackathon_shared.settings import get_settings
from hackathon_sources.registry import SourceRegistryRepository


def verify() -> None:
    database_url = os.environ["DATABASE_URL"]
    if os.getenv("APP_ENV", "test") == "production":
        raise RuntimeError("refusing to run admin integration check against production")
    if os.getenv("HIP_ALLOW_ADMIN_DB_DRILL") != "1":
        raise RuntimeError("set HIP_ALLOW_ADMIN_DB_DRILL=1 only in an isolated CI database")

    key = "ci-admin-" + uuid4().hex
    token, csrf = "ci-session-" + uuid4().hex, "ci-csrf-" + uuid4().hex
    os.environ["ADMIN_API_KEY"] = key
    get_settings.cache_clear()
    engine = create_engine(database_url, pool_pre_ping=True)
    user_id = source_id = review_id = None

    def db():
        with engine.begin() as conn:
            yield conn

    try:
        with engine.begin() as conn:
            user_id = str(conn.execute(text("INSERT INTO users(display_name) VALUES ('W6 CI tester') RETURNING id::text")).scalar_one())
            source_id = str(conn.execute(text("""
                INSERT INTO sources(name,domain,source_family,ingestion_mode)
                VALUES ('W6 CI source',:domain,'w6-ci','API') RETURNING id::text
            """), {"domain": f"ci-{uuid4().hex}.test"}).scalar_one())
            review_id = str(conn.execute(text("""
                INSERT INTO review_queue_items(queue_type,entity_type,entity_id)
                VALUES ('W6_CI','source',:id) RETURNING id::text
            """), {"id": source_id}).scalar_one())
            conn.execute(text("INSERT INTO admin_user_roles(user_id,role) VALUES (CAST(:u AS uuid),'VIEWER')"), {"u": user_id})
            conn.execute(text("""
                INSERT INTO auth_sessions(user_id,provider,token_hash,csrf_hash,expires_at)
                VALUES (CAST(:u AS uuid),'GOOGLE',:t,:c,now() + interval '1 hour')
            """), {"u": user_id, "t": hash_secret(token), "c": hash_secret(csrf)})

        app.dependency_overrides[connection_dependency] = db
        client = TestClient(app)
        client.cookies.set(get_settings().session_cookie_name, token)
        client.cookies.set(get_settings().csrf_cookie_name, csrf)
        source_url = f"/admin/sources/{source_id}/active"
        review_url = f"/admin/review-queue/{review_id}"
        headers = {"x-csrf-token": csrf}
        pause_body = {"active": False, "reason": "paused by integration test"}

        assert client.get("/admin/source-health").status_code == 200
        assert client.patch(source_url, json=pause_body, headers=headers).status_code == 403  # VIEWER
        with engine.begin() as conn:
            conn.execute(text("UPDATE admin_user_roles SET role='OPERATOR' WHERE user_id=CAST(:id AS uuid)"), {"id": user_id})
        assert client.patch(source_url, json=pause_body).status_code == 403  # no CSRF
        pause = client.patch(source_url, json=pause_body, headers=headers)
        assert pause.status_code == 200, pause.text
        assert pause.json()["changed"] is True
        with engine.begin() as conn:
            assert SourceRegistryRepository(conn).is_active(source_id) is False

        for state in ("IN_REVIEW", "RESOLVED"):
            response = client.patch(review_url, json={"state": state, "reason": "CI test review transition"}, headers=headers)
            assert response.status_code == 200, response.text
        assert client.patch(review_url, json={"state": "DISMISSED", "reason": "illegal transition"}, headers=headers).status_code == 409
        assert client.post("/admin/search/reindex", json={"reason": "CI tests indexing"}, headers=headers).status_code == 403
        with engine.begin() as conn:
            conn.execute(text("UPDATE admin_user_roles SET role='ADMIN' WHERE user_id=CAST(:id AS uuid)"), {"id": user_id})
        reindex = client.post("/admin/search/reindex", json={"reason": "CI tests indexing"}, headers=headers)
        assert reindex.status_code == 200, reindex.text
        resume = client.patch(source_url, json={"active": True, "reason": "CI tests resume"}, headers={"x-admin-key": key})
        assert resume.status_code == 200 and resume.json()["active"] is True
        with engine.begin() as conn:
            assert SourceRegistryRepository(conn).is_active(source_id) is True
            rows = conn.execute(text("""
                SELECT action, actor_kind, actor_user_id::text AS actor_user_id
                  FROM admin_audit_log WHERE entity_id = :id ORDER BY id
            """), {"id": source_id}).mappings().all()
            assert [(r["action"], r["actor_kind"]) for r in rows] == [
                ("SOURCE_PAUSE", "USER_SESSION"), ("SOURCE_RESUME", "API_KEY")
            ]
            assert rows[0]["actor_user_id"] == user_id
            assert rows[1]["actor_user_id"] is None
    finally:
        app.dependency_overrides.clear()
        with engine.begin() as conn:
            if source_id:
                conn.execute(text("DELETE FROM admin_audit_log WHERE entity_id = :id"), {"id": source_id})
                conn.execute(text("DELETE FROM review_queue_items WHERE entity_id = :id AND queue_type = 'W6_CI'"), {"id": source_id})
                conn.execute(text("DELETE FROM sources WHERE id = CAST(:id AS uuid)"), {"id": source_id})
            if user_id:
                conn.execute(text("DELETE FROM admin_audit_log WHERE actor_user_id = CAST(:id AS uuid)"), {"id": user_id})
                conn.execute(text("DELETE FROM users WHERE id = CAST(:id AS uuid)"), {"id": user_id})
        engine.dispose()
        get_settings.cache_clear()
    print("W6 PostgreSQL admin RBAC, audit, source pause/resume and review integration: PASS")


if __name__ == "__main__":
    verify()
