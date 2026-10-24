"""PostgreSQL drill: staging preflight validates migration and ALL source switches.

No network, DNS, HTTP fetcher, or staging DB is used. The fixture registers
temporary test-only source families in the disposable local CI database.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from hackathon_sources.registry import SourceRegistryRepository, load_source_definitions
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from scripts.ops.staging_campaign_dispatch import DispatchRefusal, preflight

ROOT = Path(__file__).resolve().parents[2]


def test_database() -> str:
    if (
        os.environ.get("APP_ENV") != "test"
        or os.environ.get("HIP_ALLOW_STAGING_PREFLIGHT_DRILL") != "1"
    ):
        raise RuntimeError("explicit local PostgreSQL test opt-in required")
    dsn = os.environ["DATABASE_URL"]
    parsed = make_url(dsn)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in {"localhost", "127.0.0.1"}
        or parsed.database not in {"hackathon", "hackathon_test"}
    ):
        raise RuntimeError("refusing non-disposable/non-local database")
    return dsn


def verify() -> None:
    tag = uuid4().hex[:12]
    sources = [
        source.model_copy(update={"source_family": f"ci-stage-{source.key}-{tag}"})
        for source in load_source_definitions(ROOT / "db/seeds/sources_v0_1.json")
        if source.key in {"dribdat_glamhack", "dribdat_imprs_astro"}
    ]
    assert len(sources) == 2
    engine = create_engine(test_database(), pool_pre_ping=True)
    inserted: list[str] = []
    try:
        with engine.begin() as conn:
            registry = SourceRegistryRepository(conn)
            for source in sources:
                registry.upsert(source)
                inserted.append(registry.get_source_id(source))

        receipt = preflight(engine, sources, make_url(test_database()).database)
        assert receipt == {
            "database_identity_verified": True,
            "schema_current": True,
            "source_permissions_checked": 2,
        }

        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE sources SET permission_status='RESTRICTED' WHERE id=CAST(:id AS uuid)"
                ),
                {"id": inserted[1]},
            )
        try:
            preflight(engine, sources, make_url(test_database()).database)
            raise AssertionError("preflight allowed one RESTRICTED source")
        except DispatchRefusal as exc:
            assert exc.code == "SOURCE_NOT_AUTHORIZED_IN_STAGING"

        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE sources SET permission_status='ALLOWED',active=false WHERE id=CAST(:id AS uuid)"
                ),
                {"id": inserted[1]},
            )
        try:
            preflight(engine, sources, make_url(test_database()).database)
            raise AssertionError("preflight allowed paused source")
        except DispatchRefusal as exc:
            assert exc.code == "SOURCE_NOT_AUTHORIZED_IN_STAGING"

        try:
            preflight(engine, sources, "unexpected_database")
            raise AssertionError("preflight ignored database identity")
        except DispatchRefusal as exc:
            assert exc.code == "DATABASE_SERVER_IDENTITY_MISMATCH"

        print(
            "PASS: PostgreSQL schema-current + all-sources staging preflight, permission revocation and pause"
        )
    finally:
        if inserted:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM sources WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": inserted},
                )
        engine.dispose()


if __name__ == "__main__":
    verify()
