"""Saved-search matching must not deserialize PostgreSQL infinity timestamps."""

from hackathon_api.schemas import FILTER_SCHEMA_VERSION
from hackathon_worker.matches import _match_saved_search


class _Result:
    def first(self):
        return (1,)


class _Connection:
    sql = ""
    params = None

    def execute(self, statement, params):
        self.sql = str(statement)
        self.params = params
        return _Result()


def test_saved_search_projects_only_a_boolean_existence_marker():
    conn = _Connection()
    assert _match_saved_search(
        conn,
        hackathon_id="00000000-0000-0000-0000-000000000001",
        raw_filters={},
        schema_version=FILTER_SCHEMA_VERSION,
        lock_target=True,
    ) is True
    assert conn.sql.startswith("SELECT 1 FROM hackathons h ")
    assert "FOR SHARE OF h" in conn.sql
    assert " AS start_sort" not in conn.sql.split(" FROM hackathons h ")[0]
    assert ":limit" in conn.sql


def test_catalog_query_projects_nullable_order_sentinels():
    from hackathon_api.repositories.hackathons import build_hackathon_query
    from hackathon_api.schemas import HackathonFilters

    query = build_hackathon_query(HackathonFilters())
    assert "NULLIF(COALESCE(h.start_at,'infinity'::timestamptz),'infinity'::timestamptz) AS start_sort" in query.sql
    assert "NULLIF(COALESCE(h.registration_close_at,'infinity'::timestamptz),'infinity'::timestamptz) AS deadline_sort" in query.sql
    # The ordering expressions retain PostgreSQL infinity as the last-sort sentinel.
    assert "ORDER BY" in query.sql
