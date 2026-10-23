"""Disposable PostgreSQL E2E for exact multi-source and dynamic facets."""

from __future__ import annotations

import os
from uuid import uuid4

from hackathon_api.repositories.search_facets import HackathonFacetRepository
from hackathon_api.schemas import AdvancedHackathonFilters
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def verify() -> None:
    if os.environ.get("APP_ENV") != "test" or os.environ.get("HIP_ALLOW_FACETS_DB_DRILL") != "1":
        raise RuntimeError("explicit disposable test opt-in required")
    dsn = os.environ["DATABASE_URL"]
    parsed = make_url(dsn)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in {"127.0.0.1", "localhost"}
        or (parsed.database != "hackathon" and "_test" not in (parsed.database or ""))
    ):
        raise RuntimeError("requires disposable local PostgreSQL")

    tag = uuid4().hex[:12]
    engine = create_engine(dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                ids: list[str] = []
                for index, (event_format, is_free) in enumerate(
                    (("ONLINE", True), ("ONLINE", False), ("HYBRID", None))
                ):
                    ids.append(
                        conn.execute(
                            text("""
                                INSERT INTO hackathons
                                    (slug,name,normalized_name,format,is_free,edition_year)
                                VALUES (:slug,:name,:normalized,:format,:is_free,2027)
                                RETURNING id::text
                            """),
                            {
                                "slug": f"ci-facet-{tag}-{index}",
                                "name": f"CI Facet {tag} Edition {index}",
                                "normalized": f"ci facet {tag} edition {index}",
                                "format": event_format,
                                "is_free": is_free,
                            },
                        ).scalar_one()
                    )
                sources: list[str] = []
                for i in range(2):
                    sources.append(
                        conn.execute(
                            text("""
                                INSERT INTO sources
                                    (name,domain,source_family,source_type,ingestion_mode)
                                VALUES (:name,:domain,:family,'ci','API')
                                RETURNING id::text
                            """),
                            {
                                "name": f"Facet source {i}",
                                "domain": f"ci-facets-{tag}-{i}.invalid",
                                "family": f"ci-facets-{tag}-{i}",
                            },
                        ).scalar_one()
                    )
                ref_ids: list[str] = []
                # Edition 0 belongs to two different sources; edition 1 to
                # source 0; edition 2 is unattributed (unknown source).
                for index, source_index, suffix in (
                    (0, 0, "zero-a"),
                    (0, 1, "zero-b"),
                    (0, 0, "zero-duplicate-same-source"),
                    (1, 0, "one"),
                ):
                    ref_ids.append(
                        conn.execute(
                            text("""
                                INSERT INTO source_event_refs
                                    (source_id,hackathon_id,url,normalized_url)
                                VALUES (
                                    CAST(:source AS uuid),CAST(:event AS uuid),
                                    :url,:url
                                ) RETURNING id::text
                            """),
                            {
                                "source": sources[source_index],
                                "event": ids[index],
                                "url": f"https://ci-facets-{tag}.invalid/{suffix}",
                            },
                        ).scalar_one()
                    )
                observation_id = conn.execute(
                    text("""
                        INSERT INTO source_observations
                            (hackathon_id,source_event_ref_id,source_id,field_path,
                             value_json,normalized_value_json,extraction_method,
                             observed_at)
                        VALUES (
                            CAST(:event AS uuid),CAST(:ref AS uuid),
                            CAST(:source AS uuid),'participation_requirements',
                            '"Students are welcome"'::jsonb,
                            '"Students are welcome"'::jsonb,'API',now()
                        ) RETURNING id
                    """),
                    {"event": ids[0], "ref": ref_ids[0], "source": sources[0]},
                ).scalar_one()
                conn.execute(
                    text("""
                        INSERT INTO canonical_field_selections
                            (hackathon_id,field_path,selected_observation_id,policy_version)
                        VALUES (
                            CAST(:event AS uuid),'participation_requirements',
                            :observation,'ci-facets-v1'
                        )
                    """),
                    {"event": ids[0], "observation": observation_id},
                )

                filters = AdvancedHackathonFilters(
                    expression={"field": "name", "op": "contains", "value": f"CI Facet {tag}"},
                    limit=1,
                )
                report = HackathonFacetRepository(conn).summarize(
                    filters,
                    fields=[
                        "format",
                        "source_domain",
                        "attrs.participation_requirements",
                        "is_free",
                    ],
                )
                assert report["matched_total"] == 3, report
                assert report["counts_scope"] == "ALL_MATCHES_NOT_CURRENT_PAGE"

                def buckets(name: str) -> dict[str | None, int]:
                    return {bucket["value"]: bucket["count"] for bucket in report["facets"][name]}

                assert buckets("format") == {"ONLINE": 2, "HYBRID": 1}
                assert buckets("is_free") == {"true": 1, "false": 1, None: 1}
                assert buckets("attrs.participation_requirements") == {
                    "Students are welcome": 1,
                    None: 2,
                }
                assert buckets("source_domain") == {
                    f"ci-facets-{tag}-0.invalid": 2,
                    f"ci-facets-{tag}-1.invalid": 1,
                    None: 1,
                }
                # Multi-source membership legitimately totals > global
                # unique editions, but duplicate references don't inflate it.
                assert sum(buckets("source_domain").values()) == 4
                limited = HackathonFacetRepository(conn).summarize(
                    filters, fields=["source_domain"], bucket_limit=1
                )
                assert limited["matched_total"] == 3
                assert len(limited["facets"]["source_domain"]) == 1

                narrowed = HackathonFacetRepository(conn).summarize(
                    AdvancedHackathonFilters(
                        expression={
                            "and": [
                                filters.expression,
                                {"field": "is_free", "op": "eq", "value": True},
                            ]
                        }
                    ),
                    fields=["format", "source_domain"],
                )
                assert narrowed["matched_total"] == 1
                assert narrowed["facets"]["format"] == [{"value": "ONLINE", "count": 1}]
                print("PASS: full-query facets, typed evidence, unknowns and multi-source overlap")
            finally:
                tx.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
