"""Rollback-only PostgreSQL E2E: country allow/deny and unknown semantics.

Uses synthetic hackathons in a disposable database; no HTTP or real users.
"""
from __future__ import annotations

import os
from uuid import uuid4

from hackathon_api.advanced_filters import compile_expression
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def verify() -> None:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("HIP_ALLOW_ELIGIBILITY_DRILL") != "1":
        raise RuntimeError("explicit disposable test environment required")
    dsn = os.environ["DATABASE_URL"]
    info = make_url(dsn)
    if (
        info.get_backend_name() != "postgresql"
        or info.host not in ("localhost", "127.0.0.1")
        or (info.database != "hackathon" and "_test" not in (info.database or ""))
    ):
        raise RuntimeError("refusing non-local or non-disposable database")

    suffix = uuid4().hex[:14]
    engine = create_engine(dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            txn = conn.begin()
            try:
                ids = {}
                for name, worldwide in [
                    ("allowed", False),
                    ("denied", False),
                    ("worldwide", True),
                    ("unknown", None),
                ]:
                    ids[name] = conn.execute(
                        text("""
                            INSERT INTO hackathons (
                                slug,name,normalized_name,eligible_worldwide
                            ) VALUES (:slug,:name,:normalized,:worldwide)
                            RETURNING id::text
                        """),
                        {
                            "slug": f"ci-country-{suffix}-{name}",
                            "name": f"CI Country {name}",
                            "normalized": f"ci country {name}",
                            "worldwide": worldwide,
                        },
                    ).scalar_one()
                for name, code, allowed in [
                    ("allowed", "BR", True),
                    ("denied", "BR", False),
                    ("denied", "US", True),
                ]:
                    conn.execute(
                        text("""
                            INSERT INTO eligibility_countries(hackathon_id,country_code,allowed)
                            VALUES (CAST(:id AS uuid),:code,:allowed)
                        """),
                        {"id": ids[name], "code": code, "allowed": allowed},
                    )

                def matches(name: str, expression: dict) -> bool:
                    params: dict = {"id": ids[name]}
                    predicate = compile_expression(expression, params)
                    result = conn.execute(
                        text(
                            "SELECT 1 FROM hackathons h "
                            "WHERE h.id=CAST(:id AS uuid) AND " + predicate
                        ),
                        params,
                    ).scalar_one_or_none()
                    return result is not None

                br = {"field": "eligible_country", "op": "eq", "value": "BR"}
                br_or_ar = {
                    "field": "eligible_country", "op": "in", "value": ["BR", "AR"]
                }
                known = {"field": "eligible_country", "op": "is_known"}
                unknown = {"field": "eligible_country", "op": "is_unknown"}
                assert matches("allowed", br) is True
                assert matches("denied", br) is False
                assert matches("denied", br_or_ar) is False
                assert matches("denied", {
                    "field": "eligible_country", "op": "eq", "value": "US"
                }) is True
                assert matches("worldwide", br) is True
                assert matches("unknown", br) is False
                assert matches("allowed", known) is True
                assert matches("denied", known) is True  # US explicitly allowed.
                assert matches("worldwide", known) is True
                assert matches("unknown", known) is False
                assert matches("unknown", unknown) is True
                assert matches("denied", unknown) is False
                # Explicit false worldwide with only a denied country is still
                # not equivalent to 'BR eligible', regardless of the record.
                conn.execute(
                    text("""
                        UPDATE eligibility_countries SET allowed=false
                         WHERE hackathon_id=CAST(:id AS uuid) AND country_code='US'
                    """),
                    {"id": ids["denied"]},
                )
                assert matches("denied", known) is False
                assert matches("denied", unknown) is True
                assert matches("denied", {"not": br}) is True
                # Missing worldwide plus no country is unknown, not known
                # negative eligibility for BR: NOT(SQL UNKNOWN) stays UNKNOWN.
                assert matches("unknown", {"not": br}) is False
                print("PASS country eligibility: allowed, denied, worldwide, unknown and NOT")
            finally:
                txn.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
