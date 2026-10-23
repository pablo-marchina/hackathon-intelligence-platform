"""Official API pagination durability and bounded, non-overstated receipts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from hackathon_sources.models import IngestionMode, PermissionStatus
from hackathon_sources.registry import load_source_definitions

from scripts.ops.controlled_catalog_ingestion import _backlog_coverage


def test_only_explicitly_authorized_registry_apis_opt_into_durable_pages() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    sources = load_source_definitions(root / "db/seeds/sources_v0_1.json")
    approved = {
        item.key
        for item in sources
        if item.permission_status is PermissionStatus.ALLOWED
        and item.ingestion_mode is IngestionMode.API
        and item.durable_api_pages
    }
    assert approved == {
        "hackclub_hackathons",
        "hackclub_programs",
        "hackclub_community_events",
        "agenda_tech_brasil",
    }
    assert all(
        not item.durable_api_pages
        for item in sources
        if item.permission_status is not PermissionStatus.ALLOWED
    )


def test_source_coverage_receipt_differentiates_bounded_and_drained() -> None:
    result = SimpleNamespace(fetched_pages=1, backlog_counts={"PENDING": 2, "DONE": 1})
    receipt = _backlog_coverage(result, max_pages=1, tracked=True)
    assert receipt["backlog_tracked"] is True
    assert receipt["unprocessed_backlog_urls"] == 2
    assert receipt["budget_hit_with_pending_urls"] is True

    result.backlog_counts = {"DONE": 3}
    receipt = _backlog_coverage(result, max_pages=1, tracked=True)
    assert receipt["unprocessed_backlog_urls"] == 0
    assert receipt["budget_hit_with_pending_urls"] is False

    result.backlog_counts = {"RETRY": 1, "CLAIMED": 1, "EXHAUSTED": 2}
    receipt = _backlog_coverage(result, max_pages=0, tracked=True)
    assert receipt["unprocessed_backlog_urls"] == 2
    assert receipt["backlog_exhausted_urls"] == 2


def test_untracked_sources_must_not_claim_zero_unprocessed_urls() -> None:
    receipt = _backlog_coverage(SimpleNamespace(fetched_pages=0), max_pages=1, tracked=False)
    assert receipt["backlog_tracked"] is False
    assert receipt["unprocessed_backlog_urls"] is None
    assert receipt["budget_hit_with_pending_urls"] is None


@pytest.mark.parametrize(
    "fetched,pending,budget,expected",
    [
        (0, 2, 1, False),
        (1, 2, 1, True),
        (1, 0, 1, False),
        (2, 2, 3, False),
    ],
)
def test_page_budget_flag_reflects_actual_pending_work(
    fetched: int, pending: int, budget: int, expected: bool
) -> None:
    result = SimpleNamespace(fetched_pages=fetched, backlog_counts={"PENDING": pending})
    assert (
        _backlog_coverage(result, max_pages=budget, tracked=True)["budget_hit_with_pending_urls"]
        is expected
    )
