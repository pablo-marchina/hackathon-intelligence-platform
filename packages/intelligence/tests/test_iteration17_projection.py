"""Schema.org source evidence is not permission to guess missing values."""
from hackathon_intelligence.structured_projection import (
    PROJECTION_VERSION,
    project_structured_metadata,
)


def test_jsonld_source_projection_preserves_evidence_and_normalized_values():
    payload = {'metadata': {
        'extraction_method': 'schema_org_json_ld',
        'description': 'Hackathon for remote teams', 'language': 'pt-BR',
        'registration_url': 'https://example.org/apply',
        'registration_valid_through': '2026-10-20T20:00:00-03:00',
        'ticket_price': '0', 'ticket_currency': 'brl',
        'is_accessible_for_free': False,
        'organizers': ['Community Org'], 'sponsors': ['Sponsor A'],
        'maximum_attendee_capacity': 100,
    }}
    entries = {v.field_path: v for v in project_structured_metadata(payload)}
    assert entries['short_description'].original == 'Hackathon for remote teams'
    assert entries['is_free'].normalized is False
    assert entries['participation_fee'].normalized == '0'
    assert entries['participation_fee_currency'].normalized == 'BRL'
    assert entries['offer_valid_through'].normalized == '2026-10-20T23:00:00+00:00'
    assert 'registration_close_at' not in entries
    assert entries['registration_url'].evidence_locator.endswith('registration_url')
    assert PROJECTION_VERSION.startswith('jsonld')


def test_missing_or_invalid_fields_remain_unknown():
    entries = project_structured_metadata({'metadata': {'extraction_method': 'schema_org_json_ld',
        'ticket_price': 'unknown', 'ticket_currency': 'BTC1',
        'registration_valid_through': '2026-10-30T16:00:00',
        'registration_url': 'file:///private',
        'is_accessible_for_free': 'false',
        'organizers': [], 'maximum_attendee_capacity': -1,
    }})
    assert entries == []
    assert project_structured_metadata({'metadata': {'ticket_price': '0'}}) == []
    assert project_structured_metadata({'metadata': 'bad'}) == []


def test_normalizer_never_changes_price_locale_or_type_without_evidence():
    meta = {'extraction_method': 'schema_org_json_ld', 'ticket_price': '1,000',
            'ticket_currency': 'USD', 'is_accessible_for_free': True}
    entries = {v.field_path: v.normalized for v in project_structured_metadata({'metadata': meta})}
    assert 'participation_fee' not in entries
    assert entries['participation_fee_currency'] == 'USD'
    assert entries['is_free'] is True


def test_program_and_non_jsonld_records_do_not_generate_rich_fields():
    assert not project_structured_metadata({'metadata': {}})
    assert not project_structured_metadata({'name': 'Hackathon', 'metadata': {'extraction_method': 'api', 'ticket_price': '0'}})
