"""No new permissions are granted by normalization; dates and fees stay factual."""

import json
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from hackathon_intelligence.incremental import (
    event_kind,
    normalize_format,
    parse_date,
    prepare_event,
)
from hackathon_sources.repositories import SourcePayloadRepository


def example(**updates):
    payload = {
        "name": "Campus Hackathon 2027",
        "source_key": "campus_feed",
        "source_event_id": "campus-2027",
        "official_url": "https://campus.example/hackathon",
        "source_url": "https://campus.example/feed",
        "starts_at_raw": "2027-04-01T09:00:00-03:00",
        "ends_at_raw": "2027-04-03T18:00:00-03:00",
        "city": "São Paulo",
        "country": "Brazil",
        "metadata": {
            "extraction_method": "schema_org_json_ld",
            "is_accessible_for_free": False,
            "ticket_price": "50.00",
            "ticket_currency": "BRL",
            "organizers": ["Campus"],
            "registration_valid_through": "2027-03-10T23:59:00-03:00",
        },
    }
    payload.update(updates)
    return payload


def test_projection_preserves_identity_dates_currency_consent():
    event = prepare_event(example())
    assert event is not None
    assert event.name_key == "campus hackathon 2027"
    assert event.start_at.isoformat() == "2027-04-01T09:00:00-03:00"
    assert event.fields["participation_fee"][1] == "50.00"
    assert event.fields["is_free"][1] is False
    assert event.fields["participation_fee_currency"][1] == "BRL"
    assert event.fields["organizers"][1] == ["Campus"]
    assert "registration_close_at" not in event.fields
    assert event.fields["offer_valid_through"][1] == "2027-03-11T02:59:00+00:00"


def test_unknown_prices_deadlines_and_format_remain_unknown():
    event = prepare_event(
        example(
            metadata={"extraction_method": "schema_org_json_ld", "ticket_price": "free!"},
            format_raw="",
        )
    )
    assert event is not None
    assert "participation_fee" not in event.fields
    assert "is_free" not in event.fields
    assert "registration_close_at" not in event.fields
    assert event.event_format == "UNKNOWN"
    assert "format" not in event.fields


@pytest.mark.parametrize(
    "name", ["Industry Conference", "Hack Club office hours", "Webinar and workshop", ""]
)
def test_unrelated_events_do_not_enter_canonical_catalog(name):
    assert prepare_event(example(name=name)) is None


def test_dates_with_unknown_timezone_are_not_assumed_to_be_utc():
    assert parse_date("2027-10-15T09:00:00") is None
    assert parse_date("2027-10-15") == datetime(2027, 10, 15, tzinfo=UTC)
    assert parse_date("not a date") is None


def test_formats_and_event_kinds():
    assert normalize_format("presencial") == "IN_PERSON"
    assert normalize_format("Hybrid online + in-person") == "HYBRID"
    assert event_kind("Global Game Jam", []) == "game_jam"
    assert event_kind("Conference", []) is None


def test_payload_staging_is_idempotent_by_event_hash_without_credentials():
    connection = Mock()
    repo = SourcePayloadRepository(connection)
    values = example()
    args = dict(
        ref_id="00000000-0000-4000-8000-000000000111",
        source_id="00000000-0000-4000-8000-000000000222",
        source_key="campus_feed",
        raw_document_id=None,
        payload=values,
    )
    repo.stage(**args)
    repo.stage(**args)
    assert connection.execute.call_count == 2
    first = connection.execute.call_args_list[0].args[1]
    second = connection.execute.call_args_list[1].args[1]
    assert first["digest"] == second["digest"]
    assert json.loads(first["payload"])["metadata"]["ticket_price"] == "50.00"
    repo.stage(
        **{
            **args,
            "payload": example(
                metadata={"extraction_method": "schema_org_json_ld", "ticket_price": "10.00"}
            ),
        }
    )
    changed = connection.execute.call_args_list[-1].args[1]
    assert changed["digest"] != first["digest"]
    sql = str(connection.execute.call_args_list[0].args[0])
    assert "source_event_payloads.content_hash IS DISTINCT FROM EXCLUDED.content_hash" in sql


def test_normalizer_never_opens_network_and_keeps_runtime_opt_in():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    processor = (
        root / "packages/intelligence/src/hackathon_intelligence/incremental.py"
    ).read_text()
    runner = (root / "scripts/run_incremental_canonical.py").read_text()
    assert "SKIP LOCKED" in processor and "FOR SHARE" in processor
    assert "pg_advisory_xact_lock" in processor
    assert "--apply" in runner
    assert "requests.get" not in processor and "httpx" not in processor


def test_do_not_automerge_missing_start_date_or_similar_title():
    from pathlib import Path

    processor = (
        Path(__file__).resolve().parents[1] / "src/hackathon_intelligence/incremental.py"
    ).read_text()
    assert "event.start_at" in processor
    assert "normalized_name=:name" in processor
    assert "LIMIT 2" in processor


def test_source_ref_distinguishes_editions_sharing_official_url():
    from hackathon_sources.repositories import SourceEventRefRepository

    class ScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return None

        def scalar_one(self):
            return self.value

    class RecordingConnection:
        def __init__(self):
            self.queries = []

        def execute(self, stmt, params):
            self.queries.append((str(stmt), params))
            return ScalarResult("00000000-0000-4000-8000-000000000042")

    connection = RecordingConnection()
    repository = SourceEventRefRepository(connection)
    base = dict(
        source_id="00000000-0000-4000-8000-000000000101",
        raw_document_id=None,
        source_url="https://organizer.example/events",
        official_url="https://organizer.example/hackathon",
    )
    repository.upsert(**base, source_event_id="edition-2027", edition_start="2027-03-01T10:00:00Z")
    one = connection.queries[-1][1]["normalized_url"]
    repository.upsert(**base, source_event_id="edition-2028", edition_start="2028-03-01T10:00:00Z")
    two = connection.queries[-1][1]["normalized_url"]
    assert one != two
    assert "__source_event_id=edition-2027" in one
    assert connection.queries[-1][1]["url"] == base["official_url"]

    repository.upsert(**base, source_event_id=None, edition_start="2027-03-01T10:00:00Z")
    by_date = connection.queries[-1][1]["normalized_url"]
    repository.upsert(**base, source_event_id=None, edition_start="2028-03-01T10:00:00Z")
    assert by_date != connection.queries[-1][1]["normalized_url"]
    assert "__event_start=2027-03-01" in by_date


def test_hackclub_specialized_source_retains_hacks_events_without_weak_general_acceptance():
    event = example(name="Sierra Hacks 2027")
    assert prepare_event(event) is None
    assert prepare_event(event, source_key="hackclub_hackathons") is not None
    assert prepare_event(example(name="Industry Conference"), source_key="campus_feed") is None


def test_incremental_migration_readiness_and_default_disabled(capsys, monkeypatch):
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from hackathon_api.main import EXPECTED_DB_REVISION

    from scripts import run_incremental_canonical

    root = Path(__file__).resolve().parents[3]
    revision = ScriptDirectory.from_config(Config(str(root / "db/alembic.ini"))).get_current_head()
    assert revision == EXPECTED_DB_REVISION == "0014_typed_attribute_catalog"
    ddl = (root / "db/sql/0010_incremental_canonical_pipeline.sql").read_text()
    assert "source_event_payloads" in ddl and "processed_hash" in ddl
    monkeypatch.setattr("sys.argv", ["run_incremental_canonical.py"])
    assert run_incremental_canonical.main() == 0
    assert '"DISABLED"' in capsys.readouterr().out
