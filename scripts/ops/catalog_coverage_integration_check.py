"""Transactional PostgreSQL drill for W17 catalog/source metrics; leaves no fixture rows.

Requires APP_ENV=test and HIP_ALLOW_COVERAGE_DB_DRILL=1. No third-party URLs
are fetched; the entire test transaction is rolled back on success or failure.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine, text

from hackathon_api.repositories.coverage import CoverageRepository


def verify() -> None:
    if os.environ.get('APP_ENV') != 'test' or os.environ.get('HIP_ALLOW_COVERAGE_DB_DRILL') != '1':
        raise RuntimeError('refusing coverage integration against non-test environment')
    engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
    instant = datetime.now(UTC) + timedelta(minutes=1)  # future cutoff includes DB defaults inserted later
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                baseline = CoverageRepository(connection).snapshot(as_of=instant)
                unique_id = uuid4().hex
                source_ids: list[str] = []
                for suffix in ('one', 'two'):
                    source_ids.append(connection.execute(text('''
                        INSERT INTO sources (name, domain, source_family, source_type,
                                             ingestion_mode, permission_status, active)
                        VALUES (:name, :domain, 'ci-coverage', 'CI', 'API', 'ALLOWED', true)
                        RETURNING id::text
                    '''), {'name': f'Coverage {suffix}', 'domain': f'{unique_id}-{suffix}.invalid'}).scalar_one())
                hackathon_ids: list[str] = []
                for i in range(2):
                    hackathon_ids.append(connection.execute(text('''
                        INSERT INTO hackathons (name, slug, normalized_name,
                                              start_at, end_at, registration_open_at,
                                              registration_close_at, official_url)
                        VALUES (:name, :slug, :normalized_name, :start, :end,
                                :open, :close, :official_url)
                        RETURNING id::text
                    '''), {
                        'name': f'Coverage CI {i}', 'slug': f'coverage-{unique_id}-{i}',
                        'normalized_name': f'coverage ci {i}',
                        'start': instant + timedelta(days=3), 'end': instant + timedelta(days=5),
                        'open': instant - timedelta(days=1) if i == 0 else None,
                        'close': instant + timedelta(days=1) if i == 0 else None,
                        'official_url': f'https://example.invalid/hackathon/{unique_id}/{i}',
                    }).scalar_one())
                ref_ids: list[str] = []
                for source_ix, hackathon_ix in ((0, 0), (1, 0), (1, 1)):
                    url = f'https://example.invalid/{unique_id}/{source_ix}/{hackathon_ix}'
                    ref_ids.append(connection.execute(text('''
                        INSERT INTO source_event_refs
                            (source_id, hackathon_id, url, normalized_url)
                        VALUES (CAST(:source_id AS uuid), CAST(:hackathon_id AS uuid), :url, :url)
                        RETURNING id::text
                    '''), {'source_id': source_ids[source_ix], 'hackathon_id': hackathon_ids[hackathon_ix], 'url': url}).scalar_one())
                for i in range(2):
                    connection.execute(text('''
                        INSERT INTO source_event_payloads
                            (source_event_ref_id, source_id, source_key, payload,
                             content_hash, processed_hash, last_error)
                        VALUES (CAST(:ref_id AS uuid), CAST(:source_id AS uuid), :source_key,
                                CAST(:payload AS jsonb), :content_hash, :processed_hash, :last_error)
                    '''), {
                        'ref_id': ref_ids[i], 'source_id': source_ids[i],
                        'source_key': f'ci-coverage-{i}',
                        'payload': '{"name":"synthetic test event"}',
                        'content_hash': 'a' * 64, 'processed_hash': 'a' * 64 if i == 0 else None,
                        'last_error': None if i == 0 else 'synthetic parsing failure',
                    })
                for i, state in enumerate(('PENDING', 'EXHAUSTED')):
                    url = f'https://{unique_id}-one.invalid/listing/{i}'
                    connection.execute(text('''
                        INSERT INTO source_url_backlog
                            (source_id, url, canonical_url, domain, kind, state, next_fetch_at)
                        VALUES (CAST(:source_id AS uuid), :url, :url, :domain,
                                'DISCOVERED', :state, :due_at)
                    '''), {'source_id': source_ids[0], 'url': url,
                           'domain': f'{unique_id}-one.invalid', 'state': state,
                           'due_at': instant - timedelta(minutes=1)})
                snapshot = CoverageRepository(connection).snapshot(as_of=instant)
                assert snapshot['catalog']['unique_active'] == baseline['catalog']['unique_active'] + 2
                assert snapshot['growth']['new_active_canonicals'] == baseline['growth']['new_active_canonicals'] + 2
                assert sum(day['new_active_canonicals'] for day in snapshot['growth']['daily']) == snapshot['growth']['new_active_canonicals']
                assert snapshot['catalog']['confirmed_registration_window'] == baseline['catalog']['confirmed_registration_window'] + 1
                assert snapshot['catalog']['unknown_registration_close'] == baseline['catalog']['unknown_registration_close'] + 1
                assert snapshot['source_counts']['registered_in_database'] == baseline['source_counts']['registered_in_database'] + 2
                assert snapshot['pipeline']['staged_payloads'] == baseline['pipeline']['staged_payloads'] + 2
                assert snapshot['pipeline']['awaiting_normalization'] == baseline['pipeline']['awaiting_normalization'] + 1
                assert snapshot['pipeline']['normalization_errors'] == baseline['pipeline']['normalization_errors'] + 1
                assert snapshot['pipeline']['normalized_current_version'] == baseline['pipeline']['normalized_current_version'] + 1
                assert snapshot['pipeline']['backlog']['backlog_pending'] == baseline['pipeline']['backlog']['backlog_pending'] + 1
                assert snapshot['pipeline']['backlog']['backlog_exhausted'] == baseline['pipeline']['backlog']['backlog_exhausted'] + 1
                assert snapshot['pipeline']['backlog']['backlog_ready_now'] == baseline['pipeline']['backlog']['backlog_ready_now'] + 1
                assert snapshot['field_completeness']['counts']['with_registration_deadline'] == baseline['field_completeness']['counts']['with_registration_deadline'] + 1
                assert snapshot['field_completeness']['counts']['with_organizer'] == baseline['field_completeness']['counts']['with_organizer']
                attributed = {s['source_id']: s['attributed_unique_active'] for s in snapshot['sources']}
                assert attributed[source_ids[0]] == 1 and attributed[source_ids[1]] == 2
                first = next(row for row in snapshot['sources'] if row['source_id'] == source_ids[0])
                second = next(row for row in snapshot['sources'] if row['source_id'] == source_ids[1])
                assert first['new_source_refs_recent'] == 1
                assert second['new_source_refs_recent'] == 2
                assert first['new_attributed_canonicals_recent'] == 1
                assert second['new_attributed_canonicals_recent'] == 2
                # Each source attributes the shared canonical; the global new count is 2, not 3.
                first = next(item for item in snapshot['sources'] if item['source_id'] == source_ids[0])
                assert first['backlog_ready_now'] == 1 and first['backlog_exhausted'] == 1
                print('Catalog coverage PostgreSQL: deduped events, pipeline, backlog, completeness PASS')
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


if __name__ == '__main__':
    verify()
