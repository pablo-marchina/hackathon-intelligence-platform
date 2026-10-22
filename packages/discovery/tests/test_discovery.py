import json
from datetime import UTC, date, datetime
from pathlib import Path

from hackathon_discovery.classifier import CLASSIFIER_VERSION, classify_page
from hackathon_discovery.feeds import (
    bounded_domain_candidates,
    common_discovery_urls,
    parse_robots_sitemaps,
    parse_rss_atom,
    parse_sitemap,
)
from hackathon_discovery.links import ExplorationBudget, discover_related_links
from hackathon_discovery.queries import QUERY_TEMPLATE_VERSION, build_templates, generate_queries
from hackathon_discovery.search import SearchResult, ingest_search_results, to_discovered_urls


def test_multilingual_query_generator_rotates_year_and_quarter() -> None:
    queries = generate_queries(today=date(2026, 9, 23), languages=("en", "pt", "ja"))
    assert queries
    assert {q.language for q in queries} == {"en", "pt", "ja"}
    assert {q.year for q in queries} >= {2026, 2027}
    windows = {q.time_window for q in queries}
    assert {"2026", "2027", "2026-Q3", "2026-Q4"}.issubset(windows)
    assert all(q.template_version == QUERY_TEMPLATE_VERSION for q in queries)
    assert all(q.attribution_key for q in queries)
    templates = build_templates(languages=("en",))
    assert len({t.template_key for t in templates}) == len(templates)
    assert any("prize" in q.query.casefold() for q in queries if q.language == "en")


def test_search_result_ingestion_preserves_provenance_policy_and_dedup() -> None:
    results = [
        SearchResult(
            url="https://known.example/event?utm_source=x",
            title="Known",
            snippet="hackathon",
            rank=1,
        ),
        SearchResult(url="https://new.example/hack", title="New", snippet="registration", rank=2),
        SearchResult(
            url="https://new.example/hack#fragment",
            title="Duplicate",
            snippet="registration",
            rank=3,
        ),
        SearchResult(
            url="https://blocked.example/hack", title="Blocked", snippet="hackathon", rank=4
        ),
    ]
    records = ingest_search_results(
        results,
        query='"hackathon" registration 2026',
        provider="authorized-test-provider",
        policy_for_url=lambda url: "BLOCKED" if "blocked.example" in url else "ALLOWED",
        existing_canonical_urls={"https://known.example/event"},
        discovered_at=datetime(2026, 9, 23, 12, tzinfo=UTC),
    )
    assert [record.action for record in records] == [
        "KNOWN_CANONICAL",
        "NEW_CANDIDATE",
        "POLICY_BLOCKED",
    ]
    assert records[1].rank == 2
    assert records[1].query.endswith("2026")
    assert records[1].evidence_authority == "DISCOVERY_ONLY"
    frontier = to_discovered_urls(records)
    assert len(frontier) == 1
    assert frontier[0].metadata["snippet_authority"] == "DISCOVERY_ONLY"


def test_robots_sitemap_rss_and_domain_caps() -> None:
    robots = (
        "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\nSitemap: https://x.example/events.xml\n"
    )
    assert parse_robots_sitemaps(robots, base_url="https://x.example/robots.txt") == [
        "https://x.example/sitemap.xml",
        "https://x.example/events.xml",
    ]
    urls, children = parse_sitemap(
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://x.example/events/a</loc><lastmod>2026-09-01</lastmod></url>"
        "<url><loc>https://x.example/events/b</loc></url></urlset>",
        max_urls=1,
    )
    assert [item.url for item in urls] == ["https://x.example/events/a"]
    assert not children
    _, children = parse_sitemap(
        "<sitemapindex><sitemap><loc>https://x.example/a.xml</loc></sitemap><sitemap><loc>https://x.example/b.xml</loc></sitemap></sitemapindex>",
        max_child_sitemaps=1,
    )
    assert children == ["https://x.example/a.xml"]
    feed = parse_rss_atom(
        "<rss><channel><item><title>Hack</title><link>https://x.example/hack</link></item></channel></rss>"
    )
    assert feed[0].title == "Hack"
    assert bounded_domain_candidates(feed * 3, domain="x.example", cap=1) == [feed[0]]
    common = common_discovery_urls("https://x.example/events")
    assert common["robots"] == ["https://x.example/robots.txt"]


def test_related_link_discovery_is_role_aware_and_bounded() -> None:
    html = """
    <a href="/rules">Official Rules</a>
    <a href="https://register.example/apply">Apply now</a>
    <a href="/faq">FAQ</a>
    <a href="/results">Winners</a>
    <a href="/schedule">Schedule</a>
    <a href="/about">About us</a>
    <a href="https://outside-two.example/register">Register</a>
    """
    budget = ExplorationBudget(max_depth=1, max_pages_per_domain=2, max_external_domains=1)
    links = discover_related_links(html, "https://event.example/hack", depth=0, budget=budget)
    roles = {link.role for link in links}
    assert {"rules", "registration", "faq", "results", "schedule", "organizer"}.issubset(roles)
    assert sum(link.external_domain for link in links) == 1
    assert links[0].role in {"rules", "registration"}
    assert discover_related_links(html, "https://event.example/hack", depth=2, budget=budget) == []


def test_classifier_contract_and_precision_golden_dataset() -> None:
    root = Path(__file__).resolve().parents[3]
    data = json.loads((root / "evals/discovery/golden_v0_2.json").read_text(encoding="utf-8"))
    tp = fp = fn = 0
    for item in data:
        result = classify_page(title=item["title"], text=item["text"])
        assert result.classifier_version == CLASSIFIER_VERSION
        assert isinstance(result.is_event, bool)
        assert result.event_type
        assert 0 <= result.confidence <= 1
        pred = result.is_hackathon_like
        label = bool(item["label"])
        tp += int(pred and label)
        fp += int(pred and not label)
        fn += int((not pred) and label)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    assert len(data) >= 80
    assert len({row["language"] for row in data}) >= 8
    assert len({row["source_url"].split("/")[2] for row in data}) >= 20
    assert precision >= 0.95
    assert recall >= 0.85
