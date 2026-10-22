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
    instant = datetime.now(UTC)
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
                for source_ix, hackathon_ix in ((0, 0), (1, 0), (1, 1)):
                    url = f'https://example.invalid/{unique_id}/{source_ix}/{hackathon_ix}'
                    connection.execute(text('''
                        INSERT INTO source_event_refs
                            (source_id, hackathon_id, url, normalized_url)
                        VALUES (CAST(:source_id AS uuid), CAST(:hackathon_id AS uuid), :url, :url)
                    '''), {'source_id': source_ids[source_ix], 'hackathon_id': hackathon_ids[hackathon_ix], 'url': url})
                snapshot = CoverageRepository(connection).snapshot(as_of=instant)
                assert snapshot['catalog']['unique_active'] == baseline['catalog']['unique_active'] + 2
                assert snapshot['catalog']['confirmed_registration_window'] == baseline['catalog']['confirmed_registration_window'] + 1
                assert snapshot['catalog']['unknown_registration_close'] == baseline['catalog']['unknown_registration_close'] + 1
                assert snapshot['source_counts']['registered_in_database'] == baseline['source_counts']['registered_in_database'] + 2
                attributed = {s['source_id']: s['attributed_unique_active'] for s in snapshot['sources']}
                assert attributed[source_ids[0]] == 1 and attributed[source_ids[1]] == 2
                print('Catalog coverage PostgreSQL: distinct global count, overlapping source attribution, unknown deadline PASS')
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


if __name__ == '__main__':
    verify()
