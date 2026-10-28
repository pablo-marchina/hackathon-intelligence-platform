"""Source-agnostic opt-in scheduler and bounded canonical processing contracts."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from hackathon_operations import dispatcher
from hackathon_operations.lease_heartbeat import LeaseLostError
from hackathon_sources.models import IngestionMode, PermissionStatus, SourceDefinition


def source(
    key: str,
    mode: IngestionMode,
    *,
    permission: PermissionStatus = PermissionStatus.ALLOWED,
    detail: bool = True,
    discovery: bool = True,
    seeds: bool = True,
) -> SourceDefinition:
    return SourceDefinition(
        key=key,
        name=key,
        domain=f"{key}.example.org",
        source_family=key,
        source_type="test",
        ingestion_mode=mode,
        permission_status=permission,
        detail_extraction_enabled=detail,
        discovery_enabled=discovery,
        seed_urls=[f"https://{key}.example.org/events"] if seeds else [],
    )


def test_scheduler_selects_only_approved_parseable_sources() -> None:
    definitions = [
        source("hackclub_hackathons", IngestionMode.API),
        source("html_allowed", IngestionMode.STATIC_HTML),
        source("rss_allowed", IngestionMode.RSS),
        source("sitemap_allowed", IngestionMode.SITEMAP),
        source("generic_api_without_parser", IngestionMode.API),
        source(
            "html_review", IngestionMode.STATIC_HTML, permission=PermissionStatus.REVIEW_REQUIRED
        ),
        source(
            "html_restricted", IngestionMode.STATIC_HTML, permission=PermissionStatus.RESTRICTED
        ),
        source("html_paused_capability", IngestionMode.STATIC_HTML, detail=False),
        source("html_no_discovery", IngestionMode.STATIC_HTML, discovery=False),
        source("html_no_seeds", IngestionMode.STATIC_HTML, seeds=False),
        source("search_only", IngestionMode.SEARCH_DISCOVERY),
        source("browser", IngestionMode.BROWSER),
    ]
    selected = dispatcher.select_scheduled_sources(definitions)
    assert [s.key for s in selected] == [
        "hackclub_hackathons",
        "html_allowed",
        "rss_allowed",
        "sitemap_allowed",
    ]
    assert len(definitions) == 12, "the unapproved sources remain in the registry"


class FakeEngine:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def begin(self):
        try:
            yield object()
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


def test_scheduler_normalizes_bounded_versions_with_exact_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter([("CREATED", "CREATED"), ("UPDATED", "LINKED"), (None, None)])
    keys: list[str] = []

    class FakeProcessor:
        def __init__(self, connection: object, *, source_key: str) -> None:
            keys.append(source_key)
            self.last_outcome = None

        def process_one(self) -> str | None:
            status, self.last_outcome = next(responses)
            return status

    monkeypatch.setattr(dispatcher, "CanonicalProcessor", FakeProcessor)
    engine = FakeEngine()
    counts = dispatcher.normalize_scheduled_source(
        engine,
        source_key="rss_allowed",
        max_versions=5,
        should_continue=lambda: True,
    )
    assert counts == {"CREATED": 1, "LINKED": 1, "UPDATED": 0, "REJECTED": 0}
    assert keys == ["rss_allowed"] * 3
    assert engine.commits == 3


def test_scheduler_does_not_commit_after_lost_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcessor:
        last_outcome = "CREATED"

        def __init__(self, connection: object, *, source_key: str) -> None:
            pass

        def process_one(self) -> str:
            return "CREATED"

    monkeypatch.setattr(dispatcher, "CanonicalProcessor", FakeProcessor)
    checks = iter([True, False])
    engine = FakeEngine()
    with pytest.raises(LeaseLostError):
        dispatcher.normalize_scheduled_source(
            engine,
            source_key="test",
            max_versions=5,
            should_continue=lambda: next(checks),
        )
    assert engine.commits == 0
    assert engine.rollbacks == 1


def test_scheduler_refuses_revoked_source_during_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcessor:
        last_outcome = None

        def __init__(self, connection: object, *, source_key: str) -> None:
            pass

        def process_one(self) -> str:
            return "SKIPPED_UNAUTHORIZED"

    monkeypatch.setattr(dispatcher, "CanonicalProcessor", FakeProcessor)
    engine = FakeEngine()
    with pytest.raises(PermissionError):
        dispatcher.normalize_scheduled_source(
            engine,
            source_key="revoked",
            max_versions=5,
            should_continue=lambda: True,
        )
    assert engine.commits == 0
    assert engine.rollbacks == 1


def test_scheduler_budget_cannot_be_unbounded() -> None:
    for invalid in (0, 1001, -1):
        with pytest.raises(ValueError, match="budget"):
            dispatcher.normalize_scheduled_source(
                FakeEngine(),
                source_key="none",
                max_versions=invalid,
                should_continue=lambda: True,
            )
