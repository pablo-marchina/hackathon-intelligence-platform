from datetime import UTC, datetime, timedelta

from hackathon_intelligence.changes import is_meaningful_change
from hackathon_intelligence.conflicts import detect_conflict
from hackathon_intelligence.dedup import EventFingerprint, build_features, dedup_decision, duplicate_score, generate_candidate_pairs
from hackathon_intelligence.normalization import normalize_url_signature
from hackathon_intelligence.reconciliation import CandidateObservation, ReconciliationPolicy, select_canonical


def test_reconciliation_prefers_authoritative_fresh_candidate() -> None:
    now = datetime.now(UTC)
    candidates = [
        CandidateObservation(1, "2026-10-01", 0.5, 0.95, 1.0, 0.5, now - timedelta(days=1)),
        CandidateObservation(2, "2026-10-02", 1.0, 0.99, 1.0, 0.8, now),
    ]
    winner, score = select_canonical(candidates, ReconciliationPolicy(), now=now)
    assert winner.observation_id == 2
    assert score > 0.8


def test_conflict_and_change_detection() -> None:
    conflict, severity = detect_conflict("registration_close_at", ["2026-10-01", "2026-10-02"])
    assert conflict and severity == "HIGH"
    assert not is_meaningful_change("name", "AI  Hackathon", "ai hackathon")


def test_equivalent_www_urls_do_not_create_conflict() -> None:
    conflict, severity = detect_conflict(
        "official_url",
        ["https://www.sierrahacks.com/", "https://sierrahacks.com"],
    )
    assert not conflict
    assert severity == "LOW"
    assert normalize_url_signature("https://www.sierrahacks.com/") == "https://sierrahacks.com/"


def test_dedup_exact_url_auto_merges_when_dates_are_compatible() -> None:
    now = datetime.now(UTC)
    left = EventFingerprint("AI Hack", "Org", now, "São Paulo", "https://www.x.test/h")
    right = EventFingerprint("AI Hackathon", "Org", now, "Sao Paulo", "https://x.test/h/")
    score = duplicate_score(build_features(left, right))
    assert dedup_decision(score) == "AUTO_MERGE"


def test_reused_official_url_does_not_merge_different_editions() -> None:
    start = datetime(2024, 2, 3, tzinfo=UTC)
    left = EventFingerprint("Bell Hacks", "Org", start, "Bell", "https://bellhacks.tech")
    right = EventFingerprint("Bell Hacks", "Org", start + timedelta(days=259), "Bell", "https://bellhacks.tech/")
    score = duplicate_score(build_features(left, right))
    assert score == 0.70
    assert dedup_decision(score) == "SEPARATE"


def test_reused_official_url_without_dates_requires_review() -> None:
    left = EventFingerprint("Recurring Hack", "Org", None, "City", "https://recurring.example")
    right = EventFingerprint("Recurring Hack 2027", "Org", None, "City", "https://www.recurring.example/")
    score = duplicate_score(build_features(left, right))
    assert score == 0.90
    assert dedup_decision(score) == "REVIEW"


def test_series_inference_groups_recurring_bell_hacks_but_not_same_window_duplicates() -> None:
    from hackathon_intelligence.series import SeriesEvent, infer_series

    bell_1 = SeriesEvent(
        "bell-feb-2024",
        "Bell Hacks",
        datetime(2024, 2, 3, 7, 30, tzinfo=UTC),
        "https://bellhacks.tech",
        "Hack Club community",
        "Bell",
    )
    bell_2 = SeriesEvent(
        "bell-oct-2024",
        "Bell Hacks",
        datetime(2024, 10, 19, 7, 30, tzinfo=UTC),
        "https://bellhacks.tech/",
        "Hack Club community",
        "Bell",
    )
    bell_3 = SeriesEvent(
        "bell-2025",
        "BellHacks",
        datetime(2025, 12, 14, 7, 30, tzinfo=UTC),
        "https://bellhacks.com/",
        "Hack Club community",
        "Bell",
    )
    sierra_a = SeriesEvent(
        "sierra-a",
        "Sierra Hacks 2025",
        datetime(2025, 5, 17, 8, 0, tzinfo=UTC),
        "https://www.sierrahacks.com/",
        "Hack Club community",
        "City",
    )
    sierra_b = SeriesEvent(
        "sierra-b",
        "Sierra Hacks 2025",
        datetime(2025, 5, 17, 8, 0, tzinfo=UTC),
        "https://sierrahacks.com/",
        "Hack Club community",
        "City",
    )

    clusters = infer_series([bell_1, bell_2, bell_3, sierra_a, sierra_b])
    assert "bellhacks" in clusters
    assignments = clusters["bellhacks"]
    assert {item.event_id for item in assignments} == {"bell-feb-2024", "bell-oct-2024", "bell-2025"}
    labels = {item.event_id: item.edition_label for item in assignments}
    assert labels["bell-feb-2024"].startswith("2024-02")
    assert labels["bell-oct-2024"].startswith("2024-10")
    assert labels["bell-2025"] == "2025"
    assert all("sierra" not in key for key in clusters)


def test_blocking_generator_avoids_all_pairs_and_records_reasons() -> None:
    base = datetime(2026, 7, 1, tzinfo=UTC)
    events = [
        EventFingerprint("AI Hackathon", "Org A", base, "Boston", "https://hack.example", ("evt-1",), ("https://github.com/org/repo",), "build AI agents"),
        EventFingerprint("AI Hackathon!", "Org A", base + timedelta(hours=2), "Boston", "https://www.hack.example/", ("evt-1",), ("https://github.com/org/repo",), "build ai agents"),
        EventFingerprint("Robotics Jam", "Org B", base + timedelta(days=60), "Toronto", "https://robotics.example", ("evt-2",), (), "robotics"),
        EventFingerprint("Climate Code", "Org C", base + timedelta(days=120), "Lisbon", "https://climate.example", ("evt-3",), (), "climate"),
    ]
    pairs = generate_candidate_pairs(events)
    assert len(pairs) < 6  # 4 events would produce 6 all-pairs combinations.
    match = next(pair for pair in pairs if {pair.left_index, pair.right_index} == {0, 1})
    assert "external" in match.blocking_reasons
    assert "url" in match.blocking_reasons
    assert match.features.url_graph_similarity == 1.0
    assert match.features.textual_similarity > 0.95
    assert match.decision == "AUTO_MERGE"


def test_url_graph_and_textual_signals_contribute_to_fuzzy_score() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    left = EventFingerprint("Agent Builder Day", "Open Lab", start, "NYC", None, (), ("https://github.com/open-lab/agents",), "build autonomous agents")
    right = EventFingerprint("Agent Builder Days", "Open Lab", start + timedelta(days=1), "NYC", None, (), ("https://github.com/open-lab/agents",), "build autonomous agent systems")
    features = build_features(left, right)
    assert features.url_graph_similarity == 1.0
    assert features.textual_similarity > 0.8
    assert duplicate_score(features) > 0.8
