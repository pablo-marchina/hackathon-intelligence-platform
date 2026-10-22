"""Real PostgreSQL handoff -> canonical -> provenance, rollback-only test.

No HTTP requests, email or persistent fixtures. Requires an explicit test DB.
"""

from __future__ import annotations

import os
from uuid import uuid4

from hackathon_intelligence.incremental import CanonicalProcessor
from hackathon_sources.repositories import SourceEventRefRepository, SourcePayloadRepository
from sqlalchemy import create_engine, text

from scripts.ops.canonical_evidence import multisource_receipt, write_ci_receipt


def verify() -> None:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("HIP_ALLOW_CANONICAL_DB_DRILL") != "1":
        raise RuntimeError(
            "refusing incremental normalization test outside an explicit test database"
        )
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    unique = uuid4().hex
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                sources = {}
                for label, status in (
                    ("a", "ALLOWED"),
                    ("b", "ALLOWED"),
                    ("restricted", "REVIEW_REQUIRED"),
                ):
                    sources[label] = connection.execute(
                        text("""
                        INSERT INTO sources (name, domain, source_family, source_type,
                                             ingestion_mode, permission_status, active)
                        VALUES (:name, :domain, :family, 'CI', 'API', :status, true)
                        RETURNING id::text
                    """),
                        {
                            "name": f"Canonical CI {label}",
                            "domain": f"{unique}-{label}.invalid",
                            "family": f"ci-canonical-{unique}",
                            "status": status,
                        },
                    ).scalar_one()
                runs = {
                    label: connection.execute(
                        text("""
                    INSERT INTO crawl_runs (source_id) VALUES (CAST(:source_id AS uuid))
                    RETURNING id::text
                """),
                        {"source_id": source_id},
                    ).scalar_one()
                    for label, source_id in sources.items()
                }
                refs: dict[str, str] = {}
                stage = SourcePayloadRepository(connection)
                references = SourceEventRefRepository(connection)
                for source, year in (("a", 2027), ("b", 2027), ("b", 2028), ("restricted", 2029)):
                    tag = f"{source}-{year}"
                    source_url = f"https://campus.example/{unique}/{tag}"
                    refs[tag] = references.upsert(
                        source_id=sources[source],
                        raw_document_id=None,
                        source_event_id=tag,
                        source_url=source_url,
                        official_url=f"https://campus.example/{unique}/editions",
                        edition_start=f"{year}-04-01T12:00:00Z",
                    )
                    stage.stage(
                        ref_id=refs[tag],
                        source_id=sources[source],
                        source_key=f"ci_{source}",
                        raw_document_id=None,
                        crawl_run_id=runs[source],
                        payload={
                            "name": "Campus Hackathon",
                            "source_event_id": tag,
                            "source_url": source_url,
                            "official_url": f"https://campus.example/{unique}/editions",
                            "starts_at_raw": f"{year}-04-01T12:00:00Z",
                            "ends_at_raw": f"{year}-04-02T12:00:00Z",
                            "city": "Campinas",
                            "country": "Brazil",
                            "metadata": {
                                "extraction_method": "schema_org_json_ld",
                                "is_accessible_for_free": False,
                                "ticket_price": "50.00",
                                "ticket_currency": "BRL",
                                "organizers": ["Campus University"],
                            },
                        },
                    )
                processor = CanonicalProcessor(connection)
                outcomes = [processor.process_one() for _ in range(3)]
                assert outcomes.count("CREATED") == 2 and outcomes.count("UPDATED") == 1, outcomes
                assert processor.process_one() is None  # restricted source remains unprocessed
                before = connection.execute(
                    text("""
                    SELECT outcome, COUNT(*) FROM canonical_processing_outcomes
                     WHERE crawl_run_id IN (CAST(:a AS uuid), CAST(:b AS uuid))
                     GROUP BY outcome
                """),
                    {"a": runs["a"], "b": runs["b"]},
                ).all()
                assert dict(before) == {"CREATED": 2, "LINKED": 1}, before
                restricted = connection.execute(
                    text("""
                    SELECT COUNT(*) FROM canonical_processing_outcomes
                     WHERE source_id=CAST(:sid AS uuid)
                """),
                    {"sid": sources["restricted"]},
                ).scalar_one()
                assert restricted == 0
                ids = {
                    k: connection.execute(
                        text("""
                    SELECT hackathon_id::text FROM source_event_refs WHERE id=CAST(:id AS uuid)
                """),
                        {"id": v},
                    ).scalar_one_or_none()
                    for k, v in refs.items()
                }
                assert ids["a-2027"] == ids["b-2027"]
                assert ids["a-2027"] != ids["b-2028"]
                assert ids["restricted-2029"] is None
                counted = (
                    connection.execute(
                        text("""
                    SELECT source_count, participation_fee::text, is_free,
                           primary_location_id IS NOT NULL AS has_location
                    FROM hackathons WHERE id=CAST(:id AS uuid)
                """),
                        {"id": ids["a-2027"]},
                    )
                    .mappings()
                    .one()
                )
                assert counted["source_count"] == 2 and counted["is_free"] is False
                assert counted["participation_fee"] in {"50.00", "50.0", "50"}
                assert counted["has_location"]
                count_obs = connection.execute(
                    text("""
                    SELECT COUNT(*) FROM source_observations
                     WHERE hackathon_id=CAST(:id AS uuid)
                       AND field_path='organizers' AND extraction_method='JSON_LD'
                """),
                    {"id": ids["a-2027"]},
                ).scalar_one()
                assert count_obs == 2
                # A changed snapshot of source a is reprocessed exactly once.
                stage.stage(
                    ref_id=refs["a-2027"],
                    source_id=sources["a"],
                    source_key="ci_a",
                    raw_document_id=None,
                    crawl_run_id=runs["a"],
                    payload={
                        "name": "Campus Hackathon",
                        "source_event_id": "a-2027",
                        "source_url": f"https://campus.example/{unique}/a-2027",
                        "official_url": f"https://campus.example/{unique}/editions",
                        "starts_at_raw": "2027-04-01T12:00:00Z",
                        "ends_at_raw": "2027-04-02T12:00:00Z",
                        "metadata": {
                            "extraction_method": "schema_org_json_ld",
                            "ticket_price": "25.00",
                            "ticket_currency": "BRL",
                        },
                    },
                )
                assert processor.process_one() == "UPDATED"
                assert processor.process_one() is None
                from hackathon_api.repositories.canonical_yield import CanonicalYieldRepository

                yield_snapshot = CanonicalYieldRepository(connection).snapshot()
                source_totals = {entry["source_id"]: entry for entry in yield_snapshot["sources"]}
                assert source_totals[sources["a"]]["created"] == 1
                assert source_totals[sources["a"]]["updated"] == 1
                assert source_totals[sources["b"]]["created"] == 1
                assert source_totals[sources["b"]]["linked"] == 1
                assert source_totals[sources["restricted"]]["processed_versions"] == 0
                assert yield_snapshot["totals"]["unattributed"] == 0
                # Exactly two new canonicals, not three references.
                grouped = {entry["crawl_run_id"]: entry for entry in yield_snapshot["runs"]}
                assert grouped[runs["a"]]["created"] == 1
                assert grouped[runs["b"]]["linked"] == 1
                rejected_ref = references.upsert(
                    source_id=sources["a"],
                    raw_document_id=None,
                    source_event_id="non-hackathon",
                    source_url=f"https://campus.example/{unique}/summit",
                    official_url=None,
                )
                stage.stage(
                    ref_id=rejected_ref,
                    source_id=sources["a"],
                    source_key="ci_a",
                    raw_document_id=None,
                    crawl_run_id=runs["a"],
                    payload={
                        "name": "University Summit",
                        "source_url": f"https://campus.example/{unique}/summit",
                    },
                )
                assert processor.process_one() == "REJECTED"
                assert processor.process_one() is None
                rejected_row = connection.execute(
                    text("""
                    SELECT outcome, hackathon_id FROM canonical_processing_outcomes
                     WHERE source_event_ref_id=CAST(:ref AS uuid)
                """),
                    {"ref": rejected_ref},
                ).one()
                assert rejected_row[0] == "REJECTED" and rejected_row[1] is None
                yield_after_rejection = CanonicalYieldRepository(connection).snapshot()
                assert yield_after_rejection["totals"]["rejected"] >= 1
                assert yield_after_rejection["totals"]["created"] >= 2
                write_ci_receipt(
                    multisource_receipt(yield_after_rejection, sources),
                    name="multisource_canonical_yield",
                )
                fee = connection.execute(
                    text("""
                    SELECT participation_fee::text FROM hackathons WHERE id=CAST(:id AS uuid)
                """),
                    {"id": ids["a-2027"]},
                ).scalar_one()
                assert fee in {"25.00", "25.0", "25"}
                active_fee_selections = connection.execute(
                    text("""
                    SELECT COUNT(*) FROM canonical_field_selections
                     WHERE hackathon_id=CAST(:id AS uuid) AND field_path='participation_fee' AND active
                """),
                    {"id": ids["a-2027"]},
                ).scalar_one()
                assert active_fee_selections == 1
                print(
                    "Canonical E2E: staging, policy, two sources, editions, provenance, idempotency PASS"
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
