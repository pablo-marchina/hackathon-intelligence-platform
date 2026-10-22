"""Iteration 20: durable syndicated crawl resume, fences, and host isolation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import hackathon_sources.persistent_runner as runner
import pytest
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

    def __init__(self, connection):
        pass

    def upsert(self, definition):
        pass

    def get_source_id(self, definition):
        return {
            "campus": "00000000-0000-4000-8000-000000000001",
            "other": "00000000-0000-4000-8000-000000000099",
        }[definition.key]

    def is_active(self, source_id):
        return self.active

    def is_fetch_authorized(self, source_id):
        return self.authorized

    def has_current_lease(self, *args):
        return True

    def authorized_for_commit(self, *args, **kwargs):
        return True


class Crawl:
    def __init__(self, conn):
        pass

    def start_run(self, source_id):
        return "00000000-0000-4000-8000-000000000002"

    def finish_run(self, *args, **kwargs):
        pass


class Frontier:
    def __init__(self, conn):
        pass

    def enqueue(self, **kwargs):
        return 10

    def mark_result(self, *args, **kwargs):
        pass


class Raw:
    def __init__(self, conn):
        pass

    def record_fetch_attempt(self, **kwargs):
        return 11

    def has_content(self, *args):
        return False

    def persist_document(self, **kwargs):
        return "00000000-0000-4000-8000-000000000003"

    def record_fetch_error(self, **kwargs):
        return 12


class Refs:
    def __init__(self, conn):
        pass

    def upsert(self, **kwargs):
        return "00000000-0000-4000-8000-000000000004"


class Payloads:
    def __init__(self, conn):
        pass

    def stage(self, **kwargs):
        pass


class MemoryBacklog:
    """A stateful fake across runs; production SQL gets its own CI drill."""

    entries = {}
    seq = 0
    history = []

    def __init__(self, conn):
        pass

    def add(self, source_id, url, *, kind, priority=0, metadata=None):
        key = (source_id, url)
        if key not in self.entries:
            type(self).seq += 1
            self.entries[key] = [
                type(self).seq,
                kind,
                "PENDING",
                None,
                0,
                None,
                dict(metadata or {}),
            ]

    def claim(self, source_id):
        items = [
            (key, row)
            for key, row in self.entries.items()
            if key[0] == source_id and row[2] == "PENDING"
        ]
        if not items:
            return None
        key, row = min(items, key=lambda pair: pair[1][0])
        token = f"token-{row[0]}"
        row[2], row[3] = "CLAIMED", token
        self.history.append(key)
        return BacklogClaim(
            row[0],
            key[1],
            token,
            row[1],
            row[4],
            row[5],
            row[6].get("pagination_depth", 0),
            row[6].get("sitemap_depth", 0),
        )

    def succeed(self, claim, *, next_offset=0, listing_hash=None, has_more=False):
        row = next((r for r in self.entries.values() if r[0] == claim.id), None)
        if not row or row[2] != "CLAIMED" or row[3] != claim.token:
            return False
        row[2], row[3] = ("PENDING" if has_more else "DONE"), None
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
        row[2], row[3] = "PENDING" if immediate else "RETRY", None
        return True


@pytest.fixture(autouse=True)
def patch_io(monkeypatch):
    MemoryBacklog.entries, MemoryBacklog.seq, MemoryBacklog.history = {}, 0, []
    Registry.authorized = Registry.active = True
    for symbol, fake in {
        "SourceRegistryRepository": Registry,
        "CrawlRepository": Crawl,
        "FrontierRepository": Frontier,
        "RawDocumentRepository": Raw,
        "SourceEventRefRepository": Refs,
        "SourcePayloadRepository": Payloads,
        "SourceUrlBacklog": MemoryBacklog,
    }.items():
        monkeypatch.setattr(runner, symbol, fake)


def source(key="campus"):
    return SourceDefinition(
        key=key,
        name="Campus feed",
        domain="campus.example",
        source_family="university",
        source_type="official_calendar",
        ingestion_mode=IngestionMode.RSS,
        permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        seed_urls=["https://campus.example/feed.xml"],
    )


def response(url, body, mime):
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type=mime,
        body=body.encode(),
        fetched_at=datetime.now(UTC),
    )


class Fetcher:
    def __init__(self, pages):
        self.pages, self.visited = pages, []

    async def fetch(self, src, url, **kwargs):
        self.visited.append(url)
        return self.pages[url]


def pages():
    root = "https://campus.example/feed.xml"
    events = [f"https://campus.example/event/{x}" for x in ("one", "two", "three")]
    rss = (
        "<rss><channel>"
        + "".join(f"<item><link>{e}</link></item>" for e in events)
        + "</channel></rss>"
    )
    result = {root: response(root, rss, "application/rss+xml")}
    for event in events:
        html = (
            '<script type="application/ld+json">'
            + json.dumps(
                {
                    "@type": "Event",
                    "name": "Hackathon on campus",
                    "startDate": "2027-01-02",
                    "url": event,
                }
            )
            + "</script>"
        )
        result[event] = response(event, html, "text/html")
    return result


def test_feed_backlog_is_durable_across_small_run_budgets():
    fetcher = Fetcher(pages())
    conn = Connection()
    first = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=2)
    )
    assert first.fetched_pages == 2 and len(first.records) == 1
    assert sum(row[2] == "PENDING" for row in MemoryBacklog.entries.values()) == 2
    second = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=3)
    )
    assert second.fetched_pages == 2 and len(second.records) == 2
    assert all(row[2] == "DONE" for row in MemoryBacklog.entries.values())
    assert second.stats()["backlog_by_state"] == {"DONE": 4}
    assert fetcher.visited == [
        "https://campus.example/feed.xml",
        "https://campus.example/event/one",
        "https://campus.example/event/two",
        "https://campus.example/event/three",
    ]


def test_same_url_is_tracked_independently_by_source():
    fetcher = Fetcher(pages())
    conn = Connection()
    for key in ("campus", "other"):
        first = asyncio.run(
            runner.run_source_persisted(
                SyndicatedEventAdapter(source(key)), fetcher, conn, max_pages=1
            )
        )
        assert first.fetched_pages == 1
    assert len(MemoryBacklog.entries) == 8  # seed + 3 details for each source
    assert {key[0] for key in MemoryBacklog.entries} == {
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000099",
    }


def test_pausing_preserves_queue_and_blocks_http():
    fetcher = Fetcher(pages())
    conn = Connection()
    asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1)
    )
    Registry.active = False
    with pytest.raises(runner.SourcePausedError):
        asyncio.run(
            runner.run_source_persisted(
                SyndicatedEventAdapter(source()), fetcher, conn, max_pages=5
            )
        )
    assert fetcher.visited == ["https://campus.example/feed.xml"]
    assert sum(row[2] == "PENDING" for row in MemoryBacklog.entries.values()) == 3


def test_url_policy_change_does_not_fetch_stale_queued_url():
    fetcher = Fetcher(pages())
    conn = Connection()
    asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1)
    )
    # Mimic a URL formerly authorized, but with a changed DNS/host policy.
    sid = "00000000-0000-4000-8000-000000000001"
    MemoryBacklog(None).add(sid, "https://other.example/old-event", kind="DISCOVERED")
    result = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=6)
    )
    assert "https://other.example/old-event" not in fetcher.visited
    assert result.errors


def test_fencing_prevents_ack_of_stolen_claim():
    q = MemoryBacklog(None)
    sid = "00000000-0000-4000-8000-000000000001"
    q.add(sid, "https://campus.example/x", kind="DISCOVERED")
    claim = q.claim(sid)
    assert claim
    assert not q.succeed(BacklogClaim(claim.id, claim.url, "wrong-token", claim.kind))
    assert q.succeed(claim)
    assert not q.succeed(claim)


def test_postgresql_backlog_sql_has_scoping_and_token_guards():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    ddl = (root / "db/sql/0011_source_url_backlog.sql").read_text()
    assert "UNIQUE(source_id, canonical_url)" in ddl
    assert "state = 'CLAIMED' AND claim_token IS NOT NULL" in ddl
    sql = __import__("inspect").getsource(SourceUrlBacklog)
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "claim_until > clock_timestamp()" in sql
    assert "claim_token=CAST(:token AS uuid)" in sql
    assert (
        "source_id=CAST(:source_id AS uuid)" in sql or "source_id = CAST(:source_id AS uuid)" in sql
    )


def test_mid_response_pause_preserves_seed_for_future_run():
    conn = Connection()
    fetcher = Fetcher(pages())
    original = fetcher.fetch

    async def fetch_then_pause(definition, url, **kwargs):
        page = await original(definition, url, **kwargs)
        Registry.active = False
        return page

    fetcher.fetch = fetch_then_pause
    result = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=5)
    )
    assert result.paused is True and result.fetched_pages == 0
    sid = "00000000-0000-4000-8000-000000000001"
    assert MemoryBacklog.entries[(sid, "https://campus.example/feed.xml")][2] == "PENDING"
    Registry.active = True
    fetcher.fetch = original
    resumed = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1)
    )
    assert resumed.fetched_pages == 1 and resumed.discovered_links == 3


def test_failed_child_does_not_drop_other_queue_items():
    fetcher = Fetcher(pages())
    del fetcher.pages["https://campus.example/event/one"]
    result = asyncio.run(
        runner.run_source_persisted(
            SyndicatedEventAdapter(source()), fetcher, Connection(), max_pages=4
        )
    )
    assert result.errors and len(result.records) == 2
    sid = "00000000-0000-4000-8000-000000000001"
    assert MemoryBacklog.entries[(sid, "https://campus.example/event/one")][2] == "RETRY"
    assert result.backlog_counts == {"DONE": 3, "RETRY": 1}


def test_large_feed_continuation_is_durable_between_runner_calls():
    from hackathon_sources.syndication import MAX_FEED_LINKS

    root = "https://campus.example/feed.xml"
    events = [f"https://campus.example/event/{i}" for i in range(MAX_FEED_LINKS + 4)]
    xml = (
        "<rss><channel>"
        + "".join(f"<item><link>{link}</link></item>" for link in events)
        + "</channel></rss>"
    )
    fetcher = Fetcher({root: response(root, xml, "application/rss+xml")})
    conn = Connection()
    first = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1)
    )
    sid = "00000000-0000-4000-8000-000000000001"
    row = MemoryBacklog.entries[(sid, root)]
    assert first.discovered_links == MAX_FEED_LINKS
    assert row[2] == "PENDING" and row[4] == MAX_FEED_LINKS and row[5]
    # Pretend the next run's 2,500 individual event pages were processed.
    # The seed's cursor must still resume only the *remaining* four entries.
    for (source_id, url), item in MemoryBacklog.entries.items():
        if source_id == sid and url != root:
            item[2] = "DONE"
    second = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1)
    )
    assert second.discovered_links == 4
    assert row[2] == "DONE" and row[4] == 0 and row[5] is None
    assert len(MemoryBacklog.entries) == MAX_FEED_LINKS + 5
    assert fetcher.visited == [root, root]


def test_iteration27_paginated_rss_survives_worker_restarts():
    root = "https://campus.example/feed.xml"
    page2 = "https://campus.example/feed.xml?page=2"
    event1 = "https://campus.example/events/first"
    event2 = "https://campus.example/events/second"
    atom = 'xmlns:atom="http://www.w3.org/2005/Atom"'
    first_xml = f'<rss {atom}><channel><atom:link rel="next" href="?page=2"/><item><link>{event1}</link></item></channel></rss>'
    second_xml = f"<rss><channel><item><link>{event2}</link></item></channel></rss>"

    def html(url, name):
        return (
            '<script type="application/ld+json">'
            + json.dumps(
                {"@type": "Event", "name": name, "startDate": "2027-04-01T10:00:00Z", "url": url}
            )
            + "</script>"
        )

    fetcher = Fetcher(
        {
            root: response(root, first_xml, "application/xml"),
            page2: response(page2, second_xml, "application/xml"),
            event1: response(event1, html(event1, "First Hackathon"), "text/html"),
            event2: response(event2, html(event2, "Second Hackathon"), "text/html"),
        }
    )
    conn = Connection()
    a = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1)
    )
    assert a.fetched_pages == 1 and a.discovered_links == 2
    sid = "00000000-0000-4000-8000-000000000001"
    assert MemoryBacklog.entries[(sid, page2)][6]["pagination_depth"] == 1
    b = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=3)
    )
    assert b.fetched_pages == 3 and b.persisted_event_refs == 2
    assert fetcher.visited == [root, event1, page2, event2]
    assert b.backlog_counts == {"DONE": 4}
    c = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=4)
    )
    assert c.fetched_pages == 0


def test_iteration27_pagination_hop_budget_survives_restart():
    from hackathon_sources.syndication import MAX_FEED_PAGE_HOPS

    root = "https://campus.example/feed.xml"
    page2 = "https://campus.example/feed.xml?page=2"
    page3 = "https://campus.example/feed.xml?page=3"

    def atom_next(url):
        return response(
            url,
            f'<feed xmlns="http://www.w3.org/2005/Atom"><link rel="next" href="{page3}"/></feed>',
            "application/atom+xml",
        )

    pages = {
        root: response(
            root,
            f'<feed xmlns="http://www.w3.org/2005/Atom"><link rel="next" href="{page2}"/></feed>',
            "application/atom+xml",
        ),
        page2: atom_next(page2),
    }
    fetcher = Fetcher(pages)
    conn = Connection()
    asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=1)
    )
    sid = "00000000-0000-4000-8000-000000000001"
    # Simulate the hundredth page of a long-running chain after many prior runs.
    MemoryBacklog.entries[(sid, page2)][6]["pagination_depth"] = MAX_FEED_PAGE_HOPS
    result = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(source()), fetcher, conn, max_pages=3)
    )
    assert result.fetched_pages == 1
    assert (sid, page3) not in MemoryBacklog.entries
    assert fetcher.visited == [root, page2]


def _sitemap_source():
    return SourceDefinition(
        key="campus",
        name="Campus sitemap",
        domain="campus.example",
        source_family="university",
        source_type="official_calendar",
        ingestion_mode=IngestionMode.SITEMAP,
        permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        seed_urls=["https://campus.example/feed.xml"],
    )


def test_iteration30_nested_sitemap_depth_survives_runner_restart():
    """A sitemap index may contain another index; depth lives in the queue."""
    root = "https://campus.example/feed.xml"
    nested = "https://campus.example/nested.xml"
    leaf = "https://campus.example/leaf.xml"
    detail = "https://campus.example/events/one"

    def sitemap_index(url, child):
        return response(
            url,
            "<sitemapindex><sitemap><loc>" + child + "</loc></sitemap></sitemapindex>",
            "application/xml",
        )

    pages = {
        root: sitemap_index(root, nested),
        nested: sitemap_index(nested, leaf),
        leaf: response(leaf, f"<urlset><url><loc>{detail}</loc></url></urlset>", "application/xml"),
        detail: response(
            detail,
            '<script type="application/ld+json">'
            + json.dumps(
                {
                    "@type": "Event",
                    "name": "Verified Hackathon",
                    "startDate": "2027-08-01T08:00:00Z",
                    "url": detail,
                }
            )
            + "</script>",
            "text/html",
        ),
    }
    fetcher = Fetcher(pages)
    conn = Connection()
    sid = "00000000-0000-4000-8000-000000000001"
    for url, expected_depth in ((root, 0), (nested, 1), (leaf, 2)):
        run = asyncio.run(
            runner.run_source_persisted(
                SyndicatedEventAdapter(_sitemap_source()), fetcher, conn, max_pages=1
            )
        )
        assert run.fetched_pages == 1 and fetcher.visited[-1] == url
        assert MemoryBacklog.entries[(sid, url)][6].get("sitemap_depth", 0) == expected_depth
    complete = asyncio.run(
        runner.run_source_persisted(
            SyndicatedEventAdapter(_sitemap_source()), fetcher, conn, max_pages=1
        )
    )
    assert complete.persisted_event_refs == 1
    assert fetcher.visited == [root, nested, leaf, detail]


def test_iteration30_sitemap_depth_cap_blocks_recursive_indexes_across_runs():
    from hackathon_sources.syndication import MAX_SITEMAP_INDEX_DEPTH

    root = "https://campus.example/feed.xml"
    nested = "https://campus.example/nested.xml"
    forbidden_child = "https://campus.example/too-deep.xml"
    fetcher = Fetcher(
        {
            root: response(
                root,
                f"<sitemapindex><sitemap><loc>{nested}</loc></sitemap></sitemapindex>",
                "application/xml",
            ),
            nested: response(
                nested,
                f"<sitemapindex><sitemap><loc>{forbidden_child}</loc></sitemap></sitemapindex>",
                "application/xml",
            ),
        }
    )
    conn = Connection()
    sid = "00000000-0000-4000-8000-000000000001"
    asyncio.run(
        runner.run_source_persisted(
            SyndicatedEventAdapter(_sitemap_source()), fetcher, conn, max_pages=1
        )
    )
    MemoryBacklog.entries[(sid, nested)][6]["sitemap_depth"] = MAX_SITEMAP_INDEX_DEPTH
    run = asyncio.run(
        runner.run_source_persisted(
            SyndicatedEventAdapter(_sitemap_source()), fetcher, conn, max_pages=5
        )
    )
    assert run.fetched_pages == 1
    assert (sid, forbidden_child) not in MemoryBacklog.entries
    assert fetcher.visited == [root, nested]


def test_iteration30_invalid_seed_closes_crawl_without_http(monkeypatch):
    """Invalid startup configuration must not leave a RUNNING crawl behind."""
    finished = []
    monkeypatch.setattr(
        Crawl, "finish_run", lambda self, rid, status, stats: finished.append((rid, status, stats))
    )
    src = _sitemap_source().model_copy(
        update={"seed_urls": ["https://campus.example/feed.xml", "https://external.example/secret"]}
    )
    fetcher = Fetcher({})
    conn = Connection()
    run = asyncio.run(
        runner.run_source_persisted(SyndicatedEventAdapter(src), fetcher, conn, max_pages=2)
    )
    assert run.status == "FAILED"
    assert len(finished) == 1 and finished[0][1] == "FAILED"
    assert not fetcher.visited
    assert conn.rollbacks == 1
    assert not MemoryBacklog.entries


@pytest.mark.parametrize(
    "meta",
    [
        {"sitemap_depth": True},
        {"sitemap_depth": -1},
        {"sitemap_depth": "3"},
        {"pagination_depth": 1.5},
    ],
)
def test_iteration30_invalid_depth_metadata_rejected_before_sql(meta):
    class NeverCalled:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("should not touch PostgreSQL")

    with pytest.raises(ValueError, match="invalid backlog traversal depth"):
        SourceUrlBacklog(NeverCalled()).add(
            "00000000-0000-4000-8000-000000000001",
            "https://campus.example/child.xml",
            kind="DISCOVERED",
            metadata=meta,
        )


@pytest.mark.parametrize("kind,expected", [("SEED", 0), ("DISCOVERED", 7)])
def test_iteration30_seed_depth_is_always_zero_when_claimed(kind, expected):
    class Result:
        def __init__(self, row=None):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    class FakeDatabase:
        def execute(self, sql, *_args):
            if "RETURNING b.id" not in str(sql):
                return Result()
            return Result(
                {
                    "id": 12,
                    "url": "https://campus.example/child.xml",
                    "kind": kind,
                    "listing_offset": 0,
                    "listing_hash": None,
                    "metadata": {"sitemap_depth": 7, "pagination_depth": 1},
                }
            )

    claim = SourceUrlBacklog(FakeDatabase()).claim("00000000-0000-4000-8000-000000000001")
    assert claim and claim.sitemap_depth == expected
    assert claim.pagination_depth == 1
