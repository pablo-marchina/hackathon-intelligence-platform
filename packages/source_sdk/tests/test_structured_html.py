"""Generic structured-event ingestion remains gated by individual source permissions."""
import asyncio
import json
from datetime import UTC, datetime

from hackathon_sources.factory import build_adapter
from hackathon_sources.models import FetchResult, SourceDefinition
from hackathon_sources.seed_adapter import SeedUrlAdapter
from hackathon_sources.structured_html import StructuredHtmlEventAdapter


def source(permission='ALLOWED', enabled=True, mode='STATIC_HTML'):
    return SourceDefinition(
        key='campus', name='Campus', domain='university.example', source_family='campus',
        source_type='official_event', ingestion_mode=mode, permission_status=permission,
        detail_extraction_enabled=enabled, seed_urls=['https://university.example/events'],
    )


def html(*nodes, content_type='text/html'):
    scripts = ''.join('<script type="application/ld+json">' + json.dumps(node) + '</script>' for node in nodes)
    return FetchResult(
        requested_url='https://university.example/events', final_url='https://university.example/events',
        status_code=200, content_type=content_type, body=scripts.encode(), fetched_at=datetime.now(UTC),
    )


def parse(*nodes):
    return asyncio.run(StructuredHtmlEventAdapter(source()).parse_events(html(*nodes)))


def test_adapter_requires_allowed_and_detail_enabled():
    assert type(build_adapter(source())) is StructuredHtmlEventAdapter
    for kwargs in ({'permission': 'REVIEW_REQUIRED'}, {'permission': 'RESTRICTED'},
                   {'enabled': False}, {'mode': 'DISCOVERY_ONLY'}):
        assert type(build_adapter(source(**kwargs))) is SeedUrlAdapter


def test_extract_rich_multisite_graph_without_following_external_links():
    payload = {'@context': 'https://schema.org', '@graph': [
        {'@type': 'Organization', 'name': 'University'},
        {'@type': 'Event', '@id': 'https://host.example/event', 'identifier': 'event-001', 'name': 'Global AI Hackathon 2026',
         'description': 'Build prototypes in teams and win prizes.',
         'url': 'https://host.example/hackathon', 'startDate': '2026-11-10T08:00:00Z',
         'endDate': '2026-11-12T20:00:00Z',
         'eventAttendanceMode': 'https://schema.org/MixedEventAttendanceMode',
         'eventStatus': 'https://schema.org/EventScheduled',
         'location': [
             {'@type': 'Place', 'name': 'Innovation Center', 'address': {'@type': 'PostalAddress', 'addressLocality': 'São Paulo', 'addressRegion': 'SP', 'addressCountry': 'BR'}},
             {'@type': 'VirtualLocation', 'url': 'https://remote.example/join'},
         ],
         'organizer': {'@type': 'Organization', 'name': 'Campus'},
         'sponsor': [{'@type': 'Organization', 'name': 'Startup'}],
         'offers': {'@type': 'Offer', 'url': 'https://register.example/signup', 'price': 0, 'priceCurrency': 'BRL'},
         'keywords': ['AI, fintech', 'Sustainability'], 'image': '/banner.png',
         'inLanguage': 'pt-BR', 'isAccessibleForFree': True,
         'maximumAttendeeCapacity': 500,
        },
    ]}
    records = parse(payload)
    assert len(records) == 1
    item = records[0]
    assert item.source_event_id == 'event-001'
    assert item.metadata['schema_org_id'] == 'https://host.example/event'
    assert item.city == 'São Paulo'
    assert item.country_code == 'BR'
    assert item.format_raw == 'HYBRID'
    assert item.tags == ['AI', 'fintech', 'Sustainability']
    assert item.metadata['organizers'] == ['Campus']
    assert item.metadata['sponsors'] == ['Startup']
    assert item.metadata['registration_url'] == 'https://register.example/signup'
    assert item.metadata['image_url'] == 'https://university.example/banner.png'
    assert item.metadata['ticket_price'] == '0'
    assert item.metadata['language'] == 'pt-BR'
    assert item.metadata['is_accessible_for_free'] is True
    assert item.metadata['maximum_attendee_capacity'] == 500
    assert str(item.official_url) == 'https://host.example/hackathon'


def test_item_list_and_repeated_event_are_deduplicated():
    node = {'@type': 'Event', 'name': 'Student Hackathon 2026', 'startDate': '2026-11-20'}
    item_list = {'@type': 'ItemList', 'itemListElement': [{'@type': 'ListItem', 'item': node}, node]}
    assert len(parse(item_list, node)) == 1


def test_non_hackathon_concert_not_promoted():
    assert parse({'@type': 'MusicEvent', 'name': 'Rock Concert 2026', 'startDate': '2026-11-01'}) == []
    assert parse({'@type': 'Event', 'name': 'Music Festival 2026', 'startDate': '2026-11-01'}) == []


def test_non_event_schema_is_not_promoted():
    assert parse({'@type': 'Article', 'name': 'How to organize a hackathon', 'description': 'Guide for planning an event'}) == []


def test_invalid_json_and_malicious_links_are_ignored():
    node = {'@type': 'Event', 'name': 'Hackathon 2026', 'url': 'javascript:alert(1)',
            'offers': {'url': 'data:text/html,abc'}, 'image': 'file:///etc/passwd'}
    result = parse(node)
    assert len(result) == 1
    assert str(result[0].official_url) == 'https://university.example/events'
    assert result[0].metadata['registration_url'] is None
    assert result[0].metadata['image_url'] is None


def test_non_html_and_bad_json_do_not_crash():
    adapter = StructuredHtmlEventAdapter(source())
    response = html({'@type': 'Event', 'name': 'Hackathon 2026'}, content_type='application/json')
    assert asyncio.run(adapter.parse_events(response)) == []
    response = html()
    response.body = b'<script type="application/ld+json">{bad</script>'
    assert asyncio.run(adapter.parse_events(response)) == []


def test_graph_node_limit_and_restricted_payload():
    payload = {'@graph': [{'@type': 'Event', 'name': f'Hackathon 2026 #{n}'} for n in range(900)]}
    assert len(parse(payload)) <= 100
    adapter = StructuredHtmlEventAdapter(source())
    response = html()
    response.body = b'<script type="application/ld+json">' + b' '*1_000_002 + b'</script>'
    assert asyncio.run(adapter.parse_events(response)) == []


def test_online_format_without_physical_place():
    result = parse({'@type': 'Event', 'name': 'Online Hackathon 2026',
                   'location': {'@type': 'VirtualLocation', 'url': 'https://meet.example/'}})
    assert result[0].format_raw == 'ONLINE'


def test_schema_id_reused_between_editions_is_not_a_merge_key():
    event = {'@type': 'Event', '@id': 'https://campus.example/hackathon',
             'name': 'Campus Hackathon', 'url': 'https://campus.example/hackathon'}
    first = dict(event, startDate='2026-11-20')
    second = dict(event, startDate='2027-11-20')
    records = parse(first, second)
    assert len(records) == 2
    assert records[0].source_event_id is None
    assert records[1].source_event_id is None


def test_postal_address_as_direct_location_is_supported():
    records = parse({'@type': 'Event', 'name': 'Hackathon 2026',
                     'location': {'@type': 'PostalAddress', 'addressLocality': 'Tokyo',
                                  'addressCountry': 'JP'}})
    assert records[0].city == 'Tokyo'
    assert records[0].country_code == 'JP'
