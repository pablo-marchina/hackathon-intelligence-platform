from datetime import UTC, datetime, timedelta

from hackathon_alerts.delivery import RetryPolicy, render_alert_email
from hackathon_alerts.events import change_alert, deadline_alerts, new_match_alert
from hackathon_alerts.repository import saved_search_fingerprint


def test_saved_search_hash_is_order_independent() -> None:
    a = saved_search_fingerprint({"status": ["UPCOMING"], "free_only": True}, "w4-filter-v1")
    b = saved_search_fingerprint({"free_only": True, "status": ["UPCOMING"]}, "w4-filter-v1")
    assert a == b


def test_all_required_alert_types_and_timezone_payload() -> None:
    change_types = {
        change_alert(
            user_id="u", hackathon_id="h", change_id="1", change_type="deadline", payload={}
        ).alert_type,
        change_alert(
            user_id="u", hackathon_id="h", change_id="2", change_type="prize", payload={}
        ).alert_type,
        change_alert(
            user_id="u", hackathon_id="h", change_id="3", change_type="postponed", payload={}
        ).alert_type,
        change_alert(
            user_id="u", hackathon_id="h", change_id="4", change_type="eligibility", payload={}
        ).alert_type,
        new_match_alert(user_id="u", hackathon_id="h", saved_search_id="s", payload={}).alert_type,
    }
    assert change_types == {
        "deadline_changed",
        "prize_changed",
        "cancellation_postponement",
        "eligibility_changed",
        "new_match",
    }
    now = datetime(2026, 9, 23, tzinfo=UTC)
    alerts = deadline_alerts(
        user_id="u",
        hackathon_id="h",
        deadline=now + timedelta(hours=23),
        timezone="America/Sao_Paulo",
        now=now,
    )
    assert alerts and alerts[-1].payload["timezone"] == "America/Sao_Paulo"
    assert "deadline_local" in alerts[-1].payload


def test_retry_is_bounded_and_email_contains_context() -> None:
    policy = RetryPolicy(max_attempts=4, base_delay_seconds=60, multiplier=2)
    assert policy.delay_seconds(1) == 60
    assert policy.delay_seconds(4) is None
    subject, body = render_alert_email(
        alert_type="deadline_changed",
        event_name="MoonStone",
        event_url="https://example.test/hackathons/moonstone",
        payload={
            "field_path": "registration_close_at",
            "deadline_local": "2026-09-30T20:00:00-03:00",
            "timezone": "America/Sao_Paulo",
        },
    )
    assert "MoonStone" in subject
    assert (
        "registration_close_at" in body
        and "America/Sao_Paulo" in body
        and "https://example.test/hackathons/moonstone" in body
    )
