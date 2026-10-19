from datetime import UTC, datetime, timedelta

from hackathon_operations.isolation import SourceAvailability, evaluate_source_failure_isolation
from hackathon_operations.quality import EventQualityInput, compute_quality
from hackathon_operations.regression import SourceRunSample, detect_source_regression
from hackathon_operations.scheduler import RecrawlInput, plan_recrawl


def test_quality_rewards_fresh_multisource_verified_event() -> None:
    now = datetime(2026, 9, 22, tzinfo=UTC)
    score = compute_quality(
        EventQualityInput(
            has_name=True,
            has_dates=True,
            has_eligibility=True,
            has_prize=True,
            has_location=True,
            source_count=5,
            last_verified_at=now - timedelta(hours=2),
            has_official_source=True,
        ),
        now=now,
    )
    assert score.overall >= 95
    assert score.freshness == 100


def test_quality_penalizes_conflicts_and_staleness() -> None:
    now = datetime(2026, 9, 22, tzinfo=UTC)
    score = compute_quality(
        EventQualityInput(
            has_name=True,
            has_dates=True,
            has_eligibility=False,
            has_prize=False,
            has_location=True,
            source_count=1,
            last_verified_at=now - timedelta(days=45),
            open_conflicts=3,
        ),
        now=now,
    )
    assert score.consistency == 25
    assert score.freshness == 10
    assert score.overall < 60


def test_parser_zero_output_is_critical_regression() -> None:
    signals = detect_source_regression(
        SourceRunSample(fetched=40, parsed=38, valid_events=30),
        SourceRunSample(fetched=40, parsed=0, valid_events=0),
    )
    codes = {signal.code for signal in signals}
    assert "PARSER_ZERO_OUTPUT" in codes
    assert "EVENT_COUNT_COLLAPSE" in codes


def test_recrawl_is_faster_near_deadline() -> None:
    now = datetime(2026, 9, 22, tzinfo=UTC)
    plan = plan_recrawl(
        RecrawlInput(
            event_start_at=now + timedelta(days=2),
            registration_close_at=now + timedelta(hours=12),
            historical_change_probability=0.8,
            user_interest=0.9,
            source_value=0.9,
        ),
        now=now,
    )
    assert plan.priority >= 0.8
    assert plan.interval == timedelta(hours=1)


def test_one_broken_source_does_not_take_product_down() -> None:
    result = evaluate_source_failure_isolation(
        [
            SourceAvailability("source-a", False, 100),
            SourceAvailability("source-b", True, 80),
            SourceAvailability("source-c", True, 40),
        ]
    )
    assert result.product_available is True
    assert result.healthy_sources == 2
    assert result.failed_sources == ("source-a",)
