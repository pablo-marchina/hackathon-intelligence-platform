"""Rollback-only PostgreSQL E2E for per-source canonical exclusivity and gaps."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hackathon_api.repositories.source_opportunities import SourceOpportunityRepository
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def verify() -> None:
    if (
        os.environ.get("APP_ENV") != "test"
        or os.environ.get("HIP_ALLOW_SOURCE_OPPORTUNITIES_DRILL") != "1"
    ):
        raise RuntimeError("explicit test-only opt-in required")
    database_url = os.environ["DATABASE_URL"]
    parsed = make_url(database_url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in ("127.0.0.1", "localhost")
        or (parsed.database != "hackathon" and "_test" not in (parsed.database or ""))
    ):
        raise RuntimeError("local disposable PostgreSQL required")

    tag = uuid4().hex[:12]
    now = datetime.now(UTC)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                baseline = SourceOpportunityRepository(conn).snapshot(as_of=now, days=30)
                source_ids: dict[str, str] = {}
                for key, permission in (
                    ("approved", "ALLOWED"),
                    ("review", "REVIEW_REQUIRED"),
                    ("empty", "ALLOWED"),
                ):
                    source_ids[key] = conn.execute(
                        text("""
                            INSERT INTO sources (
                                name, domain, source_family, source_type,
                                ingestion_mode, permission_status, active,
                                discovery_enabled, detail_extraction_enabled
                            ) VALUES (
                                :name,:domain,:family,'ci','API',:permission,true,true,true
                            ) RETURNING id::text
                        """),
                        {
                            "name": f"CI {key} {tag}",
                            "domain": f"ci-{key}-{tag}.invalid",
                            "family": f"ci-opportunity-{tag}",
                            "permission": permission,
                        },
                    ).scalar_one()

                events: dict[str, str] = {}
                for label, details in (
                    ("exclusive", True),
                    ("shared", False),
                    ("review_only", False),
                    ("unattributed", False),
                ):
                    events[label] = conn.execute(
                        text("""
                            INSERT INTO hackathons (
                                name, slug, normalized_name, format,
                                registration_close_at, is_free
                            ) VALUES (
                                :name,:slug,:normalized,'ONLINE',:deadline,:free
                            ) RETURNING id::text
                        """),
                        {
                            "name": f"CI {label} Hackathon",
                            "slug": f"ci-opportunity-{tag}-{label}",
                            "normalized": f"ci {label} hackathon",
                            "deadline": now + timedelta(days=5) if details else None,
                            "free": True if details else None,
                        },
                    ).scalar_one()

                refs: dict[str, str] = {}
                for source, event, name in (
                    ("approved", "exclusive", "exclusive"),
                    ("approved", "exclusive", "duplicate_same_source"),
                    ("approved", "shared", "shared_a"),
                    ("review", "shared", "shared_b"),
                    ("review", "review_only", "review_only"),
                ):
                    ref_id = conn.execute(
                        text("""
                            INSERT INTO source_event_refs (
                                source_id,hackathon_id,url,normalized_url
                            ) VALUES (
                                CAST(:source AS uuid),CAST(:event AS uuid),:url,:url
                            ) RETURNING id::text
                        """),
                        {
                            "source": source_ids[source],
                            "event": events[event],
                            "url": f"https://ci-{tag}.invalid/{name}",
                        },
                    ).scalar_one()
                    refs[name] = ref_id

                # An inactive reference must not increase the number of source
                # providers or suppress exclusivity.
                conn.execute(
                    text("""
                        UPDATE source_event_refs SET active=false
                         WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": refs["duplicate_same_source"]},
                )
                for source, ref, outcome, event, age in (
                    ("approved", "exclusive", "CREATED", "exclusive", 1),
                    ("review", "shared_b", "LINKED", "shared", 2),
                    ("review", "review_only", "CREATED", "review_only", 120),
                ):
                    conn.execute(
                        text("""
                            INSERT INTO canonical_processing_outcomes (
                                source_event_ref_id,source_id,content_hash,
                                outcome,hackathon_id,processed_at
                            ) VALUES (
                                CAST(:ref AS uuid),CAST(:source AS uuid),
                                :hash,:outcome,CAST(:event AS uuid),:processed
                            )
                        """),
                        {
                            "ref": refs[ref],
                            "source": source_ids[source],
                            "hash": uuid4().hex + uuid4().hex,
                            "outcome": outcome,
                            "event": events[event],
                            "processed": now - timedelta(days=age),
                        },
                    )

                snapshot = SourceOpportunityRepository(conn).snapshot(as_of=now, days=30)
                global_counts = snapshot["global"]
                previous = baseline["global"]
                assert (
                    global_counts["total_active_canonicals"] - previous["total_active_canonicals"]
                    == 4
                )
                assert (
                    global_counts["canonicals_with_active_source"]
                    - previous["canonicals_with_active_source"]
                    == 3
                )
                assert (
                    global_counts["exclusive_to_one_source"] - previous["exclusive_to_one_source"]
                    == 2
                )
                assert (
                    global_counts["shared_across_sources"] - previous["shared_across_sources"] == 1
                )
                assert (
                    global_counts["without_active_source"] - previous["without_active_source"] == 1
                )

                rows = {row["source_id"]: row for row in snapshot["sources"]}
                a, b, empty = (rows[source_ids[key]] for key in ("approved", "review", "empty"))
                assert a["attributed_unique_active"] == 2
                assert a["exclusive_canonicals"] == 1
                assert a["shared_canonicals"] == 1
                assert a["exclusive_percent"] == 50.0
                assert a["missing_registration_deadline"] == 1
                assert a["unknown_cost"] == 1
                assert a["known_cost_percent"] == 50.0
                assert a["recent_created"] == 1
                assert a["recent_linked"] == 0
                assert a["eligible_for_controlled_collection"] is True
                assert b["attributed_unique_active"] == 2
                assert b["exclusive_canonicals"] == 1
                assert b["shared_canonicals"] == 1
                assert b["recent_created"] == 0  # Historical import outside the window.
                assert b["recent_linked"] == 1
                assert b["eligible_for_controlled_collection"] is False
                assert b["suggested_next_step"] == "REVIEW_PERMISSIONS_OR_PAUSE"
                assert empty["attributed_unique_active"] == 0
                assert empty["exclusive_percent"] is None
                assert empty["suggested_next_step"] == "VALIDATE_WITH_CONTROLLED_COLLECTION"

                # Publisher-provided award text counts as known award details,
                # but does not become a fictional monetary prize pool.
                observation_id = conn.execute(
                    text("""
                        INSERT INTO source_observations (
                            hackathon_id, source_event_ref_id, source_id,
                            field_path, value_json, normalized_value_json,
                            extraction_method, observed_at
                        ) VALUES (
                            CAST(:event AS uuid), CAST(:ref AS uuid),
                            CAST(:source AS uuid), 'prize_details_text',
                            CAST(:value AS jsonb), CAST(:value AS jsonb),
                            'JSON_LD', :observed
                        ) RETURNING id
                    """),
                    {
                        "event": events["exclusive"],
                        "ref": refs["exclusive"],
                        "source": source_ids["approved"],
                        "value": '"Community award"',
                        "observed": now,
                    },
                ).scalar_one()
                conn.execute(
                    text("""
                        INSERT INTO canonical_field_selections (
                            hackathon_id, field_path, selected_observation_id,
                            policy_version, active
                        ) VALUES (
                            CAST(:event AS uuid), 'prize_details_text',
                            :observation, 'ci-awards-v1', true
                        )
                    """),
                    {"event": events["exclusive"], "observation": observation_id},
                )
                updated = SourceOpportunityRepository(conn).snapshot(as_of=now)
                approved_after = next(
                    row for row in updated["sources"]
                    if row["source_id"] == source_ids["approved"]
                )
                assert approved_after["missing_prize_details"] == a["missing_prize_details"] - 1
                assert updated["global"] == snapshot["global"]
                print(
                    "PASS: distinct source contribution, overlap, gaps and permission-aware triage"
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
