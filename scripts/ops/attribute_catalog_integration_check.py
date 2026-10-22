"""Rollback-only PostgreSQL verification of curated attributes and provenance.

Synthetic fixtures; never fetches any real external source. Run only in test.
"""

from __future__ import annotations

import json
import os
from uuid import uuid4

from hackathon_api.attribute_catalog import load_attribute_fields, selected_attributes
from hackathon_api.repositories.hackathons import build_hackathon_query
from hackathon_api.schemas import AdvancedHackathonFilters
from sqlalchemy import create_engine, text


def verify() -> None:
    if os.getenv("APP_ENV") != "test" or os.getenv("HIP_ALLOW_ATTRIBUTES_DB_DRILL") != "1":
        raise RuntimeError("refusing attribute verification outside test environment")
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    unique = uuid4().hex
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                source_id = connection.execute(
                    text("""
                        INSERT INTO sources
                            (name, domain, source_family, source_type,
                             ingestion_mode, permission_status, active)
                        VALUES (:name,:domain,'ci-attributes','CI','API','ALLOWED',true)
                        RETURNING id
                    """),
                    {"name": "CI attribute source", "domain": f"{unique}.invalid"},
                ).scalar_one()
                event_ids = []
                for i in range(2):
                    event_ids.append(
                        connection.execute(
                            text("""
                                INSERT INTO hackathons (name,slug,normalized_name)
                                VALUES (:name,:slug,:normalized_name)
                                RETURNING id
                            """),
                            {
                                "name": f"CI Attribute Event {i}",
                                "slug": f"ci-attribute-{unique}-{i}",
                                "normalized_name": f"ci attribute event {i}",
                            },
                        ).scalar_one()
                    )
                ref_id = connection.execute(
                    text("""
                        INSERT INTO source_event_refs
                            (source_id,hackathon_id,url,normalized_url)
                        VALUES (:source,:event,:url,:url) RETURNING id
                    """),
                    {
                        "source": source_id,
                        "event": event_ids[0],
                        "url": f"https://{unique}.invalid/events/one",
                    },
                ).scalar_one()
                for key, value in (
                    ("language", "pt"),
                    ("capacity", 800),
                    ("source_event_status", "EventScheduled"),
                ):
                    observation_id = connection.execute(
                        text("""
                            INSERT INTO source_observations
                                (hackathon_id,source_event_ref_id,source_id,field_path,
                                 value_json,normalized_value_json,extraction_method,
                                 extraction_confidence,observed_at)
                            VALUES (:event,:ref,:source,:key,CAST(:value AS jsonb),
                                    CAST(:value AS jsonb),'JSON_LD',0.95,now())
                            RETURNING id
                        """),
                        {
                            "event": event_ids[0],
                            "ref": ref_id,
                            "source": source_id,
                            "key": key,
                            "value": json.dumps(value),
                        },
                    ).scalar_one()
                    connection.execute(
                        text("""
                            INSERT INTO canonical_field_selections
                                (hackathon_id,field_path,selected_observation_id,policy_version)
                            VALUES (:event,:field,:observation,'ci-attribute-v1')
                        """),
                        {
                            "event": event_ids[0],
                            "field": key,
                            "observation": observation_id,
                        },
                    )
                # A selected record with malformed numeric JSON remains unknown;
                # never cast untrusted text to numeric or throw an SQL exception.
                broken_observation = connection.execute(
                    text("""
                        INSERT INTO source_observations
                            (hackathon_id,field_path,value_json,normalized_value_json,
                             extraction_method,observed_at)
                        VALUES (:event,'capacity','"unlimited"'::jsonb,
                                '"unlimited"'::jsonb,'API',now())
                        RETURNING id
                    """),
                    {"event": event_ids[1]},
                ).scalar_one()
                connection.execute(
                    text("""
                        INSERT INTO canonical_field_selections
                            (hackathon_id,field_path,selected_observation_id,policy_version)
                        VALUES (:event,'capacity',:observation,'ci-attribute-v1')
                    """),
                    {"event": event_ids[1], "observation": broken_observation},
                )
                fields = load_attribute_fields(connection)
                assert {"attrs.language", "attrs.capacity", "attrs.source_event_status"} <= set(
                    fields
                )
                attrs = selected_attributes(connection, event_ids[0])
                assert {item["key"] for item in attrs} == {
                    "language",
                    "capacity",
                    "source_event_status",
                }
                assert next(item for item in attrs if item["key"] == "language")["source_url"] == (
                    f"https://{unique}.invalid/events/one"
                )
                assert selected_attributes(connection, event_ids[1]) == []

                cases = (
                    (
                        event_ids[0],
                        {
                            "and": [
                                {"field": "attrs.language", "op": "eq", "value": "pt"},
                                {"field": "attrs.capacity", "op": "gte", "value": 500},
                            ]
                        },
                        True,
                    ),
                    (event_ids[0], {"field": "attrs.capacity", "op": "lt", "value": 500}, False),
                    (event_ids[1], {"field": "attrs.capacity", "op": "is_unknown"}, True),
                    (event_ids[1], {"field": "attrs.capacity", "op": "gte", "value": 0}, False),
                    (
                        event_ids[1],
                        {"not": {"field": "attrs.capacity", "op": "eq", "value": 0}},
                        False,
                    ),
                )
                for event_id, expression, expected in cases:
                    filters = AdvancedHackathonFilters(expression=expression)
                    spec = build_hackathon_query(
                        filters, target_id=str(event_id), dynamic_fields=fields
                    )
                    result = connection.execute(text(spec.sql), spec.params).mappings().all()
                    assert (len(result) == 1) is expected, expression
                print("PASS: approved typed attributes, provenance, SQL and unknown semantics")
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
