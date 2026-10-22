"""W2 replay bridge writes source evidence and maps only known canonical columns."""
import asyncio

from scripts import run_w2_live


class FakeGate:
    def __init__(self):
        self.inserts = {}
        self.updates = {}

    async def read_all(self):
        return [{
            'id': '00000000-0000-4000-8000-000000000017',
            'source_key': 'hackclub_hackathons',
            'raw_document_id': None,
            'payload': {
                'name': 'Hackathon 2026', 'source_event_id': 'hackathon-2026',
                'official_url': 'https://university.example/hackathon',
                'starts_at_raw': '2026-11-12', 'ends_at_raw': '2026-11-13',
                'metadata': {
                    'extraction_method': 'schema_org_json_ld',
                    'is_accessible_for_free': False,
                    'ticket_price': '10.50', 'ticket_currency': 'BRL',
                    'registration_url': 'https://university.example/apply',
                    'registration_valid_through': '2026-11-01T10:00:00Z',
                    'organizers': ['University'],
                },
            },
        }]

    async def insert(self, table, rows, batch=100):
        self.inserts.setdefault(table, []).extend(rows)
        if table == 'source_observations':
            return [{**row, 'id': i + 1} for i, row in enumerate(rows)]
        return []

    async def update(self, table, rows, batch=100):
        self.updates.setdefault(table, []).extend(rows)

    async def close(self):
        pass


def test_w2_historical_gate_bridges_jsonld_evidence_to_canonical_fields(monkeypatch):
    gate = FakeGate()
    monkeypatch.setattr(run_w2_live, 'Gate', lambda: gate)
    assert asyncio.run(run_w2_live.main()) == 0
    obs = {v['field_path']: v for v in gate.inserts['source_observations']}
    assert obs['participation_fee']['value_json'] == '10.50'
    assert obs['participation_fee']['extraction_method'] == 'JSON_LD'
    assert obs['is_free']['normalized_value_json'] is False
    assert obs['registration_url']['value_json'] == 'https://university.example/apply'
    assert obs['offer_valid_through']['evidence_locator'].endswith('.registration_valid_through')
    assert 'registration_close_at' not in obs
    selections = {v['field_path'] for v in gate.inserts['canonical_field_selections']}
    assert {'participation_fee', 'is_free', 'registration_url', 'organizers'} <= selections
    h = next(v for v in gate.updates['hackathons'] if v.get('is_active'))
    assert h['participation_fee'] == '10.50'
    assert h['is_free'] is False
    assert h['registration_url'] == 'https://university.example/apply'
    assert 'registration_close_at' not in h
