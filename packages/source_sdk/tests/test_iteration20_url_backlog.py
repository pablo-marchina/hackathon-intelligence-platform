"""Iteration 20: durable syndicated crawl resume, fences, and host isolation."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

import hackathon_sources.persistent_runner as runner
from hackathon_sources.backlog import BacklogClaim, SourceUrlBacklog
from hackathon_sources.models import FetchResult, IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.syndication import SyndicatedEventAdapter


class Connection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
    def commit(self):
        self.commits += 1
    def rollback(self):
        self.rollbacks += 1


class Registry:
    authorized = True
    active = True
    def __init__(self, connection): pass
    def upsert(self, definition): pass
    def get_source_id(self, definition):
        return {'campus': '00000000-0000-4000-8000-000000000001',
                'other': '00000000-0000-4000-8000-000000000099'}[definition.key]
    def is_active(self, source_id): return self.active
    def is_fetch_authorized(self, source_id): return self.authorized
    def has_current_lease(self, *args): return True
    def authorized_for_commit(self, *args, **kwargs): return True


class Crawl:
    def __init__(self, conn): pass
    def start_run(self, source_id): return '00000000-0000-4000-8000-000000000002'
    def finish_run(self, *args, **kwargs): pass


class Frontier:
    def __init__(self, conn): pass
    def enqueue(self, **kwargs): return 10
    def mark_result(self, *args, **kwargs): pass


class Raw:
    def __init__(self, conn): pass
    def record_fetch_attempt(self, **kwargs): return 11
    def has_content(self, *args): return False
    def persist_document(self, **kwargs): return '00000000-0000-4000-8000-000000000003'
    def record_fetch_error(self, **kwargs): return 12


class Refs:
    def __init__(self, conn): pass
    def upsert(self, **kwargs): return '00000000-0000-4000-8000-000000000004'


class Payloads:
    def __init__(self, conn): pass
    def stage(self, **kwargs): pass


class MemoryBacklog:
    """A stateful fake across runs; production SQL gets its own CI drill."""
    entries = {}
    seq = 0
    history = []

    def __init__(self, conn): pass
    def add(self, source_id, url, *, kind, priority=0, metadata=None):
        key = (source_id, url)
        if key not in self.entries:
            type(self).seq += 1
            self.entries[key] = [type(self).seq, kind, 'PENDING', None, 0, None]
    def claim(self, source_id):
        items = [(key, row) for key, row in self.entries.items()
                 if key[0] == source_id and row[2] == 'PENDING']
        if not items:
            return None
        key, row = min(items, key=lambda pair: pair[1][0])
        token = f'token-{row[0]}'
        row[2], row[3] = 'CLAIMED', token
        self.history.append(key)
        return BacklogClaim(row[0], key[1], token, row[1], row[4], row[5])
    def succeed(self, claim, *, next_offset=0, listing_hash=None, has_more=False):
        row = next((r for r in self.entries.values() if r[0] == claim.id), None)
        if not row or row[2] != 'CLAIMED' or row[3] != claim.token:
            return False
        row[2], row[3] = ('PENDING' if has_more else 'DONE'), None
        row[4], row[5] = (next_offset, listing_hash) if has_more else (0, None)
        return True
    def stats(self, source_id):
        result = {}
        for (sid, _), row in self.entries.items():
            if sid == source_id:
                result[row[2]] = result.get(row[2], 0) + 1
        return result
    def fail(self, claim, *, reason, immediate=False):
        row = next((r for r in self.entries.values() if r[0] == claim.id), None)
        if not row or row[3] != claim.token:
            return False
        row[2], row[3] = 'PENDING' if immediate else 'RETRY', None
        return True


@pytest.fixture(autouse=True)
def patch_io(monkeypatch):
    MemoryBacklog.entries, MemoryBacklog.seq, MemoryBacklog.history = {}, 0, []
    Registry.authorized = Registry.active = True
    for symbol, fake in {'SourceRegistryRepository': Registry, 'CrawlRepository': Crawl,
                         'FrontierRepository': Frontier, 'RawDocumentRepository': Raw,
                         'SourceEventRefRepository': Refs, 'SourcePayloadRepository': Payloads,
                         'SourceUrlBacklog': MemoryBacklog}.items():
        monkeypatch.setattr(runner, symbol, fake)


def source(key='campus'):
    return SourceDefinition(
        key=key, name='Campus feed', domain='campus.example', source_family='university',
        source_type='official_calendar', ingestion_mode=IngestionMode.RSS,
        permission_status=PermissionStatus.ALLOWED, detail_extraction_enabled=True,
        seed_urls=['https://campus.example/feed.xml'])


def response(url, body, mime):
    return FetchResult(requested_url=url, final_url=url, status_code=200,
                       content_type=mime, body=body.encode(), fetched_at=datetime.now(UTC))


class Fetcher:
    def __init__(self, pages):
        self.pages, self.visited = pages, []
    async def fetch(self, src, url, **kwargs):
        self.visited.append(url)
        return self.pages[url]


def pages():
    root = 'https://campus.example/feed.xml'
    events = [f'https://campus.example/event/{x}' for x in ('one', 'two', 'three')]
    rss = '<rss><channel>' + ''.join(f'<item><link>{e}</link></item>' for e in events) + '</channel></rss>'
    result = {root: response(root, rss, 'application/rss+xml')}
    for event in events:
        html = '<script type="application/ld+json">' + json.dumps({
            '@type': 'Event', 'name': 'Hackathon on campus',
            'startDate': '2027-01-02', 'url': event}) + '</script>'
        result[event] = response(event, html, 'text/html')
    return result


def test_feed_backlog_is_durable_across_small_run_budgets():
    fetcher = Fetcher(pages())
    conn = Connection()
    first = asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=2))
    assert first.fetched_pages == 2 and len(first.records) == 1
    assert sum(row[2] == 'PENDING' for row in MemoryBacklog.entries.values()) == 2
    second = asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=3))
    assert second.fetched_pages == 2 and len(second.records) == 2
    assert all(row[2] == 'DONE' for row in MemoryBacklog.entries.values())
    assert second.stats()['backlog_by_state'] == {'DONE': 4}
    assert fetcher.visited == [
        'https://campus.example/feed.xml', 'https://campus.example/event/one',
        'https://campus.example/event/two',
        'https://campus.example/event/three',
    ]


def test_same_url_is_tracked_independently_by_source():
    fetcher = Fetcher(pages())
    conn = Connection()
    for key in ('campus', 'other'):
        first = asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source(key)), fetcher, conn, max_pages=1))
        assert first.fetched_pages == 1
    assert len(MemoryBacklog.entries) == 8  # seed + 3 details for each source
    assert {key[0] for key in MemoryBacklog.entries} == {
        '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000099'}


def test_pausing_preserves_queue_and_blocks_http():
    fetcher = Fetcher(pages())
    conn = Connection()
    asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1))
    Registry.active = False
    with pytest.raises(runner.SourcePausedError):
        asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=5))
    assert fetcher.visited == ['https://campus.example/feed.xml']
    assert sum(row[2] == 'PENDING' for row in MemoryBacklog.entries.values()) == 3


def test_url_policy_change_does_not_fetch_stale_queued_url():
    fetcher = Fetcher(pages())
    conn = Connection()
    asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1))
    # Mimic a URL formerly authorized, but with a changed DNS/host policy.
    sid = '00000000-0000-4000-8000-000000000001'
    MemoryBacklog(None).add(sid, 'https://other.example/old-event', kind='DISCOVERED')
    result = asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=6))
    assert 'https://other.example/old-event' not in fetcher.visited
    assert result.errors


def test_fencing_prevents_ack_of_stolen_claim():
    q = MemoryBacklog(None)
    sid = '00000000-0000-4000-8000-000000000001'
    q.add(sid, 'https://campus.example/x', kind='DISCOVERED')
    claim = q.claim(sid)
    assert claim
    assert not q.succeed(BacklogClaim(claim.id, claim.url, 'wrong-token', claim.kind))
    assert q.succeed(claim)
    assert not q.succeed(claim)


def test_postgresql_backlog_sql_has_scoping_and_token_guards():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    ddl = (root / 'db/sql/0011_source_url_backlog.sql').read_text()
    assert 'UNIQUE(source_id, canonical_url)' in ddl
    assert "state = 'CLAIMED' AND claim_token IS NOT NULL" in ddl
    sql = __import__('inspect').getsource(SourceUrlBacklog)
    assert 'FOR UPDATE SKIP LOCKED' in sql
    assert 'claim_until > clock_timestamp()' in sql
    assert 'claim_token=CAST(:token AS uuid)' in sql
    assert 'source_id=CAST(:source_id AS uuid)' in sql or 'source_id = CAST(:source_id AS uuid)' in sql


def test_mid_response_pause_preserves_seed_for_future_run():
    conn = Connection()
    fetcher = Fetcher(pages())
    original = fetcher.fetch
    async def fetch_then_pause(definition, url, **kwargs):
        page = await original(definition, url, **kwargs)
        Registry.active = False
        return page
    fetcher.fetch = fetch_then_pause
    result = asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=5))
    assert result.paused is True and result.fetched_pages == 0
    sid = '00000000-0000-4000-8000-000000000001'
    assert MemoryBacklog.entries[(sid, 'https://campus.example/feed.xml')][2] == 'PENDING'
    Registry.active = True
    fetcher.fetch = original
    resumed = asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1))
    assert resumed.fetched_pages == 1 and resumed.discovered_links == 3


def test_failed_child_does_not_drop_other_queue_items():
    fetcher = Fetcher(pages())
    del fetcher.pages['https://campus.example/event/one']
    result = asyncio.run(runner.run_source_persisted(
        SyndicatedEventAdapter(source()), fetcher, Connection(), max_pages=4))
    assert result.errors and len(result.records) == 2
    sid = '00000000-0000-4000-8000-000000000001'
    assert MemoryBacklog.entries[(sid, 'https://campus.example/event/one')][2] == 'RETRY'
    assert result.backlog_counts == {'DONE': 3, 'RETRY': 1}


def test_large_feed_continuation_is_durable_between_runner_calls():
    from hackathon_sources.syndication import MAX_FEED_LINKS
    root = 'https://campus.example/feed.xml'
    events = [f'https://campus.example/event/{i}' for i in range(MAX_FEED_LINKS + 4)]
    xml = '<rss><channel>' + ''.join(f'<item><link>{link}</link></item>' for link in events) + '</channel></rss>'
    fetcher = Fetcher({root: response(root, xml, 'application/rss+xml')})
    conn = Connection()
    first = asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1))
    sid = '00000000-0000-4000-8000-000000000001'
    row = MemoryBacklog.entries[(sid, root)]
    assert first.discovered_links == MAX_FEED_LINKS
    assert row[2] == 'PENDING' and row[4] == MAX_FEED_LINKS and row[5]
    # Pretend the next run's 2,500 individual event pages were processed.
    # The seed's cursor must still resume only the *remaining* four entries.
    for (source_id, url), item in MemoryBacklog.entries.items():
        if source_id == sid and url != root:
            item[2] = 'DONE'
    second = asyncio.run(runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1))
    assert second.discovered_links == 4
    assert row[2] == 'DONE' and row[4] == 0 and row[5] is None
    assert len(MemoryBacklog.entries) == MAX_FEED_LINKS + 5
    assert fetcher.visited == [root, root]
